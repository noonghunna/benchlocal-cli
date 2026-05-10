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
        → run full mode (medium + ReasonMath-15 + optional sandboxed packs)

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

from benchlocal_cli.runner import PACK_MODES, SANDBOX_MODES, Runner, list_packs, load_pack
from benchlocal_cli.types import RunResult


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="benchlocal-cli",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Run BenchLocal-style behavioral quality packs against any "
            "OpenAI-compatible endpoint."
        ),
        epilog=(
            "Modes:\n"
            "  --quick    2 packs, 30 scenarios, no Docker        (~5-10 min)\n"
            "  --medium   5 packs, 75 scenarios, no Docker        (~15-25 min)\n"
            "  --full     8 packs, 150 scenarios, requires Docker (~25-40 min)\n"
            "\n"
            "Examples:\n"
            "  benchlocal-cli run --quick --endpoint http://localhost:8010 --model qwen3.6-27b\n"
            "  benchlocal-cli run --full  --endpoint http://localhost:8010 --model qwen3.6-27b\n"
            "  benchlocal-cli run --pack toolcall-15 --endpoint http://localhost:8010 --model qwen3.6-27b\n"
            "  benchlocal-cli list         # show all available packs\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list available packs")

    run = sub.add_parser(
        "run",
        help="run benchmark packs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--quick", action="store_true", help="2 packs, ~5-10 min, no Docker")
    mode.add_argument("--medium", action="store_true", help="5 packs (default), ~15-25 min, no Docker")
    mode.add_argument("--full", action="store_true", help="8 packs incl. sandboxed, ~25-40 min, requires Docker")
    run.add_argument("--pack", help="run a single named pack (overrides --quick/--medium/--full)")
    run.add_argument("--endpoint", required=True, help="OpenAI-compatible base URL (e.g. http://localhost:8010)")
    run.add_argument("--model", required=True, help="model id served by the endpoint")
    run.add_argument("--timeout-per-case", type=float, default=60.0, help="per-scenario HTTP timeout (default: 60s)")
    run.add_argument("--output", choices=["markdown", "json"], default="markdown", help="output format (default: markdown)")
    run.add_argument("--save-json", help="also save raw JSON results to this path")
    run.add_argument("--repeat", type=int, default=1, help="repeat each scenario N times (default: 1)")
    run.add_argument(
        "--enable-sandboxed-packs",
        action="store_true",
        help="DEPRECATED: --full enables sandboxed packs by default; kept for backwards compat",
    )
    run.add_argument(
        "--no-sandboxed-packs",
        action="store_true",
        help="opt out of sandboxed packs even on --full (use --medium for clean deterministic-only)",
    )
    run.add_argument(
        "--sandboxed-only",
        action="store_true",
        help="run only the sandboxed packs (bugfind-15, cli-40, hermesagent-20) — skips deterministic packs; useful when debugging verifiers",
    )
    run.add_argument("--sandbox-image-tag", default="latest", help="Docker tag for sandbox images (default: latest)")
    run.add_argument(
        "--sandbox-log-dir",
        help="capture sandbox container stdout/stderr to <dir>/sandbox-<pack-id>.log "
             "before container teardown (useful for post-run forensics)",
    )
    run.add_argument("--enable-thinking", action="store_true", help="run with reasoning/thinking enabled (default: off)")
    run.add_argument("--thinking-max-tokens", type=int, default=4096)
    run.add_argument("--extra-body", help="JSON object merged into each chat-completions request body")
    run.add_argument("--mock-responses-from-json", help="JSON object mapping scenario id to OpenAI response (testing only)")
    return parser


def _print_list() -> None:
    print("Pack | Version | Scenarios | Verifier | Status")
    print("---|---:|---:|---|---")
    for meta in list_packs():
        status = "sandboxed" if meta.get("supports_sandboxed_only") else "ready"
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
    thinking = "on" if result.thinking_enabled else "off"
    lines = [
        f"=== benchlocal-cli --{result.mode}  (endpoint: {result.endpoint}, model: {result.model}, thinking={thinking}, {result.started_at}) ===",
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


def _load_extra_body(value: str | None) -> dict | None:
    if not value:
        return None
    data = json.loads(value)
    if not isinstance(data, dict):
        raise ValueError("--extra-body must be a JSON object")
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
        if args.sandboxed_only:
            from benchlocal_cli.runner import SANDBOXED_PACK_IDS
            pack_ids = list(SANDBOXED_PACK_IDS)
            mode = "sandboxed-only"
        elif args.pack:
            pack_ids = [args.pack]
        else:
            pack_ids = PACK_MODES[mode]
        # --full implies sandboxed packs by default; --no-sandboxed-packs opts out.
        # --sandboxed-only also implies sandbox is enabled (no point otherwise).
        # Single-pack runs (--pack) auto-enable sandbox if the pack requires it.
        sandboxed_enabled = (
            args.enable_sandboxed_packs
            or mode in SANDBOX_MODES
            or args.sandboxed_only
        )
        if args.pack:
            try:
                meta, _ = load_pack(args.pack)
                if meta.get("supports_sandboxed_only"):
                    sandboxed_enabled = True
            except Exception:
                pass  # let Runner surface the unknown-pack error
        if args.no_sandboxed_packs and not args.sandboxed_only:
            sandboxed_enabled = False
        runner = Runner(
            endpoint=args.endpoint,
            model=args.model,
            timeout_per_case=args.timeout_per_case,
            enable_sandboxed_packs=sandboxed_enabled,
            mock_responses=_load_mock(args.mock_responses_from_json),
            thinking_enabled=args.enable_thinking,
            thinking_max_tokens=args.thinking_max_tokens,
            extra_body=_load_extra_body(args.extra_body),
            sandbox_image_tag=args.sandbox_image_tag,
            sandbox_log_dir=args.sandbox_log_dir,
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
