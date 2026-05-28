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
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from benchlocal_cli import __version__
from benchlocal_cli.runner import PACK_MODES, SANDBOX_MODES, Runner, _utc_now, list_packs, load_pack
from benchlocal_cli.types import PackResult, RunResult, ScenarioRun


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

    # v0.8 — `inspect` subcommand for surfacing saved-JSON forensics
    from benchlocal_cli.inspect import add_inspect_subparser
    add_inspect_subparser(sub)
    # v0.8 — `history` subcommand for querying the run-history CSV
    from benchlocal_cli.history import add_history_subparser
    add_history_subparser(sub)

    run = sub.add_parser(
        "run",
        help="run benchmark packs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = run.add_mutually_exclusive_group()
    mode.add_argument("--quick", action="store_true", help="2 packs, ~5-10 min, no Docker")
    mode.add_argument("--medium", action="store_true", help="5 packs (default), ~15-25 min, no Docker")
    mode.add_argument("--full", action="store_true", help="8 packs incl. sandboxed, ~25-40 min, requires Docker")
    mode.add_argument("--reasoning", action="store_true", help="reasoning packs: HE+, LCB v6, GPQA metadata, GSM-Symbolic; separate from --full")
    run.add_argument("--pack", help="run a single named pack (overrides --quick/--medium/--full)")
    run.add_argument("--endpoint", required=True, help="OpenAI-compatible base URL (e.g. http://localhost:8010)")
    run.add_argument("--model", required=True, help="model id served by the endpoint")
    run.add_argument("--timeout-per-case", type=float, default=60.0, help="per-scenario HTTP timeout (default: 60s)")
    run.add_argument("--output", choices=["markdown", "json"], default="markdown", help="output format (default: markdown)")
    run.add_argument("--save-json", help="also save raw JSON results to this path")
    run.add_argument("--repeat", type=int, default=1, help="repeat each scenario N times (default: 1)")
    # v0.9.3: incremental progress (#23) — live output during long runs
    run.add_argument(
        "--progress",
        action="store_true",
        help="emit per-scenario progress to stderr as [N/M] pack/scenario pass|fail (Xs). "
             "Makes long runs observable without waiting for the final scoreboard.",
    )
    run.add_argument(
        "--incremental",
        action="store_true",
        help="flush partial JSON to --save-json after each pack completes (not just at end). "
             "Requires --save-json. A crash mid-run preserves whatever packs finished.",
    )
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
        "--max-transient-retries",
        type=int,
        default=_env_int("BENCHLOCAL_MAX_TRANSIENT_RETRIES", 3),
        help=(
            "retry transient model endpoint failures this many times before failing "
            "a scenario (default: 3; env BENCHLOCAL_MAX_TRANSIENT_RETRIES)"
        ),
    )
    run.add_argument(
        "--sandbox-log-dir",
        help=(
            "capture sandbox container stdout/stderr to <dir>/sandbox-<pack-id>.log "
            "before container teardown; defaults next to --save-json for sandboxed runs, "
            "or use none to disable"
        ),
    )
    thinking = run.add_mutually_exclusive_group()
    thinking.add_argument(
        "--enable-thinking",
        dest="thinking_override",
        action="store_const",
        const=True,
        default=None,
        help="force reasoning/thinking on for every pack (default: use each pack's default_thinking metadata)",
    )
    thinking.add_argument(
        "--no-thinking",
        dest="thinking_override",
        action="store_const",
        const=False,
        help="force reasoning/thinking off for every pack, ignoring pack defaults",
    )
    run.add_argument(
        "--thinking-max-tokens",
        type=int,
        default=None,
        help="max_tokens to request when thinking is enabled "
             "(default: --max-tokens if set, else 16384)",
    )
    run.add_argument(
        "--thinking-sampler",
        help=(
            "JSON object used as the thinking-on distribution sampler "
            "(default: {\"temperature\":1.0,\"top_p\":0.95,\"top_k\":20,\"min_p\":0.0}). "
            "Explicit --temperature/--top-p/--top-k/--min-p flags override this; "
            "--sampling-from-server omits sampler params from requests."
        ),
    )
    # v0.9.1: opt-in sampling overrides (#19) — evaluate models at their
    # recommended temperature. Default behavior (per-pack temp=0) unchanged.
    # Any override tags the run as non-canonical in output + saved JSON.
    run.add_argument("--temperature", type=float, default=None, help="override sampling temperature (default: per-pack, usually 0)")
    run.add_argument("--top-p", type=float, default=None, help="override top-p / nucleus sampling (default: per-pack)")
    run.add_argument("--top-k", type=int, default=None, help="override top-k sampling (default: per-pack)")
    run.add_argument("--min-p", type=float, default=None, help="override min-p sampling (default: per-pack)")
    run.add_argument("--repeat-penalty", type=float, default=None, help="override repeat/frequency penalty (default: per-pack)")
    # #28: global length cap for both arms. Overrides the per-pack max_tokens
    # default for the base/no-think arm; also becomes the thinking-arm budget
    # unless --thinking-max-tokens is given (which wins for that arm). Lets a
    # thinking-vs-no-think A/B be pinned symmetric with one flag (e.g.
    # `--max-tokens 16384`). Tags the run non-canonical like other overrides.
    run.add_argument("--max-tokens", type=int, default=None, help="override max_tokens / length budget for both arms (default: per-pack; thinking arm uses --thinking-max-tokens if set)")
    # v0.9.2: inherit sampling from server (#21) — omit all sampling params
    # from the request so the server applies its own configured defaults.
    # Mutually exclusive with --temperature/--top-p/--top-k/--min-p/--repeat-penalty.
    run.add_argument(
        "--sampling-from-server",
        action="store_true",
        help=(
            "inherit sampling from the serving config (llama.cpp --temp, vLLM "
            "--override-generation-config). Omits all sampling params from requests "
            "so the server's defaults apply. Reads back via GET /props (llama.cpp) "
            "and records the values. Mutually exclusive with --temperature/--top-p/etc."
        ),
    )
    run.add_argument("--extra-body", help="JSON object merged into each chat-completions request body")
    run.add_argument("--mock-responses-from-json", help="JSON object mapping scenario id to OpenAI response (testing only)")
    # v0.8 — diagnostic tooling
    run.add_argument(
        "--previous-result",
        help="path to a previously-saved RunResult JSON. Compares the current run "
             "scenario-by-scenario and emits a delta column in the output (regressions, "
             "fixes, stable). Per-(pack_id, scenario_id) keying; multi-repeat aggregates "
             "to ≥50%% pass-rate (override via BENCHLOCAL_DELTA_PASS_THRESHOLD).",
    )
    run.add_argument(
        "--exit-on-regression",
        action="store_true",
        help="exit code 3 when --previous-result delta has any regressions. CI-friendly. "
             "Requires --previous-result to also be set. Blocked when sampling overrides "
             "are active (non-canonical runs shouldn't gate CI).",
    )
    run.add_argument(
        "--history-file",
        help="append a summary row to this CSV after the run completes (one row "
             "per run; columns: timestamp, run_id, mode, model, total_pass, total, "
             "score, per-pack pass/total, runner_version). Falls back to "
             "BENCHLOCAL_HISTORY_FILE env. Use `benchlocal-cli history` to query.",
    )
    return parser


def _print_list() -> None:
    print("Pack | Version | Scenarios | Verifier | Thinking | Status")
    print("---|---:|---:|---|---|---")
    for meta in list_packs():
        if meta.get("requires_dataset_access"):
            status = "gated"
        else:
            status = "sandboxed" if meta.get("supports_sandboxed_only") else "ready"
        thinking = meta.get("default_thinking", "off")
        print(
            f"{meta['pack_id']} | {meta['version']} | {meta['scenario_count']} | "
            f"{meta['verifier_module']} | {thinking} | {status}"
        )


def _mode_from_args(args: argparse.Namespace) -> str:
    if args.pack:
        return "custom"
    if args.quick:
        return "quick"
    if args.full:
        return "full"
    if getattr(args, "reasoning", False):
        return "reasoning"
    return "medium"


def _pack_line(pack: PackResult) -> str:
    """Format a single pack result line for incremental output (#23)."""
    if pack.skipped:
        status = "skipped"
    elif pack.status not in ("ok", "stubbed"):
        status = pack.status
    else:
        status = "ok" if pack.total else pack.status
    score = f"{pack.score:.0%}" if pack.total else "-"
    p50 = "-" if pack.latency["p50"] is None else f"{pack.latency['p50']:.2f}s"
    return f"{pack.pack_id} (v{pack.version}) | {pack.passed} / {pack.total} | {score} | {p50} | {status}"


def _scenario_progress(run: ScenarioRun, index: int, total: int) -> None:
    """Print per-scenario progress line to stderr (#23)."""
    result_char = "✓" if run.result.passed else "✗"
    latency = f"{run.result.latency_seconds:.1f}s" if run.result.latency_seconds > 0 else "?"
    print(
        f"  [{index}/{total}] {run.id} {result_char} {run.result.failure_mode} ({latency})",
        file=sys.stderr,
        flush=True,
    )


def _compute_partial_totals(packs: list) -> dict:
    """Compute totals for a partial run (incremental JSON save, #23)."""
    total = sum(p.total for p in packs)
    passed = sum(p.passed for p in packs)
    return {"passed": passed, "total": total, "score": (passed / total if total else 0.0)}


def _thinking_label(result: RunResult) -> str:
    if result.thinking_mode == "force-on":
        return "on"
    if result.thinking_mode == "force-off":
        return "off"
    pack_modes = {pack.thinking_enabled for pack in result.packs}
    if pack_modes == {True}:
        return "on(pack-defaults)"
    if pack_modes == {False}:
        return "off(pack-defaults)"
    if pack_modes == {False, True}:
        return "mixed(pack-defaults)"
    return "pack-defaults"


def _markdown(result: RunResult) -> str:
    thinking = _thinking_label(result)
    # v0.8: delta column rendered ONLY when --previous-result was actually
    # computed (Codex review #4 — keep default markdown byte-stable for
    # pinned downstream parsers like club-3090's quality-test.sh).
    delta_pack_index: dict[str, dict] | None = None
    if result.delta is not None:
        delta_pack_index = {p["pack_id"]: p for p in result.delta.get("by_pack") or []}

    # v0.9.1/#21: non-canonical banner when sampling is non-default
    canonical_tag = ""
    if result.sampling_source == "server":
        if result.server_defaults:
            sd_str = ", ".join(f"{k}={v}" for k, v in result.server_defaults.items())
            canonical_tag = f" ⚠ NON-CANONICAL (sampling: server defaults — {sd_str})"
        else:
            canonical_tag = " ⚠ NON-CANONICAL (sampling: server defaults — value not exposed by endpoint)"
    elif result.sampling_overrides:
        override_str = ", ".join(f"{k}={v}" for k, v in result.sampling_overrides.items())
        canonical_tag = f" ⚠ NON-CANONICAL (sampling: {override_str})"

    lines = [
        f"=== benchlocal-cli --{result.mode}  (endpoint: {result.endpoint}, model: {result.model}, thinking={thinking}, {result.started_at}){canonical_tag} ===",
        "",
    ]
    show_variance = delta_pack_index is None and any(pack.variance for pack in result.packs)
    if delta_pack_index is None:
        if show_variance:
            lines.append("Pack | Pass / Total | Score | Std | CV | p50 latency | p95 latency | Status")
            lines.append("---|---:|---:|---:|---:|---:|---:|---")
        else:
            lines.append("Pack | Pass / Total | Score | p50 latency | p95 latency | Status")
            lines.append("---|---:|---:|---:|---:|---")
    else:
        lines.append("Pack | Pass / Total | Score | Δ (vs prev) | p50 latency | p95 latency | Status")
        lines.append("---|---:|---:|---|---:|---:|---")

    for pack in result.packs:
        if pack.skipped:
            status = "skipped"
        elif pack.status not in ("ok", "stubbed"):
            # #3: surface a real failure mode (e.g. agent_runner_timeout) even
            # when total>0, so a graceful partial (18/30) isn't masked as "ok".
            status = pack.status
        else:
            status = "ok" if pack.total else pack.status
        score = f"{pack.score:.0%}" if pack.total else "-"
        p50 = "-" if pack.latency["p50"] is None else f"{pack.latency['p50']:.2f}s"
        p95 = "-" if pack.latency["p95"] is None else f"{pack.latency['p95']:.2f}s"
        if delta_pack_index is None:
            if show_variance:
                variance = pack.variance or {}
                std = "-" if variance.get("std") is None else f"{float(variance['std']):.1%}"
                cv = "-" if variance.get("cv") is None else f"{float(variance['cv']):.2f}"
                lines.append(
                    f"{pack.pack_id} (v{pack.version}) | {pack.passed} / {pack.total} | {score} | {std} | {cv} | {p50} | {p95} | {status}"
                )
            else:
                lines.append(
                    f"{pack.pack_id} (v{pack.version}) | {pack.passed} / {pack.total} | {score} | {p50} | {p95} | {status}"
                )
        else:
            d = delta_pack_index.get(pack.pack_id)
            if d:
                regr = d.get("regressions", 0)
                fixes = d.get("fixes", 0)
                if regr:
                    delta_cell = f"⚠ {regr} regr / {fixes} fix"
                elif fixes:
                    delta_cell = f"+{fixes} fix"
                elif d.get("new") or d.get("dropped"):
                    delta_cell = f"new={d.get('new', 0)}, dropped={d.get('dropped', 0)}"
                else:
                    delta_cell = "stable"
            else:
                delta_cell = "-"
            lines.append(
                f"{pack.pack_id} (v{pack.version}) | {pack.passed} / {pack.total} | {score} | {delta_cell} | {p50} | {p95} | {status}"
            )

    if delta_pack_index is None:
        lines.extend(
            [
                "",
                f"TOTAL | {result.totals['passed']} / {result.totals['total']} | {result.totals['score']:.0%} |  |  |  |  |" if show_variance else f"TOTAL | {result.totals['passed']} / {result.totals['total']} | {result.totals['score']:.0%} |  |  |",
            ]
        )
    else:
        d = result.delta or {}
        regr = d.get("total_regressions", 0)
        fixes = d.get("total_fixes", 0)
        delta_summary = f"⚠ {regr} regressions, {fixes} fixes" if regr else f"+{fixes} fixes" if fixes else "stable"
        lines.extend(
            [
                "",
                f"TOTAL | {result.totals['passed']} / {result.totals['total']} | {result.totals['score']:.0%} | {delta_summary} |  |  |",
            ]
        )
        if d.get("warnings"):
            lines.append("")
            lines.append("Delta warnings:")
            lines.extend(f"- {w}" for w in d["warnings"])
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


def _load_thinking_sampler(value: str | None) -> dict | None:
    if not value:
        return None
    data = json.loads(value)
    if not isinstance(data, dict):
        raise ValueError("--thinking-sampler must be a JSON object")
    return data


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _pack_ids_include_sandboxed(pack_ids: list[str]) -> bool:
    for pack_id in pack_ids:
        try:
            meta, _ = load_pack(pack_id)
        except Exception:
            continue
        if meta.get("supports_sandboxed_only"):
            return True
    return False


def _resolve_sandbox_log_dir(
    *,
    requested: str | None,
    save_json: str | None,
    pack_ids: list[str],
    sandboxed_enabled: bool,
) -> str | None:
    if requested is not None:
        if requested.strip().lower() == "none":
            return None
        return requested

    if not sandboxed_enabled or not _pack_ids_include_sandboxed(pack_ids):
        return None

    if save_json:
        return str(Path(save_json).parent / "sandbox-logs")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    return str(Path("benchlocal-runs") / timestamp / "sandbox-logs")


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns process exit code."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            _print_list()
            return 0

        if args.command == "inspect":
            from benchlocal_cli.inspect import inspect_result
            return inspect_result(args.path, args)

        if args.command == "history":
            from benchlocal_cli.history import history_main
            return history_main(args)

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
        # Build sampling overrides from CLI flags (#19)
        sampling_overrides: dict = {}
        if args.temperature is not None:
            sampling_overrides["temperature"] = args.temperature
        if args.top_p is not None:
            sampling_overrides["top_p"] = args.top_p
        if args.top_k is not None:
            sampling_overrides["top_k"] = args.top_k
        if args.min_p is not None:
            sampling_overrides["min_p"] = args.min_p
        if args.repeat_penalty is not None:
            sampling_overrides["repeat_penalty"] = args.repeat_penalty
        # #28: --max-tokens caps the base/no-think arm (flows through as a
        # sampling override) and, below, the thinking arm too unless
        # --thinking-max-tokens overrides it.
        if args.max_tokens is not None:
            sampling_overrides["max_tokens"] = args.max_tokens

        # --thinking-max-tokens (arm-specific) wins for the thinking arm; else
        # fall back to --max-tokens (global cap); else the historical 16384.
        effective_thinking_max = (
            args.thinking_max_tokens if args.thinking_max_tokens is not None
            else args.max_tokens if args.max_tokens is not None
            else 16384
        )

        # --sampling-from-server is mutually exclusive with explicit sampling-
        # distribution overrides (#21). --max-tokens is a length budget, not a
        # distribution param, and is preserved under --sampling-from-server, so
        # it's allowed alongside it.
        _distribution_overrides = {k: v for k, v in sampling_overrides.items() if k != "max_tokens"}
        if args.sampling_from_server and _distribution_overrides:
            print(
                "benchlocal-cli: --sampling-from-server is mutually exclusive with "
                "--temperature/--top-p/--top-k/--min-p/--repeat-penalty. "
                "Use one or the other.",
                file=sys.stderr,
            )
            return 1

        # --incremental requires --save-json (#23)
        if args.incremental and not args.save_json:
            print(
                "benchlocal-cli: --incremental requires --save-json (nothing to flush to).",
                file=sys.stderr,
            )
            return 1

        # Build progress callbacks (#23)
        on_pack_complete = None
        on_scenario_complete = None

        if args.progress:
            on_scenario_complete = _scenario_progress

        # For incremental output, we need to track state for partial JSON saves
        incremental_state = {
            "packs": [],
            "started_at": None,
            "save_path": args.save_json if args.incremental else None,
        }

        def _on_pack_complete_incremental(pack: PackResult) -> None:
            """Callback for per-pack incremental output (#23)."""
            # Print the pack line immediately
            print(_pack_line(pack), file=sys.stderr, flush=True)

            # Track for incremental JSON save
            if incremental_state["save_path"]:
                incremental_state["packs"].append(pack)
                # Build a partial RunResult and save it
                partial_result = RunResult(
                    schema_version="1",
                    runner_version=__version__,
                    endpoint=args.endpoint,
                    model=args.model,
                    mode=mode,
                    started_at=incremental_state["started_at"],
                    finished_at=_utc_now(),
                    packs=incremental_state["packs"],
                    totals=_compute_partial_totals(incremental_state["packs"]),
                    thinking_enabled=bool(args.thinking_override),
                    thinking_mode=(
                        "force-on" if args.thinking_override is True
                        else "force-off" if args.thinking_override is False
                        else "pack-defaults"
                    ),
                    warnings=[],
                    sampling_overrides=sampling_overrides or None,
                    sampling_source="server" if args.sampling_from_server else None,
                )
                try:
                    with Path(incremental_state["save_path"]).open("w", encoding="utf-8") as handle:
                        json.dump(partial_result.to_dict(), handle, indent=2, sort_keys=True)
                except Exception as exc:
                    print(f"benchlocal-cli: warning — incremental save failed: {exc}", file=sys.stderr)

        if args.progress or args.incremental:
            on_pack_complete = _on_pack_complete_incremental

        # Block --exit-on-regression when sampling is non-canonical
        if args.exit_on_regression and (sampling_overrides or args.sampling_from_server):
            print(
                "benchlocal-cli: --exit-on-regression is blocked when sampling overrides "
                "or --sampling-from-server are active (non-canonical runs shouldn't gate CI). "
                "Run without overrides for the reproducible baseline.",
                file=sys.stderr,
            )
            return 1

        # Set started_at for incremental saves before runner.run() sets its own
        if incremental_state["save_path"]:
            incremental_state["started_at"] = _utc_now()

        runner = Runner(
            endpoint=args.endpoint,
            model=args.model,
            timeout_per_case=args.timeout_per_case,
            enable_sandboxed_packs=sandboxed_enabled,
            mock_responses=_load_mock(args.mock_responses_from_json),
            thinking_enabled=args.thinking_override,
            thinking_max_tokens=effective_thinking_max,
            extra_body=_load_extra_body(args.extra_body),
            sandbox_image_tag=args.sandbox_image_tag,
            sandbox_log_dir=_resolve_sandbox_log_dir(
                requested=args.sandbox_log_dir,
                save_json=args.save_json,
                pack_ids=pack_ids,
                sandboxed_enabled=sandboxed_enabled,
            ),
            max_transient_retries=args.max_transient_retries,
            sampling_overrides=sampling_overrides or None,
            sampling_from_server=args.sampling_from_server,
            thinking_sampler=_load_thinking_sampler(args.thinking_sampler),
            on_pack_complete=on_pack_complete,
            on_scenario_complete=on_scenario_complete,
        )
        result = runner.run(pack_ids, mode=mode, repeat=max(1, args.repeat))

        # v0.8: --previous-result delta classification
        if args.previous_result:
            from benchlocal_cli import delta as delta_module
            try:
                run_delta = delta_module.classify(result.to_dict(), args.previous_result)
                result.delta = run_delta.to_dict()
            except FileNotFoundError as exc:
                # Hard fail — user gave us a path that doesn't exist
                print(f"benchlocal-cli: --previous-result error: {exc}", file=sys.stderr)
                return 1

        if args.exit_on_regression and not args.previous_result:
            print(
                "benchlocal-cli: --exit-on-regression requires --previous-result",
                file=sys.stderr,
            )
            return 1

        result_dict = result.to_dict()
        if args.save_json:
            with Path(args.save_json).open("w", encoding="utf-8") as handle:
                json.dump(result_dict, handle, indent=2, sort_keys=True)

        # v0.8 — opt-in history append (--history-file PATH or BENCHLOCAL_HISTORY_FILE env)
        history_path = args.history_file or os.environ.get("BENCHLOCAL_HISTORY_FILE")
        if history_path:
            try:
                from benchlocal_cli.history import append_run
                append_run(result_dict, history_path)
            except Exception as exc:
                print(f"benchlocal-cli: warning — history append failed: {exc}", file=sys.stderr)
        if args.output == "json":
            print(json.dumps(result_dict, indent=2, sort_keys=True))
        else:
            print(_markdown(result))

        if args.exit_on_regression and result.delta and result.delta.get("total_regressions", 0) > 0:
            return 3  # CI-friendly regression exit code
        return 0 if result.totals["total"] > 0 else 2
    except Exception as exc:
        print(f"benchlocal-cli: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
