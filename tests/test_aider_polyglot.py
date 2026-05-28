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
# Aider checkout contract — benchmark.py must see the real Aider git repo
# ============================================================================


def test_detect_aider_git_contract_ok(tmp_path, monkeypatch):
    server = _server()
    aider_dir = tmp_path / "aider"
    aider_dir.mkdir()
    monkeypatch.setattr(server, "AIDER_DIR", aider_dir)
    monkeypatch.setattr(server, "_AIDER_GIT_CONTRACT_CACHE", None)

    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)

        class Result:
            returncode = 0
            stdout = "abc123\n" if "rev-parse" in argv else ""
            stderr = ""

        return Result()

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    out = server._detect_aider_git_contract()

    assert out == {"ok": True, "head": "abc123"}
    assert calls[0][-3:] == ["rev-parse", "--verify", "HEAD"]
    assert calls[1][-3:] == ["cat-file", "-e", "HEAD:aider/__init__.py"]


def test_detect_aider_git_contract_fails_when_head_lacks_aider_package(tmp_path, monkeypatch):
    server = _server()
    aider_dir = tmp_path / "aider"
    aider_dir.mkdir()
    monkeypatch.setattr(server, "AIDER_DIR", aider_dir)
    monkeypatch.setattr(server, "_AIDER_GIT_CONTRACT_CACHE", None)

    def fake_run(argv, **_kwargs):
        class Result:
            stdout = ""
            stderr = ""

        result = Result()
        if "rev-parse" in argv:
            result.returncode = 0
            result.stdout = "f46766c\n"
        else:
            result.returncode = 1
            result.stderr = "fatal: path 'aider/__init__.py' does not exist"
        return result

    monkeypatch.setattr(server.subprocess, "run", fake_run)

    out = server._detect_aider_git_contract()

    assert out["ok"] is False
    assert out["head"] == "f46766c"
    assert out["reason"] == "aider git HEAD lacks aider/__init__.py"


def test_resolve_health_surfaces_broken_aider_git_contract(monkeypatch):
    server = _server()
    monkeypatch.setattr(server, "_detect_benchmark_cli_signature", lambda: {"ok": True})
    monkeypatch.setattr(
        server,
        "_detect_aider_git_contract",
        lambda: {"ok": False, "reason": "aider git HEAD lacks aider/__init__.py"},
    )
    monkeypatch.setattr(
        server,
        "_exercise_count_status",
        lambda: {"canonical_count": 30, "resolved_count": 30, "missing": [], "exact_match": True},
    )

    out = server._resolve_health()

    assert out["status"] == "setup-error"
    assert out["aider_git_contract"]["ok"] is False
    assert "aider/__init__.py" in out["aider_git_contract"]["reason"]


def test_verify_start_fails_fast_on_broken_aider_git_contract(monkeypatch):
    server = _server()
    monkeypatch.setattr(
        server,
        "_detect_aider_git_contract",
        lambda: {"ok": False, "reason": "aider git HEAD lacks aider/__init__.py"},
    )

    out = server._verify_start(
        {
            "scenario_id": "aider-polyglot-30-batch",
            "scenario": {"messages": []},
            "model_endpoint": "http://host.docker.internal:8010/v1",
            "model_name": "qwen",
        }
    )

    assert out["passed"] is False
    assert out["failure_mode"] == "server_error"
    assert "aider git checkout contract broken" in out["detail"]
    assert out["trace"]["aider_git_contract"]["ok"] is False


def test_verify_start_runs_benchmark_from_aider_checkout(tmp_path, monkeypatch):
    server = _server()
    aider_dir = tmp_path / "aider"
    aider_dir.mkdir()
    monkeypatch.setattr(server, "AIDER_DIR", aider_dir)
    monkeypatch.setattr(server, "_detect_aider_git_contract", lambda: {"ok": True, "head": "abc123"})
    monkeypatch.setattr(server, "_detect_benchmark_cli_signature", lambda: {"ok": True})
    monkeypatch.setattr(
        server,
        "_exercise_count_status",
        lambda: {"canonical_count": 30, "resolved_count": 30, "missing": [], "exact_match": True},
    )
    monkeypatch.setattr(server, "_stage_exercises_workspace", lambda _job_dir: None)
    monkeypatch.setattr(server, "_build_benchmark_args", lambda **_kwargs: ["python3", "benchmark.py", "run"])
    monkeypatch.setattr(server, "_walk_per_exercise_results", lambda _run_dir: {"python/foo": {}})
    monkeypatch.setattr(
        server,
        "_grade_aider_batch_result",
        lambda _per, threshold=0.5, score_completed_only=False: {
            "passed": True,
            "failure_mode": "passed",
            "pass_rate": 1.0,
            "passed_count": 30,
            "total_count": 30,
            "found_count": 30,
            "missing_results": [],
            "extra_results": [],
            "per_exercise": {},
        },
    )

    captured = {}

    class FakeProc:
        returncode = 0
        pid = 12345

        def communicate(self, timeout=None):
            captured["timeout"] = timeout
            return "stdout", "stderr"

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return FakeProc()

    monkeypatch.setattr(server.subprocess, "Popen", fake_popen)

    out = server._verify_start(
        {
            "scenario_id": "aider-polyglot-30-batch",
            "scenario": {"messages": []},
            "model_endpoint": "http://host.docker.internal:8010/v1/chat/completions",
            "model_name": "local-model",
        }
    )

    assert out["passed"] is True
    assert captured["cwd"] == str(aider_dir)
    assert captured["env"]["AIDER_BENCHMARK_DIR"].startswith("/tmp/aider-polyglot-runs/")
    assert captured["env"]["AIDER_BENCHMARK_DIR"].endswith("/tmp.benchmarks")
    assert captured["env"]["OPENAI_BASE_URL"] == "http://host.docker.internal:8010/v1"
    assert captured["env"]["OPENAI_API_BASE"] == "http://host.docker.internal:8010/v1"


# ============================================================================
# _qualify_aider_model — litellm provider routing
# ============================================================================


def test_qualify_aider_model_prefixes_slash_free_alias():
    server = _server()
    assert server._qualify_aider_model("my-model") == "openai/my-model"


def test_qualify_aider_model_prefixes_gguf_path():
    server = _server()
    assert server._qualify_aider_model("/models/repo/model.gguf") == "openai//models/repo/model.gguf"


def test_qualify_aider_model_prefixes_bare_hf_repo_id():
    server = _server()
    assert server._qualify_aider_model("org/model") == "openai/org/model"


def test_qualify_aider_model_preserves_known_provider_prefix():
    server = _server()
    assert server._qualify_aider_model("openai/qwen") == "openai/qwen"
    assert server._qualify_aider_model("anthropic/claude-sonnet-4") == "anthropic/claude-sonnet-4"


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


def test_grade_timeout_partial_scores_completed_only():
    server = _server()
    canonical_keys = [f"{e['language']}/{e['name']}" for e in server.CANONICAL_EXERCISES]
    per = {}
    for i, key in enumerate(canonical_keys[:26]):
        per[key] = _fixture_result(passed=(i < 17))

    out = server._grade_aider_batch_result(per, threshold=0.5, score_completed_only=True)

    assert out["passed_count"] == 17
    assert out["total_count"] == 26
    assert out["canonical_total_count"] == 30
    assert out["found_count"] == 26
    assert abs(out["pass_rate"] - (17 / 26)) < 1e-12
    assert out["score_completed_only"] is True
    assert len(out["missing_results"]) == 4


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
    assert cfg.request_timeout_s == 3900.0


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
