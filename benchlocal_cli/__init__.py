"""benchlocal-cli — CLI port of BenchLocal quality bench packs.

Public API:
    benchlocal_cli.runner.Runner   — core orchestrator
    benchlocal_cli.cli.main        — CLI entry point (`benchlocal-cli ...`)

Pack data lives in `benchlocal_cli/packs/<pack-id>.jsonl`.
Verifier modules live in `benchlocal_cli/scoring/`.
"""

__version__ = "0.9.3"
