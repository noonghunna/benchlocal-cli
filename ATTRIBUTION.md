# Attribution

This repo ports MIT-licensed bench pack scenarios from the [BenchLocal](https://github.com/stevibe/BenchLocal) project and its individual pack repositories. All upstream work is by [@stevibe](https://github.com/stevibe).

## Source

| Pack | Upstream repo | Version ported | Upstream commit | License |
|---|---|---|---|---|
| ToolCall-15 | [stevibe/ToolCall-15](https://github.com/stevibe/ToolCall-15) | 1.0.1 | `615b1576e257a1b859f6e9183d52408bb4e05ee3` | MIT |
| InstructFollow-15 | [stevibe/InstructFollow-15](https://github.com/stevibe/InstructFollow-15) | 1.0.0 | `536a1044aef0acbfdbc5a19ca49170f5346d3cd4` | MIT |
| StructOutput-15 | [stevibe/StructOutput-15](https://github.com/stevibe/StructOutput-15) | 1.0.0 | `b82f11cc85bbbb7814c2d85e47fa9fa8104d5c4a` | MIT |
| ReasonMath-15 | [stevibe/ReasonMath-15](https://github.com/stevibe/ReasonMath-15) | 1.0.0 | `78ef138cc26ad6b11f89238e20430de9a9e862aa` | MIT |
| DataExtract-15 | [stevibe/DataExtract-15](https://github.com/stevibe/DataExtract-15) | 1.0.0 | `ec3a131ff218ced7c6d0d524af764eae5c25665b` | MIT |
| BugFind-15 | [stevibe/BugFind-15](https://github.com/stevibe/BugFind-15) | 1.0.1 | `131aadea0ae118ef40cb468e7c35d1fde8676c98` | MIT |
| HermesAgent-20 | [stevibe/HermesAgent-20](https://github.com/stevibe/HermesAgent-20) | 1.0.0 | `57d7766bf3db8c40696e3ed937d43c8c85f4cd6c` | MIT |
| CLI-40 | [stevibe/CLI-40](https://github.com/stevibe/CLI-40) | 1.0.2 | `d43939fc6760b6869f14efb558fc8814606c8f41` | MIT |

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
bash tools/sync-vendor.sh ToolCall-15
node tools/build-packs.js ToolCall-15
git diff vendor/ToolCall-15 benchlocal_cli/packs/toolcall-15.jsonl
```

Run `node tools/build-packs.js --all` after broad upstream syncs. The generated JSONL metadata records `_synced_from_commit` for traceability.

## Reporting drift / issues

If our ported verifier scores meaningfully diverge from the BenchLocal desktop app on the same model + endpoint, that's a porting bug — open an issue here. We aim for byte-equivalent verifier outcomes on every scenario.

## Acknowledgements

Thanks to @stevibe for building BenchLocal and the well-documented bench pack format that made this port tractable.
