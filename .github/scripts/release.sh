#!/usr/bin/env bash
# The checks of the Release workflow (.github/workflows/release.yml, decision 29). A release is a tag vX.Y.Z on a commit
# of main whose custom_components/sonoff2hue/manifest.json has the version X.Y.Z. It gets the GitHub Release
# "sonoff2hue X.Y.Z", from which the Marketplace installs the tag's source tree. The Release is Latest if X.Y.Z is the
# highest released version, and its notes start at the highest released version below X.Y.Z. The released versions are
# the tags vX.Y.Z of the repository's published GitHub Releases. Git tags alone don't count, so a stray or refused tag,
# such as a v9.9.9 that the checks stopped, changes neither.
#
#   release.sh version <ref>                  the tag is exactly vX.Y.Z: prints X.Y.Z for refs/tags/vX.Y.Z
#   release.sh check-version <X.Y.Z> <commit> X.Y.Z is the version in the commit's
#                                             custom_components/sonoff2hue/manifest.json
#   release.sh check-main <commit> [<main>]   the commit is on main (default origin/main; needs main's history)
#   release.sh released <owner/repository>    the released versions: the tags vX.Y.Z of the repository's published
#                                             GitHub Releases, neither drafts nor pre-releases, as X.Y.Z in numeric
#                                             order. It lists them with gh, which needs GH_TOKEN in a workflow
#   release.sh latest <X.Y.Z>                 whether the GitHub Release of X.Y.Z is Latest: true if X.Y.Z is the
#                                             highest of itself and the released versions on stdin, else false
#   release.sh previous <X.Y.Z>               where the GitHub Release's notes start: the highest released version
#                                             below X.Y.Z, from the released versions on stdin, or nothing
#
# A check that fails prints a GitHub Actions error on stderr and exits with 1; wrong usage exits with 2. It needs bash
# 3.2 or later and git, and check-version and released need python3; released also needs gh.
# .github/scripts/release-test.sh tests it, and the whole workflow, without a network: with a scratch git repository
# and a stand-in for gh.
set -euo pipefail

manifest_path=custom_components/sonoff2hue/manifest.json
version_re='^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'
tag_re="^v${version_re#^}"
work=""

usage() {
    sed -n '/^#   release\.sh /,/^#$/s/^# \{0,1\}//p' "$0" >&2
    exit 2
}

# A GitHub Actions error annotation, escaped as a workflow command's message.
error() {
    local message=$*
    message=${message//'%'/%25}
    message=${message//$'\r'/%0D}
    message=${message//$'\n'/%0A}
    printf '::error::%s\n' "$message" >&2
}

fail() {
    error "$@"
    exit 1
}

# A folder for intermediate files, removed on exit.
make_work() {
    work=$(mktemp -d)
    trap 'rm -rf "$work"' EXIT
}

# The lines of stdin that are plain versions X.Y.Z, in numeric order (1.10.0 after 1.9.0) and without duplicates.
versions() {
    local line
    while IFS= read -r line || [ -n "$line" ]; do
        if [[ $line =~ $version_re ]]; then printf '%s\n' "$line"; fi
    done | sort -t . -k 1,1n -k 2,2n -k 3,3n -u
}

cmd_version() { # ref
    [ $# -eq 1 ] || usage
    local tag=${1#refs/tags/}
    if [ "$tag" = "$1" ] || [[ ! $tag =~ $tag_re ]]; then
        fail "$tag isn't a release tag. Only a tag vX.Y.Z is released: three numbers without leading zeros and" \
            "without a suffix such as -rc.1, for example v1.2.3. No GitHub Release was created."
    fi
    printf '%s\n' "${tag#v}"
}

# Compares the version of the manifest on stdin with the expected one, exactly: exits with 0 if they're the same, with
# 3 and the manifest's version, as JSON, or "which has none" if they aren't, and with 1 and the reason on stderr if the
# manifest isn't a JSON object.
compare_version_py=$(
    cat << 'PY'
import json
import sys

try:
    manifest = json.loads(sys.stdin.buffer.read())
except ValueError as error:
    sys.exit(f"isn't valid JSON ({error})")
if not isinstance(manifest, dict):
    sys.exit("isn't a JSON object")
if "version" not in manifest:
    print("which has none")
    sys.exit(3)
if manifest["version"] != sys.argv[1]:
    print(json.dumps(manifest["version"], ensure_ascii=False))
    sys.exit(3)
PY
)

cmd_check_version() { # X.Y.Z commit
    [ $# -eq 2 ] || usage
    [[ $1 =~ $version_re ]] || usage
    local version=$1 commit=$2 declared status=0
    make_work
    git show "$commit:$manifest_path" > "$work/manifest" 2> /dev/null \
        || fail "Couldn't read $manifest_path in the tagged commit $commit. No GitHub Release was created."
    declared=$(python3 -c "$compare_version_py" "$version" < "$work/manifest" 2> "$work/errors") || status=$?
    case $status in
        0) echo "v$version is the version in the tagged commit's $manifest_path." ;;
        3) fail "The tag v$version doesn't match the version in the tagged commit's $manifest_path, $declared." \
            "Raise the version through a pull request first, then tag the merged commit on main with v<version>." \
            "No GitHub Release was created." ;;
        1) fail "The tagged commit's $manifest_path $(cat "$work/errors"). No GitHub Release was created." ;;
        *) fail "Couldn't read the version in the tagged commit's $manifest_path (exit code $status):" \
            "$(cat "$work/errors") No GitHub Release was created." ;;
    esac
}

cmd_check_main() { # commit [main]
    [ $# -eq 1 ] || [ $# -eq 2 ] || usage
    local main=${2:-origin/main} commit status=0
    git rev-parse --verify --quiet "$main^{commit}" > /dev/null \
        || fail "Couldn't find $main to check the tagged commit against. No GitHub Release was created."
    commit=$(git rev-parse --verify --quiet "$1^{commit}") \
        || fail "Couldn't find the tagged commit $1. No GitHub Release was created."
    git merge-base --is-ancestor "$commit" "$main" || status=$?
    case $status in
        0) echo "The tagged commit $commit is on main." ;;
        1) fail "The tagged commit $commit isn't on main. Only a commit of main is released: merge its pull request" \
            "first, then tag the merged commit on main. No GitHub Release was created." ;;
        *) fail "Couldn't check whether the tagged commit $commit is on main (git merge-base exited with" \
            "$status). No GitHub Release was created." ;;
    esac
}

# Prints the tags of the published releases in gh's answer, the file named by the argument, one per line. gh merges the
# pages of a list into one JSON array; a list of pages one after the other is read too. Exits with 1 and the reason on
# stderr if the answer isn't a list of releases.
release_tags_py=$(
    cat << 'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as answer:
    text = answer.read()
decoder = json.JSONDecoder()
position = 0
pages = 0
while True:
    while position < len(text) and text[position] in " \t\r\n":
        position += 1
    if position == len(text):
        break
    try:
        page, position = decoder.raw_decode(text, position)
    except ValueError as error:
        sys.exit(f"gh's answer isn't JSON ({error})")
    pages += 1
    if not isinstance(page, list) or not all(isinstance(release, dict) for release in page):
        sys.exit(f"gh's answer isn't a list of releases: {json.dumps(page)[:200]}")
    for release in page:
        tag = release.get("tag_name")
        if not release.get("draft") and not release.get("prerelease") and isinstance(tag, str):
            print(tag)
if pages == 0:
    sys.exit("gh printed nothing")
PY
)

cmd_released() { # owner/repository
    [ $# -eq 1 ] || usage
    [[ $1 =~ ^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$ ]] || usage
    local repository=$1 status=0 tag released
    make_work
    gh api --paginate "repos/$repository/releases?per_page=100" > "$work/answer" 2> "$work/errors" || status=$?
    if [ $status -ne 0 ]; then
        sed 's/^/  /' "$work/errors" >&2
        fail "Couldn't list the GitHub Releases of $repository (gh exited with $status). No GitHub Release was" \
            "created; run the job again."
    fi
    python3 -c "$release_tags_py" "$work/answer" > "$work/tags" 2> "$work/errors" \
        || fail "Couldn't read the GitHub Releases of $repository: $(cat "$work/errors"). No GitHub Release was" \
            "created; run the job again."
    released=$(while IFS= read -r tag; do
        if [[ $tag =~ $tag_re ]]; then printf '%s\n' "${tag#v}"; fi
    done < "$work/tags" | versions)
    if [ -z "$released" ]; then
        echo "$repository has no GitHub Release of a version vX.Y.Z yet." >&2
        return
    fi
    echo "$repository has GitHub Releases of the versions $(printf '%s\n' "$released" | paste -sd ' ' -)." >&2
    printf '%s\n' "$released"
}

cmd_latest() { # X.Y.Z, with the released versions on stdin
    [ $# -eq 1 ] || usage
    [[ $1 =~ $version_re ]] || usage
    local version=$1 highest
    # Other lines, such as v1.2.3, 1.0 or latest, aren't released versions.
    highest=$({
        cat
        printf '%s\n' "$version"
    } | versions | tail -n 1)
    if [ "$highest" = "$version" ]; then
        echo "Latest: $version is the highest released version." >&2
        echo true
    else
        echo "Not Latest: $highest is a higher released version." >&2
        echo false
    fi
}

cmd_previous() { # X.Y.Z, with the released versions on stdin
    [ $# -eq 1 ] || usage
    [[ $1 =~ $version_re ]] || usage
    local version=$1 previous
    # The line before X.Y.Z among the released versions and X.Y.Z itself, in numeric order. A lower patch released
    # later, such as 1.0.5 after 1.1.0, starts at 1.0.4: its notes list what changed for users of 1.0.4.
    previous=$({
        cat
        printf '%s\n' "$version"
    } | versions | awk -v version="$version" 'done { next } $0 == version { print below; done = 1 } { below = $0 }')
    if [ -z "$previous" ]; then
        echo "No released version is lower than $version, so the GitHub Release's notes have no start tag." >&2
        return
    fi
    echo "The GitHub Release's notes start at v$previous, the highest released version below $version." >&2
    printf '%s\n' "$previous"
}

command=${1:-}
[ $# -eq 0 ] || shift
case $command in
    version) cmd_version "$@" ;;
    check-version) cmd_check_version "$@" ;;
    check-main) cmd_check_main "$@" ;;
    released) cmd_released "$@" ;;
    latest) cmd_latest "$@" ;;
    previous) cmd_previous "$@" ;;
    *) usage ;;
esac
