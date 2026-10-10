"""#192: name the sampler in effect; don't call it a defect.

club-3090 runs its quality evals on the server's sampler by default
(club-3090#1594), but every such run printed `⚠ NON-CANONICAL (sampling: …)` and
warned that results "are NOT comparable to the default temp=0 baseline", which
reads as "this run is wrong". The facts stay; they are stated as a sampler label,
like #189's `[TOKEN BUDGET: …]`. What is sent and recorded does not change.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import benchlocal_cli.runner as runner_module
from benchlocal_cli.cli import main

MOCK = {"SO-01": {"choices": [{"message": {"content": '{"title":"The Great Gatsby","year":1925}'}}]}}
DEFAULTS = '{"temperature": 1.0, "top_p": 0.95, "top_k": 20, "source": "vLLM --override-generation-config"}'
SAME_SAMPLER = "compare only with runs under the same sampler"
OLD_WORDING = ("NON-CANONICAL", "⚠", "NOT comparable", "non-canonical", "temp=0 baseline")


def _run(tmp_path: Path, capsys, *extra: str, name: str = "result.json"):
    mock = tmp_path / "mock.json"
    mock.write_text(json.dumps(MOCK))
    out = tmp_path / name
    rc = main(["run", "--endpoint", "http://mock", "--model", "mock", "--measured-tps", "100",
               "--mock-responses-from-json", str(mock), "--scenario", "structoutput-15/SO-01",
               "--save-json", str(out), *extra])
    captured = capsys.readouterr()
    saved = json.loads(out.read_text()) if out.exists() else {}
    return rc, captured.out, captured.err, saved


def _header(stdout: str) -> str:
    return next(line for line in stdout.splitlines() if line.startswith("=== benchlocal-cli"))


def _no_old_wording(*texts: str) -> None:
    for text in texts:
        for old in OLD_WORDING:
            assert old not in text, f"{old!r} still in {text!r}"


@pytest.fixture()
def no_props(monkeypatch):
    # vLLM / SGLang: GET /props reports no sampling defaults.
    monkeypatch.setattr(runner_module.Runner, "_read_server_defaults", lambda self, warnings: None)


def test_explicit_overrides_are_labelled(tmp_path, capsys):
    rc, out, _err, saved = _run(tmp_path, capsys, "--temperature", "0.7", "--top-p", "0.8")
    assert rc == 0
    header = _header(out)
    assert "[SAMPLING: temperature=0.7, top_p=0.8]" in header
    warnings = saved["warnings"]
    assert f"sampling: temperature=0.7, top_p=0.8 — {SAME_SAMPLER}" in warnings
    _no_old_wording(header, *warnings)
    # recorded exactly as before
    assert saved["sampling_overrides"] == {"temperature": 0.7, "top_p": 0.8}
    assert saved.get("sampling_source") is None


def test_server_defaults_supplied_are_labelled(tmp_path, capsys, no_props):
    rc, out, _err, saved = _run(tmp_path, capsys, "--sampling-from-server", "--server-defaults", DEFAULTS)
    assert rc == 0
    header = _header(out)
    assert ("[SAMPLING: server defaults — temperature=1.0, top_p=0.95, top_k=20; "
            "supplied: vLLM --override-generation-config]") in header
    warnings = saved["warnings"]
    assert ("sampling: server defaults (temperature=1.0, top_p=0.95, top_k=20; supplied: vLLM "
            f"--override-generation-config) — {SAME_SAMPLER}") in warnings
    _no_old_wording(header, *warnings)
    assert saved["sampling_source"] == "server"
    assert saved["server_defaults"] == {"temperature": 1.0, "top_p": 0.95, "top_k": 20}
    assert saved["server_defaults_source"] == "supplied: vLLM --override-generation-config"
    assert saved.get("sampling_overrides") is None


def test_server_defaults_from_props_are_labelled(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(runner_module.Runner, "_read_server_defaults",
                        lambda self, warnings: {"temperature": 0.7, "top_p": 0.8})
    rc, out, _err, saved = _run(tmp_path, capsys, "--sampling-from-server")
    assert rc == 0
    header = _header(out)
    assert "[SAMPLING: server defaults — temperature=0.7, top_p=0.8]" in header
    assert f"sampling: server defaults (temperature=0.7, top_p=0.8) — {SAME_SAMPLER}" in saved["warnings"]
    _no_old_wording(header, *saved["warnings"])


def test_server_defaults_not_exposed_are_labelled(tmp_path, capsys, no_props):
    rc, out, _err, saved = _run(tmp_path, capsys, "--sampling-from-server")
    assert rc == 0
    header = _header(out)
    assert "[SAMPLING: server defaults — not exposed by the endpoint]" in header
    assert f"sampling: server defaults (not exposed by the endpoint) — {SAME_SAMPLER}" in saved["warnings"]
    _no_old_wording(header, *saved["warnings"])
    assert saved["sampling_source"] == "server" and saved.get("server_defaults") is None


def test_pack_sampler_stays_unlabelled(tmp_path, capsys):
    rc, out, _err, saved = _run(tmp_path, capsys)
    assert rc == 0
    assert "[SAMPLING" not in _header(out)
    assert not any(w.startswith("sampling:") for w in saved.get("warnings") or [])


@pytest.mark.parametrize("extra", [("--temperature", "0.7"), ("--sampling-from-server",)])
def test_exit_on_regression_refusal_gives_the_reason(tmp_path, capsys, no_props, extra):
    _run(tmp_path, capsys, name="baseline.json")
    rc, _out, err, _ = _run(tmp_path, capsys, *extra, "--previous-result", str(tmp_path / "baseline.json"),
                            "--exit-on-regression", name="sampled.json")
    assert rc == 1
    assert "sampled runs are not deterministic, so a regression gate needs the packs' fixed sampler" in err
    assert "drop --sampling-from-server / the sampler overrides" in err
    _no_old_wording(err)
