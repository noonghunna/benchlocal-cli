# Contributing

## Upstream pack sync workflow

BenchLocal pack sources are vendored under `vendor/<PackName>/`. Generated runtime packs live under `benchlocal_cli/packs/` and should not be hand-edited for upstream syncs.

```bash
bash scripts/sync-vendor.sh ToolCall-15
node scripts/build-packs.js ToolCall-15
git diff vendor/ToolCall-15 benchlocal_cli/packs/toolcall-15.jsonl
git add vendor/ToolCall-15 benchlocal_cli/packs/toolcall-15.jsonl
git commit -m "feat: sync ToolCall-15 to upstream commit <sha>"
```

For all packs:

```bash
for pack in ToolCall-15 InstructFollow-15 StructOutput-15 ReasonMath-15 DataExtract-15 BugFind-15 HermesAgent-20 CLI-40; do
  bash scripts/sync-vendor.sh "$pack"
done
node scripts/build-packs.js --all
pytest tests/
```

## Rules

- Keep Python runtime dependencies minimal: `httpx` and `jsonschema` only.
- Treat `vendor/` as the source of truth for pack content.
- Document lossy callback-to-assert translations in `docs/EXTRACTOR_NOTES.md`.
- Sandbox-backed packs stay `_stub` until the sandbox verifier runtime lands.
