from __future__ import annotations

from benchlocal_cli.diagnostics import pack_diagnostics


def _response(reason: str | None) -> dict:
    return {
        "choices": [
            {
                "message": {"content": "answer"},
                "finish_reason": reason,
            }
        ]
    }


def test_pack_diagnostics_counts_all_completion_shapes_and_extraction_trace():
    runs = [
        {
            "raw_response": _response("length"),
            "result": {
                "verifier_trace": {
                    "extraction_method": "last_fenced",
                    "extraction_issue": "none",
                    "response_field_used": "message.content",
                }
            },
            "retry_attempts": [
                {
                    "raw_response": _response("stop"),
                    "verifier_trace": {
                        "extraction_method": "code_start",
                        "extraction_issue": "prose_before_code",
                    },
                    "response_field_used": "message.reasoning_content",
                }
            ],
        },
        {
            "raw_response": {
                "multi_turn": True,
                "responses": [_response("length"), _response("stop")],
            },
            "result": {"verifier_trace": None},
        },
        {
            "raw_response": {"error": "no completion"},
            "result": {"verifier_trace": None},
        },
    ]

    assert pack_diagnostics(runs) == {
        "finish_reasons": {
            "total": 4,
            "length": 2,
            "length_rate": 0.5,
            "counts": {"length": 2, "stop": 2},
        },
        "extraction": {
            "methods": {"code_start": 1, "last_fenced": 1},
            "issues": {"none": 1, "prose_before_code": 1},
            "response_fields": {
                "message.content": 1,
                "message.reasoning_content": 1,
            },
        },
    }


def test_pack_diagnostics_tracks_missing_finish_reason_as_unknown():
    diagnostics = pack_diagnostics([{"raw_response": _response(None)}])

    assert diagnostics["finish_reasons"] == {
        "total": 1,
        "length": 0,
        "length_rate": 0.0,
        "counts": {"unknown": 1},
    }
