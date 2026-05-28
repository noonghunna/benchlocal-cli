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


def _normalize_markdown_row(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    return [cell.strip().lower() for cell in stripped.strip("|").split("|")]


def _is_markdown_separator(line: str, width: int) -> bool:
    cells = _normalize_markdown_row(line)
    if cells is None or len(cells) != width:
        return False
    return all(re.fullmatch(r":?-+:?", cell.replace(" ", "")) for cell in cells)


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
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            normalized_lines = [_normalize_markdown_row(line) for line in lines]
            positions = []
            for header in assertion["headers"]:
                expected = _normalize_markdown_row(header)
                if expected is None:
                    return result(scenario, False, "wrong_structure", f"invalid markdown header spec {header!r}")
                try:
                    idx = normalized_lines.index(expected)
                except ValueError:
                    return result(scenario, False, "wrong_structure", f"missing markdown header {header}")
                if idx + 1 >= len(lines) or not _is_markdown_separator(lines[idx + 1], len(expected)):
                    return result(scenario, False, "wrong_structure", "missing markdown separator")
                positions.append(idx)
            if positions != sorted(positions):
                return result(scenario, False, "wrong_structure", "markdown headers out of order")
        elif kind == "format_regex":
            if not re.search(assertion["pattern"], text, re.MULTILINE | re.DOTALL):
                return result(scenario, False, "wrong_structure", "format regex did not match")
        else:
            return result(scenario, False, "verifier_fail", f"unknown struct_output assertion: {kind}")
    return result(scenario, True, "passed", "all structured-output assertions passed")
