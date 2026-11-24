#!/usr/bin/env bash
# Checks every commit in a revision range on its own, like the "Commits" workflow does for a pull request: lint,
# formatting, types and tests (scripts/check). It works in a temporary git worktree with a clean checkout per commit,
# so the current working tree and its index are left alone, even while other people or agents use them.
# It uses this checkout's .venv unless PYTHON names another interpreter.
#
# Usage: .github/scripts/test-each-commit.sh origin/main..HEAD
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "usage: $0 <revision-range>   (for example: origin/main..HEAD)" >&2
  exit 2
fi
range=$1
python=${PYTHON:-$(git rev-parse --show-toplevel)/.venv/bin/python}
if [ ! -x "$python" ]; then
  echo "No Python at $python. Run scripts/setup first, or set PYTHON." >&2
  exit 2
fi

commits=$(git rev-list --reverse "$range")
if [ -z "$commits" ]; then
  echo "No commits to test in $range."
  exit 0
fi

worktree=$(mktemp -d "${TMPDIR:-/tmp}/test-each-commit.XXXXXX")
cleanup() {
  git worktree remove --force "$worktree" 2>/dev/null || {
    rm -rf "$worktree"
    git worktree prune
  }
}
trap cleanup EXIT
git worktree add --quiet --detach "$worktree" HEAD

total=0
failed=0
results=""
for sha in $commits; do
  total=$((total + 1))
  title=$(git log -1 --no-show-signature --format='%h  %s' "$sha")
  echo "=== $title"
  git -C "$worktree" checkout --quiet --detach "$sha"
  git -C "$worktree" clean -fdxq
  if PYTHON=$python "$worktree/scripts/check" -p no:cacheprovider; then
    results+="ok    $title"$'\n'
  else
    failed=$((failed + 1))
    results+="FAIL  $title"$'\n'
  fi
done

echo
printf '%s' "$results"
echo
if [ $failed -gt 0 ]; then
  echo "$failed of $total commit(s) failed."
  exit 1
fi
echo "All $total commit(s) passed."
