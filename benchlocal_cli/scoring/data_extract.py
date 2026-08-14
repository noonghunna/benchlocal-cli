"""DataExtract scoring — field-level checks on JSON output."""

from __future__ import annotations

import json
import re
from typing import Any

from benchlocal_cli.scoring.common import content, get_path, parse_json_text, result
from benchlocal_cli.types import ScenarioResult

# A top-level shape mismatch is BINDING (see `_score_expected`): an object requested
# but an array returned breaks every downstream consumer doing `result["field"]`.
# The other compliance notes (extra / missing top-level fields) stay informational —
# missing fields are already penalised by the field walker, and extra fields do not
# break consumption.
_SHAPE_MISMATCH_PREFIX = "top-level shape mismatch"

_ARRAY_OBJECT_ANCHORS = {
    "DE-02.items": "name",
    "DE-07.$root": "name",
    "DE-13.line_items": "description",
    "DE-13.discounts": "description",
}


def _has_field(data: Any, field: str) -> bool:
    try:
        get_path(data, field)
    except (KeyError, IndexError, TypeError):
        return False
    return True


def _is_plain_object(value: Any) -> bool:
    return isinstance(value, dict)


def _top_level_shape(value: Any) -> str:
    if isinstance(value, list):
        return "array"
    if _is_plain_object(value):
        return "object"
    return "other"


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _json_value(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return repr(value)


def _typed_mismatch(expected_type: str, expected: Any, actual: Any) -> str:
    return (
        f"expected {expected_type} {_json_value(expected)}, "
        f"received {_json_type(actual)} {_json_value(actual)}"
    )


def _compare_scalar(expected: Any, actual: Any) -> tuple[bool, str | None]:
    if expected is None:
        return actual is None, None if actual is None else _typed_mismatch("null", expected, actual)
    if isinstance(expected, str):
        if not isinstance(actual, str):
            return False, _typed_mismatch("string", expected, actual)
        ok = actual.strip() == expected.strip()
        return ok, None if ok else _typed_mismatch("string", expected, actual)
    if isinstance(expected, bool):
        if not isinstance(actual, bool):
            return False, _typed_mismatch("boolean", expected, actual)
        return actual is expected, None if actual is expected else _typed_mismatch("boolean", expected, actual)
    if isinstance(expected, int | float) and not isinstance(expected, bool):
        if not isinstance(actual, int | float) or isinstance(actual, bool):
            return False, _typed_mismatch("number", expected, actual)
        ok = abs(float(actual) - float(expected)) <= 0.01
        return ok, None if ok else _typed_mismatch("number", expected, actual)
    return False, "unsupported scalar type"


def _compare_scalar_array(expected: list[Any], actual: Any) -> tuple[int, int, list[str]]:
    if not isinstance(actual, list):
        return 0, 1, ["expected array"]
    if len(expected) != len(actual):
        return 0, 1, [f"expected {len(expected)} items but received {len(actual)}"]

    remaining = list(actual)
    for expected_item in expected:
        for index, candidate in enumerate(remaining):
            ok, _ = _compare_scalar(expected_item, candidate)
            if ok:
                remaining.pop(index)
                break
        else:
            return 0, 1, ["array values did not match expected set"]
    return 1, 1, []


def _compare_object_array(expected: list[dict[str, Any]], actual: Any, scenario_id: str, path: str) -> tuple[int, int, list[str]]:
    width = len(expected[0]) if expected else 1
    if not isinstance(actual, list):
        return 0, len(expected) * width, ["expected array"]

    anchor = _ARRAY_OBJECT_ANCHORS.get(f"{scenario_id}.{path or '$root'}")
    if not anchor:
        return 0, len(expected) or 1, [f"missing anchor key for {scenario_id}.{path or '$root'}"]

    actual_by_anchor = {
        str(item[anchor]): item
        for item in actual
        if isinstance(item, dict) and isinstance(item.get(anchor), str)
    }

    correct = 0
    total = 0
    notes: list[str] = []
    for expected_item in expected:
        actual_item = actual_by_anchor.get(str(expected_item.get(anchor)))
        for key, expected_value in expected_item.items():
            total += 1
            if actual_item is None:
                notes.append(f"missing object with {anchor}={expected_item.get(anchor)}")
                continue
            sub_correct, sub_total, sub_notes = _compare_value(
                expected_value,
                actual_item.get(key),
                scenario_id,
                f"{path}.{key}" if path else key,
            )
            correct += sub_correct
            total += sub_total - 1
            notes.extend(sub_notes)
    return correct, total, notes


def _compare_object(expected: dict[str, Any], actual: Any, scenario_id: str, path: str = "") -> tuple[int, int, list[str]]:
    if not isinstance(actual, dict):
        return 0, len(expected), ["expected object"]

    correct = 0
    total = 0
    notes: list[str] = []
    for key, expected_value in expected.items():
        nested_path = f"{path}.{key}" if path else key
        sub_correct, sub_total, sub_notes = _compare_value(expected_value, actual.get(key), scenario_id, nested_path)
        correct += sub_correct
        total += sub_total
        notes.extend(sub_notes)
    return correct, total, notes


def _compare_value(expected: Any, actual: Any, scenario_id: str, path: str) -> tuple[int, int, list[str]]:
    if isinstance(expected, list):
        if all(isinstance(item, dict) for item in expected):
            return _compare_object_array(expected, actual, scenario_id, path)
        return _compare_scalar_array(expected, actual)
    if isinstance(expected, dict):
        return _compare_object(expected, actual, scenario_id, path)

    ok, reason = _compare_scalar(expected, actual)
    return (1 if ok else 0), 1, [] if ok else [f"{path}: {reason or 'mismatch'}"]


def _compliance_notes(expected: Any, actual: Any) -> list[str]:
    notes: list[str] = []
    expected_shape = _top_level_shape(expected)
    actual_shape = _top_level_shape(actual)
    if expected_shape != actual_shape:
        notes.append(f"{_SHAPE_MISMATCH_PREFIX}: expected {expected_shape}, received {actual_shape}")

    if isinstance(expected, dict) and isinstance(actual, dict):
        expected_keys = set(expected)
        actual_keys = set(actual)
        extra = sorted(actual_keys - expected_keys)
        missing = sorted(expected_keys - actual_keys)
        if extra:
            notes.append(f"extra top-level fields: {', '.join(extra)}")
        if missing:
            notes.append(f"missing top-level fields: {', '.join(missing)}")
    return notes


def _realign_for_scoring(expected: Any, actual: Any) -> tuple[Any, bool]:
    """Align a singleton container with the expected top-level shape, for SCORING only.

    A model that answers `[{...}]` where an object was requested has made a real
    compliance error, and `_score_expected` still fails it for that. But the field
    walker bails before reading the payload (`_compare_object` returns
    `0, len(expected)` the moment `actual` is not a dict), so the reported score was
    `0/N atomic fields correct (0%)` even when every extracted value was byte-perfect.
    That number is a false statement about the extraction, and it hides genuine
    content regressions behind the shape error — a run can look like a total
    extraction failure when the model is wrapping an otherwise-correct answer.

    Realigning here lets the score describe the CONTENT. The shape verdict is carried
    separately by `_compliance_notes` and is binding, so no scenario's pass/fail
    changes — only its reported percentage becomes honest.

    Only exact singletons are realigned. Two objects where one was requested is
    over-extraction (a genuine content miss, e.g. dataextract-15 DE-04 returning both
    meetings from a thread) and is deliberately left to score as-is. Nested shape
    mismatches are also left alone: this runs once, at the top level.
    """
    if isinstance(expected, dict) and isinstance(actual, list):
        if len(actual) == 1 and isinstance(actual[0], dict):
            return actual[0], True
    if isinstance(expected, list) and isinstance(actual, dict):
        if len(expected) == 1:
            return [actual], True
    return actual, False


def _score_expected(scenario: dict, data: Any, expected: Any) -> ScenarioResult:
    # Compliance is judged on what the model ACTUALLY returned, never the realigned form.
    compliance = _compliance_notes(expected, data)
    scored, realigned = _realign_for_scoring(expected, data)
    correct, total, notes = _compare_value(expected, scored, str(scenario.get("id", "unknown")), "")
    score = round((correct / total) * 100) if total else 0
    shape_ok = not any(note.startswith(_SHAPE_MISMATCH_PREFIX) for note in compliance)
    passed = score >= 85 and shape_ok
    trace = {
        "upstream_style_score": score,
        "correct_fields": correct,
        "total_fields": total,
        "status_threshold": "pass >= 85 and top-level shape matches",
        "compliance_notes": compliance,
        "comparison_notes": notes,
        "scored_after_shape_realign": realigned,
    }
    note_text = " | ".join([*compliance, *notes])
    return ScenarioResult(
        scenario_id=str(scenario.get("id", "unknown")),
        passed=passed,
        failure_mode="passed" if passed else "verifier_fail",  # type: ignore[arg-type]
        detail=f"{correct}/{total} atomic fields correct ({score}%). {note_text}".strip(),
        verifier_trace=trace,
    )


def score_scenario(scenario: dict, response: dict) -> ScenarioResult:
    try:
        data = parse_json_text(content(response))
    except Exception as exc:
        return result(scenario, False, "invalid_json", f"response was not valid JSON: {exc}")

    if "expected" in scenario:
        return _score_expected(scenario, data, scenario["expected"])

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
