from __future__ import annotations

import os
import re

from benchlocal_cli.cli import _resolve_sandbox_log_dir


def test_default_sandbox_log_dir_uses_save_json_sibling(tmp_path):
    save_json = tmp_path / "runs" / "qwen.json"

    resolved = _resolve_sandbox_log_dir(
        requested=None,
        save_json=str(save_json),
        pack_ids=["bugfind-15"],
        sandboxed_enabled=True,
    )

    assert resolved == str(tmp_path / "runs" / "sandbox-logs")


def test_default_sandbox_log_dir_disabled_for_deterministic_only(tmp_path):
    resolved = _resolve_sandbox_log_dir(
        requested=None,
        save_json=str(tmp_path / "medium.json"),
        pack_ids=["toolcall-15"],
        sandboxed_enabled=True,
    )

    assert resolved is None


def test_default_sandbox_log_dir_disabled_when_sandboxing_disabled(tmp_path):
    resolved = _resolve_sandbox_log_dir(
        requested=None,
        save_json=str(tmp_path / "full.json"),
        pack_ids=["bugfind-15"],
        sandboxed_enabled=False,
    )

    assert resolved is None


def test_sandbox_log_dir_none_is_explicit_opt_out(tmp_path):
    resolved = _resolve_sandbox_log_dir(
        requested="none",
        save_json=str(tmp_path / "full.json"),
        pack_ids=["bugfind-15"],
        sandboxed_enabled=True,
    )

    assert resolved is None


def test_explicit_sandbox_log_dir_is_preserved(tmp_path):
    explicit = tmp_path / "custom-logs"

    resolved = _resolve_sandbox_log_dir(
        requested=str(explicit),
        save_json=str(tmp_path / "full.json"),
        pack_ids=["bugfind-15"],
        sandboxed_enabled=True,
    )

    assert resolved == str(explicit)


def test_default_sandbox_log_dir_without_save_json_uses_run_directory():
    resolved = _resolve_sandbox_log_dir(
        requested=None,
        save_json=None,
        pack_ids=["bugfind-15"],
        sandboxed_enabled=True,
    )

    assert resolved is not None
    assert os.path.isabs(resolved)
    assert re.search(r"/benchlocal-runs/\d{8}-\d{6}Z/sandbox-logs$", resolved)


def test_relative_save_json_yields_absolute_log_dir(tmp_path, monkeypatch):
    """#168: the log dir reaches `docker run -v`, which refuses a relative source."""
    monkeypatch.chdir(tmp_path)

    resolved = _resolve_sandbox_log_dir(
        requested=None,
        save_json="results/aider-polyglot.json",
        pack_ids=["aider-polyglot-30"],
        sandboxed_enabled=True,
    )

    assert resolved == os.path.join(os.getcwd(), "results", "sandbox-logs")


def test_relative_explicit_log_dir_is_made_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    resolved = _resolve_sandbox_log_dir(
        requested="logs/sandbox",
        save_json=None,
        pack_ids=["bugfind-15"],
        sandboxed_enabled=True,
    )

    assert resolved == os.path.join(os.getcwd(), "logs", "sandbox")
