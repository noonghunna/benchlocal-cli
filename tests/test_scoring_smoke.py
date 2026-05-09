"""Smoke tests — confirm scoring modules import + stub raises NotImplementedError.

TODO (Codex): replace these with real unit tests as scoring modules are implemented.
Each scoring module should have its own test file:

    test_scoring_tool_call.py
    test_scoring_instruct_follow.py
    test_scoring_struct_output.py
    test_scoring_reason_math.py
    test_scoring_data_extract.py
    test_scoring_stub.py

Each should:
1. Import the scoring module
2. Call score_scenario() with handcrafted scenario + response fixtures
3. Assert the returned ScenarioResult matches expectations for each failure mode
4. Cover ALL assertion primitives documented in docs/PACK_FORMAT.md
"""

from __future__ import annotations

import pytest


def test_scoring_modules_importable():
    """All scoring modules should at least import without syntax errors."""
    from benchlocal_cli.scoring import (  # noqa: F401
        _stub,
        data_extract,
        instruct_follow,
        reason_math,
        struct_output,
        tool_call,
    )


def test_stub_returns_verifier_not_implemented():
    """The _stub module should always return verifier_not_implemented."""
    from benchlocal_cli.scoring import _stub

    result = _stub.score_scenario(
        scenario={"id": "bugfind-15-001", "pack_id": "bugfind-15"},
        response={},
    )
    assert result["passed"] is False
    assert result["failure_mode"] == "verifier_not_implemented"
    assert "bugfind-15" in result["detail"]


def test_pre_alpha_scoring_modules_raise():
    """Scoring modules raise NotImplementedError until Codex fills them in."""
    from benchlocal_cli.scoring import (
        data_extract,
        instruct_follow,
        reason_math,
        struct_output,
        tool_call,
    )

    fake_scenario = {"id": "x"}
    fake_response = {}

    for module in [tool_call, instruct_follow, struct_output, reason_math, data_extract]:
        with pytest.raises(NotImplementedError):
            module.score_scenario(fake_scenario, fake_response)
