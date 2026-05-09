"""StructOutput scoring — JSON / YAML / grammar schema validation.

TODO (Codex): implement.

Supported verifier types:
    - json_schema                    (validate against jsonschema spec in scenario)
    - yaml_schema                    (parse YAML, validate against schema)
    - markdown_structure             (expected H1/H2/H3 hierarchy + section count)
    - exact_json                     (response must equal expected JSON object after normalize)
    - jsonpath_assertions            (e.g. $.users[0].email matches email regex)
    - csv_columns                    (expected columns in CSV-like output)
    - grammar_match                  (custom grammar verification — for FSM bounded outputs)

Failure mode dispatch:
    - invalid_json       → response failed JSON.parse
    - schema_violation   → JSON parsed but schema rejected
    - wrong_structure    → markdown structure doesn't match
    - timeout / http_error / server_error — common to all packs

Reference upstream: https://github.com/stevibe/StructOutput-15
"""

from __future__ import annotations

# TODO (Codex): replace with real implementation.
def score_scenario(scenario: dict, response: dict) -> dict:
    """Stub. See module docstring."""
    raise NotImplementedError("benchlocal-cli scoring.struct_output is pre-alpha.")
