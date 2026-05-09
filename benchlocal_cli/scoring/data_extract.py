"""DataExtract scoring — field-level checks on JSON output."""

from __future__ import annotations

import re
from typing import Any

from benchlocal_cli.scoring.common import content, get_path, parse_json_text, result
from benchlocal_cli.types import ScenarioResult


def _has_field(data: Any, field: str) -> bool:
    try:
        get_path(data, field)
    except (KeyError, IndexError, TypeError):
        return False
    return True


def score_scenario(scenario: dict, response: dict) -> ScenarioResult:
    try:
        data = parse_json_text(content(response))
    except Exception as exc:
        return result(scenario, False, "invalid_json", f"response was not valid JSON: {exc}")

    for assertion in scenario.get("verifier", {}).get("asserts", []):
        kind = assertion.get("kind")
        field = assertion.get("field")
        if kind == "field_required":
            if not _has_field(data, field):
                return result(scenario, False, "missing_field", f"missing field {field}")
        elif kind == "field_exact_value":
            if not _has_field(data, field):
                return result(scenario, False, "missing_field", f"missing field {field}")
            if get_path(data, field) != assertion["value"]:
                return result(scenario, False, "verifier_fail", f"{field} value mismatch")
        elif kind == "field_regex":
            if not _has_field(data, field):
                return result(scenario, False, "missing_field", f"missing field {field}")
            if not re.search(assertion["pattern"], str(get_path(data, field))):
                return result(scenario, False, "verifier_fail", f"{field} regex mismatch")
        elif kind == "field_in_set":
            if not _has_field(data, field):
                return result(scenario, False, "missing_field", f"missing field {field}")
            if get_path(data, field) not in assertion["values"]:
                return result(scenario, False, "verifier_fail", f"{field} not in set")
        elif kind == "no_extra_fields":
            if not isinstance(data, dict):
                return result(scenario, False, "wrong_structure", "top-level JSON was not an object")
            extra = sorted(set(data) - set(assertion["allowed"]))
            if extra:
                return result(scenario, False, "extra_fields", f"extra fields: {', '.join(extra)}")
        else:
            return result(scenario, False, "verifier_fail", f"unknown data_extract assertion: {kind}")
    return result(scenario, True, "passed", "all data extraction assertions passed")
