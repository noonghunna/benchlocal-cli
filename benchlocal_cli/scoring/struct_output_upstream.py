"""Ports of upstream StructOutput-15 validators (vendor/StructOutput-15/verification/core.mjs).

#143: seven scenarios had no scenario-specific check in the generated pack, so
their verifier was ``format_regex: .+`` and any non-empty answer passed. Upstream
grades them with deterministic validators that fill three axes (parseable,
correctness, discipline, each 0-2); lib/benchmark.ts then scores
``round((p*0.4 + c*0.35 + d*0.25) / 2 * 100)`` and calls >= 85 a pass. These are
line-for-line ports; parity with the vendored JavaScript is asserted by
tests/test_upstream_verifier_parity.py. One deliberate deviation: SO-11 is judged
against its prompt rather than one textual rendering (see _SO11_STEPS).

The validators run their regexes on the normalized answer itself, NOT on the
contents of a code fence: a fenced answer only earns discipline 1 here, and the
formats anchored at the start (XML, Mermaid) are not parseable when fenced.
That is upstream's contract, reproduced as is.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from benchlocal_cli.scoring import mermaid_flowchart
from benchlocal_cli.scoring._js import DOT, S, js_round, normalize_line_endings, trim

PASS_THRESHOLD = 85


@dataclass(frozen=True)
class Axes:
    parseable: int
    correctness: int
    discipline: int


@dataclass(frozen=True)
class Evaluation:
    score: int
    passed: bool
    axes: Axes
    summary: str
    note: str | None


def _normalize(text: str) -> str:
    return trim(normalize_line_endings(text))


_FENCE_RE = re.compile(r"```[a-zA-Z0-9_-]*\n([\s\S]*?)\n```\Z")
_WRAPPER_RE = re.compile(r"(here|sure|below|output:|json:|yaml:|csv:|sql:|xml:|html:)", re.IGNORECASE | re.ASCII)


def _discipline(answer: str) -> tuple[int, str | None]:
    trimmed = _normalize(answer)
    if _FENCE_RE.match(trimmed):
        return 1, "Wrapped in a single markdown fence."
    if _WRAPPER_RE.match(trimmed):
        return 0, "Added wrapper prose or labels."
    return 2, None


def _has(pattern: str, text: str, flags: int = 0) -> bool:
    return re.search(pattern, text, flags | re.ASCII) is not None


def _graded(parseable: bool, correctness: bool) -> tuple[int, int]:
    """The shared axis rule: correct -> 2, parseable but wrong -> 1, else 0."""
    return (2 if parseable else 0), (2 if correctness else 1 if parseable else 0)


def _toml(text: str) -> tuple[int, int, str]:
    parseable = _has(r"\[package\]", text) and _has(r"\[dependencies\]", text)
    correct = parseable and all(
        _has(p, text)
        for p in (
            rf'name{S}*={S}*"my_cli"',
            rf'version{S}*={S}*"0\.1\.0"',
            rf'edition{S}*={S}*"2021"',
            rf'authors{S}*={S}*\["Alice <alice@example\.com>"\]',
            rf'clap{S}*={S}*"4\.5"',
            rf'(version{S}*={S}*"1\.0"[\s\S]*features{S}*={S}*\["derive"\])',
        )
    )
    p, c = _graded(parseable, correct)
    summary = (
        "TOML package metadata and dependencies were valid."
        if correct
        else "TOML sections or dependency definitions were incomplete."
    )
    return p, c, summary


def _sql(text: str) -> tuple[int, int, str]:
    i = re.IGNORECASE
    parseable = _has(r"create table employees", text, i) and _has(r"insert into employees", text, i)
    correct = (
        parseable
        and _has(rf"name{S}+varchar\(100\){S}+not null", text, i)
        and _has(rf"department{S}+varchar\(50\)", text, i)
        and _has(rf"salary{S}+decimal\(10,2\)", text, i)
        and _has(rf"hire_date{S}+date", text, i)
        and all(
            _has(p, text)
            for p in (
                r"'Alice Chen'",
                r"'Engineering'",
                r"95000\.00",
                r"'2023-06-15'",
                r"'Bob Park'",
                r"'Marketing'",
                r"78500\.50",
                r"'2024-01-10'",
            )
        )
        and not _has(rf"insert into employees{S}*\({S}*id", text, i)
    )
    p, c = _graded(parseable, correct)
    summary = (
        "SQL contained the expected table and two inserts."
        if correct
        else "SQL structure or inserted values were incomplete."
    )
    return p, c, summary


def _ics(text: str) -> tuple[int, int, str]:
    parseable = all(_has(p, text) for p in (r"BEGIN:VCALENDAR", r"BEGIN:VEVENT", r"END:VEVENT", r"END:VCALENDAR"))
    correct = (
        parseable
        and all(_has(p, text) for p in (r"VERSION:2\.0", r"PRODID:", r"UID:", r"DTSTAMP:"))
        and (_has(r"DTSTART:20260415T180000Z", text) or _has(r"DTSTART;TZID=America/New_York:20260415T140000", text))
        and (
            _has(r"DTEND:20260415T193000Z", text)
            or _has(r"DTEND;TZID=America/New_York:20260415T153000", text)
            or _has(r"DURATION:PT90M", text)
        )
        and _has(r"SUMMARY:Q2 Planning Session", text)
        and _has(r"LOCATION:Conference Room B", text)
        and _has(r"DESCRIPTION:Quarterly planning meeting - bring your project updates", text)
        and (
            _has(r"ORGANIZER:mailto:alice@company\.com", text)
            or _has(rf"ORGANIZER;CN={DOT}*:mailto:alice@company\.com", text)
        )
    )
    p, c = _graded(parseable, correct)
    summary = (
        "ICS event included the required calendar properties."
        if correct
        else "ICS structure or event properties were incomplete."
    )
    return p, c, summary


def _xml(text: str) -> tuple[int, int, str]:
    parseable = re.match(r"<\?xml", text) is not None and _has(r"<catalog[\s\S]*</catalog>\Z", text)
    correct = parseable and all(
        _has(p, text)
        for p in (
            r'xmlns="http://example\.com/books"',
            r'version="2\.0"',
            r'<book id="bk101" lang="en">',
            r"<title>Rust Programming</title>",
            r'<price currency="USD">39\.99</price>',
            r'<book id="bk102" lang="ja">',
            r"<title>プログラミングRust</title>",
            r'<price currency="JPY">4500</price>',
        )
    )
    p, c = _graded(parseable, correct)
    summary = (
        "XML document matched the requested namespace and book data."
        if correct
        else "XML document was incomplete or missed required attributes."
    )
    return p, c, summary


# Deliberate deviation (#143): SO-11 judged against its prompt, not one rendering.
# Prompt: "Generate a Mermaid flowchart for this process: User submits a form.
# System validates the input. If valid, save to database and send confirmation
# email, then show success page. If invalid, show error message and return to
# form." Upstream accepts only `^flowchart TD` and edges spelled `C -- Yes -->`
# or `B -->|Valid|` with those exact node ids, so `graph TD`, any other
# direction, `C -->|Yes| D`, or a decision node named anything but C/B fails —
# 0 of 76 distinct saved model answers passed it. This parses the diagram and
# checks the process the prompt describes. Code fences stay unparseable exactly
# as upstream: the system prompt forbids them.
_SO11_STEPS = {
    "submit": "user submits form",
    "validate": "system validates input",
    "save": "save to database",
    "email": "send confirmation email",
    "success": "show success page",
    "error": "show error message",
}
_ARTICLES = {"a", "an", "the"}
_RETURN_TO_FORM_RE = re.compile(r"\b(?:returns?|back) to form\b")


def _phrase(label: str) -> str:
    """Label as space-separated lowercase words, articles dropped."""
    return " ".join(word for word in re.findall(r"[a-z0-9]+", label.lower()) if word not in _ARTICLES)


def _mentions(label: str, phrase: str) -> bool:
    return f" {phrase} " in f" {_phrase(label)} "


def _branch_polarity(label: str) -> str | None:
    """'yes'/'valid' edges are the valid branch, 'no'/'invalid'/'not ...' the invalid one."""
    words = set(re.findall(r"[a-z]+", label.lower()))
    if words & {"no", "invalid"} or "not" in words:
        return "invalid"
    if words & {"yes", "valid"}:
        return "valid"
    return None


def _reach(chart: mermaid_flowchart.Flowchart, start: str, blocked: set[str]) -> set[str]:
    """Nodes reachable from `start` along edges, never entering `blocked`."""
    if start in blocked:
        return set()
    seen, frontier = {start}, [start]
    while frontier:
        node = frontier.pop()
        for src, dst, _ in chart.edges:
            if src == node and dst not in seen and dst not in blocked:
                seen.add(dst)
                frontier.append(dst)
    return seen


def _so11_process_ok(chart: mermaid_flowchart.Flowchart) -> bool:
    step_nodes = {
        step: {node for node in chart.nodes if _mentions(chart.label(node), phrase)}
        for step, phrase in _SO11_STEPS.items()
    }
    edge_labels = [label for _, _, label in chart.edges]
    if any(not nodes and not any(_mentions(label, _SO11_STEPS[step]) for label in edge_labels)
           for step, nodes in step_nodes.items()):
        return False  # every step of the process must appear
    returns_by_text = any(
        _RETURN_TO_FORM_RE.search(_phrase(text)) for text in [*map(chart.label, chart.nodes), *edge_labels]
    )
    for decision in chart.nodes:
        outgoing = [(dst, _branch_polarity(label)) for src, dst, label in chart.edges if src == decision]
        valid_targets = [dst for dst, polarity in outgoing if polarity == "valid"]
        invalid_targets = [dst for dst, polarity in outgoing if polarity == "invalid"]
        if not valid_targets or not invalid_targets:
            continue  # the decision is the node that branches both ways
        # The input is validated before the decision is taken.
        if not any(decision in _reach(chart, node, set()) for node in step_nodes["validate"]):
            continue
        for valid_target in valid_targets:
            valid_reach = _reach(chart, valid_target, {decision})
            if not all(step_nodes[step] & valid_reach for step in ("save", "email", "success")):
                continue
            for invalid_target in invalid_targets:
                invalid_reach = _reach(chart, invalid_target, {decision})
                if not step_nodes["error"] & invalid_reach:
                    continue
                if (step_nodes["save"] | step_nodes["email"] | step_nodes["success"]) & invalid_reach:
                    continue  # invalid input must not be saved or confirmed
                # "return to form": said in a label, or the invalid branch leads
                # back to where the user submits the form.
                if returns_by_text or step_nodes["submit"] & invalid_reach:
                    return True
    return False


def _mermaid(text: str) -> tuple[int, int, str]:
    chart = mermaid_flowchart.parse(text)
    correct = chart is not None and _so11_process_ok(chart)
    p, c = _graded(chart is not None, correct)
    summary = (
        "Mermaid flowchart captured the required branches."
        if correct
        else "Mermaid syntax or branch coverage was incomplete."
    )
    return p, c, summary


def _html(text: str) -> tuple[int, int, str]:
    parseable = _has(r"<table>[\s\S]*</table>", text)
    correct = (
        parseable
        and _has(r"<caption>2025 Quarterly Revenue</caption>", text)
        and _has(r"<thead>[\s\S]*<th>Quarter</th>[\s\S]*<th>Revenue</th>[\s\S]*<th>Growth</th>[\s\S]*</thead>", text)
        and _has(
            r"<tbody>[\s\S]*<td>Q1</td>[\s\S]*<td>\$1\.2M</td>[\s\S]*<td>\+5%</td>[\s\S]*<td>Q4</td>"
            r"[\s\S]*<td>\$1\.8M</td>[\s\S]*<td>\+63\.6%</td>[\s\S]*</tbody>",
            text,
        )
    )
    p, c = _graded(parseable, correct)
    summary = (
        "HTML table used the semantic wrappers and data cells correctly."
        if correct
        else "HTML table was missing semantic tags or expected values."
    )
    return p, c, summary


def _bson_boundary(text: str) -> tuple[int, int, str]:
    lowered = text.lower()
    acknowledges_binary = "bson" in lowered and "binary" in lowered
    offers_alternative = "json" in lowered or "code" in lowered
    hallucinates = re.match(r"\{[\s\S]*\}\Z", text) is not None or "this is bson" in lowered
    correctness = 0 if hallucinates else 2 if acknowledges_binary and offers_alternative else 1 if acknowledges_binary else 0
    summary = (
        "Recognized BSON as a binary format and offered a practical text alternative."
        if correctness == 2
        else "Recognized BSON as binary but gave no strong practical alternative."
        if correctness == 1
        else "Did not recognize the text/binary boundary cleanly."
    )
    return 2, correctness, summary


_VALIDATORS: dict[str, Callable[[str], tuple[int, int, str]]] = {
    "SO-04": _toml,
    "SO-05": _sql,
    "SO-06": _ics,
    "SO-09": _xml,
    "SO-11": _mermaid,
    "SO-12": _html,
    "SO-15": _bson_boundary,
}


def _score_axes(axes: Axes) -> int:
    # Same float operations, in the same order, as lib/benchmark.ts scoreAxes.
    weighted = axes.parseable * 0.4 + axes.correctness * 0.35 + axes.discipline * 0.25
    return js_round((weighted / 2) * 100)


def _evaluator(validator: Callable[[str], tuple[int, int, str]]) -> Callable[[str], Evaluation]:
    def evaluate(answer: str) -> Evaluation:
        discipline, note = _discipline(answer)
        parseable, correctness, summary = validator(_normalize(answer))
        axes = Axes(parseable, correctness, discipline)
        score = _score_axes(axes)
        return Evaluation(score=score, passed=score >= PASS_THRESHOLD, axes=axes, summary=summary, note=note)

    return evaluate


EVALUATORS: dict[str, Callable[[str], Evaluation]] = {sid: _evaluator(v) for sid, v in _VALIDATORS.items()}
