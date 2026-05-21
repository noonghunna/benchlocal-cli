"""Tests for v0.9.0 aider-polyglot-30 pack: endpoint resolver, args
builder, grading. Server-side bits are covered via fixtures rather than
booting Docker (separate `pytest -m docker` contract test in CI)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ============================================================================
# resolve_endpoint_for_container — Codex 2nd-pass #2
# ============================================================================


def test_resolve_endpoint_localhost_rewrites():
    from benchlocal_cli.sandbox import resolve_endpoint_for_container
    assert resolve_endpoint_for_container("http://localhost:8010/v1") == \
        "http://host.docker.internal:8010/v1"


def test_resolve_endpoint_127_loopback_rewrites():
    from benchlocal_cli.sandbox import resolve_endpoint_for_container
    assert resolve_endpoint_for_container("http://127.0.0.1:8030") == \
        "http://host.docker.internal:8030"
    assert resolve_endpoint_for_container("http://127.5.0.1:8030") == \
        "http://host.docker.internal:8030"


def test_resolve_endpoint_ipv6_loopback_rewrites():
    from benchlocal_cli.sandbox import resolve_endpoint_for_container
    assert resolve_endpoint_for_container("http://[::1]:8010/v1/chat/completions") == \
        "http://host.docker.internal:8010/v1/chat/completions"


def test_resolve_endpoint_zerozero_raises():
    from benchlocal_cli.sandbox import resolve_endpoint_for_container
    with pytest.raises(ValueError, match="0.0.0.0"):
        resolve_endpoint_for_container("http://0.0.0.0:8010/v1")


def test_resolve_endpoint_real_host_unchanged():
    from benchlocal_cli.sandbox import resolve_endpoint_for_container
    for endpoint in (
        "http://172.17.0.1:8010/v1",
        "http://api.example.com:443/v1",
        "https://my-vllm.internal.lan/v1",
        "http://host.docker.internal:8010/v1",  # already container-reachable
    ):
        assert resolve_endpoint_for_container(endpoint) == endpoint, f"unexpected change for {endpoint}"


def test_resolve_endpoint_preserves_path_query_userinfo():
    from benchlocal_cli.sandbox import resolve_endpoint_for_container
    out = resolve_endpoint_for_container("http://user:pass@localhost:8010/v1/chat/completions?stream=true")
    assert "host.docker.internal" in out
    assert "/v1/chat/completions" in out
    assert "user:pass" in out
    assert "stream=true" in out


def test_resolve_endpoint_empty_passthrough():
    from benchlocal_cli.sandbox import resolve_endpoint_for_container
    assert resolve_endpoint_for_container("") == ""


# ============================================================================
# _build_benchmark_args — Codex 2nd-pass #4 (centralized + testable)
# ============================================================================


def _server():
    return _load("aider_polyglot_server", "sandboxes/aider-polyglot/server.py")


def test_build_benchmark_args_basic():
    server = _server()
    args = server._build_benchmark_args(
        run_name="test-run",
        model="qwen-test",
    )
    assert args[0] == "python3"
    assert args[2] == "test-run"
    assert "--model" in args
    assert args[args.index("--model") + 1] == "qwen-test"
    assert "--edit-format" in args
    assert args[args.index("--edit-format") + 1] == "whole"
    assert "--threads" in args
    assert args[args.index("--threads") + 1] == "1"
    assert "--exercises-dir" in args
    assert args[args.index("--exercises-dir") + 1] == "polyglot-benchmark"
    assert "--new" in args


def test_build_benchmark_args_custom_edit_format():
    server = _server()
    args = server._build_benchmark_args(
        run_name="r", model="m", edit_format="diff", threads=4,
    )
    assert args[args.index("--edit-format") + 1] == "diff"
    assert args[args.index("--threads") + 1] == "4"


def test_build_benchmark_args_num_tests_optional():
    server = _server()
    args1 = server._build_benchmark_args(run_name="r", model="m")
    assert "--num-tests" not in args1
    args2 = server._build_benchmark_args(run_name="r", model="m", num_tests=2)
    assert "--num-tests" in args2
    assert args2[args2.index("--num-tests") + 1] == "2"


# ============================================================================
# _grade_aider_batch_result — single-scoreboard aggregation
# ============================================================================


def _fixture_result(passed: bool, tries: int = 1, **kw) -> dict:
    """Build a synthetic .aider.results.json shape."""
    return {
        "tests_outcomes": [True] * (tries - 1) + [passed] if tries > 0 else [],
        "duration": kw.get("duration", 5.5),
        "cost": kw.get("cost", 0.001),
        "model_errors": kw.get("model_errors", 0),
        "edit_format": kw.get("edit_format", "whole"),
        "commands": kw.get("commands", 1),
    }


def test_grade_zero_pass_rate_fails_below_threshold():
    server = _server()
    # Build per_exercise dict with all 30 canonical names → all failed
    per = {f"{e['language']}/{e['name']}": _fixture_result(False) for e in server.CANONICAL_EXERCISES}
    out = server._grade_aider_batch_result(per, threshold=0.5)
    assert out["passed"] is False
    assert out["pass_rate"] == 0.0
    assert out["passed_count"] == 0
    assert out["total_count"] == 30
    assert out["failure_mode"] == "verifier_fail"


def test_grade_below_threshold_fails():
    server = _server()
    per = {}
    keys = [f"{e['language']}/{e['name']}" for e in server.CANONICAL_EXERCISES]
    for i, k in enumerate(keys):
        per[k] = _fixture_result(passed=(i < 14))  # 14 passed (under 15 threshold for 0.5)
    out = server._grade_aider_batch_result(per, threshold=0.5)
    assert out["passed_count"] == 14
    assert out["pass_rate"] < 0.5
    assert out["passed"] is False


def test_grade_at_threshold_passes():
    server = _server()
    per = {}
    keys = [f"{e['language']}/{e['name']}" for e in server.CANONICAL_EXERCISES]
    for i, k in enumerate(keys):
        per[k] = _fixture_result(passed=(i < 15))  # exactly 15/30 = threshold
    out = server._grade_aider_batch_result(per, threshold=0.5)
    assert out["passed_count"] == 15
    assert out["pass_rate"] == 0.5
    assert out["passed"] is True
    assert out["failure_mode"] == "passed"


def test_grade_full_pass():
    server = _server()
    per = {f"{e['language']}/{e['name']}": _fixture_result(True) for e in server.CANONICAL_EXERCISES}
    out = server._grade_aider_batch_result(per, threshold=0.5)
    assert out["passed_count"] == 30
    assert out["pass_rate"] == 1.0
    assert out["passed"] is True
    assert out["missing_results"] == []
    assert out["extra_results"] == []


def test_grade_surfaces_missing_results():
    """If upstream produced fewer than 30 result files (subprocess crashed
    mid-run), surface the missing keys so inspect can see them."""
    server = _server()
    canonical_keys = [f"{e['language']}/{e['name']}" for e in server.CANONICAL_EXERCISES]
    per = {k: _fixture_result(True) for k in canonical_keys[:15]}  # only 15 ran
    out = server._grade_aider_batch_result(per, threshold=0.5)
    assert out["found_count"] == 15
    assert out["total_count"] == 30
    assert out["passed_count"] == 15
    assert len(out["missing_results"]) == 15
    # 15/30 = 0.5 → at threshold (passes), but found_count vs total_count
    # tells the truth that we only saw half
    assert out["passed"] is True


def test_grade_surfaces_extra_results():
    """If upstream ran exercises NOT in our canonical 30 (substring keyword
    collision), the canonical_keys denominator catches it."""
    server = _server()
    canonical_keys = [f"{e['language']}/{e['name']}" for e in server.CANONICAL_EXERCISES]
    per = {k: _fixture_result(True) for k in canonical_keys}
    per["python/EXTRA_EXERCISE_NOT_CANONICAL"] = _fixture_result(True)
    out = server._grade_aider_batch_result(per, threshold=0.5)
    assert "python/EXTRA_EXERCISE_NOT_CANONICAL" in out["extra_results"]
    # The total_count is still 30 (canonical), not 31. Pass_rate based on canonical.
    assert out["total_count"] == 30


# ============================================================================
# CANONICAL_EXERCISES integrity
# ============================================================================


def test_canonical_exercises_has_30_across_6_languages():
    server = _server()
    assert len(server.CANONICAL_EXERCISES) == 30
    languages = {e["language"] for e in server.CANONICAL_EXERCISES}
    assert languages == {"cpp", "go", "java", "javascript", "python", "rust"}


def test_canonical_exercises_unique_keys():
    """No duplicate (language, name) tuples in the curated list."""
    server = _server()
    keys = [(e["language"], e["name"]) for e in server.CANONICAL_EXERCISES]
    assert len(keys) == len(set(keys)), "duplicate (language, name) in exercises.json"


def test_canonical_exercises_has_difficulty_diversity():
    """Each language should have at least one non-medium exercise."""
    server = _server()
    by_lang: dict[str, set[str]] = {}
    for e in server.CANONICAL_EXERCISES:
        by_lang.setdefault(e["language"], set()).add(e["difficulty"])
    # Curation goal: at least 2 difficulty levels per language overall
    overall = set()
    for diffs in by_lang.values():
        overall.update(diffs)
    assert overall >= {"easy", "medium", "hard"}, (
        f"selection lacks difficulty diversity: {overall}"
    )


# ============================================================================
# v0.9.0 ScenarioResult new fields
# ============================================================================


def test_scenario_result_pass_rate_fields_optional():
    """v0.9.0: pass_rate/passed_count/total_count are optional. Existing
    v0.8.x code that doesn't set them must still construct ScenarioResult
    without error."""
    from benchlocal_cli.types import ScenarioResult
    r = ScenarioResult(scenario_id="X", passed=True, failure_mode="passed", detail="ok")
    assert r.pass_rate is None
    assert r.passed_count is None
    assert r.total_count is None


def test_scenario_result_pass_rate_serializes():
    """v0.9.0: pass_rate fields serialize through to_dict()."""
    from benchlocal_cli.types import ScenarioResult
    r = ScenarioResult(
        scenario_id="aider-polyglot-30-batch",
        passed=True, failure_mode="passed", detail="23/30",
        pass_rate=0.7666, passed_count=23, total_count=30,
    )
    d = r.to_dict()
    assert d["pass_rate"] == 0.7666
    assert d["passed_count"] == 23
    assert d["total_count"] == 30


# ============================================================================
# Pack registration in SANDBOX_REGISTRY
# ============================================================================


def test_pack_registered_in_sandbox_registry():
    from benchlocal_cli.sandbox import SANDBOX_REGISTRY
    assert "aider-polyglot-30" in SANDBOX_REGISTRY
    cfg = SANDBOX_REGISTRY["aider-polyglot-30"]
    assert cfg.host_port == 9004
    assert cfg.multi_turn is True
    assert cfg.request_timeout_s == 3000.0


def test_pack_loads_via_runner():
    from benchlocal_cli.runner import load_pack
    meta, scenarios = load_pack("aider-polyglot-30")
    assert meta["pack_id"] == "aider-polyglot-30"
    assert meta["scenario_count"] == 1
    assert meta.get("supports_sandboxed_only") is True
    assert len(scenarios) == 1
    assert scenarios[0]["id"] == "aider-polyglot-30-batch"


# ============================================================================
# _link_or_copy — #6 EXDEV resilience (job dir on a host bind-mount)
# ============================================================================

def test_link_or_copy_hardlinks_when_possible(tmp_path):
    server = _server()
    src = tmp_path / "a.txt"
    src.write_text("exercise content")
    dst = tmp_path / "b.txt"
    server._link_or_copy(str(src), str(dst))
    assert dst.read_text() == "exercise content"
    assert src.stat().st_ino == dst.stat().st_ino  # same inode → hard link


def test_link_or_copy_falls_back_on_cross_device(tmp_path, monkeypatch):
    """When os.link raises EXDEV (job dir is a host bind-mount, #6), staging
    must copy the file rather than fail the whole batch."""
    server = _server()
    src = tmp_path / "src.txt"
    src.write_text("exercise content")
    dst = tmp_path / "dst.txt"

    def _raise_exdev(_s, _d):
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(server.os, "link", _raise_exdev)
    server._link_or_copy(str(src), str(dst))
    assert dst.read_text() == "exercise content"
    assert src.stat().st_ino != dst.stat().st_ino  # copied, not linked
