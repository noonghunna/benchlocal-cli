"""Thinking-validity detection (#126).

A reasoning A/B can produce plausible-but-invalid numbers with no warning.
Two failure modes, both detectable post-hoc from the responses already
collected (no extra requests):

- thinking was REQUESTED but the model never thought — the chat template has
  no thinking branch, or the server isn't parsing reasoning. The "thinking"
  arm is just the no-thinking scores wearing a label.
- thinking was DISABLED but the server thinks anyway — e.g. llama.cpp was
  booted with ``--reasoning on`` (which overrides the request), or the
  off-switch this harness sent is not the one the model's chat template
  reads. Both arms are the same run; the A/B shows a clean null and reads as
  a legitimate finding.

The second is the dangerous one: it produces a believable comparison from a
contaminated baseline and nothing downstream reveals it. Detection therefore
fails loudly on ANY reasoning observed in a disabled arm, while a requested
arm only warns when NO response shows reasoning (models may think
conditionally, so partial presence is legitimate).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

# llama.cpp emits ``reasoning_content`` when ``--reasoning-format`` parsing is
# on; ``reasoning`` appears on some providers. A ``<think>`` marker left in
# ``content`` means thinking happened but was NOT extracted
# (``--reasoning-format none``) — the grader then scores the reasoning text as
# the answer, so it counts as reasoning present.
_REASONING_FIELDS = ("reasoning_content", "reasoning")
_THINK_MARKER = "<think>"


def message_has_reasoning(message: Mapping[str, Any]) -> bool:
    for field in _REASONING_FIELDS:
        value = message.get(field)
        if isinstance(value, str) and value.strip():
            return True
    content = message.get("content")
    if isinstance(content, str) and _THINK_MARKER in content:
        return True
    return False


def _iter_messages(raw_response: Any) -> Iterable[Mapping[str, Any]]:
    """Yield assistant messages, recursing into multi-turn ``responses`` lists
    (the same shape ``pack_diagnostics`` walks)."""
    if not isinstance(raw_response, Mapping):
        return
    nested = raw_response.get("responses")
    if isinstance(nested, list):
        for item in nested:
            yield from _iter_messages(item)
        return
    choices = raw_response.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
        message = choices[0].get("message")
        if isinstance(message, Mapping):
            yield message


def response_has_reasoning(raw_response: Any) -> bool | None:
    """True/False when the response carries at least one assistant message;
    None when there is nothing to judge (timeout / HTTP failure / non-model
    traffic), which is not evidence either way."""
    saw_message = False
    for message in _iter_messages(raw_response):
        saw_message = True
        if message_has_reasoning(message):
            return True
    return False if saw_message else None


def pack_thinking_validity(pack_result: Any) -> dict | None:
    """Inspect a finished pack's responses against what was requested.

    Returns None when nothing can be judged (no pack scenarios produced a
    model response). ``expected`` reflects the pack's resolved thinking state
    (force-on / force-off / pack default), as recorded on the PackResult.
    """
    expected_on = bool(getattr(pack_result, "thinking_enabled", False))
    inspected = 0
    with_reasoning = 0
    for run in getattr(pack_result, "scenarios", []) or []:
        observed = response_has_reasoning(getattr(run, "raw_response", None))
        if observed is None:
            continue
        inspected += 1
        if observed:
            with_reasoning += 1
    if inspected == 0:
        return None
    if expected_on and with_reasoning == 0:
        status = "silent"
    elif not expected_on and with_reasoning > 0:
        status = "contaminated"
    else:
        status = "ok"
    return {
        "expected": "on" if expected_on else "off",
        "responses": inspected,
        "with_reasoning": with_reasoning,
        "status": status,
    }


def thinking_validity_for_packs(
    packs: Iterable[Any],
) -> tuple[dict[str, dict], list[str]]:
    """Run the check over finished packs. Returns (observations, warnings);
    observations maps pack_id -> the per-pack observation dict (only packs
    where something could be judged)."""
    observations: dict[str, dict] = {}
    warnings: list[str] = []
    for pack in packs:
        if getattr(pack, "skipped", False):
            continue
        observation = pack_thinking_validity(pack)
        if observation is None:
            continue
        pack_id = str(getattr(pack, "pack_id", "") or "?")
        observations[pack_id] = observation
        warning = validity_warning(pack_id, observation)
        if warning:
            warnings.append(warning)
    return observations, warnings


def validity_warning(pack_id: str, observation: Mapping[str, Any]) -> str | None:
    status = observation.get("status")
    responses = observation.get("responses")
    with_reasoning = observation.get("with_reasoning")
    if status == "silent":
        return (
            f"{pack_id}: thinking was requested but no reasoning output was returned "
            f"in any of {responses} responses. The model may not support thinking, or "
            "the server may not be parsing it (llama.cpp --reasoning-format). "
            "These results are NOT a valid thinking arm."
        )
    if status == "contaminated":
        return (
            f"{pack_id}: thinking was disabled but {with_reasoning} of {responses} "
            "responses returned reasoning content — the server is likely forcing it "
            "(e.g. llama.cpp --reasoning on), or the off-switch this harness sent is "
            "not the one this model's chat template reads (check /props for "
            "enable_thinking vs reasoning_effort). This arm is CONTAMINATED and not "
            "a valid baseline."
        )
    return None
