"""Scenario selection parsing and validation shared by targeted runs and resume."""

from __future__ import annotations

import difflib
from collections.abc import Iterable
from pathlib import Path

from benchlocal_cli.runner import list_packs, load_pack


def parse_scenarios_file(path: str | Path) -> list[str]:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"--scenarios-file not found: {path}")
    selected: list[str] = []
    for line_number, raw_line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        value = raw_line.split("#", 1)[0].strip()
        if not value:
            continue
        if any(char.isspace() for char in value):
            raise ValueError(
                f"{source}:{line_number}: expected one PACK_ID/SCENARIO_ID per line"
            )
        selected.append(value)
    return selected


def requested_ids(cli_values: Iterable[str] | None, scenarios_file: str | None) -> list[str]:
    values = list(cli_values or [])
    if scenarios_file:
        values.extend(parse_scenarios_file(scenarios_file))
    return list(dict.fromkeys(values))


def scenario_catalog() -> tuple[list[str], dict[str, list[str]]]:
    pack_order = [str(meta["pack_id"]) for meta in list_packs()]
    catalog: dict[str, list[str]] = {}
    for pack_id in pack_order:
        _meta, scenarios = load_pack(pack_id)
        catalog[pack_id] = [str(scenario["id"]) for scenario in scenarios]
    return pack_order, catalog


def validate_selection(values: Iterable[str]) -> tuple[list[str], dict[str, list[str]]]:
    """Return canonical qualified IDs and a pack-indexed selection."""
    requested = list(dict.fromkeys(values))
    if not requested:
        return [], {}

    pack_order, catalog = scenario_catalog()
    qualified = [
        f"{pack_id}/{scenario_id}"
        for pack_id in pack_order
        for scenario_id in catalog[pack_id]
    ]
    known = set(qualified)
    unknown = [value for value in requested if value not in known]
    if unknown:
        details: list[str] = []
        for value in unknown:
            matches = difflib.get_close_matches(value, qualified, n=3, cutoff=0.45)
            suffix = f"; near matches: {', '.join(matches)}" if matches else ""
            details.append(f"{value!r}{suffix}")
        raise ValueError("unknown scenario selection: " + "; ".join(details))

    requested_set = set(requested)
    canonical = [value for value in qualified if value in requested_set]
    by_pack: dict[str, list[str]] = {}
    for value in canonical:
        pack_id, scenario_id = value.split("/", 1)
        by_pack.setdefault(pack_id, []).append(scenario_id)
    return canonical, by_pack


def intersect_selection(
    canonical: list[str],
    by_pack: dict[str, list[str]],
    allowed_pack_ids: Iterable[str] | None,
) -> tuple[list[str], dict[str, list[str]]]:
    if allowed_pack_ids is None:
        return canonical, by_pack
    allowed = set(allowed_pack_ids)
    filtered = [value for value in canonical if value.split("/", 1)[0] in allowed]
    filtered_by_pack = {
        pack_id: ids for pack_id, ids in by_pack.items() if pack_id in allowed
    }
    if not filtered:
        raise ValueError(
            "scenario selection is empty after intersecting with the requested pack set"
        )
    return filtered, filtered_by_pack
