# Contributing

## Upstream pack sync workflow

BenchLocal pack sources are vendored under `vendor/<PackName>/`. Generated runtime packs live under `benchlocal_cli/packs/` and should not be hand-edited for upstream syncs.

```bash
bash tools/sync-vendor.sh ToolCall-15
node tools/build-packs.js ToolCall-15
git diff vendor/ToolCall-15 benchlocal_cli/packs/toolcall-15.jsonl
git add vendor/ToolCall-15 benchlocal_cli/packs/toolcall-15.jsonl
git commit -m "feat: sync ToolCall-15 to upstream commit <sha>"
```

For all packs:

```bash
for pack in ToolCall-15 InstructFollow-15 StructOutput-15 ReasonMath-15 DataExtract-15 BugFind-15 HermesAgent-20 CLI-40; do
  bash tools/sync-vendor.sh "$pack"
done
node tools/build-packs.js --all
pytest tests/
```

## Sandbox verifier workflow

BugFind-15, HermesAgent-20, and CLI-40 require Docker verifier containers for full runs.

```bash
pip install -e '.[sandbox]'
bash tools/build-sandboxes.sh
bash tools/test-sandboxes.sh
benchlocal-cli run --full --enable-sandboxed-packs --endpoint http://localhost:8020 --model local-model
```

## Rules

- Keep Python runtime dependencies minimal: `httpx` and `jsonschema` only.
- Treat `vendor/` as the source of truth for pack content.
- Document lossy callback-to-assert translations in `docs/EXTRACTOR_NOTES.md`.
- Sandbox-backed packs keep `_stub` in JSONL as the runner dispatch marker; do not hand-edit generated pack files to point at container internals.
