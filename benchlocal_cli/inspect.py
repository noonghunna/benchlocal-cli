"""`benchlocal-cli inspect <result.json>` — surface saved-JSON forensics
without manual jq / python -c grep.

Per Codex review of the v0.8 brief:
- B.0 MVP scope: --scenario, --pack, --failed, --mode, --full, --format json (#3)
- Default truncation: verifier_trace ~80 lines, conversation 5 turns (#3)
- Missing-field tolerance for older v0.5/v0.6/v0.7.0 saved JSONs (#6)
- Color on TTY only

v0.8.1 Phase B.5 additions:
- --diff <other.json>: side-by-side scenario comparison vs another run
- --logs DIR: pull associated sandbox stdout/stderr after rendering
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


# Truncation defaults (Codex review #3 — gives a usable default; --full disables)
DEFAULT_TRACE_LINES = 80
DEFAULT_CONVERSATION_TURNS = 5
DEFAULT_RAW_RESPONSE_CHARS = 4000


def _is_tty() -> bool:
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


_PASS_GREEN = "\033[32m"
_FAIL_RED = "\033[31m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def _color(text: str, code: str) -> str:
    return f"{code}{text}{_RESET}" if _is_tty() else text


def _truncate_lines(value: str, max_lines: int) -> tuple[str, int]:
    """Truncate to max_lines; returns (truncated_text, dropped_count)."""
    if not value:
        return value, 0
    lines = value.splitlines()
    if len(lines) <= max_lines:
        return value, 0
    return "\n".join(lines[:max_lines]), len(lines) - max_lines


def _safe_get(d: dict | None, key: str, default: Any = None) -> Any:
    return d.get(key, default) if isinstance(d, dict) else default


def _scenario_response_field(run: dict) -> str:
    """Old saved JSONs (v0.5/v0.6) used `response`; v0.7+ uses `raw_response`.
    Also handle the multi-turn case where raw_response is {"multi_turn": true,
    "responses": [...]}.
    """
    rr = run.get("raw_response") or run.get("response") or {}
    if isinstance(rr, dict) and rr.get("multi_turn"):
        responses = rr.get("responses") or []
        if responses:
            return json.dumps(responses[-1], indent=2)[:DEFAULT_RAW_RESPONSE_CHARS]
        return "<multi-turn run, no responses captured>"
    if isinstance(rr, dict):
        choices = rr.get("choices")
        if isinstance(choices, list) and choices:
            msg = (choices[0] or {}).get("message") if isinstance(choices[0], dict) else None
            if isinstance(msg, dict):
                return json.dumps(msg, indent=2)[:DEFAULT_RAW_RESPONSE_CHARS]
        return json.dumps(rr, indent=2)[:DEFAULT_RAW_RESPONSE_CHARS]
    return str(rr)[:DEFAULT_RAW_RESPONSE_CHARS]


def _matches(run: dict, pack_id: str | None, args: argparse.Namespace) -> bool:
    if args.scenario and run.get("id") != args.scenario:
        return False
    if args.pack and pack_id != args.pack:
        return False
    if args.failed and run.get("passed"):
        return False
    if args.mode and run.get("failure_mode") != args.mode:
        return False
    return True


def _format_scenario(pack: dict, run: dict, full: bool) -> list[str]:
    """Render one scenario as a list of markdown-ish lines."""
    sid = run.get("id") or "?"
    passed = bool(run.get("passed"))
    failure_mode = run.get("failure_mode") or "?"
    detail = run.get("detail") or ""
    latency = run.get("latency_seconds") or 0.0
    tokens = run.get("tokens_completion")
    repeat = run.get("repeat_index") or 1
    turn_count = run.get("turn_count")

    badge = _color("PASS", _PASS_GREEN) if passed else _color("FAIL", _FAIL_RED)
    pack_id = pack.get("pack_id") or "?"
    out = [
        f"## {pack_id} :: {sid}  [{badge}]  ({failure_mode})",
        f"- pack version: {pack.get('version', '?')}",
        f"- detail: {detail[:300]}",
        f"- latency: {latency:.2f}s · tokens_completion: {tokens} · repeat: {repeat}"
        + (f" · turns: {turn_count}" if turn_count is not None else ""),
    ]

    expected = _safe_get(run.get("raw_scenario"), "expected") or _safe_get(run.get("raw_scenario"), "success_case")
    if expected:
        exp_str = json.dumps(expected, indent=2) if isinstance(expected, (dict, list)) else str(expected)
        if not full:
            exp_str = exp_str[:600]
        out.append("\n### Expected")
        out.append("```")
        out.append(exp_str)
        out.append("```")

    out.append("\n### Final response")
    out.append("```")
    out.append(_scenario_response_field(run))
    out.append("```")

    # verifier_trace (Codex #6: tolerate absence in v0.5/v0.6 JSONs)
    trace = run.get("verifier_trace")
    if trace:
        out.append("\n### Verifier trace")
        trace_json = json.dumps(trace, indent=2)
        if not full:
            trace_json, dropped = _truncate_lines(trace_json, DEFAULT_TRACE_LINES)
            out.append("```")
            out.append(trace_json)
            if dropped:
                out.append(_color(f"... [{dropped} more lines truncated — use --full to see all]", _DIM))
            out.append("```")
        else:
            out.append("```")
            out.append(trace_json)
            out.append("```")
    else:
        out.append(_color("\n### Verifier trace: (none — pre-v0.7.2 saved JSON or in-process verifier)", _DIM))

    # conversation (multi-turn only)
    conversation = run.get("conversation") or []
    if conversation:
        out.append(f"\n### Conversation ({len(conversation)} messages)")
        if not full and len(conversation) > DEFAULT_CONVERSATION_TURNS * 2:
            shown = conversation[: DEFAULT_CONVERSATION_TURNS * 2]
            dropped = len(conversation) - len(shown)
        else:
            shown = conversation
            dropped = 0
        for i, msg in enumerate(shown):
            role = msg.get("role", "?") if isinstance(msg, dict) else "?"
            content = (msg.get("content") if isinstance(msg, dict) else str(msg)) or ""
            content_str = (
                json.dumps(content, indent=2) if isinstance(content, (dict, list)) else str(content)
            )
            if not full and len(content_str) > 400:
                content_str = content_str[:400] + " ..."
            out.append(f"  [{i}] {role}: {content_str}")
        if dropped:
            out.append(_color(f"  ... [{dropped} more messages — use --full to see all]", _DIM))

    return out


def inspect_result(
    result_path: str | Path,
    args: argparse.Namespace,
) -> int:
    """Main entry point. Returns process exit code."""
    path = Path(result_path)
    if not path.is_file():
        print(f"benchlocal-cli inspect: file not found: {result_path}", file=sys.stderr)
        return 1
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"benchlocal-cli inspect: failed to read {result_path}: {exc}", file=sys.stderr)
        return 1

    # v0.8.1: --diff loads a second result and renders side-by-side
    diff_path = getattr(args, "diff", None)
    diff_index: dict[tuple[str, str], tuple[dict, dict]] | None = None
    if diff_path:
        diff_p = Path(diff_path)
        if not diff_p.is_file():
            print(f"benchlocal-cli inspect: --diff file not found: {diff_path}", file=sys.stderr)
            return 1
        try:
            diff_result = json.loads(diff_p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"benchlocal-cli inspect: failed to read --diff: {exc}", file=sys.stderr)
            return 1
        diff_index = _build_index(diff_result)

    # v0.8.1: --logs resolves sandbox stdout/stderr files
    logs_dir: Path | None = None
    if getattr(args, "logs", None):
        logs_dir = Path(args.logs)
        if not logs_dir.is_dir():
            print(f"benchlocal-cli inspect: --logs not a directory: {args.logs}", file=sys.stderr)
            return 1

    schema_version = result.get("schema_version", "unknown")
    matched: list[tuple[dict, dict]] = []
    for pack in result.get("packs") or []:
        pack_id = pack.get("pack_id")
        for run in pack.get("scenarios") or []:
            run = _hoist_result_fields(run)
            if _matches(run, pack_id, args):
                matched.append((pack, run))

    if not matched:
        print(f"# benchlocal-cli inspect — no scenarios matched filters", file=sys.stderr)
        if any([args.scenario, args.pack, args.failed, args.mode]):
            filter_desc = []
            if args.scenario:
                filter_desc.append(f"scenario={args.scenario}")
            if args.pack:
                filter_desc.append(f"pack={args.pack}")
            if args.failed:
                filter_desc.append("failed=True")
            if args.mode:
                filter_desc.append(f"mode={args.mode}")
            print(f"#   filters: {', '.join(filter_desc)}", file=sys.stderr)
        return 2

    if args.format == "json":
        out = []
        for pack, run in matched:
            entry = {
                "pack_id": pack.get("pack_id"),
                "pack_version": pack.get("version"),
                **run,
            }
            if diff_index is not None:
                key = (pack.get("pack_id") or "?", run.get("id") or "?")
                prev_pair = diff_index.get(key)
                entry["previous_run"] = prev_pair[1] if prev_pair else None
            out.append(entry)
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0

    # markdown / human-readable
    header = (
        f"# benchlocal-cli inspect — {path.name}"
        + (f"  (vs {Path(diff_path).name})" if diff_path else "")
        + "\n"
        f"_run: {result.get('mode', '?')} mode · "
        f"endpoint: {result.get('endpoint', '?')} · "
        f"model: {result.get('model', '?')} · "
        f"schema: v{schema_version} · "
        f"{result.get('started_at', '?')}_\n"
        f"_matched: {len(matched)} scenario(s)_"
    )
    print(header)
    print()

    for pack, run in matched:
        if diff_index is not None:
            key = (pack.get("pack_id") or "?", run.get("id") or "?")
            prev_pair = diff_index.get(key)
            previous_run = prev_pair[1] if prev_pair else None
            for line in _format_diff(pack, run, previous_run, full=args.full):
                print(line)
        else:
            for line in _format_scenario(pack, run, full=args.full):
                print(line)
        if logs_dir is not None:
            log_path = _resolve_log_path(logs_dir, run, pack)
            if log_path:
                for line in _format_log_tail(log_path):
                    print(line)
            else:
                print(_color(
                    f"\n### Sandbox log: not found in {logs_dir} "
                    f"(no `verifier_trace.sandbox_log_file` and no fallback "
                    f"sandbox-{pack.get('pack_id')}.log)",
                    _DIM,
                ))
        print()
    return 0


def _hoist_result_fields(run: dict) -> dict:
    """Older saved JSONs (and the v0.7.x ScenarioRun.to_dict) nested
    pass/failure_mode under `result`. Hoist them up so filters work uniformly."""
    if "passed" not in run and isinstance(run.get("result"), dict):
        return {**run, **run["result"]}
    return run


def _build_index(result: dict) -> dict[tuple[str, str], tuple[dict, dict]]:
    """Build {(pack_id, scenario_id): (pack_dict, run_dict)} from a result."""
    out: dict[tuple[str, str], tuple[dict, dict]] = {}
    for pack in result.get("packs") or []:
        pack_id = pack.get("pack_id") or "?"
        for run in pack.get("scenarios") or []:
            run = _hoist_result_fields(run)
            sid = run.get("id") or "?"
            # Last write wins for multi-repeat (most recent repeat run).
            # For diff we just want a representative run per (pack, id).
            out[(pack_id, sid)] = (pack, run)
    return out


def _format_diff(pack: dict, current_run: dict, previous_run: dict | None, full: bool) -> list[str]:
    """v0.8.1: render the current-vs-previous diff for one scenario.
    Layout: header + side-by-side prev/cur columns for verdict + key fields."""
    sid = current_run.get("id") or "?"
    pack_id = pack.get("pack_id") or "?"
    out = [f"## {pack_id} :: {sid}  [DIFF]"]

    if previous_run is None:
        out.append(_color(f"- previous run has no row for ({pack_id}, {sid}) — scenario is NEW in current.", _DIM))
        out.extend(_format_scenario(pack, current_run, full=full))
        return out

    cur_pass = bool(current_run.get("passed"))
    prev_pass = bool(previous_run.get("passed"))
    cur_badge = _color("PASS", _PASS_GREEN) if cur_pass else _color("FAIL", _FAIL_RED)
    prev_badge = _color("PASS", _PASS_GREEN) if prev_pass else _color("FAIL", _FAIL_RED)

    if cur_pass and not prev_pass:
        flip = _color(" (FIX)", _PASS_GREEN)
    elif not cur_pass and prev_pass:
        flip = _color(" (REGRESSION)", _FAIL_RED)
    elif cur_pass:
        flip = " (stable PASS)"
    else:
        flip = " (stable fail)"

    out.append(f"verdict:{flip}")
    out.append(f"  previous: {prev_badge}  ({previous_run.get('failure_mode', '?')})  {(previous_run.get('detail') or '')[:120]}")
    out.append(f"  current:  {cur_badge}  ({current_run.get('failure_mode', '?')})  {(current_run.get('detail') or '')[:120]}")

    cur_resp = _scenario_response_field(current_run)
    prev_resp = _scenario_response_field(previous_run)
    if cur_resp != prev_resp:
        out.append("\n### Final response (changed)")
        out.append("--- previous ---")
        out.append("```")
        out.append(prev_resp[:600] if not full else prev_resp)
        out.append("```")
        out.append("--- current ---")
        out.append("```")
        out.append(cur_resp[:600] if not full else cur_resp)
        out.append("```")
    else:
        out.append(_color("\n### Final response: unchanged between runs", _DIM))

    # Upstream score delta (v0.7.4 saved JSONs)
    cur_trace = current_run.get("verifier_trace") or {}
    prev_trace = previous_run.get("verifier_trace") or {}
    cur_inner = cur_trace.get("trace") if isinstance(cur_trace.get("trace"), dict) else cur_trace
    prev_inner = prev_trace.get("trace") if isinstance(prev_trace.get("trace"), dict) else prev_trace
    cur_score = (cur_inner or {}).get("upstream_score")
    prev_score = (prev_inner or {}).get("upstream_score")
    if cur_score is not None or prev_score is not None:
        out.append(f"\n### Upstream score: previous={prev_score}  →  current={cur_score}")

    # Latency delta
    cur_lat = current_run.get("latency_seconds") or 0.0
    prev_lat = previous_run.get("latency_seconds") or 0.0
    if cur_lat or prev_lat:
        delta = cur_lat - prev_lat
        sign = "+" if delta >= 0 else ""
        out.append(f"### Latency: prev={prev_lat:.2f}s → cur={cur_lat:.2f}s ({sign}{delta:.2f}s)")

    return out


def _resolve_log_path(logs_dir: Path, run: dict, pack: dict) -> Path | None:
    """v0.8.1: --logs DIR resolution. Prefer `verifier_trace.sandbox_log_file`
    (per-scenario field added in v0.8.1 Phase A); fall back to
    `<DIR>/sandbox-<pack_id>.log` for v0.7.2-v0.8.0 saved JSONs."""
    trace = run.get("verifier_trace") or {}
    inner = trace.get("trace") if isinstance(trace.get("trace"), dict) else trace
    log_file = (inner or {}).get("sandbox_log_file") or trace.get("sandbox_log_file")
    if isinstance(log_file, str) and log_file:
        candidate = logs_dir / log_file
        if candidate.is_file():
            return candidate
    # Fallback: <DIR>/sandbox-<pack_id>.log
    pack_id = pack.get("pack_id") or ""
    if pack_id:
        candidate = logs_dir / f"sandbox-{pack_id}.log"
        if candidate.is_file():
            return candidate
    return None


def _format_log_tail(log_path: Path, max_bytes: int = 4000) -> list[str]:
    """Render the tail of a sandbox log file."""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [_color(f"\n### Sandbox log: read error — {exc}", _FAIL_RED)]
    if len(text) > max_bytes:
        text = "...\n" + text[-max_bytes:]
    out = [f"\n### Sandbox log: {log_path}", "```", text, "```"]
    return out


def add_inspect_subparser(subparsers) -> None:
    """Wire inspect into the main CLI parser. Imported by cli.py."""
    parser = subparsers.add_parser(
        "inspect",
        help="surface forensics from a saved RunResult JSON",
        description=(
            "Read a saved --save-json output and surface per-scenario "
            "details (final response, verifier trace, conversation) "
            "without manual JSON grepping."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  benchlocal-cli inspect results/run.json --scenario HA-01\n"
            "  benchlocal-cli inspect results/run.json --pack hermesagent-20 --failed\n"
            "  benchlocal-cli inspect results/run.json --failed --mode timeout\n"
            "  benchlocal-cli inspect results/run.json --full --format json | jq\n"
        ),
    )
    parser.add_argument("path", help="path to saved RunResult JSON (--save-json output)")
    parser.add_argument("--scenario", help="show only this scenario id (e.g. HA-01)")
    parser.add_argument("--pack", help="show only scenarios in this pack (e.g. hermesagent-20)")
    parser.add_argument("--failed", action="store_true", help="show only failed scenarios")
    parser.add_argument(
        "--mode",
        help="show only scenarios with this failure_mode (e.g. timeout, verifier_fail, agent_runner_timeout)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="don't truncate verifier_trace (default: 80 lines) or conversation (default: 5 turns)",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="output format (default: markdown)",
    )
    # v0.8.1 — Phase B.5
    parser.add_argument(
        "--diff",
        metavar="OTHER_RESULT_JSON",
        help="render side-by-side scenario comparison vs another saved RunResult. "
             "Per-(pack_id, scenario_id) match. Combine with --scenario to focus.",
    )
    parser.add_argument(
        "--logs",
        metavar="DIR",
        help="pull sandbox stdout/stderr files from this directory (the same path "
             "passed to `run --sandbox-log-dir`). Resolved via per-scenario "
             "`verifier_trace.sandbox_log_file` (v0.8.1+) with fallback to "
             "<DIR>/sandbox-<pack_id>.log.",
    )
