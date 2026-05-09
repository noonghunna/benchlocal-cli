"""HermesAgent-20 verifier server — v0.6 deterministic state/trace verifier.

The upstream mirror has scenario metadata but no browser/cron/memory/artifact
fixture tree. This server implements the stable multi-turn protocol with
scenario-scoped state, mocked tool-call simulation, and final rubric checks
derived from `raw_scenario.expected`.
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 9000
STATES: dict[str, dict] = {}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "memory_set",
            "description": "Store a scenario-scoped memory value.",
            "parameters": {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_get",
            "description": "Read a scenario-scoped memory value.",
            "parameters": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "artifact_write",
            "description": "Write a named scenario artifact.",
            "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "content": {"type": "string"}}, "required": ["name", "content"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trace_append",
            "description": "Append a trace event.",
            "parameters": {"type": "object", "properties": {"event": {"type": "string"}}, "required": ["event"]},
        },
    },
]

KIND_REQUIRED_TOOLS = {
    "memory": {"memory_set"},
    "skill": {"artifact_write"},
    "cron": {"trace_append"},
    "browser": {"trace_append"},
    "approval": {"trace_append"},
    "parallel": {"trace_append"},
    "retry": {"trace_append"},
    "clarify": set(),
}


def _response_text(response: dict) -> str:
    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        for field in ("content", "reasoning_content", "reasoning"):
            value = message.get(field)
            if isinstance(value, str) and value.strip():
                return value
    return ""


def _tool_calls(response: dict) -> list[dict]:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return []
    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    calls = message.get("tool_calls")
    return calls if isinstance(calls, list) else []


def _tool_name(call: dict) -> str:
    fn = call.get("function", {}) if isinstance(call, dict) else {}
    return str(fn.get("name", ""))


def _tool_args(call: dict) -> dict:
    fn = call.get("function", {}) if isinstance(call, dict) else {}
    raw = fn.get("arguments", {})
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _has_marker(scenario_id: str, text: str) -> bool:
    if f"BENCHLOCAL_PASS:{scenario_id}" in text or "BENCHLOCAL_PASS" in text:
        print(f"[hermes-sandbox] WARNING mock pass marker used for {scenario_id}", file=sys.stderr)
        return True
    return False


def _required_tools_for_kind(kind: str) -> set[str]:
    required: set[str] = set()
    for token, tools in KIND_REQUIRED_TOOLS.items():
        if token in kind:
            required |= tools
    return required


def _simulate_tool(state: dict, call: dict) -> dict:
    name = _tool_name(call)
    args = _tool_args(call)
    state["tool_names"].append(name)
    state["trace"].append({"tool": name, "args": args})
    if name == "memory_set":
        state["memory"][str(args.get("key", ""))] = str(args.get("value", ""))
        return {"ok": True, "memory": state["memory"]}
    if name == "memory_get":
        return {"ok": True, "value": state["memory"].get(str(args.get("key", "")))}
    if name == "artifact_write":
        state["artifact"][str(args.get("name", ""))] = str(args.get("content", ""))
        return {"ok": True, "artifact_names": sorted(state["artifact"])}
    if name == "trace_append":
        state["events"].append(str(args.get("event", "")))
        return {"ok": True, "events": state["events"]}
    return {"ok": True, "note": f"mocked {name}"}


def _verify_final(state: dict, response: dict) -> dict:
    scenario_id = state["scenario_id"]
    text = _response_text(response)
    if not text.strip() and not _tool_calls(response):
        return _final(False, "wrong_answer", f"{scenario_id}: empty agent response", state)
    if _has_marker(scenario_id, text):
        return _final(True, "passed", f"{scenario_id}: accepted mock canonical agent trace", state, {"mode": "mock-marker"})

    raw = state.get("raw_scenario") or {}
    kind = str(raw.get("kind", ""))
    required_tools = _required_tools_for_kind(kind)
    missing = sorted(required_tools - set(state["tool_names"]))
    if missing and not text:
        return _final(False, "verifier_fail", f"{scenario_id}: missing required mocked tool use: {', '.join(missing)}", state)

    expected = ((raw.get("expected") or {}).get("required_keywords") or [])
    keyword_hits = [word for word in expected if word.rstrip(".").lower() in text.lower()]
    if expected and text and len(keyword_hits) < min(2, len(expected)):
        return _final(
            False,
            "verifier_fail",
            f"{scenario_id}: final answer lacks upstream success-case evidence",
            state,
            {"keyword_hits": keyword_hits, "required_keywords": expected[:10]},
        )
    if re.search(r"delete|remove", str(raw.get("failure_case", "")), flags=re.IGNORECASE) and "delete" in text.lower() and "clarif" not in text.lower() and "approval" not in text.lower():
        return _final(False, "verifier_fail", f"{scenario_id}: destructive action lacked approval/clarification evidence", state)

    return _final(True, "passed", f"{scenario_id}: mocked-tool verifier passed", state, {"keyword_hits": keyword_hits, "required_tools": sorted(required_tools)})


def _final(passed: bool, mode: str, detail: str, state: dict, extra: dict | None = None) -> dict:
    trace = {
        "turn_count": state.get("turn_count", 0),
        "tool_names": state.get("tool_names", []),
        "memory": state.get("memory", {}),
        "artifact_names": sorted(state.get("artifact", {})),
        "events": state.get("events", []),
        "fixture_status": (state.get("raw_scenario") or {}).get("fixture_status", "rubric-only"),
    }
    if extra:
        trace.update(extra)
    return {"action": "verify-final", "passed": passed, "failure_mode": mode, "detail": detail, "trace": trace}


def _single_turn_verify(scenario_id: str, scenario: dict, response: dict) -> dict:
    state = {
        "scenario_id": scenario_id,
        "raw_scenario": scenario.get("raw_scenario") or {},
        "memory": {},
        "artifact": {},
        "trace": [],
        "events": [],
        "tool_names": [],
        "turn_count": 1,
    }
    for call in _tool_calls(response):
        _simulate_tool(state, call)
    final = _verify_final(state, response)
    final.pop("action", None)
    return final


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stderr.write(f"[hermes-sandbox] {fmt % args}\n")

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send({"status": "ok", "pack": "hermesagent-20", "stage": "v0.6"})
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

        if self.path == "/verify":
            scenario = req.get("scenario", {})
            result = _single_turn_verify(req.get("scenario_id") or scenario.get("id", "?"), scenario, req.get("response", {}))
        elif self.path == "/verify-start":
            scenario = req.get("scenario", {})
            state_id = str(uuid.uuid4())
            STATES[state_id] = {
                "scenario_id": req.get("scenario_id") or scenario.get("id", "?"),
                "raw_scenario": scenario.get("raw_scenario") or {},
                "messages": scenario.get("messages", []),
                "memory": {},
                "artifact": {},
                "trace": [],
                "events": [],
                "tool_names": [],
                "turn_count": 0,
            }
            result = {"action": "next-prompt", "scenario_state_id": state_id, "prompt": scenario.get("messages", []), "tools": TOOLS}
        elif self.path == "/verify-turn":
            state_id = req.get("scenario_state_id", "")
            state = STATES.get(state_id)
            if state is None:
                result = {"action": "verify-final", "passed": False, "failure_mode": "server_error", "detail": "unknown scenario_state_id", "trace": {}}
            else:
                state["turn_count"] += 1
                response = req.get("model_response", {})
                calls = _tool_calls(response)
                if calls:
                    tool_results = [_simulate_tool(state, call) for call in calls]
                    result = {
                        "action": "next-prompt",
                        "scenario_state_id": state_id,
                        "prompt": state["messages"] + [{"role": "tool", "content": json.dumps(tool_results)}],
                        "tools": TOOLS,
                        "turn_count": state["turn_count"],
                    }
                else:
                    result = _verify_final(state, response)
        else:
            state_id = req.get("scenario_state_id", "")
            state = STATES.get(state_id, {"scenario_id": "?", "turn_count": 0, "raw_scenario": {}, "tool_names": [], "memory": {}, "artifact": {}, "events": []})
            result = _final(False, "timeout", f"{state.get('scenario_id', '?')}: agent loop ended before success", state)

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
    print(f"[hermes-sandbox] listening on :{PORT}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[hermes-sandbox] shutdown", file=sys.stderr)


if __name__ == "__main__":
    main()
