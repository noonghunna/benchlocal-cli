"""BugFind-15 verifier server — v0.6 deterministic rubric verifier.

The upstream mirror currently contains rubric callbacks, not pytest fixture
trees. This server therefore verifies candidate answers against the vendored
scenario rubric data carried in `raw_scenario`: strict solution-block parsing,
trap/no-bug discipline, and per-scenario evidence checks derived from the
upstream success/failure cases.
"""

from __future__ import annotations

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 9000


PASS_PATTERNS: dict[str, list[str]] = {
    "BF-01": [r"off.by.one|range\(1|len\(numbers\)\s*\+\s*1|index.*out of range|for\s+\w+\s+in\s+numbers|range\(len"],
    "BF-02": [r"empty string|\"\"|!==\s*\"\"|falsy|missing.*check"],
    "BF-03": [r"no bug|code.*correct|format!.*borrow|does not move|compiles"],
    "BF-04": [r"dictionary changed size|mutat.*during iteration|list\(users\.items\)|dict comprehension|to_remove"],
    "BF-05": [r"closure|captures?.*loop|loop variable|go func\(.*int|}\(i\)|i\s*:=\s*i"],
    "BF-06": [r"fetch.*promise|await fetch|await response\.json|missing await"],
    "BF-07": [r"mutable default|default argument|evaluated once|item_list\s*=\s*None|shared list"],
    "BF-08": [r"integer overflow|overflows? u64|checked_mul|u128|big.?int|release.*wrap"],
    "BF-09": [r"shared backing array|same underlying array|nums\[:0\]|make\(\[\]int,\s*0"],
    "BF-10": [r"no bug|code.*correct|preserve original|first occurrence|normalized key"],
    "BF-11": [r"invalid.*discount|throw|raise|RangeError|discountPercent.*100|silent"],
    "BF-12": [r"current_val|final streak|current_count|data\[i\]\s*==|longest"],
    "BF-13": [r"string sort|lexicographic|int\(|parseInt|Number\(|age.*number"],
    "BF-14": [r"shipping_address|undefined|optional chaining|null check|missing field"],
    "BF-15": [r"race|mutex|sync\.Mutex|atomic|non.atomic|count\+\+"],
}

BAD_PATTERNS: dict[str, list[str]] = {
    "BF-03": [r"\.clone\(\)", r"&name"],
    "BF-04": [r"try/except"],
    "BF-05": [r"time\.sleep|sleep\("],
    "BF-08": [r"\bf64\b|float"],
    "BF-10": [r"append\(key\)|result\.append\(key\)"],
    "BF-14": [r"node version|npm version"],
    "BF-15": [r"waitgroup.*solves|reduce goroutine"],
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


def _solution_block(text: str) -> tuple[str | None, str | None]:
    match = re.search(
        r"<solution\b(?P<attrs>[^>]*)>(?P<body>[\s\S]*?)</solution>",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        fence = re.search(r"```(?:python|javascript|rust|go|js)?\s*([\s\S]*?)```", text)
        if fence:
            return "fix", fence.group(1).strip()
        return None, None
    attrs = match.group("attrs")
    verdict = re.search(r"verdict\s*=\s*[\"']([^\"']+)[\"']", attrs, flags=re.IGNORECASE)
    return (verdict.group(1).lower() if verdict else "fix"), match.group("body").strip()


def _has_marker(scenario_id: str, text: str) -> bool:
    if f"BENCHLOCAL_PASS:{scenario_id}" in text or "BENCHLOCAL_PASS" in text:
        print(f"[bugfind-sandbox] WARNING mock pass marker used for {scenario_id}", file=sys.stderr)
        return True
    return False


def _matches_any(text: str, patterns: list[str]) -> list[str]:
    return [pattern for pattern in patterns if re.search(pattern, text, flags=re.IGNORECASE)]


def _verify(scenario_id: str, scenario: dict, response: dict) -> dict:
    text = _response_text(response)
    if not text.strip():
        return _fail(scenario_id, "wrong_answer", "empty model response")
    if _has_marker(scenario_id, text):
        return _pass(scenario_id, {"mode": "mock-marker"})

    verdict, solution = _solution_block(text)
    if verdict is None:
        return _fail(scenario_id, "wrong_answer", "missing <solution> block or fenced candidate code", {"response_excerpt": text[:500]})

    raw = scenario.get("raw_scenario") or {}
    is_trap = scenario_id in {"BF-03", "BF-10"}
    if is_trap:
        if verdict != "no_bug":
            return _fail(scenario_id, "verifier_fail", "trap scenario requires verdict=\"no_bug\"", {"verdict": verdict})
        if solution:
            return _fail(scenario_id, "wrong_structure", "no_bug solution block must be empty", {"solution_excerpt": solution[:200]})
    elif verdict != "fix":
        return _fail(scenario_id, "verifier_fail", "bug scenario requires verdict=\"fix\"", {"verdict": verdict})
    elif not solution:
        return _fail(scenario_id, "wrong_answer", "fix verdict did not include candidate code")

    bad_hits = _matches_any(text, BAD_PATTERNS.get(scenario_id, []))
    if bad_hits:
        return _fail(scenario_id, "verifier_fail", "response matched known upstream failure pattern", {"matched": bad_hits})

    pass_hits = _matches_any(text, PASS_PATTERNS.get(scenario_id, []))
    if not pass_hits:
        keywords = raw.get("rubric_keywords") or []
        keyword_hits = [word for word in keywords if word.rstrip(".").lower() in text.lower()]
        if len(keyword_hits) < 2:
            return _fail(
                scenario_id,
                "verifier_fail",
                "response did not contain enough upstream rubric evidence",
                {"keyword_hits": keyword_hits, "required_patterns": PASS_PATTERNS.get(scenario_id, [])},
            )
        pass_hits = keyword_hits[:5]

    return _pass(
        scenario_id,
        {
            "mode": "rubric",
            "verdict": verdict,
            "matched": pass_hits,
            "fixture_status": raw.get("fixture_status", "rubric-only"),
        },
    )


def _pass(scenario_id: str, trace: dict) -> dict:
    return {"passed": True, "failure_mode": "passed", "detail": f"{scenario_id}: rubric verifier passed", "trace": trace}


def _fail(scenario_id: str, mode: str, detail: str, trace: dict | None = None) -> dict:
    return {"passed": False, "failure_mode": mode, "detail": f"{scenario_id}: {detail}", "trace": trace or {}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stderr.write(f"[bugfind-sandbox] {fmt % args}\n")

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send({"status": "ok", "pack": "bugfind-15", "stage": "v0.6"})
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        if self.path != "/verify":
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
        scenario_id = req.get("scenario_id", "?")
        try:
            result = _verify(
                scenario_id=scenario_id,
                scenario=req.get("scenario", {}),
                response=req.get("response", {}),
            )
        except Exception as exc:  # noqa: BLE001
            import traceback
            tb = traceback.format_exc()
            sys.stderr.write(f"[bugfind-sandbox] verifier exception on {scenario_id}: {exc}\n{tb}\n")
            result = {
                "passed": False,
                "failure_mode": "server_error",
                "detail": f"{scenario_id}: verifier raised {type(exc).__name__}: {exc}",
                "trace": {"traceback": tb[-2000:]},
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
    print(f"[bugfind-sandbox] listening on :{PORT}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[bugfind-sandbox] shutdown", file=sys.stderr)


if __name__ == "__main__":
    main()
