"""ReasonMath scoring with answer-line and numeric checks."""

from __future__ import annotations

import math
import re
from fractions import Fraction

from benchlocal_cli.scoring.common import content, content_channels_with_sources, result
from benchlocal_cli.types import ScenarioResult


def _numbers(text: str) -> list[float]:
    return [float(match.replace(",", "")) for match in re.findall(r"-?\d+(?:,\d{3})*(?:\.\d+)?", text)]


def _answer_region(text: str) -> str:
    """The final ``ANSWER:`` line if present (the pack's format contract), else
    the full text. Used to scope lenient value matching to the actual answer."""
    matches = re.findall(r"(?im)^[ \t]*ANSWER[ \t]*:[ \t]*(.*)$", text)
    return matches[-1] if matches else text


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _norm_answer(s: str) -> str:
    """``_norm`` plus the spellings a final line varies in without changing
    meaning: spacing around ``;``, ``=`` and ``:``, and a trailing period.
    Applied to both sides of the comparison, so an answer that matched before
    still matches (#207)."""
    s = _norm(s)
    s = re.sub(r"\s*;\s*", "; ", s)
    s = re.sub(r"\s*=\s*", "=", s)
    s = re.sub(r"\s*:\s*", ": ", s)
    return re.sub(r"[\s.]+$", "", s)


def _norm_checkpoint_label(value: str) -> str:
    return _norm(value.replace("_", " "))


# A number in an answer value: a fraction (3/4), a percentage (75%) or a
# decimal (0.75). Compared exactly, as Fractions, so 3/4 == 0.75 == 75% but
# 75.5% or 13/4 never matches 3/4 by containment.
_VALUE_NUMBER = re.compile(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?|\.\d+)\s*(%)?")


def _value_numbers(text: str) -> list[Fraction]:
    numbers = []
    for match in _VALUE_NUMBER.finditer(text):
        numerator, denominator, plain, percent = match.groups()
        if numerator is not None:
            if Fraction(denominator) == 0:
                return []
            numbers.append(Fraction(numerator) / Fraction(denominator))
        else:
            value = Fraction(plain)
            numbers.append(value / 100 if percent else value)
    return numbers


def _labelled_values(matcher: dict, payload: str) -> bool:
    """Each part is ``label=value`` or ``label: value``, separated by ``;`` (or
    by ``,`` when the line has no ``;``). Every expected value must be claimed
    by exactly one part whose label matches only that value's pattern, and
    every number in the part's value must equal it."""
    expected = matcher["values"]
    text = re.sub(r"[\s.]+$", "", _norm(payload))
    pair_separator = ";" if ";" in text else ","
    parts = [part.strip() for part in text.split(pair_separator) if part.strip()]
    if len(parts) != len(expected):
        return False
    claimed: set[int] = set()
    for part in parts:
        separator = "=" if "=" in part else ":" if ":" in part else None
        if separator is None:
            return False
        label, value = part.split(separator, 1)
        label = label.replace("_", " ")
        owners = [i for i, field in enumerate(expected) if re.search(field["label"], label)]
        if len(owners) != 1 or owners[0] in claimed:
            return False
        numbers = _value_numbers(value)
        target = _value_numbers(str(expected[owners[0]]["value"]))
        if not numbers or len(target) != 1 or any(number != target[0] for number in numbers):
            return False
        claimed.add(owners[0])
    return len(claimed) == len(expected)


def _lists_full_order(text: str, items: list[str]) -> bool:
    """True when the items appear as one run separated only by non-letters
    (``white, red, green, blue, yellow``; ``red -> blue -> …``) and the run
    names every item, i.e. the answer commits to an arrangement."""
    pattern = r"\b(" + "|".join(re.escape(item) for item in items) + r")\b"
    run: set[str] = set()
    previous_end = None
    for match in re.finditer(pattern, text):
        if previous_end is not None and re.search(r"[a-z]", text[previous_end:match.start()]):
            run = set()
        run.add(match.group(1))
        previous_end = match.end()
        if run >= set(items):
            return True
    return False


def _no_solution_claim(matcher: dict, payload: str) -> bool:
    """The answer states that no valid solution exists and does not also give
    one."""
    text = _norm(payload)
    if not re.search(matcher["claim"], text):
        return False
    if matcher.get("negation") and re.search(matcher["negation"], text):
        return False
    items = [str(item).lower() for item in matcher.get("order_items", [])]
    return not (items and _lists_full_order(text, items))


_ANSWER_MATCHERS = {
    "labelled_values": _labelled_values,
    "no_solution_claim": _no_solution_claim,
}


def _answer_line(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    matches = [line for line in lines if line.startswith("ANSWER: ")]
    return matches[-1] if matches else ""


def _answer_payload(answer_line: str) -> str:
    return re.sub(r"^ANSWER:\s*", "", answer_line, flags=re.IGNORECASE).strip()


def _try_single_value_match(canonical_answer: str, answer_line: str) -> bool:
    canonical_payload = _answer_payload(canonical_answer)
    answer_payload_text = _answer_payload(answer_line)

    if ";" in canonical_payload or "=" not in canonical_payload:
        return False

    key, expected = canonical_payload.split("=", 1)
    expected_key = _norm_checkpoint_label(key)
    expected_value = _norm(expected)
    actual = _norm(answer_payload_text)

    if actual == expected_value:
        return True

    actual_without_label = re.sub(r"^[a-z_][a-z0-9_ ]*=\s*", "", actual, flags=re.IGNORECASE).strip()
    if actual_without_label == expected_value:
        return True

    return expected_key in actual and expected_value in actual


def _answer_axis(assertion: dict, raw_answer: str) -> tuple[int, str | None]:
    answer_line = _answer_line(raw_answer)
    if not answer_line:
        return 0, 'Missing final "ANSWER: " line.'

    canonical_answer = assertion.get("canonical_answer") or f"ANSWER: {assertion['value'].strip()}"
    normalized = _norm_answer(answer_line)
    canonical = _norm_answer(canonical_answer)
    accepted = [_norm_answer(value) for value in assertion.get("accepted_answers", [])]
    partial = [_norm_answer(value) for value in assertion.get("partial_answers", [])]

    if normalized == canonical or normalized in accepted:
        return 2, None

    matcher = assertion.get("answer_matcher")
    if matcher and _ANSWER_MATCHERS[matcher["kind"]](matcher, _answer_payload(answer_line)):
        return 2, f"Accepted by the {matcher['kind']} answer matcher."

    if _try_single_value_match(canonical_answer, answer_line):
        return 2, None

    if normalized in partial:
        return 1, "Matched a scenario-defined partial answer."

    return 0, f"Unexpected final line: {answer_line}"


def _checkpoint_matches(checkpoint_text: str, normalized_text: str) -> bool:
    normalized_checkpoint = _norm(checkpoint_text)
    if normalized_checkpoint in normalized_text:
        return True
    if "=" not in checkpoint_text:
        return False
    left, right = checkpoint_text.split("=", 1)
    return _norm_checkpoint_label(left) in normalized_text and _norm(right) in normalized_text


def _trace_axis(
    assertion: dict,
    raw_answer: str,
    channels: list[tuple[str, str]] | None = None,
) -> tuple[int, str | None, list[str]]:
    checkpoints = assertion.get("checkpoints") or []
    if not checkpoints:
        return 2, None, []

    normalized = _norm(raw_answer)
    matched = []
    matched_source_set: set[str] = set()
    for checkpoint in checkpoints:
        checkpoint_text = str(checkpoint)
        if not _checkpoint_matches(checkpoint_text, normalized):
            continue
        matched.append(checkpoint_text)
        for source, channel_text in channels or []:
            if _checkpoint_matches(checkpoint_text, _norm(channel_text)):
                matched_source_set.add(source)

    matched_sources = [source for source, _ in channels or [] if source in matched_source_set]
    source_note = f" Trace sources: {', '.join(matched_sources)}." if matched_sources else ""
    if len(matched) == len(checkpoints):
        return 2, f"Matched checkpoints across {', '.join(matched_sources)}." if matched_sources else None, matched_sources
    if matched:
        return 1, f"Matched {len(matched)}/{len(checkpoints)} checkpoints.{source_note}", matched_sources
    return 0, f"No published checkpoints matched.{source_note}", matched_sources


def _scored_result(
    scenario: dict,
    passed: bool,
    failure_mode: str,
    detail: str,
    trace: dict,
) -> ScenarioResult:
    return ScenarioResult(
        scenario_id=str(scenario.get("id", "unknown")),
        passed=passed,
        failure_mode=failure_mode,  # type: ignore[arg-type]
        detail=detail,
        verifier_trace=trace,
    )


def score_scenario(scenario: dict, response: dict) -> ScenarioResult:
    text = content(response).strip()
    trace_channels = content_channels_with_sources(response)
    trace_text = "\n".join(channel_text for _, channel_text in trace_channels).strip()
    for assertion in scenario.get("verifier", {}).get("asserts", []):
        kind = assertion.get("kind")
        if kind == "exact_numeric":
            nums = _numbers(text)
            if not nums:
                return result(scenario, False, "no_answer_found", "no numeric answer found")
            if not any(math.isclose(num, float(assertion["value"]), rel_tol=0, abs_tol=0) for num in nums):
                return result(scenario, False, "wrong_answer", f"expected numeric value {assertion['value']}")
        elif kind == "tolerance_numeric":
            nums = _numbers(text)
            if not nums:
                return result(scenario, False, "no_answer_found", "no numeric answer found")
            target = float(assertion["value"])
            tol = float(assertion["tolerance"])
            if not any(abs(num - target) <= tol for num in nums):
                return result(scenario, False, "wrong_answer", f"expected {target} within {tol}")
        elif kind == "exact_string":
            if any(key in assertion for key in ("canonical_answer", "accepted_answers", "partial_answers", "checkpoints")):
                matcher_kind = (assertion.get("answer_matcher") or {}).get("kind")
                if "answer_matcher" in assertion and matcher_kind not in _ANSWER_MATCHERS:
                    return result(scenario, False, "verifier_fail", f"unknown reason_math answer_matcher: {matcher_kind}")
                answer_points, answer_note = _answer_axis(assertion, text)
                trace_points, trace_note, matched_sources = _trace_axis(assertion, trace_text, trace_channels)
                score = round(100 * (0.7 * (answer_points / 2) + 0.3 * (trace_points / 2)))
                passed = answer_points == 2
                notes = " ".join(part for part in (answer_note, trace_note) if part)
                trace = {
                    "upstream_style_score": score,
                    "answer_axis_points": answer_points,
                    "trace_axis_points": trace_points,
                    "trace_sources": matched_sources,
                    "status_threshold": "pass when answer_axis_points == 2; trace axis is diagnostic",
                    "notes": notes or None,
                }
                return _scored_result(
                    scenario,
                    passed,
                    "passed" if passed else "wrong_answer",
                    f"Answer axis {answer_points}/2, trace axis {trace_points}/2 ({score}%). {notes}".strip(),
                    trace,
                )

            value = assertion["value"].strip()
            # Strict match (original behaviour) — unchanged, never regresses.
            if value in text or text.strip() == value:
                continue
            # Lenient fallback: the pack's format contract makes a single-value
            # answer a bare value (``key=value`` is only required for multi-value
            # answers), so match each expected value *key-agnostically* within
            # the final ANSWER line. Accepts key synonyms (avg_speed vs
            # average_speed), bare values, and whitespace variance — without
            # ever turning a previously-passing answer into a failure.
            region = _norm(_answer_region(text))
            expected_values = [
                _norm(part.split("=", 1)[1] if "=" in part else part)
                for part in value.split(";")
            ]
            if not (expected_values and all(ev and ev in region for ev in expected_values)):
                return result(scenario, False, "wrong_answer", f"expected string {value!r}")
        elif kind == "regex_match":
            if not re.search(assertion["pattern"], text, re.IGNORECASE | re.MULTILINE):
                return result(scenario, False, "wrong_answer", "regex did not match answer")
        else:
            return result(scenario, False, "verifier_fail", f"unknown reason_math assertion: {kind}")
    return result(scenario, True, "passed", "all reasoning/math assertions passed")
