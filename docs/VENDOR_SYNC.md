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
