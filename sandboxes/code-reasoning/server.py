"""Code-reasoning verifier sandbox for HumanEval+ and LiveCodeBench fast packs."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 9000

CODE_FENCED_RE = re.compile(r"```(?:python|py)?[ \t\r]*\n?(.*?)```", re.DOTALL | re.IGNORECASE)
CODE_DEF_RE = re.compile(r"^(def\s+\w+.*?)(?=\n\S|\Z)", re.DOTALL | re.MULTILINE)
CODE_START_RE = re.compile(r"^(?:from\s+\S+\s+import\s+.+|import\s+.+|class\s+\w+\b.*|def\s+\w+\s*\(.*|@\w+.*)$", re.MULTILINE)
OPENING_FENCE_RE = re.compile(r"^```(?:python|py)?[ \t\r]*\n?", re.IGNORECASE)
OPENING_FENCE_ANYWHERE_RE = re.compile(r"```(?:python|py)?[ \t\r]*\n?", re.IGNORECASE)
LANGUAGE_LABEL_RE = re.compile(r"^\s*(?:python|py)\s*\n", re.IGNORECASE)


def _message(response: dict) -> dict:
    choices = response.get("choices") if isinstance(response, dict) else None
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        msg = first.get("message") if isinstance(first.get("message"), dict) else {}
        return msg
    return {}


def _response_text(response: dict) -> tuple[str, str | None]:
    """Resolve exactly one answer channel using the shared content-first policy."""
    msg = _message(response)
    for prefix, container in (("", response), ("message.", msg)):
        for key in ("content", "reasoning_content", "reasoning"):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return value, f"{prefix}{key}"
    return "", None


def _strip_unmatched_fence(code: str) -> str:
    code = code.strip()
    code = OPENING_FENCE_RE.sub("", code, count=1).strip()
    if code.endswith("```"):
        code = code[:-3].strip()
    return code


def extract_code_with_info(
    text: str,
    *,
    response_field_used: str | None = None,
) -> tuple[str, dict]:
    """Extract code from one already-selected response channel."""

    def _info(method: str, issue: str) -> dict:
        info = {"extraction_method": method, "extraction_issue": issue}
        if response_field_used is not None:
            info["response_field_used"] = response_field_used
        return info

    clean = text.lstrip()
    matches = CODE_FENCED_RE.findall(text)
    if matches:
        return matches[-1], _info("last_fenced", "none")
    if OPENING_FENCE_RE.match(clean):
        return _strip_unmatched_fence(clean), _info("opening_fence", "unterminated_fence")
    match = OPENING_FENCE_ANYWHERE_RE.search(clean)
    if match:
        remainder = clean[match.end():]
        if remainder.strip():
            return (
                _strip_unmatched_fence(remainder),
                _info("opening_fence_anywhere", "prose_before_unterminated_fence"),
            )
        # Nothing after the fence, so it is a stray CLOSER, not an opener --
        # the language group in OPENING_FENCE_ANYWHERE_RE is optional, so a
        # bare trailing ``` satisfies the "opening" pattern. Claiming the
        # record here would return the empty remainder AND short-circuit the
        # CODE_START_RE / CODE_DEF_RE heuristics below, which is what recovers
        # unfenced code. Drop the stray fence and keep going.
        clean = clean[:match.start()].rstrip()
    label = LANGUAGE_LABEL_RE.match(clean)
    if label:
        after_label = clean[label.end():].lstrip()
        match = CODE_START_RE.search(after_label)
        if match:
            prefix = after_label[:match.start()]
            issue = "language_label_before_code" if not prefix.strip() else "language_label_then_prose_before_code"
            return (
                _strip_unmatched_fence(after_label[match.start():]),
                _info("code_start_after_language_label", issue),
            )
    match = CODE_START_RE.search(clean)
    if match:
        prefix = clean[:match.start()]
        issue = "no_fenced_block" if not prefix.strip() else "prose_before_code"
        return _strip_unmatched_fence(clean[match.start():]), _info("code_start", issue)
    match = CODE_DEF_RE.search(clean)
    if match:
        return match.group(1), _info("def", "no_fenced_block")
    return "", _info("empty", "empty_code")


def _run_python(source: str, timeout: int = 30) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tf:
        tf.write(source)
        path = tf.name
    try:
        proc = subprocess.run([sys.executable, path], timeout=timeout, capture_output=True)
        ok = proc.returncode == 0
        err = proc.stderr.decode("utf-8", errors="ignore")[-1200:] if not ok else ""
        return ok, err
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"[:300]
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _with_lcb_prelude(code: str) -> str:
    lines = code.splitlines()
    insert_at = 0
    while insert_at < len(lines) and lines[insert_at].startswith("from __future__ import "):
        insert_at += 1
    lines.insert(insert_at, "from typing import *")
    suffix = "\n" if code.endswith("\n") else ""
    return "\n".join(lines) + suffix


def _lcb_fn_name(problem: dict) -> str:
    meta = problem.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    for key in ("func_name", "function_name", "fn_name", "entry_point"):
        if meta.get(key):
            return str(meta[key])
    starter = problem.get("starter_code") or ""
    m = re.search(r"def\s+(\w+)\s*\(", starter)
    return m.group(1) if m else ""


_LCB_TAIL = r'''
import json as _json
_tests = _json.loads(__BENCHLOCAL_TESTS__)
_FN_NAME = __BENCHLOCAL_FN_NAME__

def _resolve_fn():
    ns = globals()
    if "Solution" in ns and hasattr(ns["Solution"], _FN_NAME):
        return getattr(ns["Solution"](), _FN_NAME)
    if _FN_NAME in ns:
        return ns[_FN_NAME]
    raise RuntimeError(f"entry point {_FN_NAME} not found")

def _parse(s):
    try:
        return _json.loads(s)
    except Exception:
        return s

def _run():
    fn = _resolve_fn()
    for i, t in enumerate(_tests):
        lines = [ln for ln in t["input"].splitlines() if ln.strip() != ""]
        args = [_parse(ln) for ln in lines]
        expected = _parse(t["output"])
        got = fn(*args)
        if _json.dumps(got, sort_keys=True) != _json.dumps(expected, sort_keys=True):
            raise AssertionError(f"test {i}: expected {expected!r}, got {got!r}")
_run()
'''


def verify_code(scenario_id: str, scenario: dict, response: dict) -> dict:
    text, response_field_used = _response_text(response)
    code, info = extract_code_with_info(
        text,
        response_field_used=response_field_used,
    )
    if not code.strip():
        return fail(scenario_id, "wrong_answer", "no runnable-looking Python code extracted", info)
    raw = scenario.get("raw_problem") or {}
    dataset = raw.get("dataset")
    if dataset == "humaneval-plus":
        source = f"{code}\n\n{raw.get('test','')}\n\ncheck({raw.get('entry_point')})\n"
        ok, err = _run_python(source)
    elif dataset == "livecodebench-v6":
        fn = _lcb_fn_name(raw)
        if not fn:
            return fail(scenario_id, "verifier_fail", "no LiveCodeBench entry point found", info)
        tests = raw.get("public_test_cases") or "[]"
        source = _with_lcb_prelude(code) + "\n" + _LCB_TAIL.replace("__BENCHLOCAL_TESTS__", repr(tests)).replace("__BENCHLOCAL_FN_NAME__", repr(fn))
        ok, err = _run_python(source)
    else:
        return fail(scenario_id, "verifier_fail", f"unknown code dataset {dataset!r}", info)
    trace = {**info, "code_excerpt": code[:800]}
    if ok:
        return {"passed": True, "failure_mode": "passed", "detail": f"{scenario_id}: tests passed", **trace}
    return fail(scenario_id, classify_failure(err, info), err or "tests failed", trace)


def classify_failure(err: str, info: dict) -> str:
    if info.get("extraction_issue") == "empty_code":
        return "wrong_answer"
    if err == "TIMEOUT":
        return "timeout"
    if "SyntaxError" in err or "IndentationError" in err:
        return "verifier_fail"
    if "AssertionError" in err:
        return "wrong_answer"
    return "verifier_fail"


def fail(scenario_id: str, mode: str, detail: str, trace: dict | None = None) -> dict:
    return {"passed": False, "failure_mode": mode, "detail": f"{scenario_id}: {detail}"[:1200], **(trace or {})}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stderr.write(f"[code-reasoning-sandbox] {fmt % args}\n")

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send({"status": "ok", "pack": "code-reasoning"})
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
            payload = verify_code(str(req.get("scenario_id", "unknown")), req.get("scenario", {}), req.get("response", {}))
        except Exception as exc:  # noqa: BLE001
            import traceback
            payload = {"passed": False, "failure_mode": "server_error", "detail": f"verifier raised {type(exc).__name__}: {exc}", "traceback": traceback.format_exc()[-2000:]}
        self._send(payload)

    def _json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        data = json.loads(self.rfile.read(length).decode("utf-8") if length else "{}")
        return data if isinstance(data, dict) else {}

    def _send(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    print("[code-reasoning-sandbox] listening on :9000", file=sys.stderr)
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
