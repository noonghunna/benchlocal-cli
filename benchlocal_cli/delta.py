"""Delta comparison between two RunResult JSONs.

Used by `benchlocal-cli run --previous-result PATH` to classify each
scenario as regression / fix / stable / new / dropped vs a prior run.

Per Codex review of the v0.8 brief:
- Scenario keying is `(pack_id, scenario_id)` not bare scenario_id (#1)
- Multi-repeat aggregates to per-(pack,scenario) pass-rate; threshold
  configurable via BENCHLOCAL_DELTA_PASS_THRESHOLD env (default 0.5) (#2)
- Markdown delta column rendered ONLY when --previous-result was passed
  (the cli.py callsite handles that) — preserves byte-stable output for
  pinned downstream parsers (#4)
- Schema-version mismatch produces a warning, not a refusal (#9)
- A pack-version mismatch warns here; --exit-on-regression refuses it (#198)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


PASS_THRESHOLD = float(os.environ.get("BENCHLOCAL_DELTA_PASS_THRESHOLD", "0.5"))


@dataclass
class PackDelta:
    """Per-pack scenario classification counts vs a previous result."""
    pack_id: str
    regressions: int = 0
    fixes: int = 0
    stable_pass: int = 0
    stable_fail: int = 0
    new: int = 0
    dropped: int = 0
    regressions_list: list[str] = field(default_factory=list)
    fixes_list: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pack_id": self.pack_id,
            "regressions": self.regressions,
            "fixes": self.fixes,
            "stable_pass": self.stable_pass,
            "stable_fail": self.stable_fail,
            "new": self.new,
            "dropped": self.dropped,
            "regressions_list": self.regressions_list,
            "fixes_list": self.fixes_list,
        }

    @property
    def status(self) -> str:
        if self.regressions:
            return "regression"
        if self.fixes:
            return "improved"
        return "stable"


@dataclass
class RunDelta:
    """Aggregate delta across all packs."""
    previous_path: str
    schema_version_match: bool
    total_regressions: int = 0
    total_fixes: int = 0
    total_stable_pass: int = 0
    total_stable_fail: int = 0
    total_new: int = 0
    total_dropped: int = 0
    by_pack: list[PackDelta] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "previous_path": self.previous_path,
            "schema_version_match": self.schema_version_match,
            "total_regressions": self.total_regressions,
            "total_fixes": self.total_fixes,
            "total_stable_pass": self.total_stable_pass,
            "total_stable_fail": self.total_stable_fail,
            "total_new": self.total_new,
            "total_dropped": self.total_dropped,
            "by_pack": [d.to_dict() for d in self.by_pack],
            "warnings": self.warnings,
        }


def _scenario_pass_rate(runs: list[dict]) -> float:
    """Codex review #2: when --repeat N > 1, aggregate to per-scenario pass-rate.
    Same scenario id may appear multiple times (one per repeat). Pass-rate ≥
    PASS_THRESHOLD (default 0.5) → considered "passed" for delta classification.
    """
    if not runs:
        return 0.0
    passes = sum(1 for r in runs if r.get("passed"))
    return passes / len(runs)


def _build_scenario_map(packs: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """Build {(pack_id, scenario_id): [run_dicts]} from a RunResult.to_dict()."""
    out: dict[tuple[str, str], list[dict]] = {}
    for pack in packs or []:
        pack_id = pack.get("pack_id") or "unknown"
        for run in pack.get("scenarios") or []:
            key = (pack_id, run.get("id") or "?")
            out.setdefault(key, []).append(run)
    return out


def load_previous_result(path: str | Path) -> dict:
    """Read a saved RunResult JSON. Returns the dict; raises on missing/invalid."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"--previous-result not found: {path}")
    return json.loads(p.read_text(encoding="utf-8"))


def _fixed_budget(result: dict) -> int | None:
    value = (result.get("sampling_overrides") or {}).get("max_tokens")
    return value if isinstance(value, int) else None


def _thinking_budget(result: dict) -> int | None:
    value = result.get("thinking_max_tokens")
    return value if isinstance(value, int) else None


def _server_thinking_budget(result: dict) -> int | None:
    value = result.get("server_thinking_budget")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def budget_mismatch(current: dict, previous_path: str | Path) -> str | None:
    """Why two runs' token budgets differ, or None (#187).

    Two budgets are compared, because a delta across different values measures
    the budget as much as the model:

    - the fixed --max-tokens override, which replaces every pack's own answer
      budget ("pack budgets" when absent);
    - the thinking budget (--thinking-max-tokens, else --max-tokens, else
      16384), recorded as thinking_max_tokens. Compared only when BOTH runs
      recorded one: a thinking-off run records none, and a run that thought
      against one that did not differs in thinking mode, not in budget.
    - the reasoning budget the server applied (--server-thinking-budget,
      informational — never sent), recorded as server_thinking_budget. Same
      rule: compared only when BOTH runs recorded one, so results from before
      the field existed, or from a caller that did not supply it, compare
      exactly as before.

    A missing or unreadable previous result is not a mismatch — the caller
    reports that on its own path.
    """
    try:
        previous = load_previous_result(previous_path)
    except (OSError, ValueError):
        return None
    diffs = []
    cur, prev = _fixed_budget(current), _fixed_budget(previous)
    if cur != prev:

        def _name(value: int | None) -> str:
            return f"max_tokens={value}" if value is not None else "pack budgets"

        diffs.append(f"current {_name(cur)}, previous {_name(prev)}")
    cur_t, prev_t = _thinking_budget(current), _thinking_budget(previous)
    if cur_t is not None and prev_t is not None and cur_t != prev_t:
        diffs.append(f"current thinking_max_tokens={cur_t}, previous thinking_max_tokens={prev_t}")
    cur_s, prev_s = _server_thinking_budget(current), _server_thinking_budget(previous)
    if cur_s is not None and prev_s is not None and cur_s != prev_s:
        diffs.append(
            f"current server_thinking_budget={cur_s}, previous server_thinking_budget={prev_s}"
        )
    if not diffs:
        return None
    return f"token budget differs ({'; '.join(diffs)})"


def runner_mismatch(current: dict, previous: dict) -> str | None:
    """Why two runs' harness code differs, or None (#194).

    Compared only when BOTH runs recorded a runner_commit (they ran from git
    checkouts), so results from before the field, or from a wheel install,
    compare exactly as before. Then a different commit or version is named.
    Informational: unlike a budget mismatch it does not stop a regression gate.
    """
    cur_c, prev_c = current.get("runner_commit"), previous.get("runner_commit")
    if not (isinstance(cur_c, str) and cur_c and isinstance(prev_c, str) and prev_c):
        return None
    cur_v, prev_v = current.get("runner_version"), previous.get("runner_version")
    if cur_c == prev_c and cur_v == prev_v:
        return None
    return f"runner differs (current {cur_v} @ {cur_c}, previous {prev_v} @ {prev_c})"


def _pack_versions(result: dict) -> dict[str, str]:
    """{pack_id: version} for every pack the result recorded a version for."""
    versions: dict[str, str] = {}
    for pack in result.get("packs") or []:
        if not isinstance(pack, dict):
            continue
        pack_id, version = pack.get("pack_id"), pack.get("version")
        if isinstance(pack_id, str) and pack_id and isinstance(version, str) and version:
            versions[pack_id] = version
    return versions


def pack_version_mismatch(current: dict, previous: dict) -> str | None:
    """Which packs the two runs scored with different pack versions, or None (#198).

    A pack's version moves when its scenarios or its scorer change (#183 took
    StructOutput-15 to 2.1.0 for two scorer fixes), so a delta across it measures
    the scorer as much as the model. Only packs both runs recorded a version for
    are compared; a pack only one run has is new or dropped, not a mismatch.
    """
    cur, prev = _pack_versions(current), _pack_versions(previous)
    diffs = [
        f"{pack_id} current {cur[pack_id]}, previous {prev[pack_id]}"
        for pack_id in sorted(cur.keys() & prev.keys())
        if cur[pack_id] != prev[pack_id]
    ]
    if not diffs:
        return None
    return f"pack version differs ({'; '.join(diffs)})"


# aider's benchmark.py --tries default, and what every aider result before
# --aider-tries ran (#184).
AIDER_DEFAULT_TRIES = 2


def effective_aider_tries(result: dict) -> int | None:
    """The retry budget a result's aider-polyglot-30 batch ran under, or None
    when it ran no aider scenarios. No `aider_tries` field means aider's
    default of 2: the field is only written when --aider-tries was given (#184)."""
    ran = any(
        isinstance(pack, dict) and pack.get("pack_id") == "aider-polyglot-30" and pack.get("scenarios")
        for pack in result.get("packs") or []
    )
    if not ran:
        return None
    value = result.get("aider_tries")
    return value if isinstance(value, int) and not isinstance(value, bool) else AIDER_DEFAULT_TRIES


def describe_aider_tries_mismatch(current: int | None, previous: int | None) -> str | None:
    """Name two different aider retry budgets, or None when they match or
    either run had no aider scenarios (#184). A retry consumes the previous
    failure's output, so the budget moves the score (16/30 -> 20/30 from 2 to 4)."""
    if current is None or previous is None or current == previous:
        return None
    return f"aider retry budget differs (current aider_tries={current}, previous aider_tries={previous})"


def classify(current: dict, previous_path: str | Path) -> RunDelta:
    """Compare current run dict to a previously-saved RunResult JSON."""
    previous = load_previous_result(previous_path)

    delta = RunDelta(
        previous_path=str(previous_path),
        schema_version_match=(
            current.get("schema_version") == previous.get("schema_version")
        ),
    )

    if not delta.schema_version_match:
        delta.warnings.append(
            f"schema_version mismatch (current={current.get('schema_version')!r}, "
            f"previous={previous.get('schema_version')!r}); proceeding best-effort"
        )

    mismatch = budget_mismatch(current, previous_path)
    if mismatch:
        delta.warnings.append(f"{mismatch}; regressions and fixes may be budget effects")
    runner = runner_mismatch(current, previous)
    if runner:
        delta.warnings.append(f"{runner}; regressions and fixes may be harness effects")
    packs = pack_version_mismatch(current, previous)
    if packs:
        delta.warnings.append(f"{packs}; regressions and fixes may be scorer effects")
    tries = describe_aider_tries_mismatch(effective_aider_tries(current), effective_aider_tries(previous))
    if tries:
        delta.warnings.append(f"{tries}; regressions and fixes may be retry-budget effects")

    current_map = _build_scenario_map(current.get("packs") or [])
    previous_map = _build_scenario_map(previous.get("packs") or [])
    selected_keys = set(current.get("selection") or []) if current.get("selection") is not None else None

    # Index per-pack deltas by pack_id for accumulation
    pack_deltas: dict[str, PackDelta] = {}

    def _ensure(pack_id: str) -> PackDelta:
        if pack_id not in pack_deltas:
            pack_deltas[pack_id] = PackDelta(pack_id=pack_id)
        return pack_deltas[pack_id]

    # Walk current scenarios → classify against previous
    for (pack_id, scenario_id), runs in current_map.items():
        cur_pass = _scenario_pass_rate(runs) >= PASS_THRESHOLD
        prev_runs = previous_map.get((pack_id, scenario_id))
        d = _ensure(pack_id)
        if prev_runs is None:
            d.new += 1
            continue
        prev_pass = _scenario_pass_rate(prev_runs) >= PASS_THRESHOLD
        if cur_pass and prev_pass:
            d.stable_pass += 1
        elif cur_pass and not prev_pass:
            d.fixes += 1
            d.fixes_list.append(scenario_id)
        elif not cur_pass and prev_pass:
            d.regressions += 1
            d.regressions_list.append(scenario_id)
        else:
            d.stable_fail += 1

    # Walk previous → find scenarios dropped from current
    for (pack_id, scenario_id), _ in previous_map.items():
        if selected_keys is not None and f"{pack_id}/{scenario_id}" not in selected_keys:
            continue
        if (pack_id, scenario_id) not in current_map:
            d = _ensure(pack_id)
            d.dropped += 1

    # Aggregate totals
    delta.by_pack = sorted(pack_deltas.values(), key=lambda p: p.pack_id)
    for d in delta.by_pack:
        delta.total_regressions += d.regressions
        delta.total_fixes += d.fixes
        delta.total_stable_pass += d.stable_pass
        delta.total_stable_fail += d.stable_fail
        delta.total_new += d.new
        delta.total_dropped += d.dropped

    return delta


def has_regressions(delta: RunDelta | None) -> bool:
    """True if --exit-on-regression should fire."""
    return delta is not None and delta.total_regressions > 0
