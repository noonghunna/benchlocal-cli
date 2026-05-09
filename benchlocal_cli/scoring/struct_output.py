"""StructOutput scoring — JSON, CSV, Markdown, and minimal YAML checks."""

from __future__ import annotations

import csv
import io
import re
from typing import Any

import jsonschema

from benchlocal_cli.scoring.common import (
    content,
    get_path,
    parse_json_text,
    result,
    strip_code_fence,
)
from benchlocal_cli.types import ScenarioResult


def _jsonpath(data: Any, path: str) -> Any:
    if not path.startswith("$."):
        raise ValueError("only $. paths are supported")
    return get_path(data, path[2:])


def _minimal_yaml_parse(text: str) -> dict:
    data: dict[str, Any] = {}
    for line in strip_code_fence(text).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"not a key/value YAML line: {line}")
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value.lower() in {"true", "false"}:
            parsed: Any = value.lower() == "true"
        elif value.lower() in {"null", "~"}:
            parsed = None
        elif re.fullmatch(r"-?\d+", value):
            parsed = int(value)
        else:
            parsed = value.strip("'\"")
        if key:
            data[key] = parsed
    if not data:
        raise ValueError("empty YAML document")
    return data


def score_scenario(scenario: dict, response: dict) -> ScenarioResult:
    text = content(response)
    parsed_json: Any = None
    have_json = False
    for assertion in scenario.get("verifier", {}).get("asserts", []):
        kind = assertion.get("kind")
        if kind == "json_parse_required":
            try:
                parsed_json = parse_json_text(text)
                have_json = True
            except Exception as exc:
                return result(scenario, False, "invalid_json", f"JSON parse failed: {exc}")
        elif kind == "json_schema":
            if not have_json:
                try:
                    parsed_json = parse_json_text(text)
                    have_json = True
                except Exception as exc:
                    return result(scenario, False, "invalid_json", f"JSON parse failed: {exc}")
            try:
                jsonschema.validate(parsed_json, assertion["schema"])
            except jsonschema.ValidationError as exc:
                return result(scenario, False, "schema_violation", exc.message)
        elif kind == "yaml_parse_required":
            try:
                _minimal_yaml_parse(text)
            except Exception as exc:
                return result(scenario, False, "wrong_structure", f"YAML parse failed: {exc}")
        elif kind == "exact_json":
            if not have_json:
                try:
                    parsed_json = parse_json_text(text)
                    have_json = True
                except Exception as exc:
                    return result(scenario, False, "invalid_json", f"JSON parse failed: {exc}")
            if parsed_json != assertion["value"]:
                return result(scenario, False, "verifier_fail", "JSON value mismatch")
        elif kind == "jsonpath_assertion":
            if not have_json:
                try:
                    parsed_json = parse_json_text(text)
                    have_json = True
                except Exception as exc:
                    return result(scenario, False, "invalid_json", f"JSON parse failed: {exc}")
            try:
                value = _jsonpath(parsed_json, assertion["path"])
            except Exception as exc:
                return result(scenario, False, "missing_field", f"jsonpath not found: {exc}")
            if "value" in assertion and value != assertion["value"]:
                return result(scenario, False, "verifier_fail", "jsonpath value mismatch")
            if "regex" in assertion and not re.search(assertion["regex"], str(value)):
                return result(scenario, False, "verifier_fail", "jsonpath regex mismatch")
        elif kind == "csv_columns":
            try:
                reader = csv.reader(io.StringIO(strip_code_fence(text)))
                columns = next(reader)
            except Exception as exc:
                return result(scenario, False, "wrong_structure", f"CSV parse failed: {exc}")
            if [column.strip() for column in columns] != assertion["expected"]:
                return result(scenario, False, "wrong_structure", "CSV columns mismatch")
        elif kind == "markdown_structure":
            lines = [line.strip() for line in text.splitlines()]
            positions = []
            for header in assertion["headers"]:
                try:
                    positions.append(lines.index(header))
                except ValueError:
                    return result(scenario, False, "wrong_structure", f"missing markdown header {header}")
            if positions != sorted(positions):
                return result(scenario, False, "wrong_structure", "markdown headers out of order")
        elif kind == "format_regex":
            if not re.search(assertion["pattern"], text, re.MULTILINE | re.DOTALL):
                return result(scenario, False, "wrong_structure", "format regex did not match")
        else:
            return result(scenario, False, "verifier_fail", f"unknown struct_output assertion: {kind}")
    return result(scenario, True, "passed", "all structured-output assertions passed")
