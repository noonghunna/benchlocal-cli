# Attribution

This repo ports MIT-licensed bench pack scenarios from the [BenchLocal](https://github.com/stevibe/BenchLocal) project and its individual pack repositories. All upstream work is by [@stevibe](https://github.com/stevibe).

## Source

| Pack | Upstream repo | Version ported | Upstream commit | License |
|---|---|---|---|---|
| ToolCall-15 | [stevibe/ToolCall-15](https://github.com/stevibe/ToolCall-15) | _TBD_ | _TBD_ | MIT |
| InstructFollow-15 | [stevibe/InstructFollow-15](https://github.com/stevibe/InstructFollow-15) | _TBD_ | _TBD_ | MIT |
| StructOutput-15 | [stevibe/StructOutput-15](https://github.com/stevibe/StructOutput-15) | _TBD_ | _TBD_ | MIT |
| ReasonMath-15 | [stevibe/ReasonMath-15](https://github.com/stevibe/ReasonMath-15) | _TBD_ | _TBD_ | MIT |
| DataExtract-15 | [stevibe/DataExtract-15](https://github.com/stevibe/DataExtract-15) | _TBD_ | _TBD_ | MIT |
| BugFind-15 | [stevibe/BugFind-15](https://github.com/stevibe/BugFind-15) | _TBD_ | _TBD_ | MIT |
| HermesAgent-20 | [stevibe/HermesAgent-20](https://github.com/stevibe/HermesAgent-20) | _TBD_ | _TBD_ | MIT |
| CLI-40 | [stevibe/CLI-40](https://github.com/stevibe/CLI-40) | _TBD_ | _TBD_ | MIT |

The `_TBD_` rows get filled in as each pack is ported. Each ported pack file (`benchlocal_cli/packs/<name>.jsonl`) carries a header line citing the source commit explicitly.

## What was ported

For each pack we lift:

- **Scenario IDs** — preserved verbatim so cross-comparison with BenchLocal desktop runs stays valid
- **Prompts** — system / user / tool definitions, unchanged in semantics
- **Sampling defaults** — temperature, top_p, max_tokens (per-pack)
- **Verifier intent** — the assertion logic, ported from TypeScript to Python; behavior should match upstream within deterministic-string-comparison tolerance

## What was NOT ported

- The Electron desktop app (`stevibe/BenchLocal` main repo) — irrelevant to a CLI port
- The TypeScript runtime adapter layer (`benchlocal/index.ts` per pack) — replaced by our Python runner
- The Bench Pack registry/install protocol — we vendor packs directly rather than fetch via the BenchLocal install flow
- Pack-specific Electron UI surfaces — N/A in a CLI

## Reporting drift / issues

If our ported verifier scores meaningfully diverge from the BenchLocal desktop app on the same model + endpoint, that's a porting bug — open an issue here. We aim for byte-equivalent verifier outcomes on every scenario.

## Acknowledgements

Thanks to @stevibe for building BenchLocal and the well-documented bench pack format that made this port tractable.
