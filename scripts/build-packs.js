#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const VENDOR = path.join(ROOT, "vendor");
const OUT = path.join(ROOT, "benchlocal_cli", "packs");

const PACKS = {
  "ToolCall-15": { file: "toolcall-15.jsonl", verifier: "tool_call", sandbox: false },
  "InstructFollow-15": { file: "instructfollow-15.jsonl", verifier: "instruct_follow", sandbox: false },
  "StructOutput-15": { file: "structoutput-15.jsonl", verifier: "struct_output", sandbox: false },
  "ReasonMath-15": { file: "reasonmath-15.jsonl", verifier: "reason_math", sandbox: false },
  "DataExtract-15": { file: "dataextract-15.jsonl", verifier: "data_extract", sandbox: false },
  "BugFind-15": { file: "bugfind-15.jsonl", verifier: "_stub", sandbox: true },
  "HermesAgent-20": { file: "hermesagent-20.jsonl", verifier: "_stub", sandbox: true },
  "CLI-40": { file: "cli-40.jsonl", verifier: "_stub", sandbox: true },
};

function readText(...parts) {
  return fs.readFileSync(path.join(...parts), "utf8");
}

function readJson(...parts) {
  return JSON.parse(readText(...parts));
}

function writeJsonl(packName, meta, scenarios) {
  const target = path.join(OUT, PACKS[packName].file);
  const lines = [meta, ...scenarios].map((record) => JSON.stringify(record));
  fs.writeFileSync(target, `${lines.join("\n")}\n`, "utf8");
}

function syncInfo(packName) {
  return readJson(VENDOR, packName, "_sync.json");
}

function packMeta(packName, scenarioCount) {
  const pack = readJson(VENDOR, packName, "benchlocal.pack.json");
  const sync = syncInfo(packName);
  const config = PACKS[packName];
  const sampling = Object.assign({ top_p: 1, max_tokens: 1024 }, camelToSnake(pack.samplingDefaults || {}));
  const defaultMaxSeconds = sampling.request_timeout_seconds || 60;
  delete sampling.request_timeout_seconds;
  return {
    __meta__: true,
    pack_id: pack.id,
    version: pack.version,
    upstream_repo: `stevibe/${packName}`,
    upstream_commit: sync.commit,
    _synced_from_commit: sync.commit,
    scenario_count: scenarioCount,
    license: "MIT",
    license_text_path: "ATTRIBUTION.md",
    sampling_defaults: sampling,
    default_max_seconds: defaultMaxSeconds,
    verifier_module: config.verifier,
    supports_sandboxed_only: config.sandbox,
    ported_at: "2026-05-09",
    porter: "Codex build-packs.js",
  };
}

function camelToSnake(value) {
  const out = {};
  for (const [key, item] of Object.entries(value)) {
    out[key.replace(/[A-Z]/g, (m) => `_${m.toLowerCase()}`)] = item;
  }
  return out;
}

function extractConstTemplate(source, name) {
  const needle = `export const ${name}`;
  const pos = source.indexOf(needle);
  if (pos === -1) {
    return "";
  }
  const tick = source.indexOf("`", pos);
  if (tick === -1) {
    return "";
  }
  return parseJsStringAt(source, tick).value;
}

function parseJsStringAt(source, start) {
  const quote = source[start];
  let i = start + 1;
  let escaped = false;
  for (; i < source.length; i += 1) {
    const ch = source[i];
    if (escaped) {
      escaped = false;
      continue;
    }
    if (ch === "\\") {
      escaped = true;
      continue;
    }
    if (ch === quote) {
      const literal = source.slice(start, i + 1);
      return { value: Function(`return (${literal});`)(), end: i + 1, literal };
    }
  }
  throw new Error(`unterminated string at ${start}`);
}

function extractStringField(block, field) {
  const match = new RegExp(`${field}\\s*:`).exec(block);
  if (!match) {
    return "";
  }
  let i = match.index + match[0].length;
  while (/\s/.test(block[i])) i += 1;
  if (!["'", '"', "`"].includes(block[i])) {
    return "";
  }
  return parseJsStringAt(block, i).value;
}

function matching(source, start, open, close) {
  let depth = 0;
  let stringQuote = null;
  let escaped = false;
  for (let i = start; i < source.length; i += 1) {
    const ch = source[i];
    if (stringQuote) {
      if (escaped) {
        escaped = false;
      } else if (ch === "\\") {
        escaped = true;
      } else if (ch === stringQuote) {
        stringQuote = null;
      }
      continue;
    }
    if (ch === "'" || ch === '"' || ch === "`") {
      stringQuote = ch;
      continue;
    }
    if (ch === open) depth += 1;
    if (ch === close) {
      depth -= 1;
      if (depth === 0) return i;
    }
  }
  throw new Error(`no matching ${close}`);
}

function extractArray(source, marker) {
  const pos = source.indexOf(marker);
  if (pos === -1) throw new Error(`marker not found: ${marker}`);
  const equals = source.indexOf("=", pos);
  const searchFrom = equals === -1 ? pos : equals;
  const start = source.indexOf("[", searchFrom);
  const lineEnd = source.indexOf("\n];", start);
  if (lineEnd !== -1) {
    return source.slice(start, lineEnd + 2);
  }
  const end = matching(source, start, "[", "]");
  return source.slice(start, end + 1);
}

function objectBlocks(arrayText) {
  const blocks = [];
  for (let i = 0; i < arrayText.length; i += 1) {
    if (arrayText[i] === "{") {
      const end = matching(arrayText, i, "{", "}");
      blocks.push(arrayText.slice(i, end + 1));
      i = end;
    }
  }
  return blocks;
}

function scenarioBlocks(source, marker) {
  const arrayText = extractArray(source, marker);
  const starts = [];
  const re = /\n\s*\{\s*\n\s*id:\s*["'`]/g;
  let match;
  while ((match = re.exec(arrayText)) !== null) {
    starts.push(match.index + arrayText.slice(match.index).indexOf("{"));
  }
  return starts.map((start, index) => {
    const end = index + 1 < starts.length ? starts[index + 1] : arrayText.length - 1;
    return arrayText.slice(start, end);
  });
}

function evalObjectArray(arrayText) {
  return Function(`return (${arrayText});`)();
}

function messages(system, user) {
  return [
    { role: "system", content: system },
    { role: "user", content: user },
  ];
}

function baseScenario(system, spec, verifierType, asserts) {
  return {
    id: spec.id,
    description: spec.description || spec.title,
    messages: messages(system, spec.userMessage || spec.promptText),
    verifier: { type: verifierType, asserts },
    sampling_overrides: { max_tokens: 1024 },
    max_seconds_override: null,
    tags: ["vendor-generated"],
    upstream_scenario_id: spec.id,
    upstream_title: spec.title,
    success_case: spec.successCase,
    failure_case: spec.failureCase,
  };
}

function toolAsserts(id) {
  const byId = {
    "TC-01": [{ kind: "exact_function_name", value: "get_weather" }, { kind: "required_args_present", args: ["location"] }, { kind: "arg_regex", arg: "location", pattern: "(?i)berlin" }],
    "TC-02": [{ kind: "exact_function_name", value: "get_stock_price" }, { kind: "exact_arg_value", arg: "ticker", value: "AAPL" }],
    "TC-03": [{ kind: "multi_call_order", expected_names: ["get_contacts", "send_email"] }],
    "TC-04": [{ kind: "exact_function_name", value: "get_weather" }, { kind: "arg_regex", arg: "location", pattern: "(?i)tokyo" }, { kind: "exact_arg_value", arg: "units", value: "fahrenheit" }],
    "TC-05": [{ kind: "exact_function_name", value: "create_calendar_event" }, { kind: "exact_arg_value", arg: "date", value: "2026-03-23" }, { kind: "exact_arg_value", arg: "time", value: "09:30" }, { kind: "arg_numeric_range", arg: "duration_minutes", min: 30, max: 30 }],
    "TC-06": [{ kind: "multi_call_order", expected_names: ["translate_text", "translate_text"] }, { kind: "tool_call_count", value: 2 }],
    "TC-07": [{ kind: "multi_call_order", expected_names: ["search_files", "read_file", "get_contacts", "send_email"] }],
    "TC-08": [{ kind: "multi_call_order", expected_names: ["get_weather", "set_reminder"] }],
    "TC-09": [{ kind: "tool_call_count", value: 2 }, { kind: "required_function_names", values: ["get_weather", "get_stock_price"] }],
    "TC-10": [{ kind: "tool_call_count", value: 0 }, { kind: "content_regex", pattern: "1945" }],
    "TC-11": [{ kind: "tool_call_count", value: 0 }, { kind: "content_regex", pattern: "\\b30\\b" }],
    "TC-12": [{ kind: "tool_call_count", value: 0 }, { kind: "content_regex", pattern: "(?i)(cannot|can't|not able|available tool|delete)" }],
    "TC-13": [{ kind: "exact_function_name", value: "search_files" }, { kind: "arg_regex", arg: "query", pattern: "(?i)johnson" }],
    "TC-14": [{ kind: "exact_function_name", value: "get_stock_price" }, { kind: "exact_arg_value", arg: "ticker", value: "AAPL" }],
    "TC-15": [{ kind: "multi_call_order", expected_names: ["web_search", "calculator"] }, { kind: "arg_regex", arg: "expression", pattern: "372520" }],
  };
  return byId[id] || [];
}

function buildToolCall() {
  const pack = "ToolCall-15";
  const source = readText(VENDOR, pack, "lib", "benchmark.ts");
  const system = extractConstTemplate(source, "SYSTEM_PROMPT");
  const tools = evalObjectArray(extractArray(source, "export const UNIVERSAL_TOOLS"));
  const blocks = scenarioBlocks(source, "export const SCENARIOS");
  const scenarios = blocks.map((block) => {
    const spec = {
      id: extractStringField(block, "id"),
      title: extractStringField(block, "title"),
      description: extractStringField(block, "description"),
      userMessage: extractStringField(block, "userMessage"),
    };
    const scenario = baseScenario(system, spec, "tool_call", toolAsserts(spec.id));
    scenario.tools = tools;
    scenario.sampling_overrides = { max_tokens: 512, tool_choice: "auto" };
    scenario.upstream_evaluate_summary = "Generated from vendored evaluate(state); dynamic tool fixtures remain in vendor/ToolCall-15/lib/benchmark.ts.";
    return scenario;
  });
  writeJsonl(pack, packMeta(pack, scenarios.length), scenarios);
}

function buildSpecPack(pack, verifier, assertForSpec) {
  const source = readText(VENDOR, pack, "lib", "benchmark.ts");
  const system = extractConstTemplate(source, "SYSTEM_PROMPT");
  const specs = evalObjectArray(extractArray(source, "const SCENARIO_SPECS"));
  const scenarios = specs.map((spec) => baseScenario(system, spec, verifier, assertForSpec(spec)));
  writeJsonl(pack, packMeta(pack, scenarios.length), scenarios);
}

function ifAsserts(spec) {
  const simple = {
    "IF-01": [{ kind: "format_regex", pattern: "^1\\. .+\\n2\\. .+\\n3\\. .+\\n4\\. .+\\n5\\. " }, { kind: "max_length_words", value: 45 }],
    "IF-02": [{ kind: "format_regex", pattern: "^[^\\n]+\\n[^\\n]+\\n[^\\n]+$" }, { kind: "max_length_words", value: 10 }],
    "IF-03": [{ kind: "required_phrase", value: "Coffee" }, { kind: "max_length_words", value: 59 }, { kind: "format_regex", pattern: "\\?$" }],
    "IF-04": [{ kind: "bullet_count", value: 6 }, { kind: "forbidden_phrase", value: "banana" }],
    "IF-10": [{ kind: "exact_length_words", value: 50 }, { kind: "format_regex", pattern: "^Humanity\\b[\\s\\S]*\\bstars\\.?$" }],
    "IF-12": [{ kind: "required_phrase", value: "IMPOSSIBLE -" }, { kind: "required_phrase", value: "30" }, { kind: "required_phrase", value: "25" }],
    "IF-14": [{ kind: "case_only", value: "uppercase" }, { kind: "required_phrase", value: "RAIN" }],
    "IF-15": [{ kind: "format_regex", pattern: "^[A-Za-z]+,\\s*[A-Za-z]+,\\s*[A-Za-z]+,\\s*[A-Za-z]+$" }],
  };
  return simple[spec.id] || [{ kind: "format_regex", pattern: ".+" }];
}

function rmAsserts(spec) {
  return [{ kind: "exact_string", value: spec.canonicalAnswer.replace(/^ANSWER:\s*/i, "") }];
}

function structAsserts(spec) {
  if (spec.id === "SO-01") {
    return [{ kind: "json_parse_required" }, { kind: "jsonpath_assertion", path: "$.title", value: "The Great Gatsby" }, { kind: "jsonpath_assertion", path: "$.year", value: 1925 }];
  }
  if (["SO-02", "SO-08", "SO-14"].includes(spec.id)) {
    return [{ kind: "csv_columns", expected: spec.id === "SO-02" ? ["name", "age", "city", "email"] : ["id", "description", "formula", "notes"] }];
  }
  if (spec.id === "SO-03") return [{ kind: "yaml_parse_required" }];
  if (spec.id === "SO-07") return [{ kind: "json_parse_required" }, { kind: "jsonpath_assertion", path: "$.user.id", value: 42 }];
  if (spec.id === "SO-10") return [{ kind: "markdown_structure", headers: ["| name | score | grade |"] }];
  if (spec.id === "SO-13") return [{ kind: "json_parse_required" }, { kind: "jsonpath_assertion", path: "$.zero", value: 0 }];
  return [{ kind: "format_regex", pattern: ".+" }];
}

function dataExtractExpected(block) {
  const marker = "expected: JSON.parse(String.raw`";
  const start = block.indexOf(marker);
  if (start === -1) return null;
  const strStart = start + "expected: JSON.parse(String.raw".length;
  const parsed = parseJsStringAt(block, strStart);
  const raw = parsed.literal.slice(1, -1);
  return JSON.parse(raw);
}

function flattenFields(value, prefix = "") {
  if (Array.isArray(value)) return [];
  if (value && typeof value === "object") {
    return Object.keys(value).map((key) => (prefix ? `${prefix}.${key}` : key));
  }
  return [];
}

function buildDataExtract() {
  const pack = "DataExtract-15";
  const source = readText(VENDOR, pack, "lib", "benchmark.ts");
  const system = extractConstTemplate(source, "SYSTEM_PROMPT");
  const blocks = objectBlocks(extractArray(source, "const SCENARIO_SPECS"));
  const scenarios = blocks.map((block) => {
    const spec = {
      id: extractStringField(block, "id"),
      title: extractStringField(block, "title"),
      description: extractStringField(block, "description"),
      userMessage: extractStringField(block, "userMessage"),
      successCase: extractStringField(block, "successCase"),
      failureCase: extractStringField(block, "failureCase"),
    };
    const expected = dataExtractExpected(block);
    const fields = flattenFields(expected);
    const asserts = fields.slice(0, 8).map((field) => ({ kind: "field_required", field }));
    if (expected && !Array.isArray(expected)) {
      asserts.push({ kind: "no_extra_fields", allowed: fields });
    }
    const scenario = baseScenario(system, spec, "data_extract", asserts);
    scenario.expected = expected;
    return scenario;
  });
  writeJsonl(pack, packMeta(pack, scenarios.length), scenarios);
}

function buildBugFind() {
  const pack = "BugFind-15";
  const source = readText(VENDOR, pack, "lib", "benchmark.ts");
  const system = extractConstTemplate(source, "SYSTEM_PROMPT");
  const blocks = scenarioBlocks(source, "export const SCENARIOS");
  const scenarios = blocks.map((block) => {
    const spec = {
      id: extractStringField(block, "id"),
      title: extractStringField(block, "title"),
      description: extractStringField(block, "description"),
      userMessage: extractStringField(block, "userMessage"),
      successCase: extractStringField(block, "successCase"),
      failureCase: extractStringField(block, "failureCase"),
    };
    return Object.assign(baseScenario(system, spec, "_stub", [{ kind: "_stub", reason: "BugFind requires Docker sandbox verifier" }]), { supports_sandboxed_only: true });
  });
  writeJsonl(pack, packMeta(pack, scenarios.length), scenarios);
}

function buildHermes() {
  const pack = "HermesAgent-20";
  const source = readText(VENDOR, pack, "lib", "benchmark.ts");
  const specs = evalObjectArray(extractArray(source, "export const SCENARIOS"));
  const scenarios = specs.map((spec) => ({
    id: spec.id,
    description: spec.description,
    messages: [{ role: "user", content: spec.promptText }],
    verifier: { type: "_stub", asserts: [{ kind: "_stub", reason: "HermesAgent requires multi-tool sandbox verifier" }] },
    sampling_overrides: { max_tokens: 1024 },
    tags: ["vendor-generated", "sandboxed-stub"],
    upstream_scenario_id: spec.id,
    upstream_title: spec.title,
    success_case: spec.successCase,
    failure_case: spec.failureCase,
  }));
  writeJsonl(pack, packMeta(pack, scenarios.length), scenarios);
}

function buildCli40() {
  const pack = "CLI-40";
  const specs = readJson(VENDOR, pack, "verification", "scenario-data.json");
  const scenarios = specs.map((spec) => ({
    id: spec.id,
    description: spec.description,
    messages: [{ role: "user", content: spec.promptText }],
    verifier: { type: "_stub", asserts: [{ kind: "_stub", reason: "CLI-40 requires Linux exec sandbox verifier" }] },
    sampling_overrides: { max_tokens: 1024 },
    tags: ["vendor-generated", "sandboxed-stub"],
    upstream_scenario_id: spec.id,
    upstream_title: spec.title,
    success_case: spec.successCase,
    failure_case: spec.failureCase,
  }));
  writeJsonl(pack, packMeta(pack, scenarios.length), scenarios);
}

function build(packName) {
  if (packName === "ToolCall-15") return buildToolCall();
  if (packName === "InstructFollow-15") return buildSpecPack(packName, "instruct_follow", ifAsserts);
  if (packName === "StructOutput-15") return buildSpecPack(packName, "struct_output", structAsserts);
  if (packName === "ReasonMath-15") return buildSpecPack(packName, "reason_math", rmAsserts);
  if (packName === "DataExtract-15") return buildDataExtract();
  if (packName === "BugFind-15") return buildBugFind();
  if (packName === "HermesAgent-20") return buildHermes();
  if (packName === "CLI-40") return buildCli40();
  throw new Error(`unknown pack ${packName}`);
}

const arg = process.argv[2];
const selected = arg === "--all" || !arg ? Object.keys(PACKS) : [arg];
for (const pack of selected) {
  build(pack);
  console.log(`wrote ${PACKS[pack].file}`);
}
