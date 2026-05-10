"""History CSV writer + `benchlocal-cli history` query subcommand.

Per Codex review of the v0.8 brief:
- Writer is opt-in (--history-file PATH or BENCHLOCAL_HISTORY_FILE env);
  default behavior unchanged so v0.7.x users see no surprise files (#4)
- File locking via fcntl.flock around the append (#5) — POSIX only;
  Windows skipped with a one-line warning, documented as non-concurrent
- csv.DictWriter with stable+expanding fieldnames so older readers
  with fewer columns still parse new rows; reader treats missing as ""
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import os
import sys
from pathlib import Path
from typing import Iterable


# Stable column order. New columns get APPENDED, never reordered.
HISTORY_FIELDNAMES: list[str] = [
    "timestamp",
    "run_id",
    "mode",
    "endpoint",
    "model",
    "thinking",
    "total_pass",
    "total",
    "score",
    # Per-pack columns get appended dynamically below per-row by the writer.
    # All historical packs we've shipped are pre-listed for column stability.
    "toolcall_pass", "toolcall_total",
    "instructfollow_pass", "instructfollow_total",
    "structoutput_pass", "structoutput_total",
    "dataextract_pass", "dataextract_total",
    "reasonmath_pass", "reasonmath_total",
    "bugfind_pass", "bugfind_total",
    "hermesagent_pass", "hermesagent_total",
    "cli_pass", "cli_total",
    "runner_version",
    "git_commit",
]


def _flock_acquire(fh) -> None:
    """POSIX-only file lock. No-op on Windows (documented non-concurrent)."""
    if os.name == "posix":
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)


def _flock_release(fh) -> None:
    if os.name == "posix":
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _row_from_run(run_dict: dict) -> dict[str, str]:
    """Project a RunResult.to_dict() down to a CSV-flat row."""
    totals = run_dict.get("totals") or {}
    row: dict[str, str] = {
        "timestamp": run_dict.get("started_at", ""),
        "run_id": run_dict.get("started_at", "").replace(":", "").replace("-", "")[:15] or "",
        "mode": run_dict.get("mode", ""),
        "endpoint": run_dict.get("endpoint", ""),
        "model": run_dict.get("model", ""),
        "thinking": "1" if run_dict.get("thinking_enabled") else "0",
        "total_pass": str(int(totals.get("passed", 0) or 0)),
        "total": str(int(totals.get("total", 0) or 0)),
        "score": f"{float(totals.get('score', 0.0) or 0.0):.4f}",
        "runner_version": run_dict.get("runner_version", ""),
        "git_commit": os.environ.get("BENCHLOCAL_GIT_COMMIT", ""),
    }
    for pack in run_dict.get("packs") or []:
        pid = (pack.get("pack_id") or "").replace("-", "_")
        # Drop any trailing -<size> suffix (e.g. "toolcall_15" → "toolcall")
        pid_short = pid.rsplit("_", 1)[0] if pid and pid.rsplit("_", 1)[-1].isdigit() else pid
        row[f"{pid_short}_pass"] = str(int(pack.get("passed", 0) or 0))
        row[f"{pid_short}_total"] = str(int(pack.get("total", 0) or 0))
    return row


def append_run(run_dict: dict, history_path: str | Path) -> None:
    """Append a row to the history CSV. Creates the file with a header on
    first write. Acquires a POSIX flock to prevent concurrent-append corruption
    (Codex review #5)."""
    history_path = Path(history_path)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    row = _row_from_run(run_dict)

    new_columns = [k for k in row if k not in HISTORY_FIELDNAMES]
    fieldnames = list(HISTORY_FIELDNAMES) + new_columns

    needs_header = not history_path.exists() or history_path.stat().st_size == 0
    with history_path.open("a", encoding="utf-8", newline="") as fh:
        _flock_acquire(fh)
        try:
            writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
            if needs_header:
                writer.writeheader()
            writer.writerow(row)
        finally:
            _flock_release(fh)


def read_history(history_path: str | Path) -> list[dict[str, str]]:
    """Read all rows. Missing columns surface as empty strings."""
    history_path = Path(history_path)
    if not history_path.is_file():
        raise FileNotFoundError(f"history file not found: {history_path}")
    with history_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader)


def filter_rows(
    rows: Iterable[dict[str, str]],
    *,
    model: str | None = None,
    pack: str | None = None,
    since: str | None = None,
    last: int | None = None,
) -> list[dict[str, str]]:
    """Apply filters in argparse order. `pack` is loose-matched (substring)
    so users can pass either `hermesagent` or `hermesagent-20`."""
    out = list(rows)
    if model:
        out = [r for r in out if r.get("model") == model]
    if pack:
        col_pass = f"{pack.replace('-', '_').rsplit('_', 1)[0]}_pass"
        out = [r for r in out if (r.get(col_pass) or "").strip()]
    if since:
        # Treat `since` as a YYYY-MM-DD prefix match on timestamp (forgiving)
        try:
            cutoff = _dt.date.fromisoformat(since)
        except ValueError:
            cutoff = None
        if cutoff:
            def _row_date(r: dict) -> _dt.date | None:
                ts = (r.get("timestamp") or "")[:10]
                try:
                    return _dt.date.fromisoformat(ts)
                except ValueError:
                    return None
            out = [r for r in out if (rd := _row_date(r)) is not None and rd >= cutoff]
    if last and last > 0:
        out = out[-last:]
    return out


def render_markdown(rows: list[dict[str, str]], pack_filter: str | None = None) -> str:
    """Render filtered history rows as a markdown table."""
    if not rows:
        return "_no rows matched_"
    if pack_filter:
        col_pass = f"{pack_filter.replace('-', '_').rsplit('_', 1)[0]}_pass"
        col_total = f"{pack_filter.replace('-', '_').rsplit('_', 1)[0]}_total"
        lines = [
            f"timestamp | model | mode | {pack_filter} pass / total",
            "---|---|---|---:",
        ]
        for r in rows:
            p = r.get(col_pass) or "0"
            t = r.get(col_total) or "0"
            lines.append(f"{r.get('timestamp', '')[:19]} | {r.get('model', '')} | {r.get('mode', '')} | {p} / {t}")
        return "\n".join(lines)
    # Default: total summary
    lines = [
        "timestamp | model | mode | total | score",
        "---|---|---|---:|---:",
    ]
    for r in rows:
        score = r.get("score", "")
        try:
            score_pct = f"{float(score):.0%}" if score else ""
        except ValueError:
            score_pct = score
        total_pass = r.get("total_pass", "")
        total = r.get("total", "")
        lines.append(
            f"{r.get('timestamp', '')[:19]} | {r.get('model', '')} | {r.get('mode', '')} | "
            f"{total_pass} / {total} | {score_pct}"
        )
    return "\n".join(lines)


def add_history_subparser(subparsers) -> None:
    """Wire the `history` subcommand into the main parser."""
    parser = subparsers.add_parser(
        "history",
        help="query the run-history CSV (writes happen via `run --history-file`)",
        description=(
            "Query a benchlocal-cli history CSV (the file `--history-file` "
            "writes to). One row per run; columns include per-pack pass/total. "
            "New columns are appended over time so old rows have empty cells."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  benchlocal-cli history --file results/quality/history.csv --last 10\n"
            "  benchlocal-cli history --model qwen3.6-27b-autoround --since 2026-05-01\n"
            "  benchlocal-cli history --pack hermesagent-20 --last 20\n"
        ),
    )
    parser.add_argument(
        "--file",
        help="path to history.csv. Falls back to BENCHLOCAL_HISTORY_FILE env, "
             "then to ./history.csv if neither is set.",
    )
    parser.add_argument("--model", help="filter to one model id")
    parser.add_argument("--pack", help="filter to runs that include a row for this pack (substring match)")
    parser.add_argument("--since", help="filter to rows with timestamp >= YYYY-MM-DD")
    parser.add_argument("--last", type=int, help="show only the last N rows after other filters")
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="output format (default: markdown)",
    )


def resolve_history_path(args_path: str | None) -> Path:
    """Resolve --file → BENCHLOCAL_HISTORY_FILE → ./history.csv in that order."""
    if args_path:
        return Path(args_path)
    env_path = os.environ.get("BENCHLOCAL_HISTORY_FILE")
    if env_path:
        return Path(env_path)
    return Path("./history.csv")


def history_main(args: argparse.Namespace) -> int:
    path = resolve_history_path(args.file)
    try:
        rows = read_history(path)
    except FileNotFoundError as exc:
        print(f"benchlocal-cli history: {exc}", file=sys.stderr)
        return 1

    filtered = filter_rows(rows, model=args.model, pack=args.pack, since=args.since, last=args.last)
    if args.format == "json":
        print(json.dumps(filtered, indent=2))
        return 0
    print(render_markdown(filtered, pack_filter=args.pack))
    return 0
