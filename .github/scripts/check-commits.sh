#!/usr/bin/env bash
# Checks every non-merge commit in a revision range, the same way the "Commits" workflow checks a pull request:
#   - the subject has at most 72 characters, starts with a capital letter and an imperative verb ("Add", not
#     "Added" or "Adds") and doesn't end with a period
#   - a blank line separates the subject from the body
#   - no fixup!, squash!, amend! or WIP commits
#   - no whitespace errors or leftover conflict markers (git diff --check)
#
# Usage: .github/scripts/check-commits.sh origin/main..HEAD
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "usage: $0 <revision-range>   (for example: origin/main..HEAD)" >&2
  exit 2
fi
range=$1
max_subject=72

# Stems of common verbs; a subject starting with one plus a suffix below ("Added", "Fixes", "Updating") isn't imperative.
stems='Add|Fix|Updat|Remov|Delet|Chang|Implement|Refactor|Improv|Creat|Renam|Mov|Bump|Clean|Document|Introduc'
stems+='|Replac|Support|Enabl|Disabl|Allow|Prevent|Handl|Mak|Us|Test|Rework|Revert|Restor|Switch|Upgrad|Merg'
stems+='|Clarif|Simplif|Ensur|Convert|Migrat|Extract|Adjust|Correct|Tweak|Format|Rewrit|Skipp|Stopp|Drop'
non_imperative="^(${stems})(ed|es|s|ing|ied|ies|ying|ped|ping)$|^(Made|Wrote|Built|Kept|Ran)$"
wip='^\[?[Ww][Ii][Pp]\]?([^A-Za-z]|$)'

annotate() { # <short sha> <message>
  if [ "${GITHUB_ACTIONS:-}" = true ]; then
    local msg=$2
    msg=${msg//'%'/%25}
    msg=${msg//$'\r'/%0D}
    msg=${msg//$'\n'/%0A}
    echo "::error title=Commit $1::$msg"
  fi
}

commits=$(git rev-list --reverse --no-merges "$range")
if [ -z "$commits" ]; then
  echo "No commits to check in $range."
  exit 0
fi

empty_tree=$(git hash-object -t tree /dev/null)
checked=0
failed=0
for sha in $commits; do
  checked=$((checked + 1))
  short=$(git rev-parse --short "$sha")
  message=$(git log -1 --no-show-signature --format=%B "$sha")
  subject=$(printf '%s\n' "$message" | sed -n 1p)
  second_line=$(printf '%s\n' "$message" | sed -n 2p)
  parent=$(git rev-parse --verify --quiet "$sha^" || echo "$empty_tree")

  problems=()
  case $subject in
    fixup!* | squash!* | amend!*)
      problems+=("${subject%%!*}! commit: squash it into its target before merging (git rebase -i --autosquash)") ;;
  esac
  if [[ $subject =~ $wip ]]; then
    problems+=("work-in-progress commit: finish or squash it before merging")
  fi
  # Commits that must be squashed anyway don't get the message style checks.
  if [ ${#problems[@]} -eq 0 ]; then
    if [ ${#subject} -gt $max_subject ]; then
      problems+=("the subject has ${#subject} characters; keep it to $max_subject or fewer")
    fi
    if [[ ! $subject =~ ^[A-Z] ]]; then
      problems+=("start the subject with a capital letter")
    fi
    if [[ $subject == *. ]]; then
      problems+=("don't end the subject with a period")
    fi
    first_word=${subject%%[^A-Za-z]*}
    if [[ $first_word =~ $non_imperative ]]; then
      problems+=("use the imperative mood: start with a verb like \"Add\" or \"Fix\", not \"$first_word\"")
    fi
    if [ -n "$second_line" ]; then
      problems+=("leave the second line of the message blank, between the subject and the body")
    fi
  fi
  if ! whitespace=$(git diff --check "$parent" "$sha" 2>&1); then
    problems+=("whitespace errors or conflict markers (git diff --check):"$'\n'"$whitespace")
  fi

  if [ ${#problems[@]} -eq 0 ]; then
    echo "ok    $short  $subject"
    continue
  fi
  failed=$((failed + 1))
  echo "FAIL  $short  $subject"
  for problem in "${problems[@]}"; do
    printf '%s\n' "$problem" | sed -e '1s/^/        - /' -e '2,$s/^/          /'
    annotate "$short" "$problem"
  done
done

echo
if [ $failed -gt 0 ]; then
  echo "$failed of $checked commit(s) need changes. Reword or fix them with an interactive rebase, then force-push."
  exit 1
fi
echo "All $checked commit(s) passed."
