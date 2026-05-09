"""Small response parsing helpers shared by deterministic scorers."""

from __future__ import annotations

import json
import re
from typing import Any

from benchlocal_cli.types import ScenarioResult


def result(
    scenario: dict,
    passed: bool,
    failure_mode: str,
    detail: str,
) -> ScenarioResult:
    return ScenarioResult(
        scenario_id=str(scenario.get("id", "unknown")),
        passed=passed,
        failure_mode=failure_mode,  # type: ignore[arg-type]
        detail=detail,
    )


def message(response: dict) -> dict:
    if "message" in response and isinstance(response["message"], dict):
        return response["message"]
    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            msg = first.get("message") or first.get("delta")
            if isinstance(msg, dict):
                return msg
    return {}


def content(response: dict) -> str:
    if "content" in response and isinstance(response["content"], str):
        return response["content"]
    msg = message(response)
    value = msg.get("content")
    if isinstance(value, str):
        return value
    return ""


def strip_code_fence(text: str) -> str:
    text = text.strip()
    match = re.fullmatch(r"```(?:[A-Za-z0-9_-]+)?\s*(.*?)\s*```", text, re.DOTALL)
    return match.group(1).strip() if match else text


def parse_json_text(text: str) -> Any:
    return json.loads(strip_code_fence(text))


def get_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if not part:
            continue
        if "[" in part:
            name, rest = part.split("[", 1)
            if name:
                current = current[name]
            for idx in re.findall(r"\[(\d+)\]", "[" + rest):
                current = current[int(idx)]
        else:
            current = current[part]
    return current
