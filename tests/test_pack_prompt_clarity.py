"""Prompt-clarity guards for the v1.1.0 pack fixes.

Follow-up to the 2026-08-12 prompt/verifier alignment audit (see
docs/PROMPT_VERIFIER_AUDIT.md) and the #123/#124/#127 thread:

- every DataExtract scenario must declare a JSON type for every field
  its verifier grades, so the pack's numeric-strip rule can never
  contradict an expected string value again
- the six fields whose expected values keep units/percent signs must be
  declared ``string`` (the regression that motivated the fix)
- StructOutput SO-07 must name the required ``user`` wrapper in the
  prompt, not only in the schema
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PACKS_DIR = Path(__file__).resolve().parents[1] / "benchlocal_cli" / "packs"

STRING_FIELDS_KEEPING_UNITS = {
    ("DE-02", "tax_rate"),
    ("DE-02", "card_last_four"),
    ("DE-06", "medication_duration"),
    ("DE-10", "visit_duration"),
    ("DE-12", "display_size"),
    ("DE-12", "storage"),
}


def _pack_records(pack: str) -> list[dict]:
    path = PACKS_DIR / pack
    return [json.loads(line) for line in path.read_text().splitlines()]


def _scenarios(pack: str) -> list[dict]:
    return [record for record in _pack_records(pack) if not record.get("__meta__")]


def _meta(pack: str) -> dict:
    return next(record for record in _pack_records(pack) if record.get("__meta__"))


def _declared_types(user_message: str) -> dict[str, str]:
    match = re.search(r"Fields(?: per person)? and JSON types:(.*)", user_message, re.S)
    assert match, "prompt does not declare field JSON types"
    types: dict[str, str] = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        name, _, declared = line[2:].partition(":")
        types[name.strip()] = declared.strip()
    return types


def _graded_fields(scenario: dict) -> set[str]:
    for assertion in scenario.get("verifier", {}).get("asserts", []):
        if assertion.get("kind") == "no_extra_fields":
            return set(assertion["allowed"])
    # DE-07 grades a top-level array of person objects.
    if scenario.get("id") == "DE-07":
        return set(scenario["expected"][0].keys())
    raise AssertionError(f"no graded-field source for {scenario.get('id')}")


def test_dataextract_prompts_declare_a_type_for_every_graded_field():
    for scenario in _scenarios("dataextract-15.jsonl"):
        user = next(m["content"] for m in scenario["messages"] if m["role"] == "user")
        declared = _declared_types(user)
        graded = _graded_fields(scenario)
        assert declared, f"{scenario['id']} declares no field types"
        missing = graded - set(declared)
        assert not missing, f"{scenario['id']} leaves fields untyped: {sorted(missing)}"
        for name, declared_type in declared.items():
            assert declared_type, f"{scenario['id']} field {name} has an empty type"


def test_dataextract_unit_keeping_fields_are_declared_string():
    """The numeric-strip rule ('10 hours -> 10') must never contradict an
    expected string value: every field whose expected value keeps units or
    a percent sign is declared a string in the prompt."""
    by_id = {scenario["id"]: scenario for scenario in _scenarios("dataextract-15.jsonl")}
    for scenario_id, field in sorted(STRING_FIELDS_KEEPING_UNITS):
        user = next(m["content"] for m in by_id[scenario_id]["messages"] if m["role"] == "user")
        declared = _declared_types(user)
        assert declared.get(field) == "string", (
            f"{scenario_id}.{field} must be declared string, got {declared.get(field)!r}"
        )


def test_so07_prompt_names_the_user_wrapper():
    scenario = next(s for s in _scenarios("structoutput-15.jsonl") if s["id"] == "SO-07")
    user = next(m["content"] for m in scenario["messages"] if m["role"] == "user")
    assert "two top-level keys" in user
    assert re.search(r"\buser\b.*object", user)
    assert re.search(r"\bmetadata\b.*object", user)


def test_prompt_clarity_fixes_ship_with_bumped_versions():
    assert _meta("dataextract-15.jsonl")["version"] == "1.1.0"
    assert _meta("structoutput-15.jsonl")["version"] == "1.1.0"
