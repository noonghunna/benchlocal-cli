"""#143: the IF/SO scenarios that used to carry a vacuous `format_regex: .+`
verifier now run Python ports of upstream's own graders. These tests pin that the
ports agree with the vendored upstream code, that junk fails and correct answers
pass through the real scorer, and that the build cannot ship a vacuous verifier.

Parity corpus (tests/fixtures/upstream_parity_corpus.json): hand-built cases per
scenario — a reference answer, junk, single-constraint failures, and the edge
cases where JS and Python semantics differ (BOM, NBSP, CRLF, U+2028, fullwidth
digits, emoji first letters, ".03", "4.5.1") — plus real saved model answers.
Each case records the score and status the vendored upstream graders produced.

IF-11 and SO-11 deliberately deviate from upstream (see the audit doc): for them
the corpus is a superset check — every answer upstream passes must still pass —
and DEVIATION_CASES pin answers the prompt allows but upstream rejects (must now
pass) and answers the prompt does not allow (must still fail).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from benchlocal_cli.runner import _negative_control_response, load_pack
from benchlocal_cli.scoring import (
    instruct_follow,
    instruct_follow_upstream,
    struct_output,
    struct_output_upstream,
)

ROOT = Path(__file__).resolve().parents[1]
CORPUS = json.loads((ROOT / "tests/fixtures/upstream_parity_corpus.json").read_text(encoding="utf-8"))["cases"]
PORTED = {
    "instructfollow-15": ["IF-05", "IF-06", "IF-07", "IF-08", "IF-09", "IF-11", "IF-13"],
    "structoutput-15": ["SO-04", "SO-05", "SO-06", "SO-09", "SO-11", "SO-12", "SO-15"],
}
DEVIATED = {"IF-11", "SO-11"}
VACUOUS_PATTERNS = {".+", ".*", "[\\s\\S]+", "[\\s\\S]*", "^.+$", "(?s).+"}


def _port(scenario_id: str):
    table = instruct_follow_upstream.EVALUATORS if scenario_id.startswith("IF-") else struct_output_upstream.EVALUATORS
    return table[scenario_id]


def _case_id(case: dict) -> str:
    return f"{case['id']}:{case['label']}"


def test_corpus_covers_every_ported_scenario_with_controls():
    for scenario_id in PORTED["instructfollow-15"] + PORTED["structoutput-15"]:
        labels = {case["label"] for case in CORPUS if case["id"] == scenario_id}
        assert "pass_reference" in labels, scenario_id
        assert any(label.startswith("junk_") for label in labels), scenario_id
        assert any(label.startswith("saved_") for label in labels), scenario_id


@pytest.mark.parametrize("case", [c for c in CORPUS if c["id"] not in DEVIATED], ids=_case_id)
def test_port_matches_recorded_upstream_verdict(case):
    evaluation = _port(case["id"])(case["answer"])
    assert evaluation.score == case["upstream_score"]
    assert evaluation.passed == (case["upstream_status"] == "pass")


def _run_upstream(cases: list[dict]) -> list[dict]:
    node = shutil.which("node")
    assert node, "node is required: this is the test that runs upstream's own graders"
    proc = subprocess.run(
        [node, "--experimental-strip-types", "--no-warnings", "tests/upstream_parity_eval.mjs"],
        input=json.dumps([{"id": case["id"], "answer": case["answer"]} for case in cases]),
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(proc.stdout)


def test_recorded_verdicts_match_live_upstream():
    """Runs the vendored upstream graders under node on the whole corpus. Fails if
    a vendor sync changed a verdict the Python port or the superset check relies on."""
    live = _run_upstream(CORPUS)
    drifted = [
        (_case_id(case), (case["upstream_score"], case["upstream_status"]), (got["score"], got["status"]))
        for case, got in zip(CORPUS, live, strict=True)
        if (got["score"], got["status"]) != (case["upstream_score"], case["upstream_status"])
    ]
    assert not drifted


# --- deliberate deviations: IF-11 and SO-11 ----------------------------------

_IF11_OK = [("I", "a. Choose fiber often.", "b. Drink water daily."),
            ("II", "a. Get sleep nightly.", "b. Add greens weekly."),
            ("III", "a. Include protein daily.", "b. Enjoy fruit regularly.")]


def _outline(top: str = "{n}.", sep: str = "\n", sub_indent: str = "", items=_IF11_OK) -> str:
    return sep.join(
        f"{top.format(n=n)}\n{sub_indent}{a}\n{sub_indent}{b}" for n, a, b in items
    )


_SO11_BASE = """flowchart TD
    A[User submits form] --> B[System validates input]
    B --> C{Valid?}
    C -->|Yes| D[Save to database]
    D --> E[Send confirmation email]
    E --> F[Show success page]
    C -->|No| G[Show error message]
    G --> A"""

# (id, label, answer, must_pass). must_pass=True rows are answers the prompt
# allows that upstream rejects (asserted against live upstream below).
DEVIATION_CASES = [
    # IF-11 — prompt: "top-level items labeled I, II, III", each with sub-items a and b.
    ("IF-11", "label_alone_with_period", _outline(), True),
    ("IF-11", "label_alone_indented_subitems", _outline(sub_indent="   "), True),
    ("IF-11", "label_alone_blank_line_between", _outline(sep="\n\n"), True),
    ("IF-11", "bare_numeral_alone", _outline(top="{n}"), True),
    ("IF-11", "bare_numeral_eat", _outline(top="{n}").replace("Add greens weekly.", "Eat greens weekly."), False),
    ("IF-11", "bare_numeral_keyword_twice", _outline(top="{n}").replace("Enjoy fruit", "Enjoy fiber"), False),
    ("IF-11", "label_alone_subitems_c_d", _outline().replace("\na. Get sleep", "\nc. Get sleep").replace("\nb. Add greens", "\nd. Add greens"), False),
    ("IF-11", "label_alone_duplicate_numeral", _outline().replace("III.", "II."), False),
    ("IF-11", "label_alone_long_subitem", _outline().replace("Drink water daily.", "Drink plenty of cold clean water daily at home."), False),
    # A numeral followed by text needs the period: "I think ..." is not a label.
    ("IF-11", "bare_numeral_then_title", _outline(top="{n} Section"), False),
    # SO-11 — prompt: submit -> validate -> if valid save + email then success;
    # if invalid error message and return to form. System prompt: no fences, no prose.
    ("SO-11", "graph_header", _SO11_BASE.replace("flowchart TD", "graph TD"), True),
    ("SO-11", "direction_lr", _SO11_BASE.replace("flowchart TD", "flowchart LR"), True),
    ("SO-11", "graph_tb", _SO11_BASE.replace("flowchart TD", "graph TB"), True),
    # Upstream accepts `-- Yes -->` only from a node literally named C.
    ("SO-11", "text_edge_labels_other_id", _SO11_BASE.replace("C{", "V{").replace("C -->|Yes|", "V -- Yes -->").replace("C -->|No|", "V -- No -->"), True),
    ("SO-11", "quoted_edge_labels", _SO11_BASE.replace("|Yes|", '|"Yes"|').replace("|No|", '|"No"|'), True),
    ("SO-11", "validation_node_branches", """graph TD
    A[User submits a form] --> B{System validates the input}
    B -- Valid --> C[Save to database]
    C --> D[Send confirmation email]
    D --> E[Show success page]
    B -- Invalid --> F[Show error message]
    F --> A""", True),
    ("SO-11", "named_ids_start_node", """graph TD
    Start([Start]) --> Submit[User submits a form]
    Submit --> Validate[System validates the input]
    Validate --> Check{Is input valid?}
    Check -->|Valid| Save[(Save to database)]
    Save --> Email[Send confirmation email]
    Email --> Done([Show success page])
    Check -->|Invalid| Err[Show error message]
    Err --> Start""", True),
    ("SO-11", "merged_save_email_return_text", """flowchart TD
    A[User submits a form] --> B[System validates the input]
    B --> C{Valid?}
    C -->|Yes| D[Save to database and send confirmation email]
    D --> E[Show success page]
    C -->|No| F[Show error message]
    F --> G[Return to form]""", True),
    ("SO-11", "semicolons_and_chain", "graph LR; A[User submits form] --> B[System validates input] --> C{Valid?}; "
     "C -->|Yes| D[Save to database] --> E[Send confirmation email] --> F[Show success page]; "
     "C -->|No| G[Show error message] --> A", True),
    ("SO-11", "branches_swapped", _SO11_BASE.replace("|Yes|", "|TMP|").replace("|No|", "|Yes|").replace("|TMP|", "|No|"), False),
    ("SO-11", "no_return_to_form", _SO11_BASE.replace("\n    G --> A", ""), False),
    ("SO-11", "invalid_branch_saves", _SO11_BASE.replace("G --> A", "G --> D"), False),
    ("SO-11", "missing_email_step", _SO11_BASE.replace("D --> E[Send confirmation email]\n    E --> F", "D --> F"), False),
    ("SO-11", "missing_success_page", _SO11_BASE.replace("\n    E --> F[Show success page]", ""), False),
    ("SO-11", "unlabelled_branches", _SO11_BASE.replace("-->|Yes|", "-->").replace("-->|No|", "-->"), False),
    ("SO-11", "missing_validation_step", _SO11_BASE.replace("A[User submits form] --> B[System validates input]\n    B --> C", "A[User submits form] --> C"), False),
    ("SO-11", "fenced", "```mermaid\n" + _SO11_BASE + "\n```", False),
    ("SO-11", "prose_before", "Here is the flowchart:\n" + _SO11_BASE, False),
    ("SO-11", "invalid_arrow_syntax", _SO11_BASE.replace("D --> E", "D => E"), False),
]


def _deviation_id(case: tuple) -> str:
    return f"{case[0]}:{case[1]}"


@pytest.mark.parametrize("case", [c for c in CORPUS if c["id"] in DEVIATED and c["upstream_status"] == "pass"], ids=_case_id)
def test_deviation_passes_everything_upstream_passes(case):
    assert _port(case["id"])(case["answer"]).passed


@pytest.mark.parametrize("case", DEVIATION_CASES, ids=_deviation_id)
def test_deviation_judges_the_prompt(case):
    scenario_id, _label, answer, must_pass = case
    assert _port(scenario_id)(answer).passed is must_pass


def test_deviation_cases_that_now_pass_are_rejected_by_live_upstream():
    """Proves each must-pass row is an answer upstream wrongly rejects, not a case
    the deviation merely happens to agree with."""
    rows = [{"id": sid, "answer": answer} for sid, _label, answer, must_pass in DEVIATION_CASES if must_pass]
    assert all(result["status"] != "pass" for result in _run_upstream(rows))


def _scenario(pack_id: str, scenario_id: str) -> dict:
    return next(s for s in load_pack(pack_id)[1] if s["id"] == scenario_id)


def _scorer(pack_id: str):
    return instruct_follow if pack_id == "instructfollow-15" else struct_output


def _response(text: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}


@pytest.mark.parametrize("pack_id,scenario_id", [(p, s) for p, ids in PORTED.items() for s in ids])
def test_shipped_pack_wires_the_upstream_evaluator(pack_id, scenario_id):
    assert _scenario(pack_id, scenario_id)["verifier"]["asserts"] == [
        {"kind": "upstream_evaluator", "evaluator": scenario_id}
    ]


@pytest.mark.parametrize("pack_id,scenario_id", [(p, s) for p, ids in PORTED.items() for s in ids])
def test_reference_answer_passes_through_the_real_scorer(pack_id, scenario_id):
    reference = next(c for c in CORPUS if c["id"] == scenario_id and c["label"] == "pass_reference")
    outcome = _scorer(pack_id).score_scenario(_scenario(pack_id, scenario_id), _response(reference["answer"]))
    assert outcome.passed, outcome.detail


@pytest.mark.parametrize("junk", ["(no answer)", "", "junk"])
@pytest.mark.parametrize("pack_id", sorted(PORTED))
def test_negative_control_passes_nothing(pack_id, junk):
    """#143's measurement, inverted: the junk control used to pass 7/15 of each pack."""
    _meta, scenarios = load_pack(pack_id)
    scorer = _scorer(pack_id)
    passed = [s["id"] for s in scenarios if scorer.score_scenario(s, _negative_control_response(junk)).passed]
    assert passed == []


def test_failing_detail_carries_the_upstream_breakdown():
    so = struct_output.score_scenario(_scenario("structoutput-15", "SO-04"), _response("Here is the TOML:\n[package]"))
    assert not so.passed
    assert "parseable" in so.detail and "discipline 0" in so.detail
    if_ = instruct_follow.score_scenario(_scenario("instructfollow-15", "IF-13"), _response("Alpha beta."))
    assert not if_.passed and if_.failure_mode == "verifier_fail"
    assert "constraints passed" in if_.detail and "Failed:" in if_.detail


def test_no_pack_ships_a_vacuous_verifier():
    offenders = []
    for path in sorted((ROOT / "benchlocal_cli/packs").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            for assertion in (row.get("verifier") or {}).get("asserts") or []:
                if assertion.get("kind") == "format_regex" and assertion.get("pattern") in VACUOUS_PATTERNS:
                    offenders.append((path.name, row.get("id")))
    assert offenders == []


def _copy_build_tree(tmp_path: Path) -> Path:
    for rel in ("tools/build-packs.js", "vendor/InstructFollow-15", "vendor/StructOutput-15"):
        src, dst = ROOT / rel, tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        (shutil.copytree if src.is_dir() else shutil.copy2)(src, dst)
    (tmp_path / "benchlocal_cli/packs").mkdir(parents=True)
    return tmp_path


@pytest.mark.parametrize("pack,file", [("InstructFollow-15", "instructfollow-15.jsonl"), ("StructOutput-15", "structoutput-15.jsonl")])
def test_build_regenerates_the_committed_pack_byte_for_byte(tmp_path, pack, file):
    tree = _copy_build_tree(tmp_path)
    subprocess.run(["node", "tools/build-packs.js", pack], cwd=tree, check=True, capture_output=True, text=True)
    assert (tree / "benchlocal_cli/packs" / file).read_bytes() == (ROOT / "benchlocal_cli/packs" / file).read_bytes()


def test_build_fails_instead_of_shipping_a_vacuous_verifier(tmp_path):
    tree = _copy_build_tree(tmp_path)
    script = tree / "tools/build-packs.js"
    source = script.read_text(encoding="utf-8")
    patched = re.sub(r'"IF-05", ', "", source, count=1)
    assert patched != source
    script.write_text(patched, encoding="utf-8")
    proc = subprocess.run(["node", "tools/build-packs.js", "InstructFollow-15"], cwd=tree, capture_output=True, text=True)
    assert proc.returncode != 0
    assert "IF-05: no scenario-specific verifier" in proc.stderr
    assert not (tree / "benchlocal_cli/packs/instructfollow-15.jsonl").exists()


def test_packs_with_the_upstream_checks_ship_as_v2():
    """The ported checks can only lower scores, so v2 results are not comparable
    with v1.x; the major bump is what tells a reader that."""
    for pack_id in PORTED:
        meta, _ = load_pack(pack_id)
        assert tuple(int(part) for part in meta["version"].split(".")) >= (2, 0, 0), pack_id
