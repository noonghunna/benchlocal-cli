"""Deterministic tool-call assertion scorer."""

from __future__ import annotations

import json
import re
from typing import Any

from benchlocal_cli.scoring.common import message, result
from benchlocal_cli.types import ScenarioResult


def _tool_calls(response: dict) -> list[dict]:
    calls = response.get("tool_calls")
    if not isinstance(calls, list):
        calls = message(response).get("tool_calls")
    return calls if isinstance(calls, list) else []


def _name(call: dict) -> str:
    fn = call.get("function") if isinstance(call.get("function"), dict) else {}
    return str(call.get("name") or fn.get("name") or "")


def _args(call: dict) -> dict[str, Any]:
    fn = call.get("function") if isinstance(call.get("function"), dict) else {}
    raw = call.get("arguments", fn.get("arguments", {}))
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        return json.loads(raw or "{}")
    return {}


def score_scenario(scenario: dict, response: dict) -> ScenarioResult:
    calls = _tool_calls(response)
    if not calls and scenario.get("verifier", {}).get("asserts"):
        return result(scenario, False, "wrong_answer", "response did not include tool_calls")

    try:
        args_by_call = [_args(call) for call in calls]
    except json.JSONDecodeError as exc:
        return result(scenario, False, "invalid_json", f"tool arguments were not JSON: {exc}")

    names = [_name(call) for call in calls]
    first_args = args_by_call[0] if args_by_call else {}
    for assertion in scenario.get("verifier", {}).get("asserts", []):
        kind = assertion.get("kind")
        if kind == "exact_function_name":
            expected = assertion["value"]
            if not names or names[0] != expected:
                return result(scenario, False, "verifier_fail", f"expected first tool {expected}, got {names[:1]}")
        elif kind == "function_name_in":
            values = set(assertion["values"])
            if not names or names[0] not in values:
                return result(scenario, False, "verifier_fail", f"first tool {names[:1]} not in {sorted(values)}")
        elif kind == "tool_call_count":
            if len(calls) != assertion["value"]:
                return result(scenario, False, "verifier_fail", f"expected {assertion['value']} tool calls, got {len(calls)}")
        elif kind == "required_args_present":
            missing = [arg for arg in assertion["args"] if arg not in first_args]
            if missing:
                return result(scenario, False, "verifier_fail", f"missing arguments: {', '.join(missing)}")
        elif kind == "forbidden_args_absent":
            present = [arg for arg in assertion["args"] if arg in first_args]
            if present:
                return result(scenario, False, "verifier_fail", f"forbidden arguments present: {', '.join(present)}")
        elif kind == "exact_arg_value":
            if first_args.get(assertion["arg"]) != assertion["value"]:
                return result(scenario, False, "verifier_fail", f"{assertion['arg']} value mismatch")
        elif kind == "arg_regex":
            value = str(first_args.get(assertion["arg"], ""))
            if not re.search(assertion["pattern"], value):
                return result(scenario, False, "verifier_fail", f"{assertion['arg']} did not match regex")
        elif kind == "arg_in_enum":
            if first_args.get(assertion["arg"]) not in assertion["values"]:
                return result(scenario, False, "verifier_fail", f"{assertion['arg']} not in enum")
        elif kind == "arg_numeric_range":
            value = first_args.get(assertion["arg"])
            if not isinstance(value, int | float):
                return result(scenario, False, "verifier_fail", f"{assertion['arg']} was not numeric")
            if ("min" in assertion and value < assertion["min"]) or ("max" in assertion and value > assertion["max"]):
                return result(scenario, False, "verifier_fail", f"{assertion['arg']} out of range")
        elif kind == "multi_call_order":
            expected = assertion["expected_names"]
            if names[: len(expected)] != expected:
                return result(scenario, False, "verifier_fail", f"expected tool order {expected}, got {names}")
        else:
            return result(scenario, False, "verifier_fail", f"unknown tool_call assertion: {kind}")

    return result(scenario, True, "passed", "all tool-call assertions passed")
