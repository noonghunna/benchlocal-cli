"""SandboxClient — Docker lifecycle + HTTP verifier client for sandboxed packs.

🚧 SCAFFOLDING ONLY — interface defined; methods raise NotImplementedError.

TODO (Codex per CODEX_BRIEF_V4.md Phase A): full implementation.

Architecture:
    - One SandboxClient per sandboxed pack (bugfind, cli, hermes)
    - On runner.run() start: start() the container if pack is in the requested list
    - Per scenario: verify(scenario, response, messages) → ScenarioResult via HTTP
    - On runner.run() end: stop() the container

The 3 sandbox containers expose:
    - bugfind  → port :9001 (host) → :9000 (container) — POST /verify
    - cli      → port :9002 (host) → :9000 (container) — POST /verify
    - hermes   → port :9003 (host) → :9000 (container) — POST /verify-{start,turn,end}

Hermes is special: multi-turn lifecycle. The runner orchestrates the loop;
SandboxClient.verify_hermes_turn() exposes the per-turn dispatch for it.

Implementation hints:
    - `docker run --rm -d --network none -p <host_port>:9000 <image>` for cli sandbox
       (--network none for security: cli sandbox runs untrusted-but-bounded commands)
    - `docker run --rm -d -p <host_port>:9000 <image>` for bugfind + hermes (no need for --network none)
    - Use httpx for HTTP client (already a runtime dep)
    - Wait for /health to return 200 before declaring container ready (poll with backoff up to 30s)
    - SIGINT handler in runner stops all SandboxClient containers cleanly
"""

from __future__ import annotations

from dataclasses import dataclass


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
        raise NotImplementedError(
            "SandboxClient.start — pre-alpha. See module docstring + CODEX_BRIEF_V4.md Phase A."
        )

    def stop(self) -> None:
        """Stop + remove the container. Idempotent (safe to call if not started)."""
        raise NotImplementedError(
            "SandboxClient.stop — pre-alpha. See module docstring."
        )

    def verify(self, scenario: dict, response: dict, messages: list[dict]) -> dict:
        """Single-turn verifier dispatch (BugFind, CLI). Returns ScenarioResult-shaped dict."""
        raise NotImplementedError(
            "SandboxClient.verify — pre-alpha. See module docstring."
        )

    # Multi-turn (Hermes-specific) — Codex Phase D
    def verify_hermes_start(self, scenario: dict) -> dict:
        """Hermes only: initialize scenario state, return first prompt + tools."""
        raise NotImplementedError(
            "SandboxClient.verify_hermes_start — pre-alpha. See CODEX_BRIEF_V4.md Phase D."
        )

    def verify_hermes_turn(self, scenario_state_id: str, model_response: dict) -> dict:
        """Hermes only: simulate one tool turn, return next prompt OR final pass/fail."""
        raise NotImplementedError(
            "SandboxClient.verify_hermes_turn — pre-alpha. See CODEX_BRIEF_V4.md Phase D."
        )

    def verify_hermes_end(self, scenario_state_id: str) -> dict:
        """Hermes only: explicit 'model gave up' or 'turn limit reached'."""
        raise NotImplementedError(
            "SandboxClient.verify_hermes_end — pre-alpha. See CODEX_BRIEF_V4.md Phase D."
        )

    def __enter__(self) -> SandboxClient:
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()
