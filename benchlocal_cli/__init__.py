"""benchlocal-cli — CLI port of BenchLocal quality bench packs.

Public API:
    benchlocal_cli.runner.Runner   — core orchestrator
    benchlocal_cli.cli.main        — CLI entry point (`benchlocal-cli ...`)

Pack data lives in `benchlocal_cli/packs/<pack-id>.jsonl`.
Verifier modules live in `benchlocal_cli/scoring/`.
"""

from importlib.metadata import PackageNotFoundError, version as _installed_version
from pathlib import Path as _Path
import re as _re


def _version() -> str:
    """Resolve the version. pyproject.toml is the single source of truth.

    This used to be a hardcoded literal and it silently went stale: v0.9.9 was
    tagged straight onto a merge commit with no bump, so the release shipped
    reporting 0.9.8. Because __version__ is stamped into every results file as
    `runner_version`, that made v0.9.9 runs indistinguishable from v0.9.8 runs --
    across a release that changed the timeout clock and hermes pinning (#105),
    i.e. across a scoring-relevant boundary.

    ⚠️ Order matters: an ADJACENT pyproject.toml wins over installed metadata.
    Reading metadata first looks cleaner and is wrong here -- a stale
    `benchlocal_cli.egg-info/` left in a checkout by an older build makes
    importlib.metadata report THAT version while you run today's source. On this
    repo a 0.9.4 egg-info did exactly that. When there is no adjacent pyproject
    (i.e. a real site-packages install) metadata is the right answer.
    """
    pyproject = _Path(__file__).resolve().parent.parent / "pyproject.toml"
    if pyproject.is_file():
        m = _re.search(r'^version\s*=\s*"([^"]+)"',
                       pyproject.read_text(encoding="utf-8"), _re.M)
        if m:
            return m.group(1)
    try:
        return _installed_version("benchlocal-cli")
    except PackageNotFoundError:
        return "0+unknown"


__version__ = _version()
