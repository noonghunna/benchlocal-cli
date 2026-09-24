"""club-3090#1396: record the sampling in effect and the rig with the results.

vLLM and SGLang expose no sampling-defaults endpoint, so a --sampling-from-server
run read "value not exposed by endpoint", and nothing recorded the topology —
reports from different rigs could not be compared. --server-defaults lets the
caller supply the defaults it resolved (the engine's own GET /props still wins);
--run-meta records rig facts. Both land in the results JSON, the summary header
and the Results Card, and both are absent-by-default so existing output is
unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import benchlocal_cli.runner as runner_module
from benchlocal_cli.cli import _load_run_meta, _load_server_defaults, main

MOCK = {"SO-01": {"choices": [{"message": {"content": '{"title":"The Great Gatsby","year":1925}'}}],
                  "usage": {"prompt_tokens": 40, "completion_tokens": 3, "total_tokens": 43}}}
DEFAULTS = '{"temperature": 1.0, "top_p": 0.95, "top_k": 20, "source": "vLLM --override-generation-config"}'


def _run(tmp_path: Path, *extra: str) -> tuple[int, dict]:
    mock = tmp_path / "mock.json"
    mock.write_text(json.dumps(MOCK))
    out = tmp_path / "result.json"
    rc = main(["run", "--endpoint", "mock", "--model", "mock", "--measured-tps", "100",
               "--mock-responses-from-json", str(mock), "--scenario", "structoutput-15/SO-01",
               "--save-json", str(out), *extra])
    return rc, (json.loads(out.read_text()) if out.exists() else {})


@pytest.fixture()
def no_props(monkeypatch):
    # The engine exposes nothing (vLLM / SGLang): GET /props finds no defaults.
    monkeypatch.setattr(runner_module.Runner, "_read_server_defaults", lambda self, warnings: None)


# ------------------------------------------------------------------ parsing

def test_server_defaults_parses_numbers_and_source():
    assert _load_server_defaults(DEFAULTS) == ({"temperature": 1.0, "top_p": 0.95, "top_k": 20}, "vLLM --override-generation-config")
    assert _load_server_defaults(None) == (None, None)


@pytest.mark.parametrize("bad, message", [
    ('["x"]', "must be a JSON object"),
    ('{"temprature": 1}', "unknown key(s) temprature"),
    ('{"top_p": "0.9"}', "top_p must be a number"),
    ('{"top_p": true}', "top_p must be a number"),
    ('{"source": 3}', "'source' must be a string"),
])
def test_server_defaults_rejects_typos_and_non_numbers(bad, message):
    with pytest.raises(ValueError, match=message.replace("(", r"\(").replace(")", r"\)")):
        _load_server_defaults(bad)


def test_run_meta_keeps_order_last_value_wins_and_rejects_bad_items():
    assert _load_run_meta(["tp=2", "gpus=2x RTX 3090", "tp=4"]) == {"tp": "4", "gpus": "2x RTX 3090"}
    assert _load_run_meta(None) is None
    for bad in ("tp", "=2", "bad key=1"):
        with pytest.raises(ValueError, match="KEY=VALUE"):
            _load_run_meta([bad])


# ------------------------------------------------------------------ end to end

def test_supplied_defaults_and_rig_reach_json_header_and_card(tmp_path, capsys, no_props):
    report = tmp_path / "card.md"
    rc, saved = _run(tmp_path, "--sampling-from-server", "--server-defaults", DEFAULTS,
                     "--run-meta", "tp=2", "--run-meta", "gpus=2x RTX 3090", "--report", "md",
                     "--report-out", str(report))
    assert rc == 0
    assert saved["server_defaults"] == {"temperature": 1.0, "top_p": 0.95, "top_k": 20}
    assert saved["server_defaults_source"] == "supplied: vLLM --override-generation-config"
    assert saved["run_meta"] == {"tp": "2", "gpus": "2x RTX 3090"}

    out = capsys.readouterr().out
    header = next(line for line in out.splitlines() if line.startswith("=== benchlocal-cli"))
    assert "server defaults — temperature=1.0, top_p=0.95, top_k=20; supplied: vLLM --override-generation-config" in header
    assert "value not exposed" not in out
    assert "\nrig: tp=2 · gpus=2x RTX 3090\n" in out

    card = report.read_text()
    assert "Rig: tp=2 · gpus=2x RTX 3090" in card
    assert "Sampling: server defaults — temperature=1.0, top_p=0.95, top_k=20 (supplied: vLLM --override-generation-config)" in card


def test_engine_reported_props_win_over_supplied_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_module.Runner, "_read_server_defaults", lambda self, warnings: {"temperature": 0.6})
    rc, saved = _run(tmp_path, "--sampling-from-server", "--server-defaults", DEFAULTS)
    assert rc == 0
    assert saved["server_defaults"] == {"temperature": 0.6}
    assert saved["server_defaults_source"] == "GET /props"


def test_server_defaults_without_sampling_from_server_is_refused(tmp_path, capsys):
    rc, _ = _run(tmp_path, "--server-defaults", DEFAULTS)
    assert rc == 1
    assert "--server-defaults only applies with --sampling-from-server" in capsys.readouterr().err


def test_absent_flags_leave_output_and_json_unchanged(tmp_path, capsys, no_props):
    rc, saved = _run(tmp_path, "--sampling-from-server")
    assert rc == 0
    assert saved.get("run_meta") is None and saved.get("server_defaults") is None
    out = capsys.readouterr().out
    assert "\nrig:" not in out
    assert "value not exposed by endpoint" in out  # the pre-#1396 wording, unchanged when nothing was supplied


# ------------------------------------------------------------------ resume

def test_resume_keeps_the_original_rig_and_supplied_defaults(tmp_path, no_props):
    """A resumed session that is not handed the flags again must not erase what
    the first session recorded (merge_resume used to overwrite server_defaults)."""
    from benchlocal_cli.persistence import ResumeState, merge_resume
    from benchlocal_cli.types import PackResult, RunResult

    rc, first = _run(tmp_path, "--sampling-from-server", "--server-defaults", DEFAULTS, "--run-meta", "tp=4")
    assert rc == 0
    config = {"target_selection": ["structoutput-15/SO-01"], "pack_ids": ["structoutput-15"],
              "started_at": first["started_at"], "sampling_source": "server",
              "server_defaults": first["server_defaults"], "server_defaults_source": first["server_defaults_source"],
              "run_meta": first["run_meta"]}
    state = ResumeState(
        source_path=tmp_path / "r.partial.jsonl", final_path=tmp_path / "r.json", sidecar_path=tmp_path / "r.partial.jsonl",
        config=config, previous_result=first, missing_selection=[], missing_by_pack={}, completed_repeats={},
    )
    # The resumed session ran without --server-defaults / --run-meta, and the engine exposes nothing.
    fresh = RunResult(
        schema_version="1", runner_version="0.10.0", endpoint="mock", model="mock", mode="custom",
        started_at=first["started_at"], finished_at=first["finished_at"],
        packs=[PackResult(pack_id="structoutput-15", version="2.0.0", upstream_commit="x", scenario_count=0,
                          passed=0, total=0, score=0.0, latency={"p50": None, "p95": None, "mean": None},
                          scenarios=[], status="ok")],
        totals={"passed": 0, "total": 0, "score": 0.0}, warnings=[], sampling_source="server",
    )
    merged = merge_resume(state, fresh)
    assert merged.run_meta == {"tp": "4"}
    assert merged.server_defaults == {"temperature": 1.0, "top_p": 0.95, "top_k": 20}
    assert merged.server_defaults_source == "supplied: vLLM --override-generation-config"
