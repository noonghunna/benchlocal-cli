# HermesAgent-20 sandbox

🚧 Pre-alpha. To be implemented per [`CODEX_BRIEF_V4.md`](../../CODEX_BRIEF_V4.md) Phase D.

The most complex of the 3 sandboxes — multi-turn agent loop with 5 mocked tools.

## What it does

Runs a 20-scenario benchmark of agent behavior across 5 deterministic mocked tools (browser, cron, memory, artifact, trace). Each scenario is a multi-turn interaction:

1. Runner sends initial scenario to sandbox
2. Sandbox returns first prompt + tool definitions
3. Runner forwards to model endpoint, gets response
4. Sandbox parses tool call, simulates the tool, returns next prompt
5. Loop until verifier passes/fails or N=20 turn limit

## Why mocked tools (not real)

| Aspect | Real tools | Mocked tools |
|---|---|---|
| Determinism | Low (network, page changes, time) | High (bit-exact reproducibility) |
| Image size | ~1.5 GB (Playwright + Chromium) | ~150 MB |
| CI suitability | Brittle — pages move, sites go down | Stable across years |
| Behavior tested | Realistic but flaky | Canonical, repeatable |

For benchmarking, determinism wins. Real-tool variants can be a v0.5+ option for users who want them.

## Build

```bash
bash tools/build-sandboxes.sh
# OR just this one:
docker build -t benchlocal-sandbox-hermes:latest sandboxes/hermes/
```

## In a benchlocal-cli run

```bash
benchlocal-cli run --pack hermesagent-20 --enable-sandboxed-packs --endpoint <model-endpoint>
```

## The 5 mocked tools

Each is keyed on input and returns a deterministic response from the scenario's fixtures:

- **`browser(url)`** — keyed on URL → page content from `browser_responses.json`
- **`cron(when)`** — schedule offset → fixed timestamp using scenario's reference clock
- **`memory.{get,set,delete}(key, [value])`** — in-process dict, cleared between scenarios
- **`artifact.{read,write}(name, [bytes])`** — in-process bytes-store, same lifecycle
- **`trace.append(event)`** — append-only log, checked at end

## Fixtures

`fixtures/<scenario-id>/` — lifted from `vendor/HermesAgent-20/lib/` by the extractor:

- `initial_state.json` — starting memory + artifact state
- `browser_responses.json` — URL → canned page content
- `expected_trace.json` — trace verifier compares against
- `expected_final_state.json` — final memory + artifact state to match

## Re-sync

```bash
bash tools/sync-vendor.sh HermesAgent-20
node tools/build-packs.js HermesAgent-20
docker build -t benchlocal-sandbox-hermes:latest sandboxes/hermes/
```
