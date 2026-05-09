# Integration with club-3090

This doc explains how [club-3090](https://github.com/noonghunna/club-3090) (and other repos following a similar pattern) consume `benchlocal-cli`.

## Install

In the parent project:

```bash
# from PyPI (once published)
pip install benchlocal-cli

# or from source
pip install -e /path/to/benchlocal-cli

# or as a git dependency in pyproject.toml / requirements.txt
pip install git+https://github.com/noonghunna/benchlocal-cli.git
```

## Usage in club-3090's `scripts/quality-test.sh`

```bash
#!/usr/bin/env bash
# scripts/quality-test.sh — wrapper around benchlocal-cli
#
# Auto-detects the running compose endpoint, resolves the model id, and runs
# benchlocal-cli with the right flags. Output goes to stdout (markdown) AND
# is saved to results/quality-<timestamp>.json for delta tracking.

set -euo pipefail

# Auto-detect (uses the same preflight library that bench.sh / soak-test.sh use)
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT_DIR}/scripts/preflight.sh"
preflight_autodetect_endpoint   # sets URL=http://localhost:PORT
preflight_autodetect_model      # sets MODEL=qwen3.6-27b-autoround (etc)

# Default mode = medium; allow --quick / --full overrides via $1
MODE="${1:---medium}"

# Save run to results/ (gitignored)
RESULTS_DIR="${ROOT_DIR}/results/quality"
mkdir -p "$RESULTS_DIR"
TS=$(date +%Y-%m-%dT%H-%M-%S)
JSON_OUT="${RESULTS_DIR}/quality-${TS}.json"

benchlocal-cli run "$MODE" \
  --endpoint "$URL" \
  --model "$MODEL" \
  --output markdown \
  --save-json "$JSON_OUT"

echo
echo "Run saved to: $JSON_OUT"
echo "For delta vs previous run, use: benchlocal-cli run $MODE --previous-result $JSON_OUT --emit-delta"
```

## Output format for club-3090's `Quality:` schema field

The compose `Quality:` field uses a compact one-liner derived from a benchlocal-cli `--medium` run:

```
Status:    ✅ Production
Quality:   ToolCall-15 14/15 (93%) · InstructFollow-15 13/15 (87%) · StructOutput-15 15/15 (100%) · DataExtract-15 12/15 (80%) (--medium, packs v1.0.x, 2026-05-09)
```

The script that generates this from a JSON result blob lives in:

```
scripts/quality-summary.sh   # consumes benchlocal-cli's JSON output, emits compose-Quality-line
```

## Where benchlocal-cli sits in the test pipeline

club-3090's existing test pipeline:

```
scripts/verify.sh        — fast smoke (15s, "does it serve?")
scripts/verify-full.sh   — functional (1-2min, "does everything work?")
scripts/verify-stress.sh — boundary (5-10min, "does it survive stress?")
scripts/bench.sh         — throughput (3-5min, "what's the TPS?")
scripts/soak-test.sh     — stability (30-60min, "does it stay healthy?")

scripts/quality-test.sh  — quality (10-30min depending on mode, "does it produce useful output?")  ← NEW
```

Quality testing slots between bench and soak. It's the "behavioral correctness" layer that the existing pipeline doesn't cover.

## Reproducibility

Each compose's `Quality:` line cites:

- The mode (`--quick` / `--medium` / `--full`)
- Pack versions (e.g. `v1.0.1` for ToolCall-15)
- Date

For a regression bisect, the JSON blob saved per-run has everything needed to re-run a single scenario:

```bash
# diff two runs
benchlocal-cli diff results/quality/quality-2026-05-08T10-00.json results/quality/quality-2026-05-09T10-00.json

# re-run a specific failing scenario for debugging
benchlocal-cli reproduce --result results/quality/quality-2026-05-09T10-00.json --scenario toolcall-15-007
```

(The `diff` and `reproduce` subcommands are post-v1; v1 ships only the `run` and `list` commands.)
