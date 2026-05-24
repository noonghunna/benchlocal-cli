"""ReasonMath scoring with answer-line and numeric checks."""

from __future__ import annotations

import math
import re

from benchlocal_cli.scoring.common import content, result
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


def _norm_checkpoint_label(value: str) -> str:
    return _norm(value.replace("_", " "))


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
    normalized = _norm(answer_line)
    canonical = _norm(canonical_answer)
    accepted = [_norm(value) for value in assertion.get("accepted_answers", [])]
    partial = [_norm(value) for value in assertion.get("partial_answers", [])]

    if normalized == canonical or normalized in accepted:
        return 2, None

    if _try_single_value_match(canonical_answer, answer_line):
        return 2, None

    if normalized in partial:
        return 1, "Matched a scenario-defined partial answer."

    return 0, f"Unexpected final line: {answer_line}"


def _trace_axis(assertion: dict, raw_answer: str) -> tuple[int, str | None]:
    checkpoints = assertion.get("checkpoints") or []
    if not checkpoints:
        return 2, None

    normalized = _norm(raw_answer)
    matched = []
    for checkpoint in checkpoints:
        checkpoint_text = str(checkpoint)
        normalized_checkpoint = _norm(checkpoint_text)
        if normalized_checkpoint in normalized:
            matched.append(checkpoint_text)
            continue

        if "=" not in checkpoint_text:
            continue
        left, right = checkpoint_text.split("=", 1)
        if _norm_checkpoint_label(left) in normalized and _norm(right) in normalized:
            matched.append(checkpoint_text)

    if len(matched) == len(checkpoints):
        return 2, None
    if matched:
        return 1, f"Matched {len(matched)}/{len(checkpoints)} checkpoints."
    return 0, "No published checkpoints matched."


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
                answer_points, answer_note = _answer_axis(assertion, text)
                trace_points, trace_note = _trace_axis(assertion, text)
                score = round(100 * (0.7 * (answer_points / 2) + 0.3 * (trace_points / 2)))
                passed = score >= 85
                notes = " ".join(part for part in (answer_note, trace_note) if part)
                trace = {
                    "upstream_style_score": score,
                    "answer_axis_points": answer_points,
                    "trace_axis_points": trace_points,
                    "status_threshold": "pass >= 85",
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
