"""ReasonMath scoring with answer-line and numeric checks."""

from __future__ import annotations

import math
import re

from benchlocal_cli.scoring.common import content, result
from benchlocal_cli.types import ScenarioResult


def _numbers(text: str) -> list[float]:
    return [float(match.replace(",", "")) for match in re.findall(r"-?\d+(?:,\d{3})*(?:\.\d+)?", text)]


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
            if value not in text and text.strip() != value:
                return result(scenario, False, "wrong_answer", f"expected string {value!r}")
        elif kind == "regex_match":
            if not re.search(assertion["pattern"], text, re.IGNORECASE | re.MULTILINE):
                return result(scenario, False, "wrong_answer", "regex did not match answer")
        else:
            return result(scenario, False, "verifier_fail", f"unknown reason_math assertion: {kind}")
    return result(scenario, True, "passed", "all reasoning/math assertions passed")
