"""club-3090#1269: hermesagent-20 scored 0/20 whenever a leg set top_k / min_p explicitly.

The Hermes runner hands `request_overrides` to the pinned Hermes runtime, which merges them
straight into the kwargs of its OpenAI client's `chat.completions.create()`. The SDK rejects any
keyword it does not declare with a client-side `TypeError` BEFORE any HTTP request, so Hermes gave
up without a single model call (the usage proxy counted 0 requests on all 20 scenarios), returned
`failed=True`, and the runner dropped that — exit 0, null answer, no agent_error.

These tests pin both halves of the fix on the vendored runner (the file the sandbox build copies):
only SDK-native keys may be top-level, engine sampler keys travel in `extra_body`, and a Hermes
`failed` return is reported as `ok: False` with its error text.
"""

from __future__ import annotations

import ast
import inspect
import typing
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "vendor" / "HermesAgent-20" / "verification" / "agent-runner.py"

# The generation block of club-3090#1269's failing leg, verbatim.
LEG_A = {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0, "max_tokens": 4096}


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
        {**LEG_A, "repetition_penalty": 1.05}, {"chat_template_kwargs": {"enable_thinking": False}}
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
