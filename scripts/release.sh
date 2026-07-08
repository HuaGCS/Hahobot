#!/usr/bin/env bash
#
# Release helper for Hahobot.
#
# Usage:
#   scripts/release.sh patch|minor|major   # bump from current version
#   scripts/release.sh 0.2.0               # set an explicit version
#   scripts/release.sh patch --dry-run     # show what would happen, change nothing
#
# What it does (in order, aborting on the first failure):
#   1. Preflight: on `main`, clean working tree, up to date with origin.
#   2. Compute the new version and bump it in pyproject.toml + hahobot/__init__.py.
#   3. Require a matching `## [X.Y.Z]` section in CHANGELOG.md (add it first).
#   4. Gate on `ruff check`, `ruff format --check`, and the full pytest suite.
#   5. Commit `chore(release): vX.Y.Z`, tag `vX.Y.Z`, push main + tag.
#   6. Create the GitHub release with notes lifted from the CHANGELOG section.
#
# Prerequisites: run from a clean `main`; `gh` authenticated; the CHANGELOG
# section for the new version written before invoking.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

PYPROJECT="pyproject.toml"
INIT="hahobot/__init__.py"
CHANGELOG="CHANGELOG.md"

die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
info() { printf '\033[36m==>\033[0m %s\n' "$*"; }

BUMP="${1:-}"
DRY_RUN=false
[[ "${2:-}" == "--dry-run" ]] && DRY_RUN=true
[[ -z "$BUMP" ]] && die "usage: scripts/release.sh <patch|minor|major|X.Y.Z> [--dry-run]"

current="$(grep -E '^version = "' "$PYPROJECT" | head -1 | sed -E 's/^version = "([^"]+)"/\1/')"
[[ -z "$current" ]] && die "could not read current version from $PYPROJECT"

IFS='.' read -r MA MI PA <<<"$current"
case "$BUMP" in
  patch) new="$MA.$MI.$((PA + 1))" ;;
  minor) new="$MA.$((MI + 1)).0" ;;
  major) new="$((MA + 1)).0.0" ;;
  [0-9]*.[0-9]*.[0-9]*) new="$BUMP" ;;
  *) die "invalid bump '$BUMP' (use patch|minor|major or X.Y.Z)" ;;
esac

info "current: $current  ->  new: $new"
tag="v$new"

# --- changelog section must exist -----------------------------------------
# Match section headers as literal strings, not regex — a version like [0.1.3]
# would otherwise be read as a regex character class and never match.
grep -qF "## [$new]" "$CHANGELOG" || die "no '## [$new]' section in $CHANGELOG — add release notes there first"

# Extract the body of this version's section (between its header and the next '## [').
notes="$(awk -v ver="## [$new]" '
  index($0, ver) == 1 {grab=1; next}
  grab && index($0, "## [") == 1 {exit}
  grab {print}
' "$CHANGELOG" | sed '/./,$!d')"
[[ -n "$notes" ]] || die "CHANGELOG section for $new is empty"

if $DRY_RUN; then
  info "[dry-run] would bump $PYPROJECT and $INIT to $new"
  info "[dry-run] release notes would be:"
  printf '%s\n' "$notes"
  info "[dry-run] would: test -> commit -> tag $tag -> push -> gh release"
  exit 0
fi

# --- preflight ------------------------------------------------------------
[[ "$(git rev-parse --abbrev-ref HEAD)" == "main" ]] || die "not on 'main'"
[[ -z "$(git status --porcelain)" ]] || die "working tree not clean — commit or stash first"
git rev-parse -q --verify "refs/tags/$tag" >/dev/null && die "tag $tag already exists"
git fetch -q origin main
[[ "$(git rev-parse HEAD)" == "$(git rev-parse origin/main)" ]] || die "local main not in sync with origin/main"

# --- bump version ---------------------------------------------------------
sed -i -E "s/^version = \"$current\"/version = \"$new\"/" "$PYPROJECT"
sed -i -E "s/(_read_pyproject_version\(\) or \")$current(\")/\1$new\2/" "$INIT"
grep -q "version = \"$new\"" "$PYPROJECT" || die "failed to bump $PYPROJECT"
grep -q "or \"$new\"" "$INIT" || die "failed to bump $INIT"

# --- gates ----------------------------------------------------------------
info "ruff check"
uv run ruff check .
info "ruff format --check"
uv run ruff format --check .
info "pytest"
uv run pytest -q

# --- commit, tag, push, release -------------------------------------------
info "commit + tag $tag"
git add "$PYPROJECT" "$INIT"
git commit -q -m "chore(release): $tag"
git tag -a "$tag" -m "$tag"

info "push main + $tag"
git push -q origin main
git push -q origin "$tag"

info "gh release create $tag"
printf '%s\n' "$notes" | gh release create "$tag" --title "$tag" --notes-file -

info "released $tag  ->  https://github.com/HuaGCS/Hahobot/releases/tag/$tag"
