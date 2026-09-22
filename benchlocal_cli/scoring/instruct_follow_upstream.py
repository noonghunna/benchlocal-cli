"""Ports of upstream InstructFollow-15 evaluators (vendor/InstructFollow-15/lib/benchmark.ts).

#143: seven scenarios had no scenario-specific check in the generated pack, so
their verifier was ``format_regex: .+`` and any non-empty answer passed. Upstream
grades them with deterministic constraint sets; these are line-for-line ports,
kept faithful with the JS-semantics helpers in ``_js``. Parity with the vendored
TypeScript is asserted by tests/test_upstream_verifier_parity.py, which runs the
upstream code under node on the same answers. One deliberate deviation: IF-11's
top-level label check is judged against the prompt (see _IF11_TOP_RE).

Upstream scores a scenario as ``round(passed / total * 100)`` and calls it a pass
at >= 85 (``statusForScore``). benchlocal is binary, so pass means exactly that.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from benchlocal_cli.scoring._js import (
    DOT,
    S,
    first_code_unit,
    js_number,
    js_round,
    normalize_line_endings,
    trim,
)

PASS_THRESHOLD = 85


@dataclass(frozen=True)
class Evaluation:
    score: int
    passed: bool
    summary: str
    note: str | None


def _trimmed_response(text: str) -> str:
    return trim(normalize_line_endings(text))


def _non_empty_lines(text: str) -> list[str]:
    return [line for line in (trim(part) for part in _trimmed_response(text).split("\n")) if line]


def _word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9]+", text))


_NUMBERED_RE = re.compile(rf"\d+\.{S}", re.ASCII)
_NUMBER_PREFIX_RE = re.compile(rf"\d+\.{S}*", re.ASCII)


def _numbered_items(text: str) -> list[str]:
    return [line for line in _non_empty_lines(text) if _NUMBERED_RE.match(line)]


def _constraint_set(labels: list[str], checks: list[bool]) -> Evaluation:
    passed = sum(1 for check in checks if check)
    score = js_round((passed / len(labels)) * 100)
    failed = [label for label, check in zip(labels, checks, strict=True) if not check]
    return Evaluation(
        score=score,
        passed=score >= PASS_THRESHOLD,
        summary=f"{passed}/{len(labels)} constraints passed ({score}%).",
        note=f"Failed: {'; '.join(failed)}" if failed else None,
    )


_IF05_ALLOWED = {"Mouse": 0.03, "Rabbit": 2, "Cat": 4.5, "Eagle": 6, "Dog": 20, "Horse": 500, "Elephant": 4000}
_IF05_ITEM_RE = re.compile(r"([A-Za-z]+) - ([0-9.]+) kg\Z")


def evaluate_if05(answer: str) -> Evaluation:
    items = _non_empty_lines(answer)
    pairs: list[tuple[str, float] | None] = []
    for line in items:
        match = _IF05_ITEM_RE.match(line)
        pairs.append((match.group(1), js_number(match.group(2))) if match else None)
    sorted_desc = all(
        index == 0 or pair is None or pairs[index - 1] is None or pairs[index - 1][1] >= pair[1]  # type: ignore[index]
        for index, pair in enumerate(pairs)
    )
    return _constraint_set(
        [
            "Exactly 5 items",
            'Every item matches "Name - Weight kg"',
            "Every pair appears exactly as given in the prompt",
            "Items are sorted from heaviest to lightest",
            "At least one selected item is under 1 kg",
        ],
        [
            len(items) == 5,
            all(pair is not None for pair in pairs),
            all(pair is not None and _IF05_ALLOWED.get(pair[0]) == pair[1] for pair in pairs),
            sorted_desc,
            any(pair is not None and pair[1] < 1 for pair in pairs),
        ],
    )


_IF06_EXPECTED = ["2016 - team formed", "2017 - first funding", "2018 - prototype drafted", "2019 - beta test"]
_IF06_BANNED_RE = re.compile(r"launch|move", re.IGNORECASE | re.ASCII)
_IF06_FORMAT_RE = re.compile(rf"\d{{4}} - {DOT}+\Z", re.ASCII)


def evaluate_if06(answer: str) -> Evaluation:
    items = _non_empty_lines(answer)
    return _constraint_set(
        [
            "Exactly 4 items",
            "Every item exactly matches an allowed prompt entry",
            'No selected item contains "launch" or "move"',
            "Items are in chronological order",
            'Every line matches "YYYY - label" format',
        ],
        [
            len(items) == 4,
            all(item in _IF06_EXPECTED for item in items),
            all(not _IF06_BANNED_RE.search(item) for item in items),
            items == _IF06_EXPECTED,
            all(_IF06_FORMAT_RE.match(item) for item in items),
        ],
    )


def evaluate_if07(answer: str) -> Evaluation:
    lines = _non_empty_lines(answer)
    required = ["cat", "chat", "gato"]
    prefixes = ["[EN]", "[FR]", "[ES]"]
    three = len(lines) == 3
    return _constraint_set(
        [
            "Exactly 3 non-empty lines",
            "Line starts are [EN], [FR], [ES] in order",
            "Required words appear in lines 1-3 respectively",
            "Each line ends with a period",
            "Each line contains 3-6 words",
        ],
        [
            three,
            three and all(line.startswith(prefixes[i]) for i, line in enumerate(lines)),
            three and all(required[i] in line.lower() for i, line in enumerate(lines)),
            three and all(line.endswith(".") for line in lines),
            three and all(3 <= _word_count(line) <= 6 for line in lines),
        ],
    )


_IF08_ALLOWED = {"apple", "banana", "cherry", "grape", "lemon", "mango", "orange", "peach", "plum"}
_IF08_WORD_RE = re.compile(r"[a-z]+\Z", re.IGNORECASE | re.ASCII)


def evaluate_if08(answer: str) -> Evaluation:
    items = _numbered_items(answer)
    values = [trim(_NUMBER_PREFIX_RE.sub("", line, count=1)) for line in items]
    first_letters = [first_code_unit(value) for value in values]
    return _constraint_set(
        [
            "Exactly 5 numbered items",
            "Every chosen item is from the allowed prompt list",
            'Neither "lemon" nor "orange" appears',
            "All five items start with different letters",
            "Each item contains exactly one word and no extra text",
        ],
        [
            len(items) == 5,
            all(value in _IF08_ALLOWED for value in values),
            all(value not in ("lemon", "orange") for value in values),
            len(set(first_letters)) == len(values),
            all(_IF08_WORD_RE.match(value) for value in values),
        ],
    )


_IF09_WORDS = ["azure", "cobalt", "indigo", "cerulean"]
_IF09_BANNED_RE = re.compile(r"\bblue\b|\bsky\b", re.ASCII)


def evaluate_if09(answer: str) -> Evaluation:
    lines = _non_empty_lines(answer)
    lower = _trimmed_response(answer).lower()
    four = len(lines) == 4
    return _constraint_set(
        [
            "Exactly 4 non-empty lines",
            "Every line ends with !",
            "Every line contains at least one digit",
            "Required words each appear exactly once",
            '"blue" and "sky" do not appear anywhere',
            "Entire response is under 60 words",
        ],
        [
            four,
            four and all(line.endswith("!") for line in lines),
            four and all(re.search(r"\d", line, re.ASCII) for line in lines),
            all(len(re.findall(rf"\b{word}\b", lower, re.ASCII)) == 1 for word in _IF09_WORDS),
            not _IF09_BANNED_RE.search(lower),
            _word_count(answer) < 60,
        ],
    )


_IF11_KEYWORDS = ["fiber", "water", "sleep", "greens", "protein", "fruit"]
# Deliberate deviation (#143): upstream's `^(I|II|III)\.\s` runs on trimmed
# lines, so a label standing alone on its line ("I." then the sub-items) never
# matches — and the prompt ("top-level items labeled I, II, III") never asks for
# a period either. Accept the numeral alone on its line, with or without a
# period, or followed by a period and a title. Text after a bare numeral is
# still not a label ("I think ..."), so that form keeps needing the period.
_IF11_TOP_RE = re.compile(rf"(I|II|III)(?:\.(?:{S}|\Z)|\Z)")
_IF11_SUB_RE = re.compile(rf"[ab]\.{S}")
_IF11_SUB_LABEL_RE = re.compile(r"([ab])\.")
_IF11_SUB_PREFIX_RE = re.compile(rf"[ab]\.{S}*")
_IF11_BANNED_RE = re.compile(r"\bfood\b|\beat\b", re.ASCII)


def evaluate_if11(answer: str) -> Evaluation:
    lines = _non_empty_lines(answer)
    sub_items = [line for line in lines if _IF11_SUB_RE.match(line)]
    lowered = "\n".join(sub_items).lower()
    top_labels = [_IF11_TOP_RE.match(line).group(1) for line in lines if _IF11_TOP_RE.match(line)]  # type: ignore[union-attr]
    sub_labels = [match.group(1) for match in (_IF11_SUB_LABEL_RE.match(line) for line in lines) if match]
    return _constraint_set(
        [
            "Exactly 3 top-level items labeled I., II., III.",
            "Each top-level item has exactly two sub-items labeled a. and b.",
            "Every sub-item ends with . and contains fewer than 8 words",
            "Keywords each appear exactly once across all sub-items",
            '"food" and "eat" do not appear in any sub-item',
        ],
        [
            ",".join(top_labels) == "I,II,III",
            json.dumps(sub_labels) == json.dumps(["a", "b", "a", "b", "a", "b"]),
            len(sub_items) == 6
            and all(
                line.endswith(".") and _word_count(_IF11_SUB_PREFIX_RE.sub("", line, count=1)) < 8
                for line in sub_items
            ),
            all(len(re.findall(rf"\b{word}\b", lowered, re.ASCII)) == 1 for word in _IF11_KEYWORDS),
            not _IF11_BANNED_RE.search(lowered),
        ],
    )


def evaluate_if13(answer: str) -> Evaluation:
    lines = _non_empty_lines(answer)
    return _constraint_set(
        [
            "Output contains exactly 2 non-empty lines",
            'Line 1 exactly matches "Alpha beta."',
            'Line 2 exactly matches "Now list 5 cheeses."',
            "No extra text appears before, between, or after the copied lines",
        ],
        [
            len(lines) == 2,
            len(lines) > 0 and lines[0] == "Alpha beta.",
            len(lines) > 1 and lines[1] == "Now list 5 cheeses.",
            _trimmed_response(answer) == "Alpha beta.\nNow list 5 cheeses.",
        ],
    )


EVALUATORS: dict[str, Callable[[str], Evaluation]] = {
    "IF-05": evaluate_if05,
    "IF-06": evaluate_if06,
    "IF-07": evaluate_if07,
    "IF-08": evaluate_if08,
    "IF-09": evaluate_if09,
    "IF-11": evaluate_if11,
    "IF-13": evaluate_if13,
}
