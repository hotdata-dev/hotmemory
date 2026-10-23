#!/usr/bin/env bash
# Prepare and publish a release. RELEASING.md describes the procedure.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

die() { echo "error: $*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "$1 is required"; }

# A Python with tomllib (3.11 or later), from uv, outside the project environment.
py() { uv run --quiet --no-project --python '>=3.11' python "$@"; }

usage() {
  cat <<'EOF'
Usage:
  ./scripts/release.sh prepare [patch|minor|major|X.Y.Z]
  ./scripts/release.sh publish

  prepare  Bump the version, move the Unreleased notes in CHANGELOG.md into a
           section for the new version, run make verify, and open a release PR.
  publish  Tag the version on main and push the tag. The tag starts the PyPI
           publish and the GitHub Release.
EOF
}

get_field() {
  py - "$1" <<'PY'
import sys, tomllib
from pathlib import Path
print(tomllib.loads(Path("pyproject.toml").read_text())["project"][sys.argv[1]])
PY
}

set_version() {
  py - "$1" <<'PY'
import re, sys
from pathlib import Path
path = Path("pyproject.toml")
text, n = re.subn(r'(?m)^version = "[^"]+"', f'version = "{sys.argv[1]}"', path.read_text(), count=1)
if n != 1:
    raise SystemExit("could not update the version in pyproject.toml")
path.write_text(text)
PY
}

bump_version() {
  py - "$1" "$2" <<'PY'
import re, sys
kind, current = sys.argv[1], sys.argv[2]
match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", current)
if not match:
    raise SystemExit(f"cannot bump {current}; pass the new version as X.Y.Z")
major, minor, patch = (int(part) for part in match.groups())
if kind == "patch":
    patch += 1
elif kind == "minor":
    minor, patch = minor + 1, 0
elif kind == "major":
    major, minor, patch = major + 1, 0, 0
else:
    raise SystemExit(f"unknown bump kind: {kind}")
print(f"{major}.{minor}.{patch}")
PY
}

ensure_clean() {
  [[ -z "$(git status --porcelain)" ]] || die "working tree is not clean"
}

update_main() {
  git fetch origin main
  git checkout main
  git pull --ff-only origin main
  ensure_clean
}

cmd_prepare() {
  local bump="${1:-}"
  [[ -n "$bump" ]] || { usage; die "missing bump kind or version"; }
  need gh
  need uv
  ensure_clean
  update_main

  local current new branch
  current="$(get_field version)"
  if [[ "$bump" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    new="$bump"
  else
    new="$(bump_version "$bump" "$current")"
  fi
  [[ "$new" != "$current" ]] || die "new version ($new) equals current ($current)"
  git rev-parse --verify --quiet "refs/tags/v${new}" >/dev/null && die "tag v${new} exists"

  py scripts/changelog.py release "$new" "$(date +%Y-%m-%d)"
  set_version "$new"
  # uv.lock records the version of this project.
  uv lock
  branch="release/v${new}"
  git checkout -b "$branch"
  make verify

  git add pyproject.toml CHANGELOG.md uv.lock
  git commit -m "Release v${new}"
  git push -u origin "$branch"
  gh pr create --base main --head "$branch" \
    --title "Release hotmemory v${new}" \
    --body "Release hotmemory v${new}.

- [x] Version set in \`pyproject.toml\`
- [x] \`CHANGELOG.md\` has a \`## [${new}]\` section
- [ ] CI passes

After the merge, run \`./scripts/release.sh publish\` from a clean \`main\`."

  echo "Prepared hotmemory v${new}. Merge the PR, then run: ./scripts/release.sh publish"
}

cmd_publish() {
  need git
  need uv
  ensure_clean
  update_main

  local version tag
  version="$(get_field version)"
  tag="v${version}"
  git rev-parse --verify --quiet "refs/tags/${tag}" >/dev/null && die "tag ${tag} exists"
  py scripts/changelog.py notes "$version" >/dev/null

  git tag -a "$tag" -m "hotmemory ${version}"
  git push origin "$tag"
  echo "Pushed ${tag}. CI publishes to PyPI and creates the GitHub Release."
}

case "${1:-}" in
  prepare) shift; cmd_prepare "${1:-}" ;;
  publish) cmd_publish ;;
  -h|--help|help|"") usage ;;
  *) usage; die "unknown command: $1" ;;
esac
