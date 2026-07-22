"""Per-scenario incremental journal and resume reconstruction."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields
from pathlib import Path
from benchlocal_cli import __version__
from benchlocal_cli.runner import (
    PACK_MODES,
    _latency,
    _repeat_variance,
    _utc_now,
    load_pack,
    resolve_thinking_enabled,
)
from benchlocal_cli.selection import selection_for_packs, validate_selection
from benchlocal_cli.types import PackResult, RunResult, ScenarioResult, ScenarioRun

JOURNAL_VERSION = "1"


def sidecar_path(save_json: str | Path) -> Path:
    return Path(f"{save_json}.partial.jsonl")


def final_path_for(source: str | Path) -> Path:
    value = str(source)
    suffix = ".partial.jsonl"
    return Path(value[: -len(suffix)] if value.endswith(suffix) else value)


class JournalWriter:
    def __init__(self, path: str | Path, run_config: dict, *, append: bool) -> None:
        self.path = Path(path)
        self.run_config = dict(run_config)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not append:
            self.path.write_text("", encoding="utf-8")
        self._pack_meta: dict[str, dict] = {}

    def append(self, run: ScenarioRun, _index: int, _total: int) -> None:
        pack_id = str(run.raw_scenario.get("pack_id") or "")
        if not pack_id:
            raise ValueError(f"scenario {run.id} is missing pack_id")
        if pack_id not in self._pack_meta:
            meta, scenarios = load_pack(pack_id)
            self._pack_meta[pack_id] = {
                "pack_id": pack_id,
                "version": meta["version"],
                "upstream_commit": meta["upstream_commit"],
                "catalog_scenario_count": len(scenarios),
            }
        record = {
            "journal_version": JOURNAL_VERSION,
            "written_at": _utc_now(),
            "run": self.run_config,
            "pack": self._pack_meta[pack_id],
            "scenario": run.to_dict(),
        }
        payload = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)


def _read_journal(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid partial journal line: {exc}") from exc
            if not isinstance(record, dict) or record.get("journal_version") != JOURNAL_VERSION:
                raise ValueError(f"{path}:{line_number}: unsupported partial journal record")
            if not isinstance(record.get("scenario"), dict):
                raise ValueError(f"{path}:{line_number}: journal record has no scored scenario")
            records.append(record)
    if not records:
        raise ValueError(f"partial journal is empty: {path}")
    return records


def _scenario_run(data: dict) -> ScenarioRun:
    result_data = data.get("result") if isinstance(data.get("result"), dict) else data
    result_names = {item.name for item in fields(ScenarioResult)}
    result_values = {key: value for key, value in result_data.items() if key in result_names}
    result_values.setdefault("scenario_id", str(data.get("id") or "?"))
    result_values.setdefault("passed", bool(data.get("passed")))
    result_values.setdefault("failure_mode", str(data.get("failure_mode") or "verifier_fail"))
    result_values.setdefault("detail", str(data.get("detail") or ""))
    result = ScenarioResult(**result_values)

    run_names = {item.name for item in fields(ScenarioRun)}
    run_values = {key: value for key, value in data.items() if key in run_names and key != "result"}
    run_values.setdefault("id", result.scenario_id)
    run_values.setdefault("raw_scenario", {})
    run_values.setdefault("raw_response", None)
    run_values.setdefault("request", {})
    run_values.setdefault("sampling_params", {})
    run_values.setdefault("status_code", None)
    return ScenarioRun(result=result, **run_values)


def _aggregate_pack(
    pack_id: str,
    runs: list[ScenarioRun],
    *,
    scenario_count: int,
    catalog_scenario_count: int | None,
    repeat: int,
    thinking_override: bool | None,
) -> PackResult:
    meta, _scenarios = load_pack(pack_id)
    counted = [run for run in runs if run.result.failure_mode != "verifier_not_implemented"]
    latencies = [run.result.latency_seconds for run in counted if run.result.latency_seconds > 0]

    if meta.get("_architecture") == "single-scoreboard" and counted:
        scenario_result = counted[0].result
        if scenario_result.total_count is not None:
            passed = scenario_result.passed_count or 0
            total = scenario_result.total_count
            score = (
                scenario_result.pass_rate
                if scenario_result.pass_rate is not None
                else (passed / total if total else 0.0)
            )
            status = (
                scenario_result.failure_mode
                if scenario_result.failure_mode
                in {
                    "agent_runner_timeout",
                    "agent_runner_crashed",
                    "model_endpoint_unreachable",
                    "server_error",
                    "result_json_malformed",
                }
                else "ok"
            )
            return PackResult(
                pack_id=pack_id,
                version=meta["version"],
                upstream_commit=meta["upstream_commit"],
                scenario_count=scenario_count,
                passed=passed,
                total=total,
                score=score,
                latency=_latency(latencies),
                scenarios=runs,
                status=status,
                thinking_enabled=resolve_thinking_enabled(meta, thinking_override),
                variance=_repeat_variance(runs, repeat),
                catalog_scenario_count=catalog_scenario_count,
            )

    passed = sum(1 for run in counted if run.result.passed)
    total = len(counted)
    return PackResult(
        pack_id=pack_id,
        version=meta["version"],
        upstream_commit=meta["upstream_commit"],
        scenario_count=scenario_count,
        passed=passed,
        total=total,
        score=passed / total if total else 0.0,
        latency=_latency(latencies),
        scenarios=runs,
        status="ok" if total else "stubbed",
        thinking_enabled=resolve_thinking_enabled(meta, thinking_override),
        variance=_repeat_variance(runs, repeat),
        catalog_scenario_count=catalog_scenario_count,
    )


def _unique_warnings(*groups: list[str] | None) -> list[str]:
    return list(dict.fromkeys(item for group in groups for item in (group or [])))


def _build_result(
    config: dict,
    scenario_rows: list[tuple[str, dict]],
    *,
    finished_at: str,
    warnings: list[str] | None = None,
    pack_templates: dict[str, PackResult] | None = None,
) -> RunResult:
    target_selection = list(config["target_selection"])
    _canonical, target_by_pack = validate_selection(target_selection)
    repeat = int(config.get("repeat") or 1)
    thinking_override = config.get("thinking_override")
    pack_order = list(config["pack_ids"])
    rows_by_pack: dict[str, dict[tuple[str, int], ScenarioRun]] = {}
    for pack_id, row in scenario_rows:
        run = _scenario_run(row)
        rows_by_pack.setdefault(pack_id, {})[(run.id, run.repeat_index)] = run

    packs: list[PackResult] = []
    templates = pack_templates or {}
    for pack_id in pack_order:
        scenario_ids = target_by_pack.get(pack_id, [])
        order = {scenario_id: index for index, scenario_id in enumerate(scenario_ids)}
        runs = list(rows_by_pack.get(pack_id, {}).values())
        runs.sort(key=lambda run: (run.repeat_index, order.get(run.id, len(order))))
        if not runs and pack_id in templates:
            packs.append(templates[pack_id])
            continue
        _meta, catalog = load_pack(pack_id)
        packs.append(
            _aggregate_pack(
                pack_id,
                runs,
                scenario_count=len(scenario_ids),
                catalog_scenario_count=(
                    len(catalog) if config.get("result_selection") is not None else None
                ),
                repeat=repeat,
                thinking_override=thinking_override,
            )
        )

    total = sum(pack.total for pack in packs)
    passed = sum(pack.passed for pack in packs)
    result = RunResult(
        schema_version=str(config.get("schema_version") or "1"),
        runner_version=str(config.get("runner_version") or __version__),
        endpoint=str(config.get("endpoint") or ""),
        model=str(config.get("model") or ""),
        mode=str(config.get("mode") or "custom"),
        started_at=str(config.get("started_at") or _utc_now()),
        finished_at=finished_at,
        packs=packs,
        totals={"passed": passed, "total": total, "score": passed / total if total else 0.0},
        thinking_enabled=bool(thinking_override),
        thinking_mode=str(config.get("thinking_mode") or "pack-defaults"),
        warnings=warnings or [],
        sampling_overrides=config.get("sampling_overrides"),
        sampling_source=config.get("sampling_source"),
        server_defaults=config.get("server_defaults"),
        selection=config.get("result_selection"),
    )
    retry_context = config.get("retry_failed")
    if retry_context is not None:
        from benchlocal_cli.retry_failed import build_retry_diagnostic

        result.retry_failed = build_retry_diagnostic(result, retry_context)
    return result


def result_from_journal(path: str | Path) -> dict:
    records = _read_journal(Path(path))
    config = records[0]["run"]
    rows = [(str(record["pack"]["pack_id"]), record["scenario"]) for record in records]
    result = _build_result(
        config,
        rows,
        finished_at=str(records[-1].get("written_at") or _utc_now()),
        warnings=["partial per-scenario journal; run is incomplete"],
    )
    return result.to_dict()


def load_result(path: str | Path) -> dict:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"result not found: {path}")
    if str(source).endswith(".partial.jsonl"):
        return result_from_journal(source)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return result_from_journal(source)
    if not isinstance(data, dict):
        raise ValueError(f"result JSON must be an object: {path}")
    return data


def _infer_config(data: dict, source: Path) -> dict:
    packs = [pack for pack in data.get("packs") or [] if isinstance(pack, dict)]
    mode = str(data.get("mode") or "custom")
    result_selection = data.get("selection")
    if result_selection is not None:
        target_selection, target_by_pack = validate_selection(result_selection)
        pack_ids = list(target_by_pack)
    else:
        present_pack_ids = [str(pack.get("pack_id")) for pack in packs if pack.get("pack_id")]
        pack_ids = list(PACK_MODES.get(mode) or present_pack_ids)
        target_selection, _target_by_pack = selection_for_packs(pack_ids)
    repeat = max(
        (
            int(run.get("repeat_index") or 1)
            for pack in packs
            for run in pack.get("scenarios") or []
            if isinstance(run, dict)
        ),
        default=1,
    )
    thinking_mode = str(data.get("thinking_mode") or "pack-defaults")
    thinking_override = True if thinking_mode == "force-on" else False if thinking_mode == "force-off" else None
    return {
        "schema_version": str(data.get("schema_version") or "1"),
        "runner_version": str(data.get("runner_version") or __version__),
        "endpoint": data.get("endpoint"),
        "model": data.get("model"),
        "mode": mode,
        "started_at": data.get("started_at"),
        "pack_ids": pack_ids,
        "target_selection": target_selection,
        "result_selection": result_selection,
        "repeat": repeat,
        "thinking_override": thinking_override,
        "thinking_mode": thinking_mode,
        "sampling_overrides": data.get("sampling_overrides"),
        "sampling_source": data.get("sampling_source"),
        "server_defaults": data.get("server_defaults"),
        "save_json": str(final_path_for(source)),
    }


@dataclass
class ResumeState:
    source_path: Path
    final_path: Path
    sidecar_path: Path
    config: dict
    previous_result: dict
    missing_selection: list[str]
    missing_by_pack: dict[str, list[str]]
    completed_repeats: dict[str, dict[str, set[int]]]

    @property
    def complete(self) -> bool:
        return not self.missing_selection


def load_resume(path: str | Path) -> ResumeState:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"--resume not found: {path}")
    if str(source).endswith(".partial.jsonl"):
        records = _read_journal(source)
        config = dict(records[0]["run"])
        previous_result = result_from_journal(source)
        final_path = final_path_for(source)
        journal_path = source
    else:
        previous_result = load_result(source)
        config = _infer_config(previous_result, source)
        final_path = source
        journal_path = sidecar_path(final_path)

    target_selection, target_by_pack = validate_selection(config["target_selection"])
    config["target_selection"] = target_selection
    config.setdefault("pack_ids", list(target_by_pack))
    config.setdefault("result_selection", previous_result.get("selection"))
    config.setdefault("repeat", 1)
    config.setdefault("save_json", str(final_path))

    completed: dict[str, dict[str, set[int]]] = {}
    for pack in previous_result.get("packs") or []:
        pack_id = str(pack.get("pack_id") or "")
        for row in pack.get("scenarios") or []:
            scenario_id = str(row.get("id") or "")
            completed.setdefault(pack_id, {}).setdefault(scenario_id, set()).add(
                int(row.get("repeat_index") or 1)
            )

    repeat = int(config["repeat"])
    missing = [
        qualified
        for qualified in target_selection
        if len(
            completed.get(qualified.split("/", 1)[0], {}).get(
                qualified.split("/", 1)[1], set()
            )
        )
        < repeat
    ]
    missing, missing_by_pack = validate_selection(missing) if missing else ([], {})
    return ResumeState(
        source_path=source,
        final_path=final_path,
        sidecar_path=journal_path,
        config=config,
        previous_result=previous_result,
        missing_selection=missing,
        missing_by_pack=missing_by_pack,
        completed_repeats=completed,
    )


def finalize_completed_journal(state: ResumeState) -> dict:
    result = json.loads(json.dumps(state.previous_result))
    result["warnings"] = [
        warning
        for warning in result.get("warnings") or []
        if warning != "partial per-scenario journal; run is incomplete"
    ]
    state.final_path.parent.mkdir(parents=True, exist_ok=True)
    state.final_path.write_text(
        json.dumps(result, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    state.sidecar_path.unlink()
    return result


def merge_resume(state: ResumeState, new_result: RunResult) -> RunResult:
    rows: list[tuple[str, dict]] = []
    for source in (state.previous_result, new_result.to_dict()):
        for pack in source.get("packs") or []:
            pack_id = str(pack.get("pack_id") or "")
            rows.extend((pack_id, row) for row in pack.get("scenarios") or [])

    templates = {
        pack.pack_id: pack
        for pack in new_result.packs
        if pack.skipped or not pack.scenarios
    }
    config = dict(state.config)
    config["runner_version"] = new_result.runner_version
    config["server_defaults"] = new_result.server_defaults
    previous_warnings = [
        warning
        for warning in state.previous_result.get("warnings") or []
        if warning != "partial per-scenario journal; run is incomplete"
    ]
    return _build_result(
        config,
        rows,
        finished_at=new_result.finished_at,
        warnings=_unique_warnings(
            previous_warnings,
            new_result.warnings,
        ),
        pack_templates=templates,
    )
