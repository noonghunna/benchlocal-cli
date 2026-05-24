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
