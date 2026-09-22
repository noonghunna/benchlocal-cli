"""JavaScript string and regex semantics needed to port upstream verifiers exactly.

The upstream BenchLocal packs grade with JavaScript. Python's defaults differ in
ways that change verdicts on real model output, so the ports use these helpers
instead of the Python builtins:

- JS ``\\s`` and ``String.prototype.trim`` use the ECMAScript WhiteSpace +
  LineTerminator set; Python's ``\\s``/``str.strip`` use a different Unicode set
  (Python adds U+001C-U+001F and U+0085, JS adds U+FEFF).
- JS ``.`` (no ``s`` flag) excludes \\n, \\r, U+2028 and U+2029; Python's only \\n.
- JS ``\\b`` and ``\\d`` are ASCII; pass ``re.ASCII`` for those.
- ``Math.round`` rounds halves up; Python's ``round`` rounds half to even.
- ``Number("1.2.3")`` is NaN rather than an exception.
- ``str[0]`` is the first UTF-16 code unit, not the first code point.
"""

from __future__ import annotations

import math
import re

# ECMAScript WhiteSpace + LineTerminator, as a character-class body.
WS = "\t\n\u000b\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
S = f"[{WS}]"
DOT = "[^\n\r\u2028\u2029]"

_TRIM_RE = re.compile(rf"\A{S}+|{S}+\Z")
_LINE_ENDINGS_RE = re.compile(r"\r\n?")


def trim(text: str) -> str:
    """``String.prototype.trim``."""
    return _TRIM_RE.sub("", text)


def normalize_line_endings(text: str) -> str:
    """``text.replace(/\\r\\n?/g, "\\n")``."""
    return _LINE_ENDINGS_RE.sub("\n", text)


def js_round(value: float) -> int:
    """``Math.round`` — halves round toward +infinity."""
    return math.floor(value + 0.5)


def js_number(text: str) -> float:
    """``Number(text)`` for the decimal strings the verifiers capture."""
    try:
        return float(text)
    except ValueError:
        return math.nan


def first_code_unit(text: str) -> str | None:
    """``text[0]`` — the first UTF-16 code unit, or ``None`` for ``undefined``."""
    if not text:
        return None
    return text.encode("utf-16-le")[:2].decode("utf-16-le", errors="surrogatepass")
