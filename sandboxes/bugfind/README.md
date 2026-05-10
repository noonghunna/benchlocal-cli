# BugFind-15 sandbox

✅ Sandboxed verifier (v0.4 lifecycle).

## What it does

Runs candidate code fixes from a model under test through pytest. Returns deterministic pass/fail.

## Build

From the repo root:

```bash
bash tools/build-sandboxes.sh   # builds all 3
# OR just this one:
docker build -t benchlocal-sandbox-bugfind:latest sandboxes/bugfind/
```

## Run standalone (debug)

```bash
docker run --rm -p 9001:9000 benchlocal-sandbox-bugfind:latest
curl -sf http://localhost:9001/health
curl -sf -X POST http://localhost:9001/verify -H "Content-Type: application/json" -d '{"scenario_id":"BF-01", ...}'
```

## In a benchlocal-cli run

```bash
benchlocal-cli run --pack bugfind-15 --enable-sandboxed-packs --endpoint <model-endpoint>
```

The runner (`benchlocal_cli/sandbox.py`) starts this container automatically when `bugfind-15` is part of the requested pack list AND `--enable-sandboxed-packs` is set.

## Fixtures

`fixtures/<scenario-id>/` — lifted from `vendor/BugFind-15/lib/` by `tools/build-packs.js` at v0.4 generation time. Contents:

- `buggy.py` — original buggy code from upstream
- `test_fix.py` — pytest tests from upstream
- `expected.json` — pass criteria

Re-sync with upstream via:

```bash
bash tools/sync-vendor.sh BugFind-15
node tools/build-packs.js BugFind-15      # regenerates fixtures/ alongside the pack JSONL
docker build -t benchlocal-sandbox-bugfind:latest sandboxes/bugfind/   # rebuild
```
