from __future__ import annotations

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
    assert re.fullmatch(r"benchlocal-runs/\d{8}-\d{6}Z/sandbox-logs", resolved)
