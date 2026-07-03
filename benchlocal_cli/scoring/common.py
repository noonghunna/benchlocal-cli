"""Small response parsing helpers shared by deterministic scorers."""

from __future__ import annotations

import json
import re
from typing import Any

from benchlocal_cli.types import ScenarioResult


_REASONING_TEXT_FIELDS = {"content", "reasoning_content", "reasoning"}
_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.IGNORECASE | re.DOTALL)
_THINK_TAG_RE = re.compile(r"</?think\b[^>]*>", re.IGNORECASE)


def sanitize_reasoning_tags(text: str) -> str:
    """Strip leaked reasoning tags while leaving clean text byte-identical."""
    cleaned = _THINK_BLOCK_RE.sub("", text)
    cleaned = _THINK_TAG_RE.sub("", cleaned)
    return text if cleaned == text else cleaned.strip()


def sanitize_response_text_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: sanitize_reasoning_tags(item)
            if key in _REASONING_TEXT_FIELDS and isinstance(item, str)
            else sanitize_response_text_fields(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_response_text_fields(item) for item in value]
    return value


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


def content_with_source(response: dict) -> tuple[str, str | None]:
    for field in ("content", "reasoning_content", "reasoning"):
        value = response.get(field)
        if isinstance(value, str) and value:
            return sanitize_reasoning_tags(value), field
    msg = message(response)
    for field in ("content", "reasoning_content", "reasoning"):
        value = msg.get(field)
        if isinstance(value, str) and value:
            return sanitize_reasoning_tags(value), f"message.{field}"
    return "", None


def content_channels_with_sources(response: dict) -> list[tuple[str, str]]:
    channels: list[tuple[str, str]] = []
    for field in ("content", "reasoning_content", "reasoning"):
        value = response.get(field)
        if isinstance(value, str) and value:
            channels.append((field, sanitize_reasoning_tags(value)))
    msg = message(response)
    for field in ("content", "reasoning_content", "reasoning"):
        value = msg.get(field)
        if isinstance(value, str) and value:
            channels.append((f"message.{field}", sanitize_reasoning_tags(value)))
    return channels


def combined_content_with_sources(response: dict) -> tuple[str, list[str]]:
    channels = content_channels_with_sources(response)
    return "\n".join(text for _, text in channels), [source for source, _ in channels]


def content(response: dict) -> str:
    return content_with_source(response)[0]


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
