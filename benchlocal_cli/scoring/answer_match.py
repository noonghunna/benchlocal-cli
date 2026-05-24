"""Small exact-answer scorer for reasoning packs (MCQ letters + numeric math)."""

from __future__ import annotations

import math
import re

from benchlocal_cli.scoring.common import content, result
from benchlocal_cli.types import ScenarioResult


_NUMBER_RE = re.compile(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?")
_FINAL_LINE_RE = re.compile(r"(?:ANSWER|FINAL)\s*[:：]\s*([^\n]+)", re.IGNORECASE)


def _response_text(response: dict) -> str:
    return content(response).strip()


def _numbers(text: str) -> list[float]:
    final = _FINAL_LINE_RE.search(text)
    search_text = final.group(1) if final else text
    return [float(match.group(0).replace(",", "")) for match in _NUMBER_RE.finditer(search_text)]


def _letter(text: str) -> str | None:
    candidates = []
    final = _FINAL_LINE_RE.search(text)
    if final:
        candidates.append(final.group(1))
    candidates.append(text)
    for candidate in candidates:
        m = re.search(r"\b([A-D])\b", candidate.strip(), re.IGNORECASE)
        if m:
            return m.group(1).upper()
    return None


def score_scenario(scenario: dict, response: dict) -> ScenarioResult:
    text = _response_text(response)
    for assertion in scenario.get("verifier", {}).get("asserts", []):
        kind = assertion.get("kind")
        if kind == "exact_letter":
            got = _letter(text)
            expected = str(assertion["value"]).strip().upper()
            if got is None:
                return result(scenario, False, "no_answer_found", "no A/B/C/D answer letter found")
            if got != expected:
                return result(scenario, False, "wrong_answer", f"expected {expected}, got {got}")
        elif kind == "exact_numeric":
            nums = _numbers(text)
            if not nums:
                return result(scenario, False, "no_answer_found", "no numeric answer found")
            target = float(str(assertion["value"]).replace(",", ""))
            if not any(math.isclose(num, target, rel_tol=0, abs_tol=0) for num in nums):
                return result(scenario, False, "wrong_answer", f"expected numeric value {assertion['value']}")
        elif kind == "tolerance_numeric":
            nums = _numbers(text)
            if not nums:
                return result(scenario, False, "no_answer_found", "no numeric answer found")
            target = float(assertion["value"])
            tol = float(assertion["tolerance"])
            if not any(abs(num - target) <= tol for num in nums):
                return result(scenario, False, "wrong_answer", f"expected {target} within {tol}")
        else:
            return result(scenario, False, "verifier_fail", f"unknown answer_match assertion: {kind}")
    return result(scenario, True, "passed", "answer-match assertions passed")
