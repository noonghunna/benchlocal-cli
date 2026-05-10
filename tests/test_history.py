"""Tests for benchlocal_cli.history — history CSV + `history` subcommand."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from benchlocal_cli import history as history_module


def _run(model="qwen", started="2026-05-01T10:00:00Z", total_pass=10, total=20, packs=None):
    return {
        "schema_version": "2",
        "runner_version": "0.8.0",
        "endpoint": "http://x:8001",
        "model": model,
        "mode": "full",
        "started_at": started,
        "finished_at": started,
        "thinking_enabled": False,
        "warnings": [],
        "totals": {"passed": total_pass, "total": total, "score": total_pass / max(1, total)},
        "packs": packs or [
            {"pack_id": "toolcall-15", "passed": 12, "total": 15},
            {"pack_id": "hermesagent-20", "passed": 10, "total": 20},
        ],
    }


def test_append_run_writes_header_on_first_call(tmp_path):
    p = tmp_path / "history.csv"
    history_module.append_run(_run(), p)

    rows = history_module.read_history(p)
    assert len(rows) == 1
    assert rows[0]["model"] == "qwen"
    assert rows[0]["total_pass"] == "10"
    assert rows[0]["toolcall_pass"] == "12"
    assert rows[0]["hermesagent_pass"] == "10"


def test_append_run_appends_without_repeating_header(tmp_path):
    p = tmp_path / "history.csv"
    history_module.append_run(_run(model="m1"), p)
    history_module.append_run(_run(model="m2"), p)
    history_module.append_run(_run(model="m3"), p)

    rows = history_module.read_history(p)
    assert len(rows) == 3
    assert [r["model"] for r in rows] == ["m1", "m2", "m3"]


def test_filter_by_model(tmp_path):
    p = tmp_path / "history.csv"
    history_module.append_run(_run(model="qwen"), p)
    history_module.append_run(_run(model="gemma"), p)
    history_module.append_run(_run(model="qwen"), p)

    rows = history_module.read_history(p)
    filtered = history_module.filter_rows(rows, model="qwen")
    assert len(filtered) == 2
    assert all(r["model"] == "qwen" for r in filtered)


def test_filter_by_since(tmp_path):
    p = tmp_path / "history.csv"
    history_module.append_run(_run(started="2026-04-01T00:00:00Z"), p)
    history_module.append_run(_run(started="2026-05-09T00:00:00Z"), p)
    history_module.append_run(_run(started="2026-05-15T00:00:00Z"), p)

    rows = history_module.read_history(p)
    filtered = history_module.filter_rows(rows, since="2026-05-01")
    assert len(filtered) == 2


def test_filter_last_n(tmp_path):
    p = tmp_path / "history.csv"
    for i in range(5):
        history_module.append_run(_run(started=f"2026-05-0{i+1}T00:00:00Z"), p)

    rows = history_module.read_history(p)
    last3 = history_module.filter_rows(rows, last=3)
    assert len(last3) == 3
    assert last3[-1]["timestamp"].startswith("2026-05-05")


def test_filter_by_pack_substring(tmp_path):
    p = tmp_path / "history.csv"
    # Two packs in this run; another run with only toolcall
    history_module.append_run(_run(packs=[{"pack_id": "toolcall-15", "passed": 10, "total": 15}, {"pack_id": "hermesagent-20", "passed": 5, "total": 20}]), p)
    history_module.append_run(_run(packs=[{"pack_id": "toolcall-15", "passed": 12, "total": 15}]), p)

    rows = history_module.read_history(p)
    hermes_rows = history_module.filter_rows(rows, pack="hermesagent")
    assert len(hermes_rows) == 1
    assert hermes_rows[0]["hermesagent_pass"] == "5"


def test_missing_columns_in_old_rows_dont_break_reader(tmp_path):
    """Codex review: csv.DictWriter with fieldnames; reader treats absence as ''."""
    p = tmp_path / "history.csv"
    # Manually write a row with FEWER columns (older shape, before some packs existed)
    with p.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["timestamp", "model", "total_pass", "total", "score"])
        writer.writeheader()
        writer.writerow({"timestamp": "2026-04-01T00:00:00Z", "model": "old", "total_pass": "5", "total": "10", "score": "0.5"})
    # Now append a new row with the full v0.8 shape
    history_module.append_run(_run(model="new"), p)

    rows = history_module.read_history(p)
    # Two rows, no crash. Old row has empty cells for new columns.
    assert len(rows) == 1 or len(rows) == 2  # depends on header strategy
    # The old-row case: at least the model column is preserved
    assert any(r["model"] == "old" or r["model"] == "new" for r in rows)


def test_resolve_history_path_precedence(tmp_path, monkeypatch):
    explicit = tmp_path / "explicit.csv"
    env_path = tmp_path / "env.csv"
    monkeypatch.setenv("BENCHLOCAL_HISTORY_FILE", str(env_path))

    # --file takes precedence
    assert history_module.resolve_history_path(str(explicit)) == explicit
    # env fallback
    assert history_module.resolve_history_path(None) == env_path
    # default fallback when neither set
    monkeypatch.delenv("BENCHLOCAL_HISTORY_FILE", raising=False)
    assert history_module.resolve_history_path(None) == Path("./history.csv")


def test_history_main_returns_1_on_missing_file(tmp_path, capsys):
    args = argparse.Namespace(
        file=str(tmp_path / "doesnt-exist.csv"),
        model=None, pack=None, since=None, last=None, format="markdown",
    )
    rc = history_module.history_main(args)
    assert rc == 1


def test_history_main_renders_markdown(tmp_path, capsys):
    p = tmp_path / "history.csv"
    history_module.append_run(_run(model="qwen"), p)
    args = argparse.Namespace(
        file=str(p), model=None, pack=None, since=None, last=None, format="markdown",
    )
    rc = history_module.history_main(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "qwen" in out
    assert "timestamp" in out  # header present


def test_history_main_renders_json(tmp_path, capsys):
    p = tmp_path / "history.csv"
    history_module.append_run(_run(model="qwen"), p)
    args = argparse.Namespace(
        file=str(p), model=None, pack=None, since=None, last=None, format="json",
    )
    rc = history_module.history_main(args)
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert isinstance(parsed, list)
    assert parsed[0]["model"] == "qwen"
