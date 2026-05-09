"""Deterministic constraint checks for free-text instruction following."""

from __future__ import annotations

import re

from benchlocal_cli.scoring.common import content, result
from benchlocal_cli.types import ScenarioResult


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9]+", text)


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.replace("\r\n", "\n").split("\n") if line.strip()]


def score_scenario(scenario: dict, response: dict) -> ScenarioResult:
    text = content(response).strip()
    for assertion in scenario.get("verifier", {}).get("asserts", []):
        kind = assertion.get("kind")
        if kind == "exact_length_words":
            if len(_words(text)) != assertion["value"]:
                return result(scenario, False, "verifier_fail", "word count mismatch")
        elif kind == "max_length_words":
            if len(_words(text)) > assertion["value"]:
                return result(scenario, False, "verifier_fail", "too many words")
        elif kind == "min_length_words":
            if len(_words(text)) < assertion["value"]:
                return result(scenario, False, "verifier_fail", "too few words")
        elif kind == "case_only":
            letters = "".join(re.findall(r"[A-Za-z]+", text))
            value = assertion["value"]
            if value == "lowercase" and letters != letters.lower():
                return result(scenario, False, "verifier_fail", "response was not lowercase")
            if value == "uppercase" and letters != letters.upper():
                return result(scenario, False, "verifier_fail", "response was not uppercase")
            if value == "titlecase" and text != text.title():
                return result(scenario, False, "verifier_fail", "response was not titlecase")
        elif kind == "format_regex":
            if not re.search(assertion["pattern"], text, re.MULTILINE | re.DOTALL):
                return result(scenario, False, "verifier_fail", "format regex did not match")
        elif kind == "required_phrase":
            if assertion["value"] not in text:
                return result(scenario, False, "verifier_fail", f"missing phrase {assertion['value']!r}")
        elif kind == "forbidden_phrase":
            if assertion["value"] in text:
                return result(scenario, False, "verifier_fail", f"forbidden phrase {assertion['value']!r} present")
        elif kind == "required_url_count":
            urls = re.findall(r"https?://\S+", text)
            if len(urls) < assertion["min"]:
                return result(scenario, False, "verifier_fail", "not enough URLs")
        elif kind == "required_section_headers":
            missing = [header for header in assertion["headers"] if header not in _lines(text)]
            if missing:
                return result(scenario, False, "verifier_fail", f"missing headers: {', '.join(missing)}")
        elif kind == "bullet_count":
            bullets = [line for line in _lines(text) if re.match(r"^[-*]\s+", line)]
            if len(bullets) != assertion["value"]:
                return result(scenario, False, "verifier_fail", "bullet count mismatch")
        elif kind == "language":
            if assertion["value"] == "english" and re.search(r"[^\x00-\x7F]", text):
                return result(scenario, False, "verifier_fail", "non-ASCII text found in english response")
        else:
            return result(scenario, False, "verifier_fail", f"unknown instruct_follow assertion: {kind}")
    return result(scenario, True, "passed", "all instruction-following assertions passed")
