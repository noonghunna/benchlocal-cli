"""Docker lifecycle and HTTP client for sandboxed pack verifiers."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

import httpx

from benchlocal_cli.types import ScenarioResult


@dataclass(frozen=True)
class SandboxConfig:
    """Static config per sandboxed pack."""
    pack_id: str           # e.g. "bugfind-15"
    image_name: str        # e.g. "benchlocal-sandbox-bugfind:latest"
    host_port: int         # e.g. 9001
    network_isolated: bool # True for cli (untrusted exec); False for bugfind + hermes
    multi_turn: bool       # True for hermes; False for bugfind + cli


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
        multi_turn=False,
    ),
    "hermesagent-20": SandboxConfig(
        pack_id="hermesagent-20",
        image_name="benchlocal-sandbox-hermes:latest",
        host_port=9003,
        network_isolated=False,
        multi_turn=True,
    ),
}


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

    def start(self, *, ready_timeout_s: float = 30.0) -> None:
        """Start the container; block until /health returns 200 or ready_timeout_s expires."""
        if self._container_id:
            return
        name = f"benchlocal-{self.config.pack_id}-{int(time.time() * 1000)}"
        cmd = [
            "docker",
            "run",
            "--rm",
            "-d",
            "--name",
            name,
            "-p",
            f"{self.config.host_port}:9000",
            self.config.image_name,
        ]
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

    def stop(self) -> None:
        """Stop + remove the container. Idempotent (safe to call if not started)."""
        if not self._container_id:
            return
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

    # Multi-turn (Hermes-specific) — Codex Phase D
    def verify_hermes_start(self, scenario: dict) -> dict:
        """Hermes only: initialize scenario state, return first prompt + tools."""
        return self._post("/verify-start", {"scenario_id": scenario.get("id"), "scenario": scenario})

    def verify_hermes_turn(self, scenario_state_id: str, model_response: dict) -> dict:
        """Hermes only: simulate one tool turn, return next prompt OR final pass/fail."""
        return self._post("/verify-turn", {"scenario_state_id": scenario_state_id, "model_response": model_response})

    def verify_hermes_end(self, scenario_state_id: str) -> dict:
        """Hermes only: explicit 'model gave up' or 'turn limit reached'."""
        return self._post("/verify-end", {"scenario_state_id": scenario_state_id})

    def _post(self, path: str, payload: dict) -> dict:
        response = httpx.post(
            f"http://127.0.0.1:{self.config.host_port}{path}",
            json=payload,
            timeout=60.0,
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


def config_for_pack(pack_id: str, image_tag: str = "latest") -> SandboxConfig:
    config = SANDBOX_REGISTRY[pack_id]
    base = config.image_name.split(":", 1)[0]
    return SandboxConfig(
        pack_id=config.pack_id,
        image_name=f"{base}:{image_tag}",
        host_port=config.host_port,
        network_isolated=config.network_isolated,
        multi_turn=config.multi_turn,
    )


def _result_from_payload(scenario_id: str, payload: dict) -> ScenarioResult:
    return ScenarioResult(
        scenario_id=scenario_id,
        passed=bool(payload.get("passed")),
        failure_mode=payload.get("failure_mode", "verifier_fail"),
        detail=str(payload.get("detail", "")),
    )
