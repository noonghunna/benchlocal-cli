# Attribution

This repo ports MIT-licensed bench pack scenarios from the [BenchLocal](https://github.com/stevibe/BenchLocal) project and its individual pack repositories. All upstream work is by [@stevibe](https://github.com/stevibe).

## Source

| Pack | Upstream repo | Version ported | Upstream commit | License |
|---|---|---|---|---|
| ToolCall-15 | [stevibe/ToolCall-15](https://github.com/stevibe/ToolCall-15) | 1.0.1 | `edd6cefe4261b67e8166e9f6d77d671042560294` | MIT |
| InstructFollow-15 | [stevibe/InstructFollow-15](https://github.com/stevibe/InstructFollow-15) | 1.0.0 | `187af97cb2b892ad57de176b16a254aba7565a65` | MIT |
| StructOutput-15 | [stevibe/StructOutput-15](https://github.com/stevibe/StructOutput-15) | 1.0.0 | `00de86e9bfc9dd3d86ba397d0cf35bcbc04efd1c` | MIT |
| ReasonMath-15 | [stevibe/ReasonMath-15](https://github.com/stevibe/ReasonMath-15) | 1.0.0 | `b97632020fa373c52ba92373dbe5dc58b744ce48` | MIT |
| DataExtract-15 | [stevibe/DataExtract-15](https://github.com/stevibe/DataExtract-15) | 1.0.0 | `00d90bf7506a1d7ffe98943d9ffd8c6eb795dbdb` | MIT |
| BugFind-15 | [stevibe/BugFind-15](https://github.com/stevibe/BugFind-15) | 1.0.0 | `59f2e96f0c64b447ad6a545fc1e2416efce512b6` | MIT |
| HermesAgent-20 | [stevibe/HermesAgent-20](https://github.com/stevibe/HermesAgent-20) | 1.0.0 | `fa40ab9fb84a329421bbdfc3062cf28f1670de71` | MIT |
| CLI-40 | [stevibe/CLI-40](https://github.com/stevibe/CLI-40) | 1.0.2 | `3b95f86e6edac47183348381a9bb211ffaf09404` | MIT |

Each ported pack file (`benchlocal_cli/packs/<name>.jsonl`) carries a header line citing the source commit explicitly.

## What was ported

For each pack we lift:

- **Scenario IDs** — preserved verbatim so cross-comparison with BenchLocal desktop runs stays valid
- **Prompts** — generated from vendored upstream TypeScript/JSON mirrors; deterministic pack system and user prompts are preserved verbatim
- **Sampling defaults** — temperature, top_p, max_tokens (per-pack)
- **Verifier intent** — deterministic Python assertion primitives generated from upstream callback intent where practical; sandbox-backed verifiers remain deferred

## What was NOT ported

- The Electron desktop app (`stevibe/BenchLocal` main repo) — irrelevant to a CLI port
- The TypeScript runtime adapter layer (`benchlocal/index.ts` per pack) — replaced by our Python runner
- The Bench Pack registry/install protocol — we vendor packs directly rather than fetch via the BenchLocal install flow
- Pack-specific Electron UI surfaces — N/A in a CLI

## How to re-sync with upstream

```bash
bash scripts/sync-vendor.sh ToolCall-15
node scripts/build-packs.js ToolCall-15
git diff vendor/ToolCall-15 benchlocal_cli/packs/toolcall-15.jsonl
```

Run `node scripts/build-packs.js --all` after broad upstream syncs. The generated JSONL metadata records `_synced_from_commit` for traceability.

## Reporting drift / issues

If our ported verifier scores meaningfully diverge from the BenchLocal desktop app on the same model + endpoint, that's a porting bug — open an issue here. We aim for byte-equivalent verifier outcomes on every scenario.

## Acknowledgements

Thanks to @stevibe for building BenchLocal and the well-documented bench pack format that made this port tractable.
