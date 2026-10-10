"""benchlocal-cli — CLI port of BenchLocal quality bench packs.

Public API:
    benchlocal_cli.runner.Runner   — core orchestrator
    benchlocal_cli.cli.main        — CLI entry point (`benchlocal-cli ...`)

Pack data lives in `benchlocal_cli/packs/<pack-id>.jsonl`.
Verifier modules live in `benchlocal_cli/scoring/`.
"""

from functools import lru_cache as _lru_cache
from importlib.metadata import PackageNotFoundError, version as _installed_version
import os as _os
from pathlib import Path as _Path
import re as _re
import subprocess as _subprocess


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


def _git_commit(package_dir: _Path) -> str | None:
    """`<short sha>` of the git checkout ``package_dir`` belongs to, plus ``+dirty``
    when tracked files under it are modified; None when it is not a checkout (#194).

    ``__version__`` comes from pyproject.toml, which changes only in the release
    bump, so every run from master between two releases is stamped with the
    previous release however far the code has moved. The commit tells them apart.

    The package must be TRACKED in the repo git finds: a site-packages copy inside
    some other project's repo (a .venv in a checkout) would otherwise report that
    project's HEAD. Untracked files don't count as dirty. Never raises: no git, not
    a checkout, an error or a timeout all mean None, and the run goes on without it.
    """
    # GIT_DIR / GIT_WORK_TREE from a calling hook would point git at another repo.
    env = {k: v for k, v in _os.environ.items() if not k.startswith("GIT_")}

    def _git(*args: str) -> str | None:
        try:
            proc = _subprocess.run(
                ["git", "-C", str(package_dir), *args],
                capture_output=True, encoding="utf-8", errors="replace",
                timeout=5, env=env, check=False,
            )
        except (OSError, _subprocess.SubprocessError):
            return None
        return proc.stdout if proc.returncode == 0 else None

    if _git("ls-files", "--error-unmatch", "--", "__init__.py") is None:
        return None
    head = (_git("rev-parse", "--short", "HEAD") or "").strip()
    status = _git("status", "--porcelain", "--untracked-files=no", "--", ".")
    if not head or status is None:
        return None
    return head + ("+dirty" if status.strip() else "")


@_lru_cache(maxsize=1)
def runner_commit() -> str | None:
    """This package's own commit (see :func:`_git_commit`), looked up once per process."""
    return _git_commit(_Path(__file__).resolve().parent)
