"""club-3090#1269: hermesagent-20 scored 0/20 whenever a leg set top_k / min_p explicitly.

The Hermes runner hands `request_overrides` to the pinned Hermes runtime, which merges them
straight into the kwargs of its OpenAI client's `chat.completions.create()`. The SDK rejects any
keyword it does not declare with a client-side `TypeError` BEFORE any HTTP request, so Hermes gave
up without a single model call (the usage proxy counted 0 requests on all 20 scenarios), returned
`failed=True`, and the runner dropped that — exit 0, null answer, no agent_error.

These tests pin both halves of the fix on the vendored runner (the file the sandbox build copies):
only SDK-native keys may be top-level, engine sampler keys travel in `extra_body`, and a Hermes
`failed` return is reported as `ok: False` with its error text.

The penalty tests below pin a second, quieter drop: presence_penalty / frequency_penalty from
`--extra-body` reached every runner-owned pack but never Hermes, because the sandbox server's
generation filter and the runner's key lists both left them out. No error, no warning — the
Hermes requests simply went out without them.
"""

from __future__ import annotations

import ast
import inspect
import typing
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "vendor" / "HermesAgent-20" / "verification" / "agent-runner.py"
SERVER = ROOT / "sandboxes" / "hermes" / "server.py"

# The generation block of club-3090#1269's failing leg, verbatim.
LEG_A = {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0, "max_tokens": 4096}


def _load_names(path: Path, wanted: set[str]) -> dict:
    """Exec only the named top-level functions / assignments of `path`, without importing it."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = [
        node for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in wanted)
        or (isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id in wanted for t in node.targets))
    ]
    ns = {"Dict": typing.Dict, "Any": typing.Any, "Optional": typing.Optional}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), ns)
    missing = wanted - set(ns)
    assert not missing, f"{path.name} is missing {missing}"
    return ns


def _load_overrides_fn():
    """Extract `_request_overrides` and its key tuples without importing the runner, which pulls
    in the Hermes runtime at module load."""
    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    wanted = {"_request_overrides", "OPENAI_NATIVE_SAMPLER_KEYS", "EXTRA_BODY_SAMPLER_KEYS"}
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in wanted for t in node.targets
        ):
            nodes.append(node)
    found = {n.name if isinstance(n, ast.FunctionDef) else n.targets[0].id for n in nodes}
    assert found == wanted, f"runner is missing {wanted - found}"
    ns = {"Dict": typing.Dict, "Any": typing.Any, "Optional": typing.Optional}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(RUNNER), "exec"), ns)
    return ns["_request_overrides"]


def test_engine_sampler_keys_travel_in_extra_body():
    overrides = _load_overrides_fn()(LEG_A, {"chat_template_kwargs": {"enable_thinking": False}})
    assert overrides == {
        "temperature": 0.7,
        "top_p": 0.8,
        "max_tokens": 4096,
        "extra_body": {
            "chat_template_kwargs": {"enable_thinking": False},
            "top_k": 20,
            "min_p": 0.0,  # a falsy 0.0 is still an explicit setting and must survive
        },
    }


def test_generation_wins_over_model_extra_body_and_empty_is_empty():
    fn = _load_overrides_fn()
    assert fn({"top_k": 20}, {"top_k": 40, "x": 1})["extra_body"] == {"top_k": 20, "x": 1}
    assert fn({}, None) == {}
    assert fn({"repetition_penalty": 1.05}, None) == {"extra_body": {"repetition_penalty": 1.05}}


def test_every_top_level_key_is_accepted_by_the_openai_sdk():
    """The real invariant: whatever the runner puts top-level must be a declared keyword of
    `chat.completions.create()`, or the call dies client-side before it is sent."""
    openai = pytest.importorskip("openai")
    from openai.resources.chat.completions import Completions

    accepted = set(inspect.signature(Completions.create).parameters)
    overrides = _load_overrides_fn()(
        {**LEG_A, "repetition_penalty": 1.05, "presence_penalty": 1.5, "frequency_penalty": 0.5},
        {"chat_template_kwargs": {"enable_thinking": False}},
    )
    rejected = sorted(k for k in overrides if k not in accepted)
    assert not rejected, f"openai {openai.__version__} rejects top-level {rejected}"
    # And the positive control: the keys this test moved really ARE rejected top-level.
    assert not {"top_k", "min_p", "repetition_penalty"} & accepted


def test_hermes_failed_return_is_reported_not_dropped():
    src = RUNNER.read_text(encoding="utf-8")
    assert 'hermes_failed = bool(result.get("failed"))' in src
    assert '"ok": not hermes_failed' in src
    assert '"error": (str(result.get("error")' in src


def test_penalties_go_top_level():
    """presence_penalty / frequency_penalty are declared keywords of `Completions.create`, so they
    travel top-level like temperature — not in extra_body, and not dropped."""
    overrides = _load_overrides_fn()({**LEG_A, "presence_penalty": 1.5, "frequency_penalty": 0.0}, None)
    assert overrides["presence_penalty"] == 1.5
    assert overrides["frequency_penalty"] == 0.0  # a falsy 0.0 is still an explicit setting
    assert "presence_penalty" not in overrides["extra_body"]


def test_server_filter_forwards_penalties():
    """The sandbox server filters the runner's sampling before the agent runner sees it; a key it
    drops never reaches the model (the original presence_penalty loss)."""
    filt = _load_names(SERVER, {"_filter_generation"})["_filter_generation"]
    out = filt({**LEG_A, "presence_penalty": 1.5, "frequency_penalty": 0.5, "seed": 7, "x": None})
    assert out["presence_penalty"] == 1.5 and out["frequency_penalty"] == 0.5
    assert "seed" not in out and "x" not in out
    assert filt(None) == {} and filt({"presence_penalty": None}) == {}


def test_server_filter_and_runner_name_the_same_keys():
    """Drift guard: every key the server forwards must be one the runner sends, and every key the
    runner sends must be one the server forwards — otherwise one side is silently dead."""
    filt = _load_names(SERVER, {"_filter_generation"})["_filter_generation"]
    runner = _load_names(RUNNER, {"OPENAI_NATIVE_SAMPLER_KEYS", "EXTRA_BODY_SAMPLER_KEYS"})
    runner_keys = set(runner["OPENAI_NATIVE_SAMPLER_KEYS"]) | set(runner["EXTRA_BODY_SAMPLER_KEYS"])
    probe = {k: 1 for k in runner_keys | {
        "seed", "typical_p", "mirostat", "repeat_penalty", "logit_bias", "stop", "n",
    }}
    assert set(filt(probe)) == runner_keys
