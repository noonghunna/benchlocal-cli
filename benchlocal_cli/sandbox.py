"""Docker lifecycle and HTTP client for sandboxed pack verifiers."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from benchlocal_cli.types import ScenarioResult


@dataclass(frozen=True)
class SandboxConfig:
    """Static config per sandboxed pack."""
    pack_id: str           # e.g. "bugfind-15"
    image_name: str        # e.g. "benchlocal-sandbox-bugfind:latest"
    host_port: int         # e.g. 9001
    network_isolated: bool # True for cli (untrusted exec); False for bugfind + hermes
    multi_turn: bool       # True for cli + hermes; False for bugfind
    # Optional host directories bind-mounted into the container.
    # Tuple of (host_path, container_path) entries — bound read-only.
    # Hermes uses two: the hermes-agent install AND its uv-managed Python
    # tree, both mounted at the same paths inside the container so that
    # venv shebangs and the python binary's hardcoded prefix resolve.
    # Empty tuple means no mounts.
    host_mounts: tuple[tuple[str, str], ...] = ()
    # Optional environment variables passed to `docker run -e ...`.
    env: tuple[tuple[str, str], ...] = ()
    # Per-pack default HTTP read timeout for /verify* calls in seconds.
    # Hermes runs upstream agent loops with real LLM calls and needs ~15min;
    # bugfind/cli stay at 60s.
    request_timeout_s: float = 60.0
    # #6: container path where the pack writes its per-unit run artifacts. When
    # the runner supplies a host run-dir (derived from --sandbox-log-dir), this
    # path is bind-mounted WRITABLE so artifacts persist to the host as they're
    # written — surviving `docker run --rm`, crashes, and timeouts. None means
    # the pack opts out of the run mount.
    run_output_dir: str | None = None
    # Extra `-e KEY=VALUE` env applied ONLY when the writable run mount is
    # active (e.g. tell the aider sandbox to keep its job dirs rather than
    # rmtree-ing them so the mounted artifacts survive the verify call).
    run_mount_env: tuple[tuple[str, str], ...] = ()


# #3: aider batch timeout budget. The inner subprocess cap (server-side
# AIDER_BENCHMARK_TIMEOUT_S) defaults here; --timeout-per-case can only RAISE
# it (never lower it below this default). request_timeout_s tracks the inner
# cap plus headroom so the outer HTTP read doesn't fire before the inner kill.
_AIDER_DEFAULT_BATCH_TIMEOUT_S = 3600.0
_AIDER_REQUEST_TIMEOUT_HEADROOM_S = 300.0


# Default registry of sandbox configs (read by Runner when --enable-sandboxed-packs is set).
SANDBOX_REGISTRY = {
    "bugfind-15": SandboxConfig(
        pack_id="bugfind-15",
        image_name="benchlocal-sandbox-bugfind:latest",
        host_port=9001,
        network_isolated=False,
        multi_turn=False,
    ),
    "cli-40": SandboxConfig(
        pack_id="cli-40",
        image_name="benchlocal-sandbox-cli:latest",
        host_port=9002,
        network_isolated=True,   # untrusted command exec — isolate from network
        multi_turn=True,
    ),
    "hermesagent-20": SandboxConfig(
        pack_id="hermesagent-20",
        image_name="benchlocal-sandbox-hermes:latest",
        host_port=9003,
        network_isolated=False,
        multi_turn=True,
        # 15-minute read timeout for /verify-start — upstream Hermes
        # agent-runner.py runs 10-20 turns of real LLM calls per scenario.
        request_timeout_s=900.0,
    ),
    "humaneval-plus-30": SandboxConfig(
        pack_id="humaneval-plus-30",
        image_name="benchlocal-sandbox-code-reasoning:latest",
        host_port=9005,
        network_isolated=True,
        multi_turn=False,
        request_timeout_s=300.0,
    ),
    "lcb-v6-30": SandboxConfig(
        pack_id="lcb-v6-30",
        image_name="benchlocal-sandbox-code-reasoning:latest",
        host_port=9006,
        network_isolated=True,
        multi_turn=False,
        request_timeout_s=300.0,
    ),
    "aider-polyglot-30": SandboxConfig(
        pack_id="aider-polyglot-30",
        image_name="benchlocal-sandbox-aider-polyglot:latest",
        host_port=9004,
        network_isolated=False,  # aider needs to call out to model_endpoint
        multi_turn=True,         # uses /verify-start with verify-final early-out
        # Read timeout: the entire batch (30 exercises × multi-turn aider
        # edit/test loops) lives inside one /verify-start call. Tracks the inner
        # subprocess cap (_AIDER_DEFAULT_BATCH_TIMEOUT_S = 3600s) plus headroom.
        # config_for_pack recomputes this from --timeout-per-case so slow rigs
        # (24 GB single card, low-power, long-context models) can raise it; the
        # default was bumped from 2700→3600s after a PL250W rig hit the cap on
        # the last exercise (#3).
        request_timeout_s=3900.0,
        # #6: persist per-exercise artifacts to the host when --sandbox-log-dir
        # is set. server.py writes job dirs under /tmp/aider-polyglot-runs and
        # rmtree's them in a `finally` unless BENCHLOCAL_AIDER_KEEP_JOBDIRS=1 —
        # so the mount AND the keep-env are both needed for durability.
        run_output_dir="/tmp/aider-polyglot-runs",
        run_mount_env=(("BENCHLOCAL_AIDER_KEEP_JOBDIRS", "1"),),
    ),
}


def resolve_endpoint_for_container(endpoint: str) -> str:
    """v0.9.0 helper (Codex 2nd-pass #2): rewrite host-side endpoint URLs to
    container-reachable form when a sandbox needs to call out to the runner's
    model endpoint.

    Rules:
      - localhost / 127.0.0.1 / 127.x → host.docker.internal
      - [::1] → host.docker.internal (IPv6 loopback)
      - 0.0.0.0 → raise ValueError (it's a bind-only address, not a target)
      - non-loopback hosts (real hostnames, RFC1918 IPs, host.docker.internal,
        FQDNs) → unchanged
      - URL paths, ports, queries, fragments → preserved

    Linux note: `host.docker.internal` only resolves inside a container that
    was started with `--add-host=host.docker.internal:host-gateway`. The
    runner adds that flag to docker run for sandboxes that opt into this
    rewrite (default-on for aider-polyglot, opt-in via env for hermes).
    """
    if not endpoint:
        return endpoint
    from urllib.parse import urlparse, urlunparse
    parsed = urlparse(endpoint)
    host = (parsed.hostname or "").lower()
    if host == "0.0.0.0":
        raise ValueError(
            "endpoint host is 0.0.0.0 (bind-only, not a routable target). "
            "Pass a real host or use 127.0.0.1/localhost."
        )
    is_loopback = (
        host == "localhost"
        or host == "::1"
        or host.startswith("127.")
    )
    if not is_loopback:
        return endpoint
    # Reassemble with the new host. preserve port + path + query + fragment.
    port_suffix = f":{parsed.port}" if parsed.port else ""
    new_netloc = f"host.docker.internal{port_suffix}"
    if parsed.username:
        userinfo = parsed.username
        if parsed.password:
            userinfo += f":{parsed.password}"
        new_netloc = f"{userinfo}@{new_netloc}"
    return urlunparse(parsed._replace(netloc=new_netloc))


# Required files in a host-installed `hermes-agent` checkout. Used by detection
# to reject empty stub directories that exist but can't satisfy the upstream
# Python imports.
_HERMES_AGENT_REQUIRED_FILES = ("run_agent.py", "hermes_state.py")


def _is_valid_hermes_agent_install(path: Path) -> bool:
    if not path.is_dir():
        return False
    return all((path / f).is_file() for f in _HERMES_AGENT_REQUIRED_FILES)


def _resolve_via_which_hermes() -> str | None:
    """Resolve the install root by following `which hermes` through its symlink.

    The official installer creates a symlink at ~/.local/bin/hermes pointing
    into <install-root>/venv/bin/hermes. Walking up 3 levels lands on the
    install root. Tolerates non-standard install layouts (custom prefixes,
    pipx-style locations) without us needing to enumerate every possibility.
    """
    binary = shutil.which("hermes")
    if not binary:
        return None
    try:
        resolved = Path(binary).resolve(strict=True)
    except OSError:
        return None
    # Try a couple of likely walk-up depths to handle both venv-based
    # (root/venv/bin/hermes → root) and direct (root/bin/hermes → root) layouts.
    for ancestor in (resolved.parent.parent.parent, resolved.parent.parent):
        if _is_valid_hermes_agent_install(ancestor):
            return str(ancestor.resolve())
    return None


def detect_hermes_agent_host_path() -> str | None:
    """Resolve a host-installed hermes-agent path for bind-mounting.

    Order:
        1. HERMES_AGENT_FORCE_BAKED=1 → return None (skip host detection;
           container falls back to image-baked install)
        2. HERMES_AGENT_HOST_PATH=<dir> → use this path; raise if missing or
           doesn't look like a hermes-agent install (must contain run_agent.py
           and hermes_state.py)
        3. Auto-detect: check /opt/hermes-agent, ~/hermes-agent,
           ~/.local/hermes-agent, ~/.hermes/hermes-agent. If exactly one valid
           install is found, use it; if multiple, raise with set-HOST_PATH
           guidance.
        4. `which hermes` → follow the symlink → walk up to install root.
           Catches non-standard install layouts (custom prefixes, pipx-style
           locations).
        5. Otherwise None (caller falls through to image-baked or fail-loud).
    """
    if os.environ.get("HERMES_AGENT_FORCE_BAKED") == "1":
        return None
    explicit = os.environ.get("HERMES_AGENT_HOST_PATH")
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_dir():
            raise RuntimeError(
                f"HERMES_AGENT_HOST_PATH={explicit} is not a directory"
            )
        if not _is_valid_hermes_agent_install(path):
            raise RuntimeError(
                f"HERMES_AGENT_HOST_PATH={explicit} does not look like a "
                f"hermes-agent install (missing one of "
                f"{', '.join(_HERMES_AGENT_REQUIRED_FILES)})"
            )
        return str(path)
    candidates = [
        Path("/opt/hermes-agent"),
        Path.home() / "hermes-agent",
        Path.home() / ".local/hermes-agent",
        # The official `hermes` installer (curl … | sh) lays its source
        # checkout at ~/.hermes/hermes-agent. This is the most common host
        # install in practice — added 2026-05-09 after the v0.7.3 A/B run.
        Path.home() / ".hermes/hermes-agent",
    ]
    found = [str(p.resolve()) for p in candidates if _is_valid_hermes_agent_install(p)]
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        raise RuntimeError(
            f"multiple hermes-agent installs found: {found}; "
            f"set HERMES_AGENT_HOST_PATH to disambiguate"
        )
    # Last resort: ask `which hermes`. Catches non-standard install layouts.
    return _resolve_via_which_hermes()


def detect_hermes_agent_commit(path: str) -> str:
    """Best-effort `git rev-parse HEAD` on the host install. Returns 'unknown'
    on any failure — never raises. Captured into /health and verifier_trace
    so saved JSONs are self-describing about which upstream commit graded them.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", path, "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            commit = proc.stdout.strip()
            if commit:
                return commit
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    return "unknown"


class SandboxClient:
    """Manages one sandbox container's lifecycle + HTTP dispatch.

    Usage (will be wired into Runner by Codex Phase A):

        client = SandboxClient(SANDBOX_REGISTRY["bugfind-15"])
        client.start()          # docker run + wait for /health
        try:
            result = client.verify(scenario, response, messages)
            # result is a ScenarioResult-shaped dict from the container
        finally:
            client.stop()        # docker stop

    Or use as context manager:
        with SandboxClient(cfg) as client:
            result = client.verify(...)
    """

    def __init__(self, config: SandboxConfig) -> None:
        self.config = config
        self._container_id: str | None = None

    def _build_docker_run_argv(self, name: str, run_dir: str | None) -> list[str]:
        """Assemble the `docker run` argv. Pure (no side effects beyond reading
        config/env) so it can be unit-tested. `run_dir` is the host path to
        bind-mount writable at `config.run_output_dir` (#6); None disables it."""
        cmd = [
            "docker",
            "run",
            "--rm",
            "-d",
            "--name",
            name,
            "-p",
            f"{self.config.host_port}:9000",
        ]
        # Optional bind-mounts (Hermes maps hermes-agent install + uv python tree).
        for host_path, container_path in self.config.host_mounts:
            cmd.extend(["-v", f"{host_path}:{container_path}:ro"])
        for key, value in self.config.env:
            cmd.extend(["-e", f"{key}={value}"])
        # #6: writable host run-dir mount — persists per-unit run artifacts to
        # the host AS THEY'RE WRITTEN (survives --rm/crash/timeout, no docker cp
        # / teardown-capture race). Only when the caller supplies a host dir AND
        # this pack declares a run_output_dir. NOTE: deliberately NOT `:ro`.
        if run_dir and self.config.run_output_dir:
            cmd.extend(["-v", f"{run_dir}:{self.config.run_output_dir}"])
            for key, value in self.config.run_mount_env:
                cmd.extend(["-e", f"{key}={value}"])
        # v0.9.0: aider-polyglot needs to call out to the runner's model
        # endpoint from inside the container. On Linux, host.docker.internal
        # only resolves with this --add-host flag (Codex 2nd-pass #2).
        # Default-on for aider-polyglot; opt-in for hermes (preserves
        # existing hermes deployments where service-name resolution
        # already works).
        if (
            self.config.pack_id == "aider-polyglot-30"
            or os.environ.get("BENCHLOCAL_HERMES_RESOLVE_LOCALHOST") == "1"
        ):
            cmd.extend(["--add-host", "host.docker.internal:host-gateway"])
        cmd.append(self.config.image_name)
        return cmd

    def start(self, *, ready_timeout_s: float = 30.0, run_dir: str | None = None) -> None:
        """Start the container; block until /health returns 200 or ready_timeout_s expires.

        When `run_dir` is supplied and the pack declares `run_output_dir`, the
        host dir is created and bind-mounted writable so run artifacts persist
        to the host live (#6)."""
        if self._container_id:
            return
        name = f"benchlocal-{self.config.pack_id}-{int(time.time() * 1000)}"
        if run_dir and self.config.run_output_dir:
            Path(run_dir).mkdir(parents=True, exist_ok=True)
        cmd = self._build_docker_run_argv(name, run_dir)
        try:
            proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(f"failed to start sandbox {self.config.pack_id}: {exc}") from exc
        self._container_id = proc.stdout.strip()
        deadline = time.monotonic() + ready_timeout_s
        last_error = ""
        while time.monotonic() < deadline:
            try:
                response = httpx.get(f"http://127.0.0.1:{self.config.host_port}/health", timeout=1.0)
                if response.status_code == 200:
                    return
                last_error = f"HTTP {response.status_code}"
            except httpx.HTTPError as exc:
                last_error = str(exc)
            time.sleep(0.25)
        self.stop()
        raise RuntimeError(f"sandbox {self.config.pack_id} did not become healthy: {last_error}")

    def stop(self, log_dir: str | None = None) -> None:
        """Stop + remove the container. Idempotent (safe to call if not started).

        If `log_dir` is provided, captures `docker logs <cid>` to
        `<log_dir>/sandbox-<pack_id>.log` BEFORE stopping the container — the
        `docker run --rm` flag wipes logs on stop, so this is a one-shot
        snapshot for post-run forensics. Failures here are non-fatal.
        """
        if not self._container_id:
            return
        if log_dir:
            try:
                from pathlib import Path
                Path(log_dir).mkdir(parents=True, exist_ok=True)
                proc = subprocess.run(
                    ["docker", "logs", self._container_id],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                log_path = Path(log_dir) / f"sandbox-{self.config.pack_id}.log"
                log_path.write_text(
                    f"# benchlocal-cli sandbox log — {self.config.pack_id}\n"
                    f"# container_id: {self._container_id}\n"
                    f"# captured_at: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
                    f"\n"
                    f"=== STDOUT ===\n{proc.stdout}\n"
                    f"=== STDERR ===\n{proc.stderr}\n",
                    encoding="utf-8",
                )
            except Exception:  # noqa: BLE001
                pass  # logs are nice-to-have; never block container teardown
        subprocess.run(["docker", "stop", self._container_id], check=False, capture_output=True, text=True)
        self._container_id = None

    def verify(self, scenario: dict, response: dict, messages: list[dict]) -> ScenarioResult:
        """Single-turn verifier dispatch (BugFind, CLI). Returns ScenarioResult-shaped dict."""
        payload = {
            "scenario_id": scenario.get("id"),
            "scenario": scenario,
            "response": response,
            "messages": messages,
        }
        data = self._post("/verify", payload)
        return _result_from_payload(str(scenario.get("id", "unknown")), data)

    def verify_multiturn_start(
        self,
        scenario: dict,
        *,
        model_endpoint: str | None = None,
        model_name: str | None = None,
        model_api_key: str | None = None,
        sampling: dict | None = None,
    ) -> dict:
        """Initialize a sandbox-owned multi-turn scenario state.

        For Hermes (v0.7.3+), `model_endpoint` / `model_name` are required so
        the sandbox can spawn the upstream agent-runner against the same
        endpoint the runner is benching. For non-Hermes packs they are
        ignored (the sandbox makes no model calls).
        """
        payload: dict = {
            "scenario_id": scenario.get("id"),
            "scenario": scenario,
        }
        if model_endpoint is not None:
            payload["model_endpoint"] = model_endpoint
        if model_name is not None:
            payload["model_name"] = model_name
        if model_api_key is not None:
            payload["model_api_key"] = model_api_key
        if sampling is not None:
            payload["sampling"] = sampling
        return self._post("/verify-start", payload)

    def verify_multiturn_turn(self, scenario_state_id: str, model_response: dict) -> dict:
        """Advance one multi-turn step; returns next prompt or final result."""
        return self._post("/verify-turn", {"scenario_state_id": scenario_state_id, "model_response": model_response})

    def verify_multiturn_end(self, scenario_state_id: str) -> dict:
        """Explicit 'model gave up' or turn-limit completion."""
        return self._post("/verify-end", {"scenario_state_id": scenario_state_id})

    # Back-compat aliases kept for existing Hermes tests/callers.
    def verify_hermes_start(self, scenario: dict) -> dict:
        return self.verify_multiturn_start(scenario)

    def verify_hermes_turn(self, scenario_state_id: str, model_response: dict) -> dict:
        return self.verify_multiturn_turn(scenario_state_id, model_response)

    def verify_hermes_end(self, scenario_state_id: str) -> dict:
        return self.verify_multiturn_end(scenario_state_id)

    def _post(self, path: str, payload: dict, *, timeout_s: float | None = None) -> dict:
        # Per-pack default; hermes runs upstream agent loops needing 15min.
        # Callers can override per-call (e.g., /verify-turn might be shorter
        # than /verify-start for the same pack).
        timeout = timeout_s if timeout_s is not None else self.config.request_timeout_s
        response = httpx.post(
            f"http://127.0.0.1:{self.config.host_port}{path}",
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"sandbox {self.config.pack_id} returned non-object JSON")
        return data

    def __enter__(self) -> SandboxClient:
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()


def _resolve_venv_python_root(host_install: str) -> tuple[str, str] | None:
    """Resolve `<install>/venv/bin/python` to its target binary's install root.

    Used to mount the venv's underlying CPython into the container at the
    same path it lives on the host, so the venv shebang + python's hardcoded
    prefix both resolve. Returns (host_root, host_root) — both sides identical
    because the path must match. None if no venv-style python is detectable.

    Common layouts handled:
      - uv-managed: `~/.local/share/uv/python/cpython-3.11-...-gnu/bin/python3.11`
        → mount root: `~/.local/share/uv/python` (covers all uv-installed pythons)
      - system python: `/usr/bin/python3.X` → no mount needed (already in container path)
    """
    venv_python = Path(host_install) / "venv" / "bin" / "python"
    try:
        target = venv_python.resolve(strict=True)
    except OSError:
        return None
    target_str = str(target)
    # uv lays pythons under `~/.local/share/uv/python/<distribution>/bin/`. Mount
    # the parent `python` dir so all uv-managed pythons are visible (small cost
    # since most rigs only have one).
    uv_marker = "/.local/share/uv/python/"
    if uv_marker in target_str:
        idx = target_str.index(uv_marker)
        uv_root = target_str[: idx + len(uv_marker) - 1]  # strip trailing /
        return (uv_root, uv_root)
    # System python under /usr already exists in the container's image; no mount.
    if target_str.startswith("/usr/"):
        return None
    # Otherwise mount the python's install root — walk up from `bin/python` 2 levels.
    install_root = str(target.parent.parent)
    return (install_root, install_root)


def config_for_pack(
    pack_id: str,
    image_tag: str = "latest",
    *,
    batch_timeout_s: float | None = None,
) -> SandboxConfig:
    config = SANDBOX_REGISTRY[pack_id]
    base = config.image_name.split(":", 1)[0]
    host_mounts: tuple[tuple[str, str], ...] = config.host_mounts
    env: tuple[tuple[str, str], ...] = config.env
    request_timeout_s = config.request_timeout_s
    if pack_id != "aider-polyglot-30" and batch_timeout_s and batch_timeout_s > request_timeout_s:
        request_timeout_s = float(batch_timeout_s)

    if pack_id == "aider-polyglot-30":
        # v0.9.0: parallelize aider's batch across N threads. Default 1 is
        # conservative for llama.cpp/ik_llama single-slot (-np 1) servers;
        # users with multi-slot endpoints can raise BENCHLOCAL_AIDER_THREADS.
        threads = os.environ.get("BENCHLOCAL_AIDER_THREADS", "1")
        env = env + (("AIDER_BENCHMARK_THREADS", threads),)
        # #3: the whole 30-exercise batch runs inside one /verify-start call,
        # so the per-case timeout governs the BATCH budget here. Slow rigs
        # (low-power single card, long-context models) need more than the
        # default. `--timeout-per-case` can only RAISE the inner subprocess
        # cap (never drop it below the default); request_timeout_s tracks it
        # with headroom so the outer HTTP read doesn't fire first.
        inner = _AIDER_DEFAULT_BATCH_TIMEOUT_S
        if batch_timeout_s and batch_timeout_s > inner:
            inner = float(batch_timeout_s)
        env = env + (("AIDER_BENCHMARK_TIMEOUT_S", str(int(inner))),)
        request_timeout_s = inner + _AIDER_REQUEST_TIMEOUT_HEADROOM_S

    if pack_id == "hermesagent-20":
        # Per-scenario subprocess wall-clock cap inside the container. Default
        # to 300s (5 min) — long enough for legitimate multi-turn agent loops
        # but short enough that a stuck scenario doesn't burn the whole bench.
        # Override via BENCHLOCAL_HERMES_SUBPROCESS_TIMEOUT_S on the runner.
        sub_timeout = os.environ.get("BENCHLOCAL_HERMES_SUBPROCESS_TIMEOUT_S", "300")
        env = env + (("HERMES_SUBPROCESS_TIMEOUT_S", sub_timeout),)
        # Hermes-agent v0.13+ enforces a 64K context-window minimum on the
        # served model. Models at smaller windows (Gemma 4 at 32K) fail this
        # check even though scenarios fit in <8K tokens. Inject the override
        # into upstream's writeHermesConfig() via env. Default 64000 (the
        # minimum that satisfies the check); set to 0 to disable.
        ctx_override = os.environ.get("BENCHLOCAL_HERMES_CONTEXT_OVERRIDE", "64000")
        env = env + (("BENCHLOCAL_HERMES_CONTEXT_OVERRIDE", ctx_override),)
        # Auto-detect a host-installed hermes-agent. We mount it at the SAME
        # path inside the container (not /opt/hermes-agent) so the venv's
        # shebangs and the uv-managed python's hardcoded prefix both resolve.
        host_path = detect_hermes_agent_host_path()
        if host_path:
            mounts = [(host_path, host_path)]
            # If the install has a venv with a uv-managed python, also bind-
            # mount the uv python tree so the symlink chain resolves inside
            # the container. System pythons (/usr/bin/python3.x) don't need
            # this — the container's own Python image already has them.
            python_mount = _resolve_venv_python_root(host_path)
            if python_mount:
                mounts.append(python_mount)
            host_mounts = tuple(mounts)
            commit = detect_hermes_agent_commit(host_path)
            env_pairs = list(env) + [
                ("BENCHLOCAL_HERMES_AGENT_COMMIT", commit),
                ("HERMES_AGENT_PATH", host_path),
            ]
            # Prefer the host venv's python if it exists — it has hermes-agent's
            # deps installed at the right versions.
            venv_python = str(Path(host_path) / "venv" / "bin" / "python")
            if Path(venv_python).is_file() or Path(venv_python).is_symlink():
                env_pairs.append(("HERMES_AGENT_PYTHON", venv_python))
            env = tuple(env_pairs)
    return SandboxConfig(
        pack_id=config.pack_id,
        image_name=f"{base}:{image_tag}",
        host_port=config.host_port,
        network_isolated=config.network_isolated,
        multi_turn=config.multi_turn,
        host_mounts=host_mounts,
        env=env,
        request_timeout_s=request_timeout_s,
        run_output_dir=config.run_output_dir,
        run_mount_env=config.run_mount_env,
    )


def _result_from_payload(scenario_id: str, payload: dict) -> ScenarioResult:
    # Preserve the full upstream payload for forensics — rawLog, notes,
    # subscore breakdowns (correctness/efficiency/discipline for CLI etc.),
    # any diagnostic fields the verifier produced. Strip the redundant
    # top-level passed/failure_mode/detail since those are already in the
    # structured ScenarioResult fields.
    trace = {k: v for k, v in payload.items() if k not in ("passed", "failure_mode", "detail")}
    return ScenarioResult(
        scenario_id=scenario_id,
        passed=bool(payload.get("passed")),
        failure_mode=payload.get("failure_mode", "verifier_fail"),
        detail=str(payload.get("detail", "")),
        verifier_trace=trace if trace else None,
    )
