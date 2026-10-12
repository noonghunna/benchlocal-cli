"""Generated packs must be what `tools/build-packs.js` produces from `vendor/`.

A hand edit to a generated JSONL is silently reverted by the next
`node tools/build-packs.js --all`. #140's DE-07 `declares_top_level_shape`
flag and its 1.2.0 pack version were both lost that way until they moved
into the build (`docs/VENDOR_SYNC.md`)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKS = ROOT / "benchlocal_cli" / "packs"


def test_build_packs_all_reproduces_the_checked_in_packs(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to run tools/build-packs.js")
    shutil.copytree(ROOT / "tools", tmp_path / "tools")
    shutil.copytree(ROOT / "vendor", tmp_path / "vendor")
    shutil.copytree(PACKS, tmp_path / "benchlocal_cli" / "packs")

    subprocess.run([node, "tools/build-packs.js", "--all"], cwd=tmp_path, check=True, capture_output=True)

    drifted = [
        path.name
        for path in sorted(PACKS.glob("*.jsonl"))
        if (tmp_path / "benchlocal_cli" / "packs" / path.name).read_bytes() != path.read_bytes()
    ]
    assert not drifted, f"regeneration changes {drifted}: move the hand edit into tools/build-packs.js or vendor/"
