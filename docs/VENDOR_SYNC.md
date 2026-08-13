# Vendor Sync

`tools/sync-vendor.sh <PackName>` mirrors upstream BenchLocal pack source into
`vendor/<PackName>/`.

## v0.7 Sandbox Fixture Source

The sandboxed packs do not expose static fixture trees such as
`scenarios/<id>/workspace` in the upstream repositories. The fixture source of
truth is the upstream `verification/` runtime:

| Pack | Fixture source |
|---|---|
| BugFind-15 | `verification/manifest.mjs` embeds buggy/fixed source files and executable checks |
| CLI-40 | `verification/core.mjs` programmatically seeds workspaces and grades outputs |
| HermesAgent-20 | `verification/core.mjs` + `verification/hermes-runtime.mjs` define the pinned runtime checks |

The sync script now copies top-level `verification/*` files for any upstream
pack that provides them. Re-run:

```bash
bash tools/sync-vendor.sh BugFind-15
bash tools/sync-vendor.sh CLI-40
bash tools/sync-vendor.sh HermesAgent-20
node tools/build-packs.js BugFind-15
node tools/build-packs.js CLI-40
node tools/build-packs.js HermesAgent-20
```

Do not hand-edit generated JSONL pack files for fixture updates; adjust
`tools/build-packs.js` or the vendored upstream files instead.

## Local divergences from upstream (re-sync hazard)

These vendored files intentionally differ from their upstream commits.
A re-sync (`sync-vendor.sh`) would silently revert them — re-apply or
upstream these changes first:

| File | Divergence | Why |
|---|---|---|
| `vendor/DataExtract-15/lib/benchmark.ts` + `benchlocal.pack.json` | All 15 prompts declare `Fields and JSON types:`; version 1.1.0 | The upstream prompts leave field types undeclared while six expected values contradict the pack's own numeric-strip rule (audit of 2026-08-12, #123/#124/#127 thread) |
| `vendor/StructOutput-15/lib/benchmark.ts` + `benchlocal.pack.json` | SO-07 prompt names the required `user`/`metadata` top-level keys; version 1.1.0 | Upstream prompt never names the wrapper the schema requires (#124) |
| `vendor/CLI-40/verification/scenario-data.json` | CLI-13/CLI-17 success/failure case text | Carries the d4a2fae fairness edits that originally landed in the JSONL only |

Regeneration must stay idempotent: `node tools/build-packs.js --all`
from a clean tree produces no diff. `tools/build-packs.js` carries the
local knobs that keep it that way (`RM_ACCEPTED_ANSWER_OVERRIDES` for
#132's RM-04 variants, `samplingMaxTokens`/`timeoutBaselineTokens` for
#105's reasonmath timeout budget).
