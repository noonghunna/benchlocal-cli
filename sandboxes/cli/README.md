# CLI-40 sandbox

✅ Sandboxed verifier (v0.4 lifecycle).

## What it does

Runs candidate shell commands from a model under test in a hardened Linux sandbox. Verifies stdout/stderr/exit-code against expected outcomes from scenario fixtures.

## Build

```bash
bash tools/build-sandboxes.sh
# OR just this one:
docker build -t benchlocal-sandbox-cli:latest sandboxes/cli/
```

## Security

Container runs:

- As non-root user (UID/GID `verifier`)
- With `--network none` (no network access)
- Working dir limited to `/tmp/cli-sandbox/` (tmpfs-backed; cleared between requests)
- Per-command timeout: 10s (configurable per scenario)
- Output capped at 64 KB

## In a benchlocal-cli run

```bash
benchlocal-cli run --pack cli-40 --enable-sandboxed-packs --endpoint <model-endpoint>
```

## Fixtures

`fixtures/<scenario-id>/` — lifted from `vendor/CLI-40/` by the extractor:

- `input.txt` — input data the command operates on
- `expected.json` — `{stdout: "...", stderr: "...", exit_code: 0, tolerance: {...}}`

Re-sync via:

```bash
bash tools/sync-vendor.sh CLI-40
node tools/build-packs.js CLI-40
docker build -t benchlocal-sandbox-cli:latest sandboxes/cli/
```
