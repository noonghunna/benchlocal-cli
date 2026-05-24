# Pack JSONL format

Every pack file at `benchlocal_cli/packs/<pack-id>.jsonl` follows this format. Each line is one JSON object; the first line is metadata, subsequent lines are scenarios.

## Metadata line

```json
{
  "__meta__": true,
  "pack_id": "toolcall-15",
  "version": "1.0.1",
  "upstream_repo": "stevibe/ToolCall-15",
  "upstream_commit": "abc123def456",
  "scenario_count": 15,
  "license": "MIT",
  "license_text_path": "ATTRIBUTION.md",
  "sampling_defaults": {
    "temperature": 0.0,
    "top_p": 1.0,
    "max_tokens": 1024,
    "tool_choice": "auto",
    "chat_template_kwargs": {"enable_thinking": false}
  },
  "default_thinking": "off",
  "default_max_seconds": 60,
  "verifier_module": "tool_call",
  "supports_sandboxed_only": false,
  "suite": "benchlocal",
  "requires_dataset_access": false,
  "dataset_access_note": null,
  "ported_at": "2026-05-09",
  "porter": "noonghunna"
}
```

Field reference:

| Field | Required | Type | Notes |
|---|---|---|---|
| `__meta__` | yes | `true` | Marks this line as metadata |
| `pack_id` | yes | string | Lowercase, hyphenated, matches filename |
| `version` | yes | semver string | Tracks upstream pack version we ported from |
| `upstream_repo` | yes | string | `<org>/<repo>` form |
| `upstream_commit` | yes | git SHA | The commit we ported from |
| `scenario_count` | yes | int | Number of scenario lines in this file |
| `license` | yes | string | Usually "MIT" (matches BenchLocal upstream) |
| `license_text_path` | no | string | Path to attribution doc; defaults to `ATTRIBUTION.md` |
| `sampling_defaults` | yes | object | Applied to every scenario unless overridden; generated packs include `chat_template_kwargs: {"enable_thinking": false}` as the request-shape base |
| `default_thinking` | no | `"on"` or `"off"` | Pack-level reasoning default. Missing means `"off"`. Runner default honors this; `--enable-thinking` / `--no-thinking` force all packs on/off. |
| `default_max_seconds` | yes | int | Default per-scenario timeout |
| `verifier_module` | yes | string | Name of `benchlocal_cli/scoring/<name>.py` to dispatch to |
| `supports_sandboxed_only` | no | bool | `true` for BugFind/HermesAgent/CLI/HumanEval+/LCB/Aider; runner skips with warning unless `--enable-sandboxed-packs` |
| `suite` | no | string | Logical suite label used by mode selectors; e.g. `reasoning` groups packs for `--reasoning`. |
| `requires_dataset_access` | no | bool | `true` for gated datasets. Runner returns a skipped `PackResult` with `status: dataset-unavailable` instead of failing the run. |
| `dataset_access_note` | no | string | Human-readable skip/warning text surfaced when `requires_dataset_access` prevents materializing a pack. |
| `ported_at` | yes | ISO date | When we ported this pack |
| `porter` | yes | string | Who did the porting |

## Scenario line

```json
{
  "id": "toolcall-15-001",
  "description": "Single-tool call with date-format constraint (Paris weather, March 15 2026)",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant with tool access."},
    {"role": "user", "content": "What's the weather like in Paris on March 15, 2026?"}
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "Get current weather for a location and date.",
        "parameters": {
          "type": "object",
          "properties": {
            "location": {"type": "string"},
            "date": {"type": "string", "format": "date"}
          },
          "required": ["location", "date"]
        }
      }
    }
  ],
  "verifier": {
    "type": "tool_call",
    "asserts": [
      {"kind": "exact_function_name", "value": "get_weather"},
      {"kind": "required_args_present", "args": ["location", "date"]},
      {"kind": "exact_arg_value", "arg": "location", "value": "Paris"},
      {"kind": "arg_regex", "arg": "date", "pattern": "^2026-03-15$"}
    ]
  },
  "sampling_overrides": {
    "max_tokens": 256
  },
  "max_seconds_override": null,
  "tags": ["single-tool", "date-format"],
  "upstream_scenario_id": "toolcall-15-001"
}
```

Scenario field reference:

| Field | Required | Type | Notes |
|---|---|---|---|
| `id` | yes | string | Globally unique within pack |
| `description` | yes | string | One-line summary for output / debugging |
| `messages` | yes | array | OpenAI chat-completions `messages` |
| `tools` | conditional | array | Required if scenario tests tool calls |
| `verifier` | yes | object | `{type, asserts}` — type tells runner which `score_scenario()` to call |
| `verifier.type` | yes | string | One of: `tool_call`, `instruct_follow`, `struct_output`, `reason_math`, `data_extract`, `answer_match`, `_stub` |
| `verifier.asserts` | yes | array | Module-specific assertion objects (see below) |
| `sampling_overrides` | no | object | Override metadata `sampling_defaults` for this scenario |
| `max_seconds_override` | no | int / null | Override metadata `default_max_seconds` |
| `tags` | no | array | Free-form tags for filtering / grouping |
| `upstream_scenario_id` | no | string | If this scenario was ported, the upstream id (usually same as `id`) |
| `raw_scenario` | no | object | Upstream-derived verifier payload for sandboxed packs. v0.7 marks sandbox packs with `fixture_status: "upstream-verification-runtime"` when verification is delegated to vendored upstream runtime code. |

## Assertion primitives

### `verifier.type = "tool_call"`

```json
{"kind": "exact_function_name", "value": "get_weather"}
{"kind": "function_name_in", "values": ["get_weather", "lookup_weather"]}
{"kind": "required_function_names", "values": ["get_weather", "get_stock_price"]}
{"kind": "tool_call_count", "value": 1}
{"kind": "content_regex", "pattern": "1945"}
{"kind": "required_args_present", "args": ["location", "date"]}
{"kind": "forbidden_args_absent", "args": ["api_key"]}
{"kind": "exact_arg_value", "arg": "location", "value": "Paris"}
{"kind": "arg_regex", "arg": "date", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"}
{"kind": "arg_in_enum", "arg": "unit", "values": ["celsius", "fahrenheit"]}
{"kind": "arg_numeric_range", "arg": "temperature", "min": 0, "max": 1}
{"kind": "multi_call_order", "expected_names": ["search", "fetch", "summarize"]}
```

### `verifier.type = "instruct_follow"`

```json
{"kind": "exact_length_words", "value": 50}
{"kind": "max_length_words", "value": 100}
{"kind": "min_length_words", "value": 20}
{"kind": "case_only", "value": "lowercase"}
{"kind": "format_regex", "pattern": "^Step \\d+:"}
{"kind": "required_phrase", "value": "TODO"}
{"kind": "forbidden_phrase", "value": "I cannot"}
{"kind": "required_url_count", "min": 3}
{"kind": "required_section_headers", "headers": ["# Summary", "# Details"]}
{"kind": "bullet_count", "value": 5}
{"kind": "language", "value": "english"}
```

### `verifier.type = "struct_output"`

```json
{"kind": "json_parse_required"}
{"kind": "json_schema", "schema": { /* valid jsonschema */ }}
{"kind": "yaml_parse_required"}
{"kind": "exact_json", "value": { /* expected JSON */ }}
{"kind": "jsonpath_assertion", "path": "$.users[0].email", "regex": "^.+@.+$"}
{"kind": "csv_columns", "expected": ["name", "age", "city"]}
{"kind": "markdown_structure", "headers": ["# Title", "## Section 1", "## Section 2"]}
```

### `verifier.type = "reason_math"`

```json
{"kind": "exact_numeric", "value": 42}
{"kind": "tolerance_numeric", "value": 3.14159, "tolerance": 0.01}
{"kind": "exact_string", "value": "x=3, y=5"}
{"kind": "regex_match", "pattern": "x = -?\\d+"}
```

### `verifier.type = "data_extract"`

```json
{"kind": "field_required", "field": "email"}
{"kind": "field_exact_value", "field": "country", "value": "USA"}
{"kind": "field_regex", "field": "phone", "pattern": "^\\+\\d{1,3}"}
{"kind": "field_in_set", "field": "status", "values": ["active", "pending", "suspended"]}
{"kind": "no_extra_fields", "allowed": ["name", "email", "phone"]}
```

### `verifier.type = "answer_match"`

```json
{"kind": "exact_numeric", "value": "20"}
{"kind": "tolerance_numeric", "value": "3.14159", "tolerance": "0.01"}
{"kind": "exact_letter", "value": "C"}
```

`answer_match` is intentionally small and deterministic. It is used by reasoning packs that have a single final answer but no executable verifier, such as GSM-Symbolic numeric answers and GPQA multiple-choice letters.

### `verifier.type = "_stub"`

For execution-backed packs (BugFind / HermesAgent / CLI / AiderPolyglot / HumanEval+ / LiveCodeBench). Without `--enable-sandboxed-packs`, the runner returns `verifier_not_implemented` and skips these scenarios. With sandboxing enabled, the runner forwards the full scenario, model response, and conversation messages to the pack's Docker verifier over HTTP:

```json
{"kind": "_stub", "reason": "BugFind requires Docker sandbox"}
```

The `_stub` verifier type is therefore a dispatch marker rather than the final scorer for sandboxed runs.

Sandboxed v0.7 scenarios include `raw_scenario` fields when the upstream mirror exposes enough metadata:

```json
{
  "raw_scenario": {
    "id": "CLI-01",
    "kind": "oneshot",
    "expected": {
      "success_case": "...",
      "failure_case": "...",
      "required_keywords": ["..."]
    },
    "fixture_status": "upstream-verification-runtime"
  }
}
```

Known v0.7 sandbox raw fields:

| Pack | Fields |
|---|---|
| BugFind-15 | `language`, `category`, `difficulty`, `success_case`, `failure_case`, `rubric_keywords`, `fixture_status` |
| CLI-40 | `kind`, `category_id`, `category`, `expected`, `input_fixtures`, `fixture_status`; messages include the upstream one-shot or multi-round system prompt from `verification/manifest.mjs` |
| HermesAgent-20 | `kind`, `category`, `expected`, `tool_fixtures`, `fixture_status` |

## Adding new assertion primitives

1. Document the primitive in this file under the appropriate verifier type
2. Implement in `benchlocal_cli/scoring/<verifier_type>.py`
3. Add a unit test in `tests/test_scoring_<verifier_type>.py`
4. Bump `runner_version` in `benchlocal_cli/__init__.py`

Don't add primitives that require non-deterministic scoring (e.g. semantic similarity, LLM-as-judge). The whole pipeline assumes bit-stable verifier outcomes.
