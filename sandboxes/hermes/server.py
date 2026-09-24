"""HermesAgent-20 verifier server — v0.7.4 upstream Node grading parity.

This server is a thin protocol translator. Upstream's `verification/server.mjs`
runs on internal port 4010 (started by entrypoint.sh) and owns the entire
scenario lifecycle: fixture staging, agent-runner.py spawn, scoring against
the scenario-specific rubric in core.mjs.

Our `/verify-start` translates the runner's request to upstream's POST
/run-scenario shape, awaits the response, and translates upstream's
{status, score, summary, verifier, ...} back to our ScenarioResult shape.

Layers (top = closest to runner; bottom = closest to model):

    runner POST /verify-start (port 9000, our shape)
      → server.py _translate_request()
        → httpx.POST /run-scenario (port 4010, upstream shape)
          → upstream server.mjs runScenario()
            → upstream core.mjs run<Kind>Scenario()
              → spawns agent-runner.py
                → upstream AIAgent loop against model_endpoint
              → reads result + applies scenario rubric
            → returns {status, score, summary, verifier, ...}
        → upstream HTTP response
      → server.py _translate_upstream_result()
    → runner consumes ScenarioResult

Failure modes preserved from v0.7.3 for back-compat:
- passed                       — upstream graded pass
- verifier_fail                — upstream graded fail or partial
- agent_runner_timeout         — upstream took >SUBPROCESS_TIMEOUT_S
- agent_runner_crashed         — upstream returned 5xx or malformed JSON
- model_endpoint_unreachable   — upstream's note indicates network failure
- result_json_malformed        — upstream returned 200 but non-JSON body
- server_error                 — server-side bug, missing config, missing install

Mock-pass marker `BENCHLOCAL_PASS:<id>` short-circuits before the upstream
call (preserved from v0.7.3 for runner-side smoke tests; upstream's grader
doesn't honor it).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx

PORT = 9000

# Internal upstream Node grader URL. server.mjs reads PORT env (default 4010);
# entrypoint.sh boots it before starting us.
UPSTREAM_NODE_URL = os.environ.get(
    "UPSTREAM_NODE_URL", f"http://127.0.0.1:{os.environ.get('PORT', '4010')}"
)

# Where the upstream hermes-agent codebase lives (bind-mount or image-baked).
# Surfaced in /health for diagnostic purposes; the actual import path is
# decided by upstream's agent-runner.py via HERMES_AGENT_PATH env.
HERMES_AGENT_PATH = Path(os.environ.get("HERMES_AGENT_PATH", "/opt/hermes-agent"))

# Outer wall-clock cap for upstream /run-scenario calls. Upstream's runScenario
# spawns the agent loop (10-15 turns of real LLM calls) + grades — needs
# ~5min headroom. Configurable via HERMES_SUBPROCESS_TIMEOUT_S env (kept name
# for back-compat with v0.7.3 deployments) or BENCHLOCAL_HERMES_SUBPROCESS_TIMEOUT_S
# from the runner.
SUBPROCESS_TIMEOUT_S = float(os.environ.get("HERMES_SUBPROCESS_TIMEOUT_S", "300"))

# The episode cap itself is enforced INSIDE upstream Node: hermes-runtime.mjs
# kills the agent subprocess at HERMES_SUBPROCESS_TIMEOUT_S (it used to be a
# fixed 600 s, which silently clamped every larger cap). This proxy's read
# therefore waits the cap PLUS headroom, so Node's own timeout result — and the
# grading that follows an agent that finished just under the cap (a Python
# helper can take up to 120 s, pytest 60 s) — reaches us instead of being cut
# off. Must stay below the runner's outer /verify-start headroom (300 s,
# benchlocal_cli/sandbox.py) so this proxy still answers first.
UPSTREAM_READ_HEADROOM_S = 180.0
UPSTREAM_READ_TIMEOUT_S = SUBPROCESS_TIMEOUT_S + UPSTREAM_READ_HEADROOM_S

# Node's runCommand rejects with this message when a watchdog fires. It reaches
# us in a 200 result's note (scenarios that catch the rejection) or in a 500
# body (scenarios that do not), and both mean the episode timed out.
_NODE_WATCHDOG_MESSAGE = "Command timed out after"

# Cap on upstream_raw size in saved JSON traces. Per Codex review #6:
# preserve the full upstream result for v0.8 inspect tooling, but bound it
# so result files don't balloon. 16KB per scenario × 20 scenarios = 320KB
# headroom on top of v0.7.3's per-scenario footprint.
UPSTREAM_RAW_MAX_BYTES = 16384

# Schema version for saved JSON. v0.7.3 = "1"; v0.7.4 traces have
# upstream_status/upstream_score/etc. instead of grading.tool_event_count.
SCHEMA_VERSION = "2"

# Thinking-only endpoints reject enable_thinking=false. Hermes therefore
# emulates its off arm with one reasoning token instead of sending false.
THINKING_BUDGET_FLOOR = 1
DEFAULT_THINKING_BUDGET = 16384


def _commit_from_path(path: Path) -> str:
    if not path.is_dir():
        return "missing"
    for env_key in ("BENCHLOCAL_HERMES_AGENT_COMMIT", "HERMES_PINNED_COMMIT"):
        baked = os.environ.get(env_key)
        if baked:
            return baked
    try:
        proc = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=False, capture_output=True, text=True, timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    return "unknown"


def _hermes_agent_source() -> str:
    """host-mount | baked | missing | unknown — surfaced in /health + verifier_trace."""
    if not HERMES_AGENT_PATH.is_dir() or not (HERMES_AGENT_PATH / "run_agent.py").is_file():
        return "missing"
    # The host-mount path is the only one that sets BENCHLOCAL_HERMES_AGENT_COMMIT
    # (sandbox.py sets it alongside HERMES_AGENT_PATH). A valid install without
    # it is the image-baked one by definition. The previous "baked" test read
    # HERMES_PINNED_COMMIT, which is a Dockerfile ARG and is absent at runtime,
    # so every baked run mis-reported itself as "unknown" (#104).
    if os.environ.get("BENCHLOCAL_HERMES_AGENT_COMMIT"):
        return "host-mount"
    return "baked"



_MODEL_ENDPOINT_REACHABLE_CACHE: dict | None = None


def _model_models_url(endpoint: str) -> str:
    base = (endpoint or "").rstrip("/")
    for suffix in ("/v1/chat/completions", "/chat/completions"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    base = base.rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return f"{base}/models"


def _endpoint_failure_reason(endpoint: str, exc: Exception) -> str:
    text = str(exc)
    host_hint = "host does not resolve from inside sandbox; try a loopback URL or BENCHLOCAL_HERMES_RESOLVE_LOCALHOST=1 for non-loopback hostnames"
    if isinstance(exc, httpx.TimeoutException):
        return f"no response within 5s from {endpoint}; check firewall / port"
    if isinstance(exc, httpx.ConnectError):
        lowered = text.lower()
        if "name" in lowered or "resolve" in lowered or "temporary failure" in lowered:
            return f"{host_hint}: {text}"
        return f"model server not running at {endpoint}; check the model is up: {text}"
    return f"could not reach {endpoint}: {text}"


def _detect_model_endpoint_reachable(endpoint: str, api_key: str | None = None) -> dict:
    """Cached sanity check that the sandbox can reach the model endpoint."""
    global _MODEL_ENDPOINT_REACHABLE_CACHE
    if _MODEL_ENDPOINT_REACHABLE_CACHE is not None:
        return _MODEL_ENDPOINT_REACHABLE_CACHE
    if not endpoint:
        _MODEL_ENDPOINT_REACHABLE_CACHE = {"ok": False, "reason": "no model endpoint provided to sandbox"}
        return _MODEL_ENDPOINT_REACHABLE_CACHE
    probe_url = _model_models_url(endpoint)
    headers = {}
    if api_key and api_key != "dummy":
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        with httpx.Client(timeout=5.0, follow_redirects=True, max_redirects=1) as client:
            resp = client.get(probe_url, headers=headers)
        if resp.status_code != 200:
            _MODEL_ENDPOINT_REACHABLE_CACHE = {
                "ok": False,
                "endpoint": endpoint,
                "probe_url": probe_url,
                "status_code": resp.status_code,
                "reason": (
                    f"endpoint returned HTTP {resp.status_code}; check the path; "
                    f"{probe_url} should return 200 on an OpenAI-compatible server"
                ),
            }
            return _MODEL_ENDPOINT_REACHABLE_CACHE
        _MODEL_ENDPOINT_REACHABLE_CACHE = {"ok": True, "endpoint": endpoint, "probe_url": probe_url}
        return _MODEL_ENDPOINT_REACHABLE_CACHE
    except (httpx.HTTPError, ValueError) as exc:
        _MODEL_ENDPOINT_REACHABLE_CACHE = {
            "ok": False,
            "endpoint": endpoint,
            "probe_url": probe_url,
            "reason": _endpoint_failure_reason(endpoint, exc),
        }
        return _MODEL_ENDPOINT_REACHABLE_CACHE


def _endpoint_preflight_response(scenario_id: str, reach: dict) -> dict:
    return {
        "action": "verify-final",
        "passed": False,
        "failure_mode": "server_error",
        "detail": f"{scenario_id}: model endpoint unreachable from sandbox: {reach.get('reason')}",
        "trace": {"schema_version": SCHEMA_VERSION, "model_endpoint_reachable": reach},
    }


def _upstream_node_ready() -> bool:
    """Best-effort probe of upstream's /health (Codex review #8 split-brain
    prevention). Returns True if upstream Node responds with `{ok: true}`.
    """
    try:
        resp = httpx.get(f"{UPSTREAM_NODE_URL}/health", timeout=2.0)
        return resp.status_code == 200 and bool(resp.json().get("ok"))
    except (httpx.HTTPError, ValueError):
        return False


def _resolve_health() -> dict:
    """Compose the full /health response. Distinguishes:
      - install missing: hermes-agent not visible at HERMES_AGENT_PATH
      - upstream-node-unreachable: install present but upstream Node didn't boot
      - ok: both fine
    """
    install_ok = HERMES_AGENT_PATH.is_dir() and (HERMES_AGENT_PATH / "run_agent.py").is_file()
    upstream_ok = _upstream_node_ready() if install_ok else False
    endpoint_reach = _MODEL_ENDPOINT_REACHABLE_CACHE
    if not install_ok:
        status = "missing-hermes-agent"
    elif not upstream_ok:
        status = "upstream-node-unreachable"
    elif endpoint_reach is not None and not endpoint_reach.get("ok"):
        status = "setup-error"
    else:
        status = "ok"
    return {
        "status": status,
        "pack": "hermesagent-20",
        "stage": "v0.7.4",
        "multi_turn": True,
        "install_ok": install_ok,
        "upstream_node_ready": upstream_ok,
        "upstream_node_url": UPSTREAM_NODE_URL,
        "hermes_agent_path": str(HERMES_AGENT_PATH),
        "hermes_agent_source": _hermes_agent_source(),
        "hermes_agent_commit": _commit_from_path(HERMES_AGENT_PATH),
        "subprocess_timeout_s": SUBPROCESS_TIMEOUT_S,
        "model_endpoint_reachable": endpoint_reach or {"ok": None, "reason": "not checked yet"},
    }


def _has_marker(scenario_id: str, text: str) -> bool:
    if not text:
        return False
    if f"BENCHLOCAL_PASS:{scenario_id}" in text or "BENCHLOCAL_PASS" in text:
        sys.stderr.write(f"[hermes-sandbox] WARNING mock pass marker used for {scenario_id}\n")
        return True
    return False


def _normalize_base_url(endpoint: str) -> str:
    """Normalize the runner's endpoint to the OpenAI base-URL shape upstream's
    `agent-runner.py` (which passes it to the OpenAI Python client) expects:
    must end with `/v1`. The OpenAI client appends `/chat/completions` itself.

    Inputs handled (Codex review #10):
      http://host:8030                     → http://host:8030/v1
      http://host:8030/                    → http://host:8030/v1
      http://host:8030/v1                  → http://host:8030/v1
      http://host:8030/v1/                 → http://host:8030/v1
      http://host:8030/v1/chat/completions → http://host:8030/v1
      http://host:8030/chat/completions    → http://host:8030/v1

    NOTE: prior to 2026-05-09 this function stripped `/v1` instead of
    ensuring it. That caused upstream's agent-runner.py to hit
    `/chat/completions` (without /v1) and 404 against vLLM, which led to
    every scenario completing in ~10s with 0 tool events. Fixed here.
    """
    base = (endpoint or "").rstrip("/")
    for suffix in ("/v1/chat/completions", "/chat/completions"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    base = base.rstrip("/")
    if base.endswith("/v1"):
        return base
    return base + "/v1"


def _filter_generation(sampling: dict | None) -> dict:
    """Pass through only the sampling kwargs upstream's request_overrides
    accepts — temperature, top_p, top_k, min_p, repetition_penalty, max_tokens.
    Mirrors agent-runner.py's old filter logic so we send the same shape."""
    out: dict = {}
    if not sampling:
        return out
    for key in ("temperature", "top_p", "top_k", "min_p", "repetition_penalty", "max_tokens"):
        if key in sampling and sampling[key] is not None:
            out[key] = sampling[key]
    return out


def _thinking_extra_body(req: dict) -> dict:
    """Build HermesAgent's provider-specific request body.

    Thinking controls deliberately bypass _filter_generation: the pinned
    framework consumes them as extra_body, not generation overrides. New
    runners provide the complete model-specific fragment; keep the legacy
    budget-based shape for older callers and Qwen's existing behavior.
    """
    resolved = req.get("thinking_extra_body")
    if isinstance(resolved, dict):
        return dict(resolved)

    enabled = req.get("enable_thinking")
    sampling = req.get("sampling") or {}
    if not isinstance(enabled, bool):
        chat_template_kwargs = sampling.get("chat_template_kwargs") or {}
        enabled = bool(chat_template_kwargs.get("enable_thinking", False))

    budget = req.get("thinking_budget")
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
        sampling_budget = sampling.get("max_tokens")
        budget = (
            sampling_budget
            if isinstance(sampling_budget, int)
            and not isinstance(sampling_budget, bool)
            and sampling_budget > 0
            else DEFAULT_THINKING_BUDGET
        )

    return {
        "enable_thinking": True,
        "thinking_budget": budget if enabled else THINKING_BUDGET_FLOOR,
    }


def _translate_request(req: dict) -> dict:
    """Map runner's /verify-start payload → upstream's POST /run-scenario shape.

    Runner sends:
      { scenario_id, scenario, model_endpoint, model_name, model_api_key,
        sampling, enable_thinking, thinking_budget, thinking_extra_body,
        preserve_reasoning_history }

    Upstream expects:
      { scenarioId, runId, model: {id, exposedModel, providerModel,
        inferenceBaseUrl, apiKey, provider, authMode}, generation: {...} }
    """
    scenario = req.get("scenario") or {}
    scenario_id = req.get("scenario_id") or scenario.get("id") or "unknown"
    model_endpoint = req.get("model_endpoint") or ""
    model_name = req.get("model_name") or ""

    generation = _filter_generation(req.get("sampling"))
    generation["preserve_reasoning_history"] = bool(
        req.get("preserve_reasoning_history")
    )

    return {
        "scenarioId": scenario_id,
        "runId": str(uuid.uuid4()),
        "model": {
            "id": model_name,
            "exposedModel": model_name,
            "providerModel": model_name,
            "inferenceBaseUrl": _normalize_base_url(model_endpoint),
            "apiKey": str(req.get("model_api_key") or "dummy"),
            "provider": "custom",
            "authMode": "bearer",
            "extraBody": _thinking_extra_body(req),
        },
        "generation": generation,
    }


_NETWORK_ERROR_PATTERNS = re.compile(
    r"connection|unreachable|refused|resolve|timed out|timeout|"
    r"name or service|getaddrinfo|EAI_AGAIN|ENOTFOUND",
    re.IGNORECASE,
)


# #157: when hermes-agent's model calls keep failing — a stalled endpoint that
# its stream stall detector / read timeout gives up on, or a server that died
# mid-episode — the agent does NOT error out. It exits 0 and reports the failure
# as its FINAL ANSWER (run_agent.py:
# f"API call failed after {max_retries} retries: {_final_summary}"). The grader
# then scores the untouched workspace and the scenario reads as a model
# `verifier_fail`. Recognise the agent's own give-up message instead.
_AGENT_API_FAILURE_RE = re.compile(r"^API call failed after \d+ retries\b")


def _agent_api_failure(upstream: dict) -> str | None:
    output = upstream.get("output")
    final = output.get("finalAnswer") if isinstance(output, dict) else None
    if isinstance(final, str) and _AGENT_API_FAILURE_RE.match(final.strip()):
        return final.strip()
    return None


def _classify_failure(upstream: dict) -> str:
    """Map upstream's response to our existing failure_mode taxonomy so v0.8
    `inspect --mode <X>` filtering keeps working. Codex review #11: preserve
    the back-compat field semantics even though the underlying source shifted.
    """
    if _agent_api_failure(upstream) is not None:
        # The endpoint failed the agent; the grade of an untouched workspace
        # says nothing about the model. Checked before `partial`/"timed out"
        # so it is never read as a model verdict or a runaway.
        return "model_endpoint_unreachable"
    status = upstream.get("status")
    if status == "partial":
        # Per brief: collapse partial to fail in v0.7.4 (binary semantics).
        # The verifier_trace.upstream_status preserves the partial signal.
        return "verifier_fail"
    note = str(upstream.get("note") or "")
    summary = str(upstream.get("summary") or "")
    haystack = (note + " " + summary).lower()
    if "timed out" in haystack or "timeout" in haystack:
        return "agent_runner_timeout"
    if _NETWORK_ERROR_PATTERNS.search(haystack):
        return "model_endpoint_unreachable"
    return "verifier_fail"


def _cap_upstream_for_trace(upstream: dict, max_bytes: int = UPSTREAM_RAW_MAX_BYTES) -> dict:
    """Return a JSON-bounded copy of `upstream` for inclusion in verifier_trace.
    `rawLog` is always replaced with a stub (the full text lives in
    `raw_log_tail` separately, capped to 4KB). If still over budget, drop
    the largest non-headline keys until under the cap.

    Codex review #6: preserve full upstream result for v0.8 inspect tooling,
    but cap to keep saved-JSON size bounded.
    """
    capped = dict(upstream)
    if "rawLog" in capped:
        capped["rawLog"] = "<truncated — see raw_log_tail in trace>"
    encoded = json.dumps(capped, ensure_ascii=False)
    if len(encoded) <= max_bytes:
        return capped
    # Drop largest keys (other than headline fields) until under the cap.
    headline = {"scenarioId", "status", "score", "summary"}
    sized = sorted(
        ((k, len(json.dumps(v, ensure_ascii=False))) for k, v in capped.items() if k not in headline),
        key=lambda kv: -kv[1],
    )
    for key, _ in sized:
        capped[key] = f"<dropped — over {max_bytes}B cap>"
        if len(json.dumps(capped, ensure_ascii=False)) <= max_bytes:
            return capped
    return capped


def _translate_upstream_result(scenario_id: str, upstream: dict, elapsed_s: float) -> dict:
    """Map upstream's response → our verify-final shape.

    Upstream returns:
      { scenarioId, status: "pass"|"partial"|"fail", score: 0-100,
        summary, note, rawLog, output, verifier: {...}, timings: {...} }

    We return:
      { action: "verify-final", passed: bool, failure_mode: str, detail: str,
        trace: {...} }
    """
    status = upstream.get("status")
    passed = status == "pass"
    failure_mode = "passed" if passed else _classify_failure(upstream)
    summary = str(upstream.get("summary") or "")
    api_failure = None if passed else _agent_api_failure(upstream)

    return {
        "action": "verify-final",
        "passed": passed,
        # Back-compat (Codex review #11): keep populated even though semantics shifted.
        "failure_mode": failure_mode,
        "detail": (
            f"{scenario_id}: agent gave up on the model endpoint mid-episode — {api_failure}"
            if api_failure
            else summary
        )[:500],
        "trace": {
            # Stable across v0.7.x — preserved for diagnostics
            "hermes_agent_path": str(HERMES_AGENT_PATH),
            "hermes_agent_source": _hermes_agent_source(),
            "hermes_agent_commit": _commit_from_path(HERMES_AGENT_PATH),
            "elapsed_s": elapsed_s,
            "schema_version": SCHEMA_VERSION,
            # New in v0.7.4 — promoted top-level fields for v0.8 inspect.
            "upstream_status": status,
            "upstream_score": upstream.get("score"),
            "upstream_note": upstream.get("note"),
            "upstream_summary": summary,
            "upstream_verifier": upstream.get("verifier"),
            "upstream_output": upstream.get("output"),
            "upstream_timings": upstream.get("timings"),
            # Lossless-ish forensics (capped per Codex review #6)
            "upstream_raw": _cap_upstream_for_trace(upstream),
            "raw_log_tail": (upstream.get("rawLog") or "")[-4000:],
        },
    }


def _mock_pass_response(scenario_id: str) -> dict:
    """Short-circuit pass for runner-side smoke tests. Mimics the v0.7.4
    schema so v0.8 inspect doesn't trip over a missing upstream_status."""
    return {
        "action": "verify-final",
        "passed": True,
        "failure_mode": "passed",
        "detail": f"{scenario_id}: accepted mock canonical agent trace",
        "trace": {
            "hermes_agent_path": str(HERMES_AGENT_PATH),
            "hermes_agent_source": _hermes_agent_source(),
            "hermes_agent_commit": _commit_from_path(HERMES_AGENT_PATH),
            "elapsed_s": 0.0,
            "schema_version": SCHEMA_VERSION,
            "mock_pass": True,
            "upstream_status": "pass",
            "upstream_score": 100,
            "upstream_summary": "mock-pass marker honored before proxy call",
        },
    }


def _missing_install_response(scenario_id: str) -> dict:
    return {
        "action": "verify-final",
        "passed": False,
        "failure_mode": "server_error",
        "detail": (
            f"hermes-agent install missing at {HERMES_AGENT_PATH}. "
            "Set HERMES_AGENT_HOST_PATH on the host or rebuild the image "
            "with --build-arg BAKE_HERMES_AGENT=1."
        ),
        "trace": {
            "hermes_agent_path": str(HERMES_AGENT_PATH),
            "hermes_agent_source": "missing",
            "schema_version": SCHEMA_VERSION,
        },
    }


def _missing_endpoint_response(scenario_id: str) -> dict:
    return {
        "action": "verify-final",
        "passed": False,
        "failure_mode": "server_error",
        "detail": (
            "hermes /verify-start missing model_endpoint or model_name; "
            "v0.7.3+ requires the runner to pass these."
        ),
        "trace": {"schema_version": SCHEMA_VERSION},
    }


def _upstream_unreachable_response(scenario_id: str, reason: str) -> dict:
    return {
        "action": "verify-final",
        "passed": False,
        "failure_mode": "server_error",
        "detail": f"{scenario_id}: upstream node grader unreachable: {reason}",
        "trace": {
            "schema_version": SCHEMA_VERSION,
            "upstream_node_url": UPSTREAM_NODE_URL,
            "upstream_node_ready": False,
        },
    }


# club-3090#1396: token accounting for the agent's own model calls.
# hermes-agent calls the model directly from inside this container, so the
# runner never sees a response and the pack reported no token counts at all —
# not per scenario, not in the run's endpoint total. The agent already asks for
# stream usage itself (`stream_options.include_usage`), so a pass-through proxy
# that relays every request UNCHANGED and reads the `usage` block off each
# response recovers the counts without touching the pinned agent. It is on by
# default; BENCHLOCAL_HERMES_USAGE_PROXY=0 restores the direct connection. If
# the proxy cannot start, the agent talks to the endpoint directly, as before,
# and the scenario simply has no count — never a failed scenario.
_HOP_BY_HOP = frozenset({
    "host", "content-length", "connection", "keep-alive", "transfer-encoding",
    "te", "trailer", "upgrade", "proxy-authorization", "proxy-authenticate",
    "accept-encoding", "content-encoding",
})


def _usage_from(obj: object) -> dict | None:
    """The `usage` block of one completion (JSON body or SSE chunk), or None."""
    usage = obj.get("usage") if isinstance(obj, dict) else None
    return usage if isinstance(usage, dict) and usage else None


class UsageProxy:
    """Loopback pass-through to the model endpoint that tallies `usage`.

    One scenario runs at a time per sandbox, so a single tally is reset by
    `begin()` and read by `end()`. Per response it keeps the LAST usage block
    seen: servers that stream cumulative usage on every chunk are then counted
    once, and servers that send it only on the final chunk are unaffected.
    """

    def __init__(self) -> None:
        import threading
        from http.server import ThreadingHTTPServer

        self._lock = threading.Lock()
        self._target = ""
        self._reset()
        proxy = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - quiet
                return

            def do_GET(self) -> None:  # noqa: N802
                proxy._relay(self, "GET")

            def do_POST(self) -> None:  # noqa: N802
                proxy._relay(self, "POST")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def _reset(self) -> None:
        self._tally = {
            "requests": 0, "requests_with_usage": 0, "prompt_tokens": 0,
            "completion_tokens": 0, "total_tokens": 0, "reasoning_tokens": 0,
        }

    def begin(self, target_base_url: str) -> str:
        """Point the proxy at the real `/v1` base; return the base the agent uses."""
        with self._lock:
            self._target = target_base_url.rstrip("/")
            self._reset()
        return f"http://127.0.0.1:{self.port}/v1"

    def end(self) -> dict:
        with self._lock:
            return dict(self._tally)

    def _record(self, usage: dict | None) -> None:
        with self._lock:
            self._tally["requests"] += 1
            if not usage:
                return
            self._tally["requests_with_usage"] += 1
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = usage.get(key)
                if isinstance(value, int) and value >= 0:
                    self._tally[key] += value
            details = usage.get("completion_tokens_details")
            reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else None
            if isinstance(reasoning, int) and reasoning >= 0:
                self._tally["reasoning_tokens"] += reasoning

    def _relay(self, handler: BaseHTTPRequestHandler, method: str) -> None:
        path = handler.path
        suffix = path[len("/v1"):] if path.startswith("/v1") else path
        url = self._target + suffix
        length = int(handler.headers.get("Content-Length") or 0)
        body = handler.rfile.read(length) if length else None
        headers = {k: v for k, v in handler.headers.items() if k.lower() not in _HOP_BY_HOP}
        headers["Accept-Encoding"] = "identity"  # usage must be readable in transit
        last_usage: dict | None = None
        try:
            # No read timeout: the episode cap (HERMES_SUBPROCESS_TIMEOUT_S) bounds
            # the agent, and a proxy-side read timeout is exactly the kind of silent
            # clamp #149 removed.
            timeout = httpx.Timeout(connect=30.0, read=None, write=60.0, pool=30.0)
            with httpx.Client(timeout=timeout) as client, client.stream(
                method, url, content=body, headers=headers
            ) as upstream:
                handler.send_response(upstream.status_code)
                for key, value in upstream.headers.items():
                    if key.lower() not in _HOP_BY_HOP:
                        handler.send_header(key, value)
                handler.send_header("Connection", "close")
                handler.end_headers()
                is_sse = "text/event-stream" in upstream.headers.get("content-type", "")
                pending = b""
                buffered = bytearray()
                for chunk in upstream.iter_raw():
                    handler.wfile.write(chunk)
                    handler.wfile.flush()
                    if is_sse:
                        pending += chunk
                        *lines, pending = pending.split(b"\n")
                        for line in lines:
                            data = line.strip()
                            if not data.startswith(b"data:"):
                                continue
                            data = data[5:].strip()
                            if not data or data == b"[DONE]":
                                continue
                            try:
                                last_usage = _usage_from(json.loads(data)) or last_usage
                            except ValueError:
                                continue
                    else:
                        buffered.extend(chunk)
                if not is_sse and buffered:
                    try:
                        last_usage = _usage_from(json.loads(bytes(buffered)))
                    except ValueError:
                        last_usage = None
        except httpx.HTTPError as exc:
            try:
                message = json.dumps({"error": {"message": f"usage proxy: upstream error: {exc}"}}).encode()
                handler.send_response(502)
                handler.send_header("Content-Type", "application/json")
                handler.send_header("Content-Length", str(len(message)))
                handler.end_headers()
                handler.wfile.write(message)
            except OSError:
                pass
        except OSError:
            pass  # the agent hung up mid-response; nothing left to relay
        finally:
            if method == "POST":
                self._record(last_usage)


_USAGE_PROXY: UsageProxy | None = None
_USAGE_PROXY_FAILED = False


def _usage_proxy() -> UsageProxy | None:
    """The shared proxy, started on first use; None when disabled or unavailable."""
    global _USAGE_PROXY, _USAGE_PROXY_FAILED
    if os.environ.get("BENCHLOCAL_HERMES_USAGE_PROXY", "1") == "0" or _USAGE_PROXY_FAILED:
        return None
    if _USAGE_PROXY is None:
        try:
            _USAGE_PROXY = UsageProxy()
        except OSError as exc:
            _USAGE_PROXY_FAILED = True
            sys.stderr.write(f"[hermes-sandbox] usage proxy unavailable ({exc}); agent calls go direct\n")
            return None
    return _USAGE_PROXY


def _verify_start_via_upstream(req: dict) -> dict:
    scenario = req.get("scenario") or {}
    scenario_id = req.get("scenario_id") or scenario.get("id") or "?"

    # Mock-pass short-circuit (Codex review #6 b — keep in our Python; don't
    # patch core.mjs). Honors marker in last user message OR an explicit
    # "use_mock_pass" field in the request.
    last_user = ""
    for m in reversed(scenario.get("messages") or []):
        if isinstance(m, dict) and m.get("role") == "user":
            last_user = str(m.get("content") or "")
            break
    if _has_marker(scenario_id, last_user) or req.get("use_mock_pass"):
        return _mock_pass_response(scenario_id)

    # Install present? (split-brain check #1)
    if _hermes_agent_source() == "missing":
        return _missing_install_response(scenario_id)

    # Required fields for upstream's /run-scenario?
    if not req.get("model_endpoint") or not req.get("model_name"):
        return _missing_endpoint_response(scenario_id)

    # Upstream Node grader healthy? (split-brain check #2 — Codex review #8)
    if not _upstream_node_ready():
        return _upstream_unreachable_response(scenario_id, "/health probe failed")

    reach = _detect_model_endpoint_reachable(
        str(req.get("model_endpoint") or ""),
        str(req.get("model_api_key") or ""),
    )
    if not reach.get("ok"):
        return _endpoint_preflight_response(scenario_id, reach)

    upstream_request = _translate_request(req)
    proxy = _usage_proxy()
    if proxy is not None:
        model = upstream_request["model"]
        model["inferenceBaseUrl"] = proxy.begin(model["inferenceBaseUrl"])
    result = _run_upstream_scenario(scenario_id, upstream_request)
    if proxy is not None:
        # Attached on every outcome: a timed-out or failed episode still spent tokens.
        result["usage"] = proxy.end()
    return result


def _run_upstream_scenario(scenario_id: str, upstream_request: dict) -> dict:
    started = time.monotonic()
    try:
        resp = httpx.post(
            f"{UPSTREAM_NODE_URL}/run-scenario",
            json=upstream_request,
            timeout=UPSTREAM_READ_TIMEOUT_S,
        )
    except httpx.TimeoutException:
        elapsed = time.monotonic() - started
        return {
            "action": "verify-final",
            "passed": False,
            "failure_mode": "agent_runner_timeout",
            "detail": (
                f"{scenario_id}: upstream /run-scenario exceeded {UPSTREAM_READ_TIMEOUT_S:.0f}s "
                f"(episode cap {SUBPROCESS_TIMEOUT_S:.0f}s + {UPSTREAM_READ_HEADROOM_S:.0f}s headroom)"
            ),
            "trace": {
                "schema_version": SCHEMA_VERSION,
                "elapsed_s": elapsed,
                "timed_out": True,
                "upstream_node_url": UPSTREAM_NODE_URL,
            },
        }
    except httpx.HTTPError as exc:
        elapsed = time.monotonic() - started
        return {
            "action": "verify-final",
            "passed": False,
            "failure_mode": "agent_runner_crashed",
            "detail": f"{scenario_id}: upstream /run-scenario error: {exc}",
            "trace": {
                "schema_version": SCHEMA_VERSION,
                "elapsed_s": elapsed,
                "upstream_node_url": UPSTREAM_NODE_URL,
                "upstream_error": str(exc),
            },
        }

    elapsed = time.monotonic() - started

    if resp.status_code != 200:
        return {
            "action": "verify-final",
            "passed": False,
            # A scenario that does not catch the agent's rejection surfaces
            # Node's watchdog as a 500; that is still the episode cap firing.
            "failure_mode": (
                "agent_runner_timeout"
                if _NODE_WATCHDOG_MESSAGE in resp.text
                else "agent_runner_crashed"
            ),
            "detail": f"{scenario_id}: upstream returned HTTP {resp.status_code}: {resp.text[:200]}",
            "trace": {
                "schema_version": SCHEMA_VERSION,
                "elapsed_s": elapsed,
                "upstream_status_code": resp.status_code,
                "upstream_body_tail": resp.text[-2000:],
            },
        }

    try:
        upstream_result = resp.json()
    except (ValueError, json.JSONDecodeError) as exc:
        return {
            "action": "verify-final",
            "passed": False,
            "failure_mode": "result_json_malformed",
            "detail": f"{scenario_id}: upstream returned 200 but body wasn't JSON: {exc}",
            "trace": {
                "schema_version": SCHEMA_VERSION,
                "elapsed_s": elapsed,
                "upstream_body_tail": resp.text[-2000:],
            },
        }

    return _translate_upstream_result(scenario_id, upstream_result, elapsed)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stderr.write(f"[hermes-sandbox] {fmt % args}\n")

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(_resolve_health())
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        if self.path not in ("/verify", "/verify-start", "/verify-turn", "/verify-end"):
            self.send_response(404)
            self.end_headers()
            return
        try:
            req = self._json_body()
        except json.JSONDecodeError as exc:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(f"invalid JSON: {exc}".encode())
            return

        try:
            if self.path == "/verify-start":
                result = _verify_start_via_upstream(req)
            elif self.path == "/verify":
                result = {
                    "passed": False,
                    "failure_mode": "server_error",
                    "detail": (
                        "hermes /verify is unsupported in v0.7.4; "
                        "use /verify-start (multi-turn early-out) instead."
                    ),
                    "trace": {"schema_version": SCHEMA_VERSION},
                }
            else:
                # /verify-turn and /verify-end are no-ops in v0.7.4 — the
                # runner consumes verify-final from /verify-start directly.
                result = {
                    "action": "verify-final",
                    "passed": False,
                    "failure_mode": "server_error",
                    "detail": (
                        f"hermes {self.path} is a no-op in v0.7.4 — runner "
                        "should consume verify-final from /verify-start."
                    ),
                    "trace": {"schema_version": SCHEMA_VERSION},
                }
        except Exception as exc:  # noqa: BLE001
            import traceback
            tb = traceback.format_exc()
            sys.stderr.write(f"[hermes-sandbox] verifier exception on {self.path}: {exc}\n{tb}\n")
            result = {
                "action": "verify-final",
                "passed": False,
                "failure_mode": "server_error",
                "detail": f"hermes verifier raised {type(exc).__name__}: {exc}",
                "trace": {
                    "schema_version": SCHEMA_VERSION,
                    "traceback": tb[-2000:],
                },
            }

        self._send(result)

    def _json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        data = json.loads(body)
        return data if isinstance(data, dict) else {}

    def _send(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    sys.stderr.write(
        f"[hermes-sandbox] listening on :{PORT} "
        f"(stage=v0.7.4, upstream={UPSTREAM_NODE_URL}, "
        f"hermes_agent_source={_hermes_agent_source()}, "
        f"episode_cap={SUBPROCESS_TIMEOUT_S:.0f}s, upstream_read={UPSTREAM_READ_TIMEOUT_S:.0f}s)\n"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("[hermes-sandbox] shutdown\n")


if __name__ == "__main__":
    main()
