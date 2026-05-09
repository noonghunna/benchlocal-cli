#!/usr/bin/env bash
set -euo pipefail

PACK="${1:-}"
if [[ -z "$PACK" ]]; then
  echo "usage: bash tools/sync-vendor.sh <PackName>" >&2
  echo "example: bash tools/sync-vendor.sh ToolCall-15" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/vendor/$PACK"
REPO="stevibe/$PACK"

command -v gh >/dev/null || {
  echo "sync-vendor.sh requires GitHub CLI (gh)" >&2
  exit 1
}

mkdir -p "$DEST/lib"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

commit="$(gh api "repos/$REPO/commits/HEAD" --jq '.sha')"

gh api "repos/$REPO/contents/benchlocal.pack.json?ref=$commit" --jq '.content' |
  base64 -d > "$tmp/benchlocal.pack.json"

while IFS= read -r name; do
  gh api "repos/$REPO/contents/lib/$name?ref=$commit" --jq '.content' |
    base64 -d > "$tmp/$name"
done < <(gh api "repos/$REPO/contents/lib?ref=$commit" --jq '.[] | select(.type=="file") | .name')

rm -rf "$DEST/lib"
mkdir -p "$DEST/lib"
cp "$tmp/benchlocal.pack.json" "$DEST/benchlocal.pack.json"
find "$tmp" -maxdepth 1 -type f ! -name benchlocal.pack.json -exec cp {} "$DEST/lib/" \;

if [[ "$PACK" == "CLI-40" ]]; then
  mkdir -p "$DEST/verification"
  gh api "repos/$REPO/contents/verification/scenario-data.json?ref=$commit" --jq '.content' |
    base64 -d > "$DEST/verification/scenario-data.json"
fi

python3 - "$DEST" "$PACK" "$commit" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

dest = Path(sys.argv[1])
pack = sys.argv[2]
commit = sys.argv[3]
files = sorted(str(path.relative_to(dest)) for path in dest.rglob("*") if path.is_file() and path.name != "_sync.json")
payload = {
    "commit": commit,
    "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "source_url": f"https://github.com/stevibe/{pack}",
    "source_files": files,
}
(dest / "_sync.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
