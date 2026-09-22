"""A small parser for Mermaid flowchart source: node ids, labels and labelled edges.

Written for the SO-11 deviation (#143): judging a flowchart against its prompt
needs the graph, not one textual rendering of it. Covers the flowchart syntax
models actually emit — `flowchart`/`graph` headers, the common node shapes
(including quoted labels), `-->`/`---`/`-.->`/`==>` links, both edge-label forms
(`A -- text --> B` and `A -->|text| B`), chains (`A --> B --> C`), `&` fan-out,
`;` statement separators, and `%%` comments. Styling, `click`, `class`,
`subgraph` headers and `end` are skipped; they carry no control flow.

Anything it cannot read makes the diagram unparseable rather than guessed at.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

HEADER_RE = re.compile(r"(?:flowchart|graph)(?:[ \t]+(?:TB|TD|BT|RL|LR))?\Z")

# Longest openers first so `((` wins over `(`.
_SHAPES = [
    ("(((", ")))"),
    ("((", "))"),
    ("([", "])"),
    ("[[", "]]"),
    ("[(", ")]"),
    ("[/", "/]"),
    ("[/", "\\]"),
    ("[\\", "\\]"),
    ("[\\", "/]"),
    ("{{", "}}"),
    ("[", "]"),
    ("(", ")"),
    ("{", "}"),
    (">", "]"),
]
_ID_RE = re.compile(r"[A-Za-z0-9_][\w]*")
# `A -- text --> B`, `A == text ==> B`, `A -. text .-> B`
_TEXT_LINK_RE = re.compile(r"(?:--(?![->])|==(?![=>])|-\.(?![.-]))\s*(?P<text>.+?)\s*(?:-{2,}[>xo]?|={2,}[>xo]?|\.-+[>xo]?)")
# `-->`, `---`, `-.->`, `==>`, `--x`, `<-->`, optionally followed by `|text|`
_ARROW_RE = re.compile(r"[<xo]?(?:-{2,}|={2,}|-\.+-)[>xo]?(?:\s*\|(?P<text>[^|]*)\|)?")
_SKIP_RE = re.compile(r"(?:classDef|class|style|linkStyle|click|subgraph|end|direction)\b")
_TAG_RE = re.compile(r"<[^>]*>")
_WS_RE = re.compile(r"\s*")
_AMP_RE = re.compile(r"\s*&\s*")


def normalize_label(text: str) -> str:
    """Lowercased, entity-decoded, tag-free label with collapsed whitespace."""
    text = html.unescape(text.replace("#quot;", '"'))
    text = _TAG_RE.sub(" ", text)
    text = text.strip().strip('"`').strip()
    return " ".join(text.lower().split())


@dataclass
class Flowchart:
    labels: dict[str, str] = field(default_factory=dict)
    edges: list[tuple[str, str, str]] = field(default_factory=list)  # (src, dst, normalized label)

    def label(self, node: str) -> str:
        return self.labels.get(node) or normalize_label(node)

    @property
    def nodes(self) -> set[str]:
        found = set(self.labels)
        for src, dst, _ in self.edges:
            found.update((src, dst))
        return found


class _Unparseable(Exception):
    pass


def _statements(body: str) -> list[str]:
    """Split on newlines and on `;` outside brackets and quotes."""
    out: list[str] = []
    for line in body.split("\n"):
        depth, quoted, start = 0, False, 0
        for index, char in enumerate(line):
            if char == '"':
                quoted = not quoted
            elif not quoted and char in "[({":
                depth += 1
            elif not quoted and char in "])}":
                depth = max(0, depth - 1)
            elif char == ";" and not quoted and depth == 0:
                out.append(line[start:index])
                start = index + 1
        out.append(line[start:])
    return [stmt.strip() for stmt in out if stmt.strip()]


def _read_node(stmt: str, pos: int, chart: Flowchart) -> tuple[str, int]:
    match = _ID_RE.match(stmt, pos)
    if not match:
        raise _Unparseable(f"expected a node id at {stmt[pos:pos + 20]!r}")
    node, pos = match.group(0), match.end()
    for opener, closer in _SHAPES:
        if not stmt.startswith(opener, pos):
            continue
        body_start = pos + len(opener)
        if stmt.startswith('"', body_start):
            end_quote = stmt.find('"', body_start + 1)
            if end_quote == -1 or not stmt.startswith(closer, end_quote + 1):
                continue
            text, pos = stmt[body_start + 1:end_quote], end_quote + 1 + len(closer)
        else:
            end = stmt.find(closer, body_start)
            if end == -1:
                continue
            text, pos = stmt[body_start:end], end + len(closer)
        label = normalize_label(text)
        if label and not chart.labels.get(node):
            chart.labels[node] = label
        break
    return node, pos


def _read_group(stmt: str, pos: int, chart: Flowchart) -> tuple[list[str], int]:
    nodes = []
    while True:
        node, pos = _read_node(stmt, pos, chart)
        nodes.append(node)
        after = _AMP_RE.match(stmt, pos)
        if not after:
            return nodes, pos
        pos = after.end()


def _read_link(stmt: str, pos: int) -> tuple[str, int] | None:
    pos = _WS_RE.match(stmt, pos).end()  # type: ignore[union-attr]
    for pattern in (_TEXT_LINK_RE, _ARROW_RE):
        match = pattern.match(stmt, pos)
        if match:
            return normalize_label(match.group("text") or ""), match.end()
    return None


def parse(source: str) -> Flowchart | None:
    """Parse a flowchart whose first non-comment line is its header, or return None."""
    lines = source.split("\n")
    index = 0
    while index < len(lines) and (not lines[index].strip() or lines[index].strip().startswith("%%")):
        index += 1
    if index == len(lines):
        return None
    # The header may be followed by `;` and statements on the same line.
    header, *rest = _statements(lines[index]) or [""]
    if not HEADER_RE.match(header):
        return None
    chart = Flowchart()
    try:
        for stmt in [*rest, *_statements("\n".join(lines[index + 1:]))]:
            if stmt.startswith("%%") or _SKIP_RE.match(stmt):
                continue
            sources, pos = _read_group(stmt, 0, chart)
            while pos < len(stmt):
                link = _read_link(stmt, pos)
                if link is None:
                    raise _Unparseable(f"expected a link at {stmt[pos:pos + 20]!r}")
                text, pos = link
                targets, pos = _read_group(stmt, _WS_RE.match(stmt, pos).end(), chart)  # type: ignore[union-attr]
                chart.edges.extend((src, dst, text) for src in sources for dst in targets)
                sources = targets
                pos = _WS_RE.match(stmt, pos).end()  # type: ignore[union-attr]
    except _Unparseable:
        return None
    return chart if chart.edges else None
