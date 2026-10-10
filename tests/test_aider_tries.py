"""#184 — aider-polyglot-30's retry budget (`--aider-tries`).

aider's benchmark.py defaults to `--tries 2`, which its public leaderboard runs,
and the budget is the largest measured lever on the pack (16/30 -> 20/30 from 2
to 4). So a non-default budget must reach the sandbox, scale the batch clock,
be echoed back as what ran, be recorded, and be compared. A default run must
look exactly as it did before: same argv, same request, same JSON.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import benchlocal_cli.cli as cli_module
from benchlocal_cli import delta
from benchlocal_cli.cli import _aider_tries_tag, _parse_aider_tries, main
from benchlocal_cli.persistence import _build_result, _infer_config
from benchlocal_cli.runner import Runner
from benchlocal_cli.sandbox import SandboxClient, config_for_pack, resolve_episode_cap

ROOT = Path(__file__).resolve().parents[1]


def _server():
    spec = importlib.util.spec_from_file_location("aider_tries_server", ROOT / "sandboxes/aider-polyglot/server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------ sandbox server


def test_default_budget_leaves_the_argv_as_it_was():
    server = _server()
    default = server._build_benchmark_args(run_name="r", model="openai/m")
    assert "--tries" not in default
    assert server._build_benchmark_args(run_name="r", model="openai/m", tries=2) == default


def test_another_budget_is_passed_as_tries():
    server = _server()
    argv = server._build_benchmark_args(run_name="r", model="openai/m", tries=4)
    assert argv[argv.index("--tries") + 1] == "4"


@pytest.mark.parametrize("value", [None, 0, -1, "4", 1.5, True])
def test_only_a_positive_integer_from_the_request_counts(value):
    req = {} if value is None else {"aider_tries": value}
    assert _server()._resolve_tries(req) == (2, "default")


def test_the_request_sets_the_budget():
    assert _server()._resolve_tries({"aider_tries": 4}) == (4, "request")
    assert _server()._resolve_tries({"aider_tries": 1}) == (1, "request")


def _wire_verify_start(server, tmp_path, monkeypatch, captured):
    aider_dir = tmp_path / "aider"
    aider_dir.mkdir()
    monkeypatch.setattr(server, "AIDER_DIR", aider_dir)
    monkeypatch.setattr(server, "_detect_aider_git_contract", lambda: {"ok": True, "head": "abc123"})
    monkeypatch.setattr(server, "_detect_benchmark_cli_signature", lambda: {"ok": True})
    monkeypatch.setattr(
        server, "_exercise_count_status",
        lambda: {"canonical_count": 30, "resolved_count": 30, "missing": [], "exact_match": True},
    )
    monkeypatch.setattr(server, "_stage_exercises_workspace", lambda _job_dir: None)
    monkeypatch.setattr(server, "_walk_per_exercise_results", lambda _run_dir: {"python/foo": {}})
    monkeypatch.setattr(
        server, "_grade_aider_batch_result",
        lambda _per, threshold=0.5, score_completed_only=False: {
            "passed": True, "failure_mode": "passed", "pass_rate": 1.0, "passed_count": 30,
            "total_count": 30, "found_count": 30, "missing_results": [], "extra_results": [],
            "per_exercise": {},
        },
    )

    class FakeProc:
        returncode = 0
        pid = 12345

        def communicate(self, timeout=None):
            return "stdout", "stderr"

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        return FakeProc()

    monkeypatch.setattr(server.subprocess, "Popen", fake_popen)


def _start(server, **extra):
    return server._verify_start({
        "scenario_id": "aider-polyglot-30-batch",
        "scenario": {"messages": []},
        "model_endpoint": "http://host.docker.internal:8010/v1",
        "model_name": "local-model",
        **extra,
    })


def test_verify_start_runs_and_echoes_the_requested_budget(tmp_path, monkeypatch):
    server, captured = _server(), {}
    _wire_verify_start(server, tmp_path, monkeypatch, captured)
    out = _start(server, aider_tries=4)
    assert captured["argv"][captured["argv"].index("--tries") + 1] == "4"
    assert (out["trace"]["tries_budget"], out["trace"]["tries_budget_source"]) == (4, "request")


def test_verify_start_without_a_budget_runs_and_echoes_the_default(tmp_path, monkeypatch):
    server, captured = _server(), {}
    _wire_verify_start(server, tmp_path, monkeypatch, captured)
    out = _start(server)
    assert "--tries" not in captured["argv"]
    assert (out["trace"]["tries_budget"], out["trace"]["tries_budget_source"]) == (2, "default")


def test_health_advertises_the_capability(monkeypatch):
    server = _server()
    monkeypatch.setattr(server, "_detect_benchmark_cli_signature", lambda: {"ok": True})
    monkeypatch.setattr(server, "_detect_aider_git_contract", lambda: {"ok": True})
    monkeypatch.setattr(server, "_exercise_count_status", lambda: {"exact_match": True})
    health = server._resolve_health()
    assert (health["supports_tries"], health["default_tries"]) == (True, 2)
    assert "--tries" in server.REQUIRED_BENCHMARK_FLAGS


# ------------------------------------------------------------------ batch clock


def test_the_batch_cap_scales_with_the_budget():
    base = resolve_episode_cap("aider-polyglot-30")
    assert (base.seconds, base.tries_scale) == (3600.0, 1.0)
    four = resolve_episode_cap("aider-polyglot-30", aider_tries=4)
    assert (four.seconds, four.tries_scale, four.source) == (7200.0, 2.0, "default")
    assert resolve_episode_cap("aider-polyglot-30", aider_tries=1).seconds == 3600.0  # never shrinks
    raised = resolve_episode_cap("aider-polyglot-30", batch_timeout_s=4000, aider_tries=4)
    assert (raised.seconds, raised.source) == (8000.0, "budget")


def test_the_scaled_cap_reaches_the_container_and_the_read_timeout():
    config = config_for_pack("aider-polyglot-30", aider_tries=4)
    assert dict(config.env)["AIDER_BENCHMARK_TIMEOUT_S"] == "7200"
    assert config.request_timeout_s == 7500.0
    assert dict(config_for_pack("aider-polyglot-30").env)["AIDER_BENCHMARK_TIMEOUT_S"] == "3600"


# ------------------------------------------------------------------ delivery: CLI -> runner -> client -> HTTP


_META = {
    "supports_sandboxed_only": True,
    "default_max_seconds": 60,
    "default_thinking": "off",
    "sampling_defaults": {"max_tokens": 256, "temperature": 0.0},
}
_SCENARIO = {
    "id": "aider-polyglot-30-batch",
    "pack_id": "aider-polyglot-30",
    "messages": [{"role": "user", "content": "fix the project"}],
    "raw_scenario": {"kind": "aider-polyglot-batch"},
    "verifier": {"type": "_stub", "asserts": []},
}


def _client_capturing(monkeypatch, echo: int | None) -> tuple[SandboxClient, list[tuple[str, dict]]]:
    client = SandboxClient(config_for_pack("aider-polyglot-30"))
    posts: list[tuple[str, dict]] = []

    def fake_post(path, payload, *, timeout_s=None):
        posts.append((path, payload))
        if path == "/verify-progress":
            return {"total_expected": 30, "completed_exercises": []}
        trace = {} if echo is None else {"tries_budget": echo}
        return {"action": "verify-final", "passed": True, "failure_mode": "passed",
                "detail": "30/30", "trace": trace}

    monkeypatch.setattr(client, "_post", fake_post)
    return client, posts


@pytest.mark.parametrize("tries", [4, None])
def test_the_budget_reaches_the_request_on_the_progress_path(monkeypatch, tries):
    # The CLI always passes on_progress_event, so the start runs through
    # _verify_aider_start_with_progress (a worker thread) — test that path.
    runner = Runner(endpoint="http://10.0.0.5:8001", model="m", enable_sandboxed_packs=True,
                    on_progress_event=lambda _e: None, aider_tries=tries)
    runner.aider_progress_poll_s = 0.01
    client, posts = _client_capturing(monkeypatch, echo=tries or 2)
    runner._sandbox_clients["aider-polyglot-30"] = client
    run = runner.run_scenario(_META, _SCENARIO)
    assert run.result.passed is True
    start = next(payload for path, payload in posts if path == "/verify-start")
    if tries is None:
        assert "aider_tries" not in start  # a default run's request is unchanged
    else:
        assert start["aider_tries"] == tries


class _HealthClient:
    def __init__(self, body=None, error=False):
        self._body, self._error, self.stopped = body, error, False

    def health(self):
        if self._error:
            raise RuntimeError("unreadable")
        return self._body

    def stop(self, *_a, **_k):
        self.stopped = True


@pytest.mark.parametrize("client", [_HealthClient({}), _HealthClient({"supports_tries": False}),
                                    _HealthClient(error=True)])
def test_an_image_without_the_capability_is_refused_before_the_batch(client):
    runner = Runner(endpoint="http://x", model="m", aider_tries=4)
    with pytest.raises(RuntimeError, match=r"does not support --aider-tries 4 .*tools/build-sandboxes\.sh aider-polyglot"):
        runner._require_aider_tries_support(client)
    assert client.stopped


def test_an_image_with_the_capability_is_used():
    client = _HealthClient({"supports_tries": True})
    Runner(endpoint="http://x", model="m", aider_tries=4)._require_aider_tries_support(client)
    assert not client.stopped


# ------------------------------------------------------------------ what is recorded


def _packs(echo=..., pack_id="aider-polyglot-30", skipped=False):
    trace = {} if echo is ... else {"trace": {} if echo is None else {"tries_budget": echo}}
    run = SimpleNamespace(result=SimpleNamespace(verifier_trace=trace))
    return [SimpleNamespace(pack_id=pack_id, skipped=skipped, scenarios=[run])]


def test_recorded_only_when_asked_for():
    warnings: list[str] = []
    assert Runner(endpoint="http://x", model="m")._recorded_aider_tries(_packs(2), warnings) is None


def test_recorded_when_asked_for_and_echoed():
    warnings: list[str] = []
    assert Runner(endpoint="http://x", model="m", aider_tries=4)._recorded_aider_tries(_packs(4), warnings) == 4
    assert warnings == []


@pytest.mark.parametrize("packs", [_packs(4, pack_id="structoutput-15"), _packs(4, skipped=True), []])
def test_not_recorded_when_the_aider_pack_did_not_run(packs):
    assert Runner(endpoint="http://x", model="m", aider_tries=4)._recorded_aider_tries(packs, []) is None


def test_a_different_echo_is_recorded_as_what_ran_and_warned():
    warnings: list[str] = []
    runner = Runner(endpoint="http://x", model="m", aider_tries=4)
    assert runner._recorded_aider_tries(_packs(2), warnings) == 2
    assert warnings == [
        "aider-polyglot-30: asked for --aider-tries 4 but the sandbox ran tries_budget 2; "
        "the result records what ran"
    ]


def test_a_batch_that_failed_before_starting_echoes_nothing_and_is_not_warned():
    warnings: list[str] = []
    assert Runner(endpoint="http://x", model="m", aider_tries=4)._recorded_aider_tries(_packs(None), warnings) == 4
    assert warnings == []


def test_header_tag():
    assert _aider_tries_tag(SimpleNamespace(aider_tries=4)) == " [AIDER TRIES: 4]"
    assert _aider_tries_tag(SimpleNamespace(aider_tries=None)) == ""


def _journal_config(**extra):
    return {"target_selection": ["aider-polyglot-30/aider-polyglot-30-batch"],
            "pack_ids": ["aider-polyglot-30"], "mode": "custom", "endpoint": "http://x",
            "model": "m", **extra}


_ROW = {"id": "aider-polyglot-30-batch", "passed": True, "failure_mode": "passed", "detail": "30/30"}


def test_journal_recovery_keeps_the_budget_when_aider_ran():
    result = _build_result(_journal_config(aider_tries=4), [("aider-polyglot-30", dict(_ROW))],
                           finished_at="2026-10-10T00:00:00Z")
    assert result.to_dict()["aider_tries"] == 4
    assert "aider_tries" not in _build_result(_journal_config(), [("aider-polyglot-30", dict(_ROW))],
                                              finished_at="2026-10-10T00:00:00Z").to_dict()
    assert _infer_config({"aider_tries": 4, "packs": []}, Path("r.json"))["aider_tries"] == 4


# ------------------------------------------------------------------ the flag


@pytest.mark.parametrize("raw, expected", [("4", 4), ("1", 1), (" 3 ", 3)])
def test_parse_accepts_positive_integers(raw, expected):
    assert _parse_aider_tries(raw) == expected


@pytest.mark.parametrize("raw", ["0", "-1", "1.5", "abc", "", "٣"])
def test_parse_rejects_anything_else(raw):
    with pytest.raises(argparse.ArgumentTypeError):
        _parse_aider_tries(raw)


GOOD = '{"title":"The Great Gatsby","year":1925}'


def _mock_run(tmp_path, capsys, *extra, name="run.json"):
    mock = tmp_path / "mock.json"
    mock.write_text(json.dumps({"SO-01": {"choices": [{"message": {"content": GOOD}}]}}))
    out = tmp_path / name
    rc = main(["run", "--endpoint", "http://mock", "--model", "mock", "--scenario", "structoutput-15/SO-01",
               "--measured-tps", "100", "--mock-responses-from-json", str(mock), "--save-json", str(out), *extra])
    captured = capsys.readouterr()
    return rc, captured.out, captured.err, (json.loads(out.read_text()) if out.is_file() else None)


def test_a_run_without_the_aider_pack_records_nothing(tmp_path, capsys):
    rc, out, err, result = _mock_run(tmp_path, capsys, "--aider-tries", "4")
    assert rc == 0, err
    assert "aider_tries" not in result
    assert "AIDER TRIES" not in out


# ------------------------------------------------------------------ comparisons


def _aider_result(tries=None, scenarios=True):
    out = {"packs": [{"pack_id": "aider-polyglot-30", "scenarios": [{"id": "aider-polyglot-30-batch"}] if scenarios else []}]}
    if tries is not None:
        out["aider_tries"] = tries
    return out


def test_effective_budget():
    assert delta.effective_aider_tries({"packs": [{"pack_id": "structoutput-15", "scenarios": [{}]}]}) is None
    assert delta.effective_aider_tries(_aider_result(scenarios=False)) is None
    assert delta.effective_aider_tries(_aider_result()) == 2  # every result before #184 ran 2
    assert delta.effective_aider_tries(_aider_result(4)) == 4


def test_describe_mismatch():
    assert delta.describe_aider_tries_mismatch(4, 2) == (
        "aider retry budget differs (current aider_tries=4, previous aider_tries=2)"
    )
    assert delta.describe_aider_tries_mismatch(2, 2) is None
    assert delta.describe_aider_tries_mismatch(None, 4) is None


def test_delta_warns_across_budgets(tmp_path):
    previous = tmp_path / "prev.json"
    previous.write_text(json.dumps(_aider_result()))
    warned = delta.classify(_aider_result(4), previous).warnings
    assert ("aider retry budget differs (current aider_tries=4, previous aider_tries=2); "
            "regressions and fixes may be retry-budget effects") in warned
    assert not any("retry budget" in w for w in delta.classify(_aider_result(2), previous).warnings)


class _ReachedTheRun(Exception):
    pass


class _StopRunner:
    """Stands in for Runner: records that the run was reached, then stops it
    (main() reports any exception as an error, so the flag is the signal)."""
    reached = False
    kwargs: dict | None = None

    def __init__(self, *args, **kwargs):
        _StopRunner.kwargs = kwargs

    def run(self, *args, **kwargs):
        _StopRunner.reached = True
        raise _ReachedTheRun("stub runner")


def _gated(tmp_path, capsys, monkeypatch, *extra):
    monkeypatch.setattr(cli_module, "Runner", _StopRunner)
    monkeypatch.setattr(_StopRunner, "reached", False)
    previous = tmp_path / "prev.json"
    previous.write_text(json.dumps(_aider_result()))
    argv = ["run", "--endpoint", "http://mock", "--model", "mock", "--pack", "aider-polyglot-30",
            "--previous-result", str(previous), "--exit-on-regression", *extra]
    rc = main(argv)
    return ("ran" if _StopRunner.reached else rc), capsys.readouterr().err


def test_exit_on_regression_refuses_a_different_budget(tmp_path, capsys, monkeypatch):
    rc, err = _gated(tmp_path, capsys, monkeypatch, "--aider-tries", "4")
    assert rc == 1
    assert ("--exit-on-regression is blocked: aider retry budget differs "
            "(current aider_tries=4, previous aider_tries=2)") in err


@pytest.mark.parametrize("extra", [(), ("--aider-tries", "2")])
def test_exit_on_regression_runs_at_the_same_budget(tmp_path, capsys, monkeypatch, extra):
    rc, err = _gated(tmp_path, capsys, monkeypatch, *extra)
    assert rc == "ran", err


def test_retry_failed_inherits_the_baseline_budget(tmp_path, capsys, monkeypatch):
    rc, *_ = _mock_run(tmp_path, capsys, name="baseline.json")
    assert rc == 0
    baseline = json.loads((tmp_path / "baseline.json").read_text())
    baseline["packs"][0]["scenarios"][0]["passed"] = False  # a failure to retry
    baseline["aider_tries"] = 4
    (tmp_path / "baseline.json").write_text(json.dumps(baseline))
    monkeypatch.setattr(cli_module, "Runner", _StopRunner)
    monkeypatch.setattr(_StopRunner, "reached", False)
    main(["run", "--retry-failed", "1", "--previous-result", str(tmp_path / "baseline.json"),
          "--measured-tps", "100", "--mock-responses-from-json", str(tmp_path / "mock.json"),
          "--save-json", str(tmp_path / "retry.json")])
    assert _StopRunner.reached, capsys.readouterr().err
    assert _StopRunner.kwargs["aider_tries"] == 4


# ------------------------------------------------------------------ --resume


def _interrupted_journal(tmp_path, monkeypatch, *extra) -> tuple[Path, Path]:
    mock = tmp_path / "mock.json"
    mock.write_text(json.dumps({"SO-01": {"choices": [{"message": {"content": GOOD}}]}}))
    out = tmp_path / "r.json"
    calls = 0
    original = Runner.run_scenario

    def interrupt_on_second(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Runner, "run_scenario", interrupt_on_second)
    with pytest.raises(KeyboardInterrupt):
        main(["run", "--endpoint", "http://mock", "--model", "mock", "--scenario", "structoutput-15/SO-01",
              "--measured-tps", "100", "--mock-responses-from-json", str(mock), "--save-json", str(out),
              "--repeat", "2", "--incremental", *extra])
    monkeypatch.setattr(Runner, "run_scenario", original)
    return Path(f"{out}.partial.jsonl"), mock


def test_resume_refuses_a_different_budget(tmp_path, monkeypatch, capsys):
    sidecar, mock = _interrupted_journal(tmp_path, monkeypatch, "--aider-tries", "4")
    capsys.readouterr()
    assert main(["run", "--resume", str(sidecar), "--mock-responses-from-json", str(mock),
                 "--aider-tries", "3"]) == 1
    assert "--resume: --aider-tries 3 differs from the original run's (4)" in capsys.readouterr().err


@pytest.mark.parametrize("journal, again", [(("--aider-tries", "4"), ("--aider-tries", "4")),
                                            (("--aider-tries", "4"), ()),
                                            ((), ("--aider-tries", "2"))])
def test_resume_accepts_the_same_budget(tmp_path, monkeypatch, capsys, journal, again):
    sidecar, mock = _interrupted_journal(tmp_path, monkeypatch, *journal)
    assert main(["run", "--resume", str(sidecar), "--mock-responses-from-json", str(mock), *again]) == 0


def test_resume_refuses_a_budget_the_original_run_did_not_ask_for(tmp_path, monkeypatch, capsys):
    sidecar, mock = _interrupted_journal(tmp_path, monkeypatch)
    capsys.readouterr()
    assert main(["run", "--resume", str(sidecar), "--mock-responses-from-json", str(mock),
                 "--aider-tries", "4"]) == 1
    assert "(none recorded, i.e. 2)" in capsys.readouterr().err
