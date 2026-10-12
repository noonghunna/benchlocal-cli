"""#207: RM-04 and RM-06 final answers are judged by meaning, not by an exact
string list. The fixtures are real final lines from saved club-3090 runs that
the string lists rejected."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from benchlocal_cli.scoring import reason_math

PACK = Path(__file__).resolve().parents[1] / "benchlocal_cli" / "packs" / "reasonmath-15.jsonl"


def _scenarios() -> dict[str, dict]:
    records = (json.loads(line) for line in PACK.read_text().splitlines() if line.strip())
    return {record["id"]: record for record in records if not record.get("__meta__")}


SCENARIOS = _scenarios()


def _response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}], "usage": {"completion_tokens": 3}}


def _score(scenario_id: str, final_line: str):
    return reason_math.score_scenario(SCENARIOS[scenario_id], _response(f"work\n{final_line}"))


RM04_CORRECT = [
    "ANSWER: Constraints are inconsistent.",
    "ANSWER: Constraints are inconsistent; no valid order exists.",
    "ANSWER: INCONSISTENT - no valid arrangement satisfies all constraints (particularly White at an end while Red-Blue are consecutive and Yellow not next to Green at position 3)",
    "ANSWER: Inconsistent constraints; no valid ordering exists",
    "ANSWER: No consistent order exists.",
    "ANSWER: No consistent ordering exists.",
    "ANSWER: No solution",
    "ANSWER: No solution — the clues are inconsistent.",
    "ANSWER: No valid arrangement exists - the constraints are inconsistent (Red must be immediately left of Blue, but no adjacent pair can accommodate this while satisfying all other constraints)",
    "ANSWER: No valid order exists; the clues are inconsistent.",
    "ANSWER: The clues are inconsistent — no order of the 5 houses satisfies all four (in both possible red-blue placements, the remaining yellow house necessarily ends up adjacent to the green house).",
    "ANSWER: The clues are inconsistent; no valid arrangement exists.",
    "ANSWER: The clues are inconsistent; no valid house order exists.",
    "ANSWER: The clues are inconsistent; no valid order exists.",
    "ANSWER: The constraints are inconsistent",
    "ANSWER: The constraints are inconsistent - no valid arrangement exists",
    "ANSWER: The constraints are inconsistent — no valid ordering exists.",
    "ANSWER: The constraints are inconsistent, so no valid order exists.",
    "ANSWER: The constraints are inconsistent; no valid order satisfies all clues.",
    "ANSWER: The constraints are inconsistent; no valid ordering of the houses exists.",
    "ANSWER: The given clues are logically inconsistent.",
    "ANSWER: constraints=inconsistent",
    "ANSWER: inconsistent constraints, no solution exists",
    "ANSWER: the constraints are inconsistent",
]

RM04_WRONG = [
    # Real answers that give an (invalid) order.
    "ANSWER: White, Red, Green, Blue, Yellow",
    "ANSWER: white, red, green, blue, yellow",
    "ANSWER: red,blue,green,white,yellow; yellow,white,green,red,blue",
    # A claim hedged with an order still commits to an order.
    "ANSWER: The constraints are inconsistent; closest order: white, red, green, blue, yellow",
    "ANSWER: No valid order; best effort is white -> red -> green -> blue -> yellow",
    # A negated claim is not a claim.
    "ANSWER: The constraints are not inconsistent.",
    "ANSWER: The clues aren't inconsistent",
]

RM06_CORRECT = [
    "ANSWER: P(Switch) = 3/4; P(Stay) = 1/4",
    "ANSWER: P(Win | Switch to Door 2) = 3/4; P(Win | Stay with Door 1) = 1/4",
    "ANSWER: P(switch to Door 2) = 3/4; P(stay with Door 1) = 1/4",
    "ANSWER: P(switch)=3/4; P(stay)=1/4",
    "ANSWER: Probability of winning by switching to Door 2 = 3/4 (75%); probability of winning by staying with Door 1 = 1/4 (25%).",
    "ANSWER: Switch to Door 2 = 3/4; Stay with Door 1 = 1/4",
    "ANSWER: Switch to Door 2=3/4; Stay with Door 1=1/4",
    "ANSWER: Switch=0.75; Stay=0.25",
    "ANSWER: Switching probability = 3/4; Staying probability = 1/4",
    "ANSWER: prob_switch=3/4; prob_stay=1/4",
    "ANSWER: probability of winning if switch=3/4; probability of winning if stay=1/4",
    "ANSWER: switch=0.75; stay=0.25",
    "ANSWER: switch=3/4;stay=1/4",
    "ANSWER: switch_door2=3/4; stay_door1=1/4",
    "ANSWER: switch_to_door2=3/4; stay_door1=1/4",
    "ANSWER: switch_to_door_2 = 3/4; stay_with_door_1 = 1/4",
    "ANSWER: switching=3/4; staying=1/4",
    "ANSWER: stay=25%; switch=75%",
    # Labelled colon forms (maintainer decision on #207).
    "ANSWER: Switch to Door 2: 3/4; Stay with Door 1: 1/4",
    "ANSWER: Switch: 3/4; Stay: 1/4",
]

RM06_WRONG = [
    # Real wrong answers.
    "ANSWER: switch to Door 2 = 1/2; stay with Door 1 = 1/2",
    "ANSWER: switch=1/2;stay=1/2",
    "ANSWER: Switching to Door",
    # Both right numbers, bound to the wrong labels.
    "ANSWER: switch=1/4; stay=3/4",
    # Exact values, not containment.
    "ANSWER: switch=75.5%; stay=24.5%",
    "ANSWER: switch=13/4; stay=1/4",
    "ANSWER: switch=3/4 or 1/2; stay=1/4",
    # Every expected value exactly once, under its own label.
    "ANSWER: switch=3/4",
    "ANSWER: switch=3/4; stay=1/4; host=1/3",
    "ANSWER: switch=3/4; switch=1/4",
    "ANSWER: switch or stay=3/4; stay=1/4",
    "ANSWER: switch=; stay=1/4",
]

# Right numbers, but not label=value / label: value pairs separated by `;`.
# Kept failing by design (#207): bare values carry no labels; prose and
# comma-separated pairs are outside the prompt's key=value format.
RM06_OUT_OF_FORMAT = [
    "ANSWER: 0.75; 0.25",
    "ANSWER: Probability of winning if you switch to Door 2 is 3/4; probability of winning if you stay with Door 1 is 1/4.",
    "ANSWER: Switching wins with probability \\(3/4\\); staying wins with probability \\(1/4\\).",
    "ANSWER: Switching to Door 2 gives a probability of 3/4; staying with Door 1 gives a probability of 1/4.",
    "ANSWER: switch to Door 2: 3/4, stay with Door 1: 1/4",
    "ANSWER: switching=3/4, staying=1/4",
]


@pytest.mark.parametrize("line", RM04_CORRECT)
def test_rm04_accepts_a_stated_inconsistency(line):
    result = _score("RM-04", line)
    assert result.passed, result.detail


@pytest.mark.parametrize("line", RM04_WRONG)
def test_rm04_rejects_an_order_or_a_negated_claim(line):
    assert not _score("RM-04", line).passed


@pytest.mark.parametrize("line", RM06_CORRECT)
def test_rm06_accepts_values_bound_to_their_labels(line):
    result = _score("RM-06", line)
    assert result.passed, result.detail


@pytest.mark.parametrize("line", RM06_WRONG + RM06_OUT_OF_FORMAT)
def test_rm06_rejects_wrong_values_labels_and_formats(line):
    assert not _score("RM-06", line).passed


def test_matcher_pass_is_named_in_the_detail():
    result = _score("RM-06", "ANSWER: switch=0.75; stay=0.25")
    assert result.passed
    assert "Accepted by the labelled_values answer matcher." in result.detail


def test_rm13_stays_strict():
    assert not _score("RM-13", "ANSWER: amount=$5721.34; interest=$721.34").passed


@pytest.mark.parametrize("scenario_id", sorted(SCENARIOS))
def test_every_listed_answer_still_passes_in_any_spacing(scenario_id):
    """The spacing / trailing-period cleanup is applied to both sides, so no
    answer the lists accepted before can stop matching."""
    asserts = [a for a in SCENARIOS[scenario_id]["verifier"]["asserts"] if "canonical_answer" in a]
    for assertion in asserts:
        for answer in [assertion["canonical_answer"], *assertion.get("accepted_answers", [])]:
            spaced = answer.replace(";", " ; ").replace("=", " = ") + "."
            for variant in (answer, spaced):
                assert _score(scenario_id, variant).passed, (scenario_id, variant)


def test_rm04_and_rm06_carry_their_matchers():
    for scenario_id, kind in (("RM-04", "no_solution_claim"), ("RM-06", "labelled_values")):
        (assertion,) = SCENARIOS[scenario_id]["verifier"]["asserts"]
        assert assertion["answer_matcher"]["kind"] == kind


def test_unknown_matcher_kind_is_a_verifier_failure():
    scenario = copy.deepcopy(SCENARIOS["RM-06"])
    scenario["verifier"]["asserts"][0]["answer_matcher"] = {"kind": "telepathy"}
    result = reason_math.score_scenario(scenario, _response("ANSWER: switch=3/4; stay=1/4"))
    assert not result.passed
    assert result.failure_mode == "verifier_fail"
