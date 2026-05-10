"""`benchlocal-cli inspect <result.json>` — surface saved-JSON forensics
without manual jq / python -c grep.

Per Codex review of the v0.8 brief:
- B.0 MVP scope: --scenario, --pack, --failed, --mode, --full, --format json (#3)
- Default truncation: verifier_trace ~80 lines, conversation 5 turns (#3)
- Missing-field tolerance for older v0.5/v0.6/v0.7.0 saved JSONs (#6)
- Color on TTY only

B.5 (--diff, --logs) is deliberately NOT here — see CODEX_BRIEF_V8.md.
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

    schema_version = result.get("schema_version", "unknown")
    matched: list[tuple[dict, dict]] = []
    for pack in result.get("packs") or []:
        pack_id = pack.get("pack_id")
        for run in pack.get("scenarios") or []:
            # The runner's saved scenario dict has both run-level and result-level
            # fields (see ScenarioRun.to_dict). For older JSONs that nested
            # result fields under `result`, hoist them up.
            if "passed" not in run and isinstance(run.get("result"), dict):
                merged = {**run, **run["result"]}
                run = merged
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
        # Strip pack-level metadata to make the output a flat list per Codex review hint
        out = []
        for pack, run in matched:
            out.append({
                "pack_id": pack.get("pack_id"),
                "pack_version": pack.get("version"),
                **run,
            })
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0

    # markdown / human-readable
    header = (
        f"# benchlocal-cli inspect — {path.name}\n"
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
        for line in _format_scenario(pack, run, full=args.full):
            print(line)
        print()
    return 0


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
