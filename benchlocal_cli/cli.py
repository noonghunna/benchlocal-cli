"""CLI entry point — `benchlocal-cli ...`

TODO (Codex): implement argument parsing + command dispatch.
Target UX (subject to refinement):

    benchlocal-cli list
        → list all available packs with version + scoring type

    benchlocal-cli run --quick --endpoint URL --model NAME
        → run quick mode (ToolCall-15 + InstructFollow-15)

    benchlocal-cli run --medium --endpoint URL --model NAME [--repeat N]
        → run medium mode (quick + StructOutput-15 + DataExtract-15)

    benchlocal-cli run --full --endpoint URL --model NAME
        → run full mode (medium + ReasonMath-15 + warn-skip for stubbed packs)

    benchlocal-cli run --pack PACK_ID --endpoint URL --model NAME
        → run a single named pack (ignores mode flag)

    benchlocal-cli run ... --output {markdown,json}
        → output format (default: markdown to stdout)

    benchlocal-cli run ... --timeout-per-case SECONDS
        → per-scenario HTTP timeout (default: 60)

    benchlocal-cli run ... --previous-result PATH
        → compare against a previous result JSON, emit delta column

See docs/DESIGN.md for the rationale behind these choices.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from benchlocal_cli.runner import PACK_MODES, Runner, list_packs
from benchlocal_cli.types import RunResult


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="benchlocal-cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list available packs")

    run = sub.add_parser("run", help="run benchmark packs")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--quick", action="store_true")
    mode.add_argument("--medium", action="store_true")
    mode.add_argument("--full", action="store_true")
    run.add_argument("--pack", help="run a single named pack")
    run.add_argument("--endpoint", required=True)
    run.add_argument("--model", required=True)
    run.add_argument("--timeout-per-case", type=float, default=60.0)
    run.add_argument("--output", choices=["markdown", "json"], default="markdown")
    run.add_argument("--save-json")
    run.add_argument("--repeat", type=int, default=1)
    run.add_argument("--enable-sandboxed-packs", action="store_true")
    run.add_argument("--mock-responses-from-json", help="JSON object mapping scenario id to OpenAI response")
    return parser


def _print_list() -> None:
    print("Pack | Version | Scenarios | Verifier | Status")
    print("---|---:|---:|---|---")
    for meta in list_packs():
        status = "sandboxed stub" if meta.get("supports_sandboxed_only") else "ready"
        print(
            f"{meta['pack_id']} | {meta['version']} | {meta['scenario_count']} | "
            f"{meta['verifier_module']} | {status}"
        )


def _mode_from_args(args: argparse.Namespace) -> str:
    if args.pack:
        return "custom"
    if args.quick:
        return "quick"
    if args.full:
        return "full"
    return "medium"


def _markdown(result: RunResult) -> str:
    lines = [
        f"=== benchlocal-cli --{result.mode}  (endpoint: {result.endpoint}, model: {result.model}, {result.started_at}) ===",
        "",
        "Pack | Pass / Total | Score | p50 latency | p95 latency | Status",
        "---|---:|---:|---:|---:|---",
    ]
    for pack in result.packs:
        status = "skipped" if pack.skipped else ("ok" if pack.total else pack.status)
        score = f"{pack.score:.0%}" if pack.total else "-"
        p50 = "-" if pack.latency["p50"] is None else f"{pack.latency['p50']:.2f}s"
        p95 = "-" if pack.latency["p95"] is None else f"{pack.latency['p95']:.2f}s"
        lines.append(
            f"{pack.pack_id} (v{pack.version}) | {pack.passed} / {pack.total} | {score} | {p50} | {p95} | {status}"
        )
    lines.extend(
        [
            "",
            f"TOTAL | {result.totals['passed']} / {result.totals['total']} | {result.totals['score']:.0%} |  |  |",
        ]
    )
    failures: list[str] = []
    for pack in result.packs:
        for scenario in pack.scenarios:
            if not scenario.result.passed and scenario.result.failure_mode != "verifier_not_implemented":
                failures.append(
                    f"{pack.pack_id} {scenario.id}: {scenario.result.failure_mode} ({scenario.result.detail})"
                )
    if failures:
        lines.append("")
        lines.append("Failure breakdown:")
        lines.extend(f"- {failure}" for failure in failures)
    if result.warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in result.warnings)
    return "\n".join(lines)


def _load_mock(path: str | None) -> dict[str, dict] | None:
    if not path:
        return None
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("--mock-responses-from-json must point to a JSON object")
    return data


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns process exit code."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            _print_list()
            return 0

        mode = _mode_from_args(args)
        pack_ids = [args.pack] if args.pack else PACK_MODES[mode]
        runner = Runner(
            endpoint=args.endpoint,
            model=args.model,
            timeout_per_case=args.timeout_per_case,
            enable_sandboxed_packs=args.enable_sandboxed_packs,
            mock_responses=_load_mock(args.mock_responses_from_json),
        )
        result = runner.run(pack_ids, mode=mode, repeat=max(1, args.repeat))
        result_dict = result.to_dict()
        if args.save_json:
            with Path(args.save_json).open("w", encoding="utf-8") as handle:
                json.dump(result_dict, handle, indent=2, sort_keys=True)
        if args.output == "json":
            print(json.dumps(result_dict, indent=2, sort_keys=True))
        else:
            print(_markdown(result))
        return 0 if result.totals["total"] > 0 else 2
    except Exception as exc:
        print(f"benchlocal-cli: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
