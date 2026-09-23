"""Tests for #160 -- stop a pack when the endpoint is gone."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from benchlocal_cli.cli import _pack_line, main
from benchlocal_cli.history import append_run
from benchlocal_cli.persistence import load_resume, merge_resume
from benchlocal_cli.runner import (
    ENDPOINT_DOWN_STATUS,
    ENDPOINT_DOWN_WARNING_PREFIX,
    Runner,
)
from benchlocal_cli.types import RunResult, ScenarioResult, ScenarioRun

PASS = (True, "passed")
WRONG = (False, "wrong_answer")
INFRA = (False, "http_error")
DEAD = {"ok": False, "detail": "ConnectError: refused", "elapsed_s": 0.0, "at": "t"}
ALIVE = {"ok": True, "detail": "HTTP 200", "elapsed_s": 0.0, "at": "t"}


def _meta(pack_id: str = "test-pack") -> dict:
    return {
        "pack_id": pack_id,
        "version": "1.0.0",
        "upstream_commit": "abc123",
        "sampling_defaults": {"max_tokens": 32},
    }


def _scenarios(count: int, pack_id: str = "test-pack") -> list[dict]:
    return [
        {"id": f"T-{i:02d}", "pack_id": pack_id, "messages": [{"role": "user", "content": "x"}]}
        for i in range(1, count + 1)
    ]


class _Harness:
    """A Runner whose scenarios and probes are scripted.

    `outcomes` maps a scenario id to the result EVERY attempt returns (an infra
    failure is retried inline, so it is returned for each attempt); `probes` is
    consumed in order, one per liveness probe.
    """

    def __init__(self, monkeypatch, packs: dict[str, int], outcomes: dict[str, tuple[bool, str]],
                 probes: list[dict], **runner_kwargs):
        self.pack_scenarios = {pack_id: _scenarios(count, pack_id) for pack_id, count in packs.items()}
        monkeypatch.setattr(
            "benchlocal_cli.runner.load_pack",
            lambda pack_id: (_meta(pack_id), self.pack_scenarios[pack_id]),
        )
        self.runner = Runner(endpoint="http://mock", model="mock", **runner_kwargs)
        self.calls: list[str] = []
        self.probe_calls = 0
        probe_queue = list(probes)

        def fake_run_scenario(_meta, scenario, *, repeat_index=1):
            self.calls.append(scenario["id"])
            passed, mode = outcomes.get(scenario["id"], PASS)
            return ScenarioRun(
                id=scenario["id"],
                result=ScenarioResult(scenario["id"], passed, mode, f"{mode} detail", 1.0),  # type: ignore[arg-type]
                raw_scenario=scenario,
                raw_response=None,
                request={},
                sampling_params={},
                status_code=200 if passed else None,
                repeat_index=repeat_index,
            )

        def fake_probe():
            self.probe_calls += 1
            return probe_queue.pop(0)

        monkeypatch.setattr(self.runner, "run_scenario", fake_run_scenario)
        monkeypatch.setattr(self.runner, "_probe_endpoint_alive", fake_probe)


def test_stops_after_k_consecutive_infra_failures_when_probe_fails(monkeypatch):
    outcomes = {"T-02": INFRA, "T-03": INFRA, "T-04": INFRA}
    harness = _Harness(monkeypatch, {"test-pack": 6}, outcomes, [DEAD])
    warnings: list[str] = []

    pack = harness.runner.run_pack("test-pack", warnings=warnings)

    # T-01 once, T-02..T-04 three inline attempts each; T-05/T-06 never run.
    assert harness.calls == ["T-01"] + ["T-02"] * 3 + ["T-03"] * 3 + ["T-04"] * 3
    assert harness.probe_calls == 1
    assert pack.status == ENDPOINT_DOWN_STATUS
    # The streak is set aside, not scored against the model.
    assert [run.id for run in pack.scenarios] == ["T-01"]
    assert (pack.passed, pack.total) == (1, 1)
    assert pack.stop["after_scenario"] == "T-04"
    assert [row["id"] for row in pack.stop["unscored"]] == ["T-02", "T-03", "T-04"]
    assert pack.stop["unscored"][0]["failure_mode"] == "http_error"
    assert [row["id"] for row in pack.stop["not_run"]] == ["T-05", "T-06"]
    assert pack.stop["probe"] == DEAD
    assert len(warnings) == 1 and warnings[0].startswith(ENDPOINT_DOWN_WARNING_PREFIX)
    assert "NOT a complete score" in warnings[0]
    assert "3 infra-failed scenario(s) were set aside unscored and 2 were not run" in warnings[0]


def test_probe_that_answers_resets_the_streak(monkeypatch):
    outcomes = {f"T-{i:02d}": INFRA for i in range(1, 7)}
    harness = _Harness(monkeypatch, {"test-pack": 6}, outcomes, [ALIVE, ALIVE])

    pack = harness.runner.run_pack("test-pack")

    # Six infra failures on a live endpoint: a probe after 3 and after 6, both
    # answered, so every scenario runs and is scored as today.
    assert harness.probe_calls == 2
    assert pack.status == "ok"
    assert pack.stop is None
    assert (pack.passed, pack.total) == (0, 6)


def test_non_infra_result_breaks_the_streak(monkeypatch):
    outcomes = {"T-01": INFRA, "T-02": INFRA, "T-03": WRONG, "T-04": INFRA, "T-05": INFRA}
    harness = _Harness(monkeypatch, {"test-pack": 5}, outcomes, [])

    pack = harness.runner.run_pack("test-pack")

    assert harness.probe_calls == 0
    assert pack.stop is None and pack.total == 5


def test_zero_disables_the_rule(monkeypatch):
    outcomes = {f"T-{i:02d}": INFRA for i in range(1, 7)}
    harness = _Harness(
        monkeypatch, {"test-pack": 6}, outcomes, [], stop_after_infra_failures=0
    )

    pack = harness.runner.run_pack("test-pack")

    assert harness.probe_calls == 0
    assert pack.stop is None and pack.total == 6


def test_synthetic_traffic_never_probes(monkeypatch):
    outcomes = {f"T-{i:02d}": INFRA for i in range(1, 7)}
    harness = _Harness(
        monkeypatch, {"test-pack": 6}, outcomes, [], mock_responses={"T-99": {}}
    )

    pack = harness.runner.run_pack("test-pack")

    assert harness.probe_calls == 0 and pack.stop is None


def test_later_pack_probes_once_and_is_not_run_while_still_down(monkeypatch):
    outcomes = {"A-01": INFRA, "A-02": INFRA, "A-03": INFRA}
    harness = _Harness(monkeypatch, {"a-pack": 4, "b-pack": 5}, outcomes, [DEAD, DEAD])
    harness.pack_scenarios["a-pack"] = [dict(s, id=f"A-{i:02d}") for i, s in
                                        enumerate(harness.pack_scenarios["a-pack"], 1)]

    result = harness.runner.run(["a-pack", "b-pack"])

    a_pack, b_pack = result.packs
    assert a_pack.status == ENDPOINT_DOWN_STATUS
    assert b_pack.status == ENDPOINT_DOWN_STATUS
    # b-pack cost one probe, not three more infra failures.
    assert harness.probe_calls == 2
    assert not any(call.startswith("T-") for call in harness.calls)
    assert b_pack.stop["after_scenario"] is None
    assert b_pack.stop["unscored"] == []
    assert len(b_pack.stop["not_run"]) == 5
    assert b_pack.total == 0
    assert any(w.startswith(f"{ENDPOINT_DOWN_WARNING_PREFIX}b-pack not run") for w in result.warnings)


def test_later_pack_runs_normally_when_endpoint_came_back(monkeypatch):
    outcomes = {"A-01": INFRA, "A-02": INFRA, "A-03": INFRA}
    harness = _Harness(monkeypatch, {"a-pack": 3, "b-pack": 2}, outcomes, [DEAD, ALIVE])
    harness.pack_scenarios["a-pack"] = [dict(s, id=f"A-{i:02d}") for i, s in
                                        enumerate(harness.pack_scenarios["a-pack"], 1)]

    result = harness.runner.run(["a-pack", "b-pack"])

    assert result.packs[1].status == "ok"
    assert result.packs[1].stop is None
    assert (result.packs[1].passed, result.packs[1].total) == (2, 2)
    assert harness.runner._endpoint_down is None


def test_pack_line_says_the_pack_was_stopped(monkeypatch):
    outcomes = {"T-02": INFRA, "T-03": INFRA, "T-04": INFRA}
    harness = _Harness(monkeypatch, {"test-pack": 6}, outcomes, [DEAD])

    line = _pack_line(harness.runner.run_pack("test-pack"))

    assert "endpoint-down; stopped — 5 not scored" in line


def test_resume_reruns_set_aside_and_not_run_scenarios(monkeypatch, tmp_path):
    """The saved result must leave the streak AND the unrun scenarios missing,
    so --resume picks up both; a completed resume drops the stop."""
    real = "toolcall-15"
    from benchlocal_cli.runner import load_pack

    _meta_real, catalog = load_pack(real)
    ids = [scenario["id"] for scenario in catalog]
    runner = Runner(endpoint="http://mock", model="mock")
    outcome = {ids[2]: INFRA, ids[3]: INFRA, ids[4]: INFRA}

    def fake_run_scenario(_meta, scenario, *, repeat_index=1):
        passed, mode = outcome.get(scenario["id"], PASS)
        return ScenarioRun(
            id=scenario["id"],
            result=ScenarioResult(scenario["id"], passed, mode, "d", 1.0),  # type: ignore[arg-type]
            raw_scenario=scenario, raw_response=None, request={}, sampling_params={},
            status_code=200, repeat_index=repeat_index,
        )

    monkeypatch.setattr(runner, "run_scenario", fake_run_scenario)
    monkeypatch.setattr(runner, "_probe_endpoint_alive", lambda: dict(DEAD))
    first = runner.run([real], mode="custom")
    saved = tmp_path / "run.json"
    saved.write_text(json.dumps(first.to_dict()), encoding="utf-8")

    state = load_resume(saved)
    assert state.missing_by_pack[real] == ids[2:]

    # The endpoint is back: the resumed session runs everything that is missing.
    outcome.clear()
    second_runner = Runner(endpoint="http://mock", model="mock")
    monkeypatch.setattr(second_runner, "run_scenario", fake_run_scenario)
    second = second_runner.run(
        [real], mode="custom", selection=state.missing_by_pack,
        completed_repeats=state.completed_repeats,
    )
    merged = merge_resume(state, second)
    pack = merged.packs[0]
    assert pack.status == "ok" and pack.stop is None
    assert (pack.passed, pack.total) == (15, 15)
    assert not any(w.startswith(ENDPOINT_DOWN_WARNING_PREFIX) for w in merged.warnings)


def test_resume_that_stops_again_keeps_the_stop(monkeypatch, tmp_path):
    real = "toolcall-15"
    from benchlocal_cli.runner import load_pack

    _meta_real, catalog = load_pack(real)
    ids = [scenario["id"] for scenario in catalog]
    # First session: ids[0] passes, ids[1..3] fail infra, stop.
    outcome = {ids[1]: INFRA, ids[2]: INFRA, ids[3]: INFRA}

    def make_runner():
        runner = Runner(endpoint="http://mock", model="mock")

        def fake_run_scenario(_meta, scenario, *, repeat_index=1):
            passed, mode = outcome.get(scenario["id"], PASS)
            return ScenarioRun(
                id=scenario["id"],
                result=ScenarioResult(scenario["id"], passed, mode, "d", 1.0),  # type: ignore[arg-type]
                raw_scenario=scenario, raw_response=None, request={}, sampling_params={},
                status_code=200, repeat_index=repeat_index,
            )

        monkeypatch.setattr(runner, "run_scenario", fake_run_scenario)
        monkeypatch.setattr(runner, "_probe_endpoint_alive", lambda: dict(DEAD))
        return runner

    saved = tmp_path / "run.json"
    saved.write_text(json.dumps(make_runner().run([real], mode="custom").to_dict()), encoding="utf-8")
    state = load_resume(saved)
    assert state.missing_by_pack[real] == ids[1:]
    # Second session: ids[1] now passes, then the endpoint dies again, so the
    # merged pack has scored rows from both sessions AND a fresh stop.
    outcome.clear()
    outcome.update({ids[2]: INFRA, ids[3]: INFRA, ids[4]: INFRA})
    second = make_runner().run(
        [real], mode="custom", selection=state.missing_by_pack,
        completed_repeats=state.completed_repeats,
    )
    merged = merge_resume(state, second)

    pack = merged.packs[0]
    assert (pack.passed, pack.total) == (2, 2)  # ids[0] + ids[1]
    assert pack.status == ENDPOINT_DOWN_STATUS
    assert pack.stop["after_scenario"] == ids[4]
    assert [row["id"] for row in pack.stop["not_run"]] == ids[5:]
    stop_warnings = [w for w in merged.warnings if w.startswith(ENDPOINT_DOWN_WARNING_PREFIX)]
    assert len(stop_warnings) == 1  # the new notice, not the stale one beside it
    assert "10 were not run" in stop_warnings[0]
    # A second resume picks up exactly the second session's leftovers.
    saved.write_text(json.dumps(merged.to_dict()), encoding="utf-8")
    assert load_resume(saved).missing_by_pack[real] == ids[2:]


def _stopped_result() -> RunResult:
    from benchlocal_cli.types import PackResult

    pack = PackResult(
        pack_id="toolcall-15", version="1", upstream_commit="x", scenario_count=15,
        passed=1, total=1, score=1.0, latency={"p50": None, "p95": None},
        status=ENDPOINT_DOWN_STATUS,
        stop={"reason": "endpoint_down", "after_scenario": "TC-04", "probe": DEAD,
              "unscored": [{"id": "TC-02"}], "not_run": [{"id": "TC-05"}]},
    )
    return RunResult(
        schema_version="1", runner_version="test", endpoint="http://mock", model="mock",
        mode="custom", started_at="2026-09-23T00:00:00Z", finished_at="2026-09-23T00:00:01Z",
        packs=[pack], totals={"passed": 1, "total": 1, "score": 1.0},
    )


def test_cli_exits_5_on_a_stopped_pack(monkeypatch):
    monkeypatch.setattr(Runner, "run", lambda self, *a, **k: _stopped_result())
    args = ["run", "--scenario", "toolcall-15/TC-01", "--endpoint", "http://mock", "--model", "mock"]
    assert main(args) == 5
    # It outranks --strict-thinking: the run is incomplete either way.
    assert main(args + ["--strict-thinking"]) == 5


def test_history_refuses_a_stopped_run(tmp_path):
    run_dict = _stopped_result().to_dict()
    with pytest.raises(ValueError, match="dead endpoint"):
        append_run(run_dict, tmp_path / "history.csv")
    append_run(run_dict, tmp_path / "history.csv", allow_partial=True)
    assert (tmp_path / "history.csv").is_file()


# --- the probe against real HTTP ---------------------------------------------


class _Stub:
    """A one-route chat endpoint whose answer each test chooses."""

    def __init__(self, status: int = 200, body: dict | str | None = None, delay: float = 0.0):
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                stub.requests.append(json.loads(self.rfile.read(length) or b"{}"))
                time.sleep(delay)
                payload = body if isinstance(body, str) else json.dumps(
                    body if body is not None else {"choices": [{"message": {"content": "O"}}]}
                )
                data = payload.encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        self.requests: list[dict] = []
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _probe(url: str, timeout: float = 5.0) -> dict:
    return Runner(endpoint=url, model="m", endpoint_probe_timeout=timeout)._probe_endpoint_alive()


def test_probe_alive_on_a_generating_endpoint():
    stub = _Stub()
    try:
        result = _probe(stub.url)
    finally:
        stub.close()
    assert result["ok"] is True
    # One token, and no thinking control a thinking-only endpoint could reject.
    assert stub.requests[0]["max_tokens"] == 1
    assert "chat_template_kwargs" not in stub.requests[0]


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (503, {"error": {"message": "Loading model"}}, "HTTP 503"),
        (200, "not json", "non-JSON"),
        (200, {"object": "error"}, "without choices"),
    ],
)
def test_probe_fails_on_anything_but_an_answer(status, body, expected):
    stub = _Stub(status=status, body=body)
    try:
        result = _probe(stub.url)
    finally:
        stub.close()
    assert result["ok"] is False
    assert expected in result["detail"]


def test_probe_fails_fast_on_a_refused_connection():
    stub = _Stub()
    url = stub.url
    stub.close()
    result = _probe(url)
    assert result["ok"] is False
    assert "ConnectError" in result["detail"]


def test_probe_fails_within_its_timeout_on_a_hung_endpoint():
    stub = _Stub(delay=3.0)
    try:
        started = time.perf_counter()
        result = _probe(stub.url, timeout=0.5)
        elapsed = time.perf_counter() - started
    finally:
        stub.close()
    assert result["ok"] is False
    assert result["detail"] == "no answer within 0.5s"
    assert elapsed < 2.5
