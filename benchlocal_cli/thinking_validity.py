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
arm is flagged `silent` only when NO response shows reasoning (models may think
conditionally, so partial presence is legitimate). A requested arm where FEWER
than SPARSE_BELOW of responses reasoned is flagged `sparse` — a warning, never a
--strict-thinking failure: it is the model's choice, but the arm's scores were
mostly produced without reasoning and should not be read as a thinking arm.

A pack whose agent calls the model ITSELF (hermesagent-20, from inside its
sandbox) shows the runner no responses at all, so the checks above can't judge
it. Its scenarios still carry the sandbox usage proxy's token tally. A disabled
arm whose tally reports reasoning tokens is flagged `agent-reasoning`: a warning,
never a --strict-thinking failure, because the tally can't say which of the
agent's calls reasoned, so it isn't the evidence `contaminated` rests on. The
tally folds a zero into "missing", so there is no negative evidence: a requested
arm stays unjudged, and so does a disabled arm whose tally shows no reasoning.
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


# A thinking-requested pack where FEWER than this share of responses carried any
# reasoning is "sparse". Adaptive-thinking models (the chat template does not
# force a <think> block, so the model decides per request) can skip reasoning on
# most of a pack; the old check only flagged ZERO, so a pack with 1 of 15
# responses reasoning read as a valid thinking arm. Sparse is a warning, not a
# --strict-thinking failure: it is usually the model's choice, not a
# misconfiguration, but the results are mostly not a thinking arm.
SPARSE_BELOW = 0.5


def agent_reasoning_tokens(run: Any) -> int | None:
    """Reasoning tokens the sandbox usage proxy tallied for calls the agent made
    itself (club-3090#1396 folds them into the scenario's result), 0 when it
    tallied calls but no reasoning, None when the scenario shows no agent calls."""
    result = getattr(run, "result", None)
    completion = getattr(result, "tokens_completion", None)
    if isinstance(completion, bool) or not isinstance(completion, int) or completion <= 0:
        return None
    reasoning = getattr(result, "tokens_reasoning", None)
    if isinstance(reasoning, bool) or not isinstance(reasoning, int) or reasoning < 0:
        return 0
    return reasoning


def pack_thinking_validity(pack_result: Any) -> dict | None:
    """Inspect a finished pack's responses against what was requested.

    Returns None when nothing can be judged (no pack scenarios produced a
    model response). ``expected`` reflects the pack's resolved thinking state
    (force-on / force-off / pack default), as recorded on the PackResult.
    """
    expected_on = bool(getattr(pack_result, "thinking_enabled", False))
    inspected = 0
    with_reasoning = 0
    agent_scenarios = 0
    agent_with_reasoning = 0
    agent_tokens = 0
    for run in getattr(pack_result, "scenarios", []) or []:
        observed = response_has_reasoning(getattr(run, "raw_response", None))
        if observed is None:
            # No runner-visible response: the agent may have called the model
            # itself. Only a disabled arm can use that tally (see module doc).
            tokens = None if expected_on else agent_reasoning_tokens(run)
            if tokens is not None:
                agent_scenarios += 1
                if tokens > 0:
                    agent_with_reasoning += 1
                    agent_tokens += tokens
            continue
        inspected += 1
        if observed:
            with_reasoning += 1
    if inspected:
        if expected_on and with_reasoning == 0:
            status = "silent"
        elif expected_on and with_reasoning / inspected < SPARSE_BELOW:
            status = "sparse"
        elif not expected_on and with_reasoning > 0:
            status = "contaminated"
        else:
            status = "ok"
        # Reasoning the runner saw itself outranks the agent tally.
        if status != "ok" or not agent_with_reasoning:
            return {
                "expected": "on" if expected_on else "off",
                "responses": inspected,
                "with_reasoning": with_reasoning,
                "status": status,
            }
    if agent_with_reasoning:
        return {
            "expected": "off",
            "responses": agent_scenarios,
            "with_reasoning": agent_with_reasoning,
            "status": "agent-reasoning",
            "source": "agent-usage",
            "reasoning_tokens": agent_tokens,
        }
    return None


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
    if status == "sparse":
        return (
            f"{pack_id}: thinking was requested but only {with_reasoning} of {responses} "
            "responses returned reasoning. Unlike a pack with none, this is usually the "
            "model choosing not to think (adaptive-thinking models whose chat template "
            "does not force a <think> block), not a misconfiguration — but most of this "
            "pack's results were produced WITHOUT reasoning, so it is not a clean "
            "thinking arm."
        )
    if status == "agent-reasoning":
        return (
            f"{pack_id}: thinking was disabled, but the agent's own model calls reported "
            f"{observation.get('reasoning_tokens')} reasoning tokens in {with_reasoning} of "
            f"{responses} scenarios (the sandbox's usage tally). The agent calls the model "
            "from inside its sandbox, so the runner can't see which calls ignored the "
            "off-switch: read this pack's no-thinking score as partly a thinking score."
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
