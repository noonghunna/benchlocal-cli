from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

from benchlocal_cli.runner import _utc_now, load_pack
from benchlocal_cli.scoring.common import content_with_source


def add_rescore_subparser(sub) -> None:
    parser = sub.add_parser(
        "rescore",
        help="re-run deterministic scorers against a saved result JSON",
    )
    parser.add_argument("path", help="saved benchlocal-cli result JSON")
    parser.add_argument("--output", help="write rescored JSON to this path (default: stdout)")
    parser.add_argument("--in-place", action="store_true", help="overwrite the input JSON with rescored results")
    parser.add_argument("--pack", help="only rescore one pack id, e.g. reasonmath-15")


def _score_saved_scenario(pack_meta: dict, scenario_index: dict[str, dict], run: dict) -> tuple[bool, str | None]:
    raw_response = run.get("raw_response")
    if not isinstance(raw_response, dict):
        return False, "missing raw_response"

    scenario_id = str(run.get("id") or "")
    scenario = scenario_index.get(scenario_id) or run.get("raw_scenario")
    if not isinstance(scenario, dict):
        return False, "missing scenario definition"

    verifier_type = scenario.get("verifier", {}).get("type") or pack_meta.get("verifier_module")
    if not verifier_type or verifier_type == "_stub":
        return False, "sandbox/stub scorer not rescored"

    module = importlib.import_module(f"benchlocal_cli.scoring.{verifier_type}")
    result = module.score_scenario(scenario, raw_response)

    previous = run.get("result") if isinstance(run.get("result"), dict) else {}
    result.latency_seconds = float(previous.get("latency_seconds") or run.get("latency_seconds") or 0.0)
    tokens = previous.get("tokens_completion", run.get("tokens_completion"))
    result.tokens_completion = tokens if isinstance(tokens, int) else None

    result_dict = result.to_dict()
    run["result"] = result_dict
    run["passed"] = result.passed
    run["failure_mode"] = result.failure_mode
    run["detail"] = result.detail
    run["latency_seconds"] = result.latency_seconds
    run["tokens_completion"] = result.tokens_completion
    run["verifier_trace"] = result.verifier_trace
    run["response_field_used"] = content_with_source(raw_response)[1]
    return True, None


def _recompute_pack(pack: dict) -> None:
    scenarios = [s for s in pack.get("scenarios", []) if isinstance(s, dict)]
    total = len(scenarios)
    passed = sum(1 for scenario in scenarios if bool(scenario.get("passed")))
    pack["passed"] = passed
    pack["total"] = total
    pack["score"] = passed / total if total else 0.0


def _recompute_totals(data: dict) -> None:
    packs = [p for p in data.get("packs", []) if isinstance(p, dict) and not p.get("skipped")]
    total = sum(int(pack.get("total") or 0) for pack in packs)
    passed = sum(int(pack.get("passed") or 0) for pack in packs)
    data["totals"] = {"passed": passed, "total": total, "score": passed / total if total else 0.0}


def rescore_result(path: str, args: Any) -> int:
    source = Path(path)
    data = json.loads(source.read_text())
    if not isinstance(data, dict):
        raise ValueError("result JSON must be an object")

    rescored = 0
    skipped: dict[str, int] = {}
    for pack in data.get("packs", []):
        if not isinstance(pack, dict):
            continue
        pack_id = str(pack.get("pack_id") or "")
        if args.pack and pack_id != args.pack:
            continue
        try:
            pack_meta, current_scenarios = load_pack(pack_id)
        except Exception:
            pack_meta, current_scenarios = {"verifier_module": None}, []
        if pack_meta.get("supports_sandboxed_only"):
            skipped[pack_id] = len(pack.get("scenarios") or [])
            continue
        scenario_index = {str(scenario.get("id")): scenario for scenario in current_scenarios if isinstance(scenario, dict)}
        for run in pack.get("scenarios", []):
            if not isinstance(run, dict):
                continue
            did_score, reason = _score_saved_scenario(pack_meta, scenario_index, run)
            if did_score:
                rescored += 1
            elif reason:
                skipped[reason] = skipped.get(reason, 0) + 1
        _recompute_pack(pack)

    _recompute_totals(data)
    data["rescored"] = {
        "at": _utc_now(),
        "scenarios": rescored,
        "skipped": skipped,
        "pack_filter": args.pack,
    }

    if args.in_place and args.output:
        raise ValueError("use either --in-place or --output, not both")
    target = source if args.in_place else Path(args.output) if args.output else None
    payload = json.dumps(data, indent=2, sort_keys=True)
    if target:
        target.write_text(payload + "\n")
    else:
        print(payload)
    return 0
