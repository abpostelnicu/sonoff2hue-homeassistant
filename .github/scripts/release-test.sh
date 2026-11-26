#!/usr/bin/env bash
# Tests of release.sh and of the workflows Release and Tag the release (decisions 29 and 37), without a network: made-up
# refs and versions, a scratch git repository, and a stand-in for gh that answers as GitHub would. The workflows' jobs
# run as GitHub Actions runs them: their steps' scripts from release.yml and tag.yml, in order, with their env,
# conditions, outputs and job outputs, until one fails.
# CI's Test job and scripts/check run it. It needs bash 3.2 or later, git, python3, and PyYAML in PYTHON (default: the
# .venv that scripts/setup makes).
set -euo pipefail

root=$(cd "$(dirname "$0")/../.." && pwd)
release=$root/.github/scripts/release.sh
workflow=$root/.github/workflows/release.yml
tag_workflow=$root/.github/workflows/tag.yml
ci_workflow=$root/.github/workflows/ci.yml
python=${PYTHON:-$root/.venv/bin/python}
case $python in
    /*) ;;
    */*) python=$PWD/$python ;;
esac
if ! "$python" -c 'import yaml' 2> /dev/null; then
    echo "No Python with PyYAML at $python. Run scripts/setup first, or set PYTHON." >&2
    exit 2
fi
checks=0
failures=0

pass() {
    checks=$((checks + 1))
    printf '  ok    %s\n' "$*"
}

fail() {
    checks=$((checks + 1))
    failures=$((failures + 1))
    printf '  FAIL  %s\n' "$*"
}

# shown COMMAND...: the command as the checks print it.
shown() {
    local command="$*"
    command=${command#in_repo }
    command=${command#with_gh }
    printf '%s' "${command/$release/release.sh}"
}

# accepted EXPECTED COMMAND...: the command succeeds and prints EXPECTED somewhere.
accepted() {
    local expected=$1 output status=0
    shift
    output=$("$@" 2>&1) || status=$?
    if [ $status -eq 0 ] && [[ $output == *"$expected"* ]]; then
        pass "accepted: $(shown "$@")"
    else
        fail "$(shown "$@") should succeed and print '$expected', but exited with $status: $output"
    fi
}

# refused EXPECTED COMMAND...: the command exits with 1 and a GitHub Actions error that contains EXPECTED.
refused() {
    local expected=$1 output status=0
    shift
    output=$("$@" 2>&1) || status=$?
    if [ $status -eq 1 ] && [[ $output == *"::error::"*"$expected"* ]]; then
        pass "refused: $(shown "$@")"
    else
        fail "$(shown "$@") should fail with an error that contains '$expected', but exited with $status: $output"
    fi
}

# usage_error COMMAND...: the command exits with 2, wrong usage.
usage_error() {
    local status=0
    "$@" > /dev/null 2>&1 || status=$?
    if [ $status -eq 2 ]; then
        pass "usage error: $(shown "$@")"
    else
        fail "$(shown "$@") should be a usage error (exit code 2), but exited with $status"
    fi
}

# prints EXPECTED SUBCOMMAND VERSION [RELEASED...]: with the RELEASED versions, release.sh SUBCOMMAND VERSION prints
# EXPECTED.
prints() {
    local expected=$1 subcommand=$2 version=$3 actual status=0
    shift 3
    actual=$(printf '%s\n' "$@" | "$release" "$subcommand" "$version" 2> /dev/null | paste -sd ' ' -) || status=$?
    if [ $status -eq 0 ] && [ "$actual" = "$expected" ]; then
        pass "$subcommand $version with '$*' released: '$expected'"
    else
        fail "$subcommand $version with '$*' released: expected '$expected', got '$actual' (exit code $status)"
    fi
}

echo "The tag's name: exactly vX.Y.Z"
accepted 1.2.3 "$release" version refs/tags/v1.2.3
accepted 0.0.0 "$release" version refs/tags/v0.0.0
accepted 10.20.30 "$release" version refs/tags/v10.20.30
for ref in refs/tags/v1.0.0-rc.1 refs/tags/v01.2.3 refs/tags/v1.02.3 refs/tags/v1.2.03 refs/tags/v1.2 \
    refs/tags/v1.2.3.4 refs/tags/1.2.3 refs/tags/V1.2.3 refs/tags/v1.2.3+build refs/tags/release/v1.2.3 \
    refs/tags/v1.2.3/x "refs/tags/v1.2.3 " "refs/tags/v1.2.3"$'\n'; do
    refused "isn't a release tag" "$release" version "$ref"
done
# A run by hand can be started on a branch as well as on a tag.
for ref in refs/heads/main refs/heads/v1.2.3 refs/pull/1/merge v1.2.3; do
    refused "$ref isn't a tag. Only a tag vX.Y.Z is released, when it is pushed or when the Release workflow is run on \
it (gh workflow run release.yml --ref vX.Y.Z). No GitHub Release was created." "$release" version "$ref"
done
usage_error "$release" version
usage_error "$release" nonsense

echo
echo "Latest: only the highest released version"
prints true latest 1.0.0
prints true latest 1.0.0 1.0.0
prints true latest 1.0.1 1.0.0
prints true latest 1.0.1 1.0.0 1.0.1
prints false latest 1.0.5 1.1.0 1.0.4
prints true latest 1.2.0 1.1.0 1.0.4 1.0.5
prints true latest 1.10.0 1.9.0
prints false latest 1.9.1 1.9.0 1.10.0
prints true latest 1.0.10 1.0.9
prints false latest 0.9.0 1.0.0
# Only plain versions X.Y.Z count: not v1.2.3, X.Y, latest or suffixes.
prints true latest 1.0.0 latest 1.0 v9.9.9 9.9 9.9.9-rc.1 2.0.0-rc.1 02.0.0 4.0.0.0 V5.0.0 release/6.0.0 7.0.0/x x ""
for version in 1.0.0-rc.1 v1.0.0 01.0.0 1.0 ""; do
    usage_error "$release" latest "$version"
done

echo
echo "The notes: they start at the highest released version below X.Y.Z, if there is one"
prints 1.0.0 previous 1.0.1 1.0.0
prints 1.0.0 previous 1.0.1 1.0.0 1.0.1
prints "" previous 1.0.0
prints "" previous 1.0.0 1.0.0
prints 1.0.4 previous 1.0.5 1.1.0 1.0.4
prints 1.1.0 previous 1.2.0 1.1.0 1.0.4 1.0.5
prints 1.9.0 previous 1.10.0 1.9.0 1.10.0
prints 1.10.0 previous 2.0.0 1.9.9 1.10.0
prints 1.0.9 previous 1.0.10 1.0.2 1.0.9
prints "" previous 0.9.0 1.0.0
prints "" previous 1.0.1 latest 1.0 v1.0.0 1.0.0-rc.1 01.0.0 1.0.0.0 V1.0.0 release/1.0.0 1.0.0/x x ""
for version in 1.0.0-rc.1 v1.0.1 01.0.1 1.0 ""; do
    usage_error "$release" previous "$version"
done

echo
echo "Rising versions: X.Y.Z is higher than every released version"
# higher RELEASED VERSION...: release.sh check-higher VERSION..., with the RELEASED versions, one per word, on stdin.
higher() {
    local released=$1
    shift
    # shellcheck disable=SC2086 # one released version per word
    printf '%s\n' $released | "$release" check-higher "$@"
}
higher_checks() {
    accepted "No version is released yet, so 1.0.0 is the first." higher "" 1.0.0
    accepted "1.1.0 is higher than every released version, the highest of which is 1.0.5." higher "1.0.0 1.0.5" 1.1.0
    accepted "1.10.0 is higher than every released version, the highest of which is 1.9.0." higher "1.9.0 1.2.0" 1.10.0
    # Only plain versions X.Y.Z are released versions.
    accepted "1.0.1 is higher than every released version, the highest of which is 1.0.0." \
        higher "1.0.0 latest v9.9.9 9.9 9.9.9-rc.1 02.0.0" 1.0.1
    refused "1.0.0 isn't higher than every released version: 1.0.0 is released. The Marketplace offers the newest \
Release, so versions are released in rising order: raise the version above 1.0.0. Nothing was tagged." \
        higher 1.0.0 1.0.0
    refused "1.0.5 isn't higher than every released version: 1.1.0 is released." higher "1.0.4 1.1.0" 1.0.5
    refused "1.9.1 isn't higher than every released version: 1.10.0 is released." higher "1.9.0 1.10.0" 1.9.1
    local version
    for version in 1.0.0-rc.1 v1.0.1 01.0.1 1.0 ""; do
        usage_error higher 1.0.0 "$version"
    done
    usage_error higher 1.0.0
}
higher_checks

echo
echo "The version and the commit, in a scratch repository"
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
repo=$scratch/repo
mkdir "$repo"
in_repo() { (cd "$repo" && "$@"); }
scratch_git() {
    git -C "$repo" -c user.name=release-test -c user.email=release-test@example.com -c commit.gpgsign=false \
        -c tag.gpgsign=false -c core.hooksPath=/dev/null "$@"
}
manifest=custom_components/sonoff2hue/manifest.json
# commit_manifest CONTENT: commits CONTENT as the manifest and prints the commit.
commit_manifest() {
    mkdir -p "$repo/${manifest%/*}"
    printf '%s\n' "$1" > "$repo/$manifest"
    scratch_git add "$manifest"
    scratch_git commit -q -m "Manifest: $1"
    scratch_git rev-parse HEAD
}
version_manifest() { printf '{"domain": "sonoff2hue", "name": "sonoff2hue", "version": "%s"}' "$1"; }
# The notes of a release pull request, with characters that a shell, printf or a template would change.
notes_text=$'## sonoff2hue\n\n- `code`, "quotes", 100%, $HOME, %s and a \\ backslash.\n- The last line.\n'
# commit_release VERSION: commits the manifest with VERSION and the notes .github/release-notes/vVERSION.md, and prints
# the commit.
commit_release() {
    mkdir -p "$repo/.github/release-notes"
    printf '%s' "$notes_text" > "$repo/.github/release-notes/v$1.md"
    scratch_git add ".github/release-notes/v$1.md"
    commit_manifest "$(version_manifest "$1")"
}
scratch_git init -q -b main
first=$(commit_manifest "$(version_manifest 1.0.0)")
second=$(commit_manifest "$(version_manifest 1.0.1)")
patch=$(commit_manifest "$(version_manifest 1.0.5)")
# The release commit of 1.1.0, and a commit after it, which still has its notes.
noted=$(commit_release 1.1.0)
printf 'A change after the release commit.\n' > "$repo/CHANGES"
scratch_git add CHANGES
scratch_git commit -q -m "A change after the release commit"
later=$(scratch_git rev-parse HEAD)
scratch_git switch -q -c side "$first"
side=$(commit_manifest "$(version_manifest 9.9.8)")
none=$(commit_manifest '{"domain": "sonoff2hue", "name": "sonoff2hue"}')
number=$(commit_manifest '{"domain": "sonoff2hue", "version": 1.0}')
newline=$(commit_manifest '{"domain": "sonoff2hue", "version": "1.0.0\n"}')
short=$(commit_manifest "$(version_manifest 1.1)")
suffixed=$(commit_manifest "$(version_manifest 1.1.0-rc.1)")
list=$(commit_manifest '["1.0.0"]')
broken=$(commit_manifest '{"version": "1.0.0",}')
scratch_git rm -q "$manifest"
scratch_git commit -q -m "No manifest"
missing=$(scratch_git rev-parse HEAD)
# A release commit that isn't on main.
scratch_git switch -q -c stray "$noted"
stray=$(commit_release 1.2.0)
scratch_git tag -a v9.9.8 -m "sonoff2hue 9.9.8" "$side"
scratch_git tag -a v1.0.0 -m "sonoff2hue 1.0.0" "$first"
scratch_git switch -q main
scratch_git update-ref refs/remotes/origin/main main

accepted "v1.0.0 is the version in the tagged commit's $manifest." in_repo "$release" check-version 1.0.0 "$first"
accepted "v1.0.1 is the version" in_repo "$release" check-version 1.0.1 "$second"
accepted "v9.9.8 is the version" in_repo "$release" check-version 9.9.8 "$side"
refused "The tag v1.0.0 doesn't match the version in the tagged commit's $manifest, \"1.0.1\". Raise the version \
through a pull request first, then tag the merged commit on main with v<version>." \
    in_repo "$release" check-version 1.0.0 "$second"
refused "The tag v9.9.9 doesn't match the version in the tagged commit's $manifest, \"1.0.0\"." \
    in_repo "$release" check-version 9.9.9 "$first"
refused "doesn't match the version in the tagged commit's $manifest, which has none." \
    in_repo "$release" check-version 1.0.0 "$none"
refused "doesn't match the version in the tagged commit's $manifest, 1.0." \
    in_repo "$release" check-version 1.0.0 "$number"
refused "doesn't match the version in the tagged commit's $manifest, \"1.0.0\\n\"." \
    in_repo "$release" check-version 1.0.0 "$newline"
refused "The tagged commit's $manifest isn't a JSON object." in_repo "$release" check-version 1.0.0 "$list"
refused "The tagged commit's $manifest isn't valid JSON (" in_repo "$release" check-version 1.0.0 "$broken"
refused "Couldn't read $manifest in the tagged commit $missing." in_repo "$release" check-version 1.0.0 "$missing"
refused "Couldn't read $manifest in the tagged commit 0123456789abcdef." \
    in_repo "$release" check-version 1.0.0 0123456789abcdef
usage_error in_repo "$release" check-version v1.0.0 "$first"
usage_error in_repo "$release" check-version 1.0.0

# manifest_version_is EXPECTED COMMIT: release.sh manifest-version COMMIT prints exactly EXPECTED.
manifest_version_is() {
    local actual status=0
    actual=$(in_repo "$release" manifest-version "$2" 2> /dev/null) || status=$?
    if [ $status -eq 0 ] && [ "$actual" = "$1" ]; then
        pass "manifest-version prints $1 for a commit whose $manifest has it"
    else
        fail "manifest-version should print '$1' for $2, but printed '$actual' (exit code $status)"
    fi
}
manifest_version_is 1.0.0 "$first"
manifest_version_is 1.1.0 "$noted"
manifest_version_is 9.9.8 "$side"
accepted "1.1.0 is the version in the commit's $manifest." in_repo "$release" manifest-version "$noted"
refused "The version in $manifest in the commit $short, \"1.1\", isn't X.Y.Z: three numbers without leading zeros \
and without a suffix, for example 1.2.3. Nothing was tagged." in_repo "$release" manifest-version "$short"
refused "The version in $manifest in the commit $suffixed, \"1.1.0-rc.1\", isn't X.Y.Z" \
    in_repo "$release" manifest-version "$suffixed"
refused "The version in $manifest in the commit $number, 1.0, isn't X.Y.Z" \
    in_repo "$release" manifest-version "$number"
refused "The version in $manifest in the commit $newline, \"1.0.0\\n\", isn't X.Y.Z" \
    in_repo "$release" manifest-version "$newline"
refused "$manifest in the commit $none has no version. Nothing was tagged." in_repo "$release" manifest-version "$none"
refused "$manifest in the commit $list isn't a JSON object." in_repo "$release" manifest-version "$list"
refused "$manifest in the commit $broken isn't valid JSON (" in_repo "$release" manifest-version "$broken"
refused "Couldn't read $manifest in the commit $missing. Nothing was tagged." \
    in_repo "$release" manifest-version "$missing"
usage_error in_repo "$release" manifest-version
usage_error in_repo "$release" manifest-version "$first" "$second"

accepted "The tagged commit $first is on main." in_repo "$release" check-main "$first"
accepted "The tagged commit $patch is on main." in_repo "$release" check-main "$patch"
accepted "The tagged commit $first is on main." in_repo "$release" check-main "$(scratch_git rev-parse v1.0.0)"
refused "The tagged commit $side isn't on main. Only a commit of main is released: merge its pull request first, then \
tag the merged commit on main." in_repo "$release" check-main "$side"
refused "The tagged commit $side isn't on main." in_repo "$release" check-main "$(scratch_git rev-parse v9.9.8)"
refused "Couldn't find the tagged commit 0123456789abcdef." in_repo "$release" check-main 0123456789abcdef
refused "Couldn't find origin/nothing" in_repo "$release" check-main "$first" origin/nothing
usage_error in_repo "$release" check-main

echo
echo "The released versions, from a stand-in gh"
mkdir "$scratch/bin"
fake=$scratch/gh
# A stand-in for gh that answers the calls of release.sh, release.yml and tag.yml as GitHub would, from the state in
# $FAKE_GH: the tags, the tag objects, the releases (newest first), the Latest one, the runs of release.yml and the
# faults to inject. The repository's files are those of the scratch repository, $FAKE_GH_REPO. It logs each call's
# arguments to $FAKE_GH/log. `gh _fake ...` sets the state up. An unexpected call, option or --jq filter exits with 2.
cat > "$scratch/bin/gh" << 'FAKE'
#!/usr/bin/env python3
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

REPOSITORY = "owner/sonoff2hue"
state = Path(os.environ["FAKE_GH"])


def load(name, default):
    path = state / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save(name, value):
    (state / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")


def http_error(status, text):
    # Like gh, the answer's body on stdout and the error on stderr.
    print(json.dumps({"message": text, "status": str(status)}), end="", flush=True)
    print(f"gh: {text} (HTTP {status})", file=sys.stderr)
    sys.exit(1)


def unexpected(what):
    print(f"gh stand-in: unexpected {what}", file=sys.stderr)
    sys.exit(2)


def git(*arguments):
    return subprocess.run(["git", "-C", os.environ["FAKE_GH_REPO"], *arguments], capture_output=True, check=False)


args = sys.argv[1:]
tags = load("tags", {})
releases = load("releases", [])
faults = load("faults", {})
# The tag objects that POST git/tags made, by their made-up SHAs.
objects = load("objects", {})


def fault(key):
    if key in faults:
        http_error(*faults[key])


if args[:1] == ["_fake"]:
    match args[1:]:
        case ["tag", tag, commit]:
            tags[tag] = commit
        case ["release", tag, *kinds]:
            releases.append({
                "tag_name": tag, "name": f"sonoff2hue {tag[1:]}", "draft": "draft" in kinds,
                "prerelease": "prerelease" in kinds, "immutable": True,
                "html_url": f"https://github.com/{REPOSITORY}/releases/tag/{tag}", "body": f"The notes of {tag}",
            })
        case ["latest", tag]:
            save("latest", tag)
        case ["fault", key, status, text]:
            faults[key] = [int(status), text]
        # move-on-create: the tag is moved to the commit while its Release is created. push-before-ref: someone pushes
        # the tag on the commit just before a workflow creates its ref.
        case ["fault", "move-on-create" | "push-before-ref" as key, commit]:
            faults[key] = commit
        case ["answer", text]:
            save("answer", text)
        case ["pages", size]:
            save("pages", int(size))
        case _:
            unexpected(f"set-up {args[1:]}")
    save("tags", tags)
    save("releases", releases)
    save("faults", faults)
    sys.exit(0)

with (state / "log").open("a", encoding="utf-8") as log:
    log.write(shlex.join(args) + "\n")

JQ = {
    ".sha": lambda value: [value["sha"]],
    ".tag_name": lambda value: [value["tag_name"]],
    r'"\(.name), \(.html_url)"': lambda value: [f"{value['name']}, {value['html_url']}"],
    ".[] | select((.draft or .prerelease) | not) | .tag_name": lambda value: [
        release["tag_name"] for release in value if not (release["draft"] or release["prerelease"])
    ],
    r'"\(.name): \(.html_url)", "Immutable: \(.immutable // false).", "", .body // ""': lambda value: [
        f"{value['name']}: {value['html_url']}", f"Immutable: {str(bool(value.get('immutable'))).lower()}.", "",
        value.get("body") or "",
    ],
    r'"The GitHub Release: [\(.name)](\(.html_url))"': lambda value: [
        f"The GitHub Release: [{value['name']}]({value['html_url']})"
    ],
}


def output(value, jq):
    if jq is None:
        print(json.dumps(value))
    elif jq in JQ:
        for line in JQ[jq](value):
            print(line)
    else:
        unexpected(f"--jq {jq}")


def api(args):
    method, paginate, jq, headers, fields, endpoints = None, False, None, [], {}, []
    rest = list(args)
    while rest:
        arg = rest.pop(0)
        if arg == "--paginate":
            paginate = True
        elif arg in ("--method", "-X", "--jq", "-H", "-f", "-F") and rest:
            value = rest.pop(0)
            if arg in ("--method", "-X"):
                method = value
            elif arg == "--jq":
                jq = value
            elif arg == "-H":
                headers.append(value)
            elif "=" in value:
                key, field = value.split("=", 1)
                fields[key] = field
            else:
                unexpected(f"api field {value}")
        elif arg.startswith("-"):
            unexpected(f"api option {arg}")
        else:
            endpoints.append(arg)
    if len(endpoints) != 1:
        unexpected(f"api arguments {args}")
    # Like gh: GET, or POST once there are fields.
    method = method or ("POST" if fields else "GET")
    endpoint = endpoints[0]
    prefix = f"repos/{REPOSITORY}/"
    if not endpoint.startswith(prefix):
        http_error(404, "Not Found")
    path = endpoint.removeprefix(prefix)
    if path.startswith("contents/"):
        contents(path, method, headers, jq)
        return
    if headers:
        unexpected(f"headers {headers}")
    if method == "POST" and not paginate:
        output(create(path, fields), jq)
        return
    if method != "GET" or fields:
        unexpected(f"api {method} {path} with {fields}")
    if path == "releases?per_page=100":
        if not paginate:
            unexpected("list of releases without --paginate")
        fault("list")
        if jq is None:
            answer = load("answer", None)
            if answer is not None:
                print(answer)
                return
            # gh merges the pages of a list; with a page size, they come one after the other instead.
            size = load("pages", None)
            if size is None:
                print(json.dumps(releases))
            else:
                print("".join(json.dumps(releases[i : i + size]) for i in range(0, len(releases), size)) or "[]")
            return
        value = releases
    elif path.startswith("releases/tags/"):
        fault("release")
        found = [r for r in releases if r["tag_name"] == path.removeprefix("releases/tags/") and not r["draft"]]
        if not found:
            http_error(404, "Not Found")
        value = found[0]
    elif path == "releases/latest":
        latest = load("latest", None)
        if latest is None:
            http_error(404, "Not Found")
        value = next(release for release in releases if release["tag_name"] == latest)
    elif path.startswith("commits/refs/tags/"):
        fault("commit")
        tag = path.removeprefix("commits/refs/tags/")
        if tag not in tags:
            http_error(422, f"No commit found for SHA: refs/tags/{tag}")
        value = {"sha": tags[tag]}
    else:
        http_error(404, "Not Found")
    output(value, jq)


def contents(path, method, headers, jq):
    # Only a file at a commit, as the raw media type answers with it: the file itself.
    url = urlsplit(path.removeprefix("contents/"))
    query = parse_qs(url.query)
    if method != "GET" or headers != ["Accept: application/vnd.github.raw+json"] or jq is not None \
            or list(query) != ["ref"]:
        unexpected(f"contents request {method} {path} {headers} {jq}")
    fault("contents")
    ref = query["ref"][0]
    if git("cat-file", "-e", f"{ref}^{{commit}}").returncode != 0:
        http_error(404, f"No commit found for the ref {ref}")
    blob = git("cat-file", "blob", f"{ref}:{url.path}")
    if blob.returncode != 0:
        http_error(404, "Not Found")
    sys.stdout.buffer.write(blob.stdout)


TAGGER = ("tagger[name]", "tagger[email]", "tagger[date]")


def create(path, fields):
    if path == "git/tags":
        # An annotated tag's object, for a commit.
        if set(fields) != {"tag", "message", "object", "type", *TAGGER} or fields["type"] != "commit" \
                or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", fields["tagger[date]"]):
            unexpected(f"tag object {fields}")
        fault("tags")
        if git("cat-file", "-e", f"{fields['object']}^{{commit}}").returncode != 0:
            http_error(422, "Object does not exist")
        sha = f"{len(objects) + 1:040x}"
        objects[sha] = {
            "sha": sha, "tag": fields["tag"], "message": fields["message"],
            "object": {"sha": fields["object"], "type": "commit"},
            "tagger": {key.removeprefix("tagger[").removesuffix("]"): fields[key] for key in TAGGER},
        }
        save("objects", objects)
        return objects[sha]
    if path == "git/refs":
        if set(fields) != {"ref", "sha"} or not fields["ref"].startswith("refs/tags/"):
            unexpected(f"ref {fields}")
        tag = fields["ref"].removeprefix("refs/tags/")
        if "push-before-ref" in faults:
            tags[tag] = faults.pop("push-before-ref")
            save("tags", tags)
            save("faults", faults)
        fault("refs")
        if tag in tags:
            http_error(422, "Reference already exists")
        # Its workflow makes annotated tags only.
        if fields["sha"] not in objects:
            unexpected(f"ref to {fields['sha']}, which isn't a tag object")
        tags[tag] = objects[fields["sha"]]["object"]["sha"]
        save("tags", tags)
        return {"ref": fields["ref"], "object": {"sha": fields["sha"], "type": "tag"}}
    unexpected(f"POST {path}")


def workflow_run(args):
    # The Release workflow, run on a tag.
    options = dict(zip(args[1::2], args[2::2]))
    if args[:1] != ["release.yml"] or len(args) != 5 or set(options) != {"--repo", "--ref"} \
            or options["--repo"] != REPOSITORY:
        unexpected(f"workflow run arguments {args}")
    fault("dispatch")
    ref = options["--ref"]
    if ref not in tags:
        print(f"could not create workflow dispatch event: HTTP 422: No ref found for: {ref}", file=sys.stderr)
        sys.exit(1)
    runs = load("runs", [])
    runs.append(ref)
    save("runs", runs)
    print(f"Created workflow_dispatch event for release.yml at {ref}")


def release_create(args):
    if not args or args[0].startswith("-"):
        unexpected(f"release create arguments {args}")
    tag, options = args[0], {}
    rest = args[1:]
    while rest:
        option = rest.pop(0)
        if option in ("--verify-tag", "--generate-notes"):
            options[option] = True
        elif option.startswith("--latest="):
            options["--latest"] = option.removeprefix("--latest=")
        elif option in ("--repo", "--target", "--title", "--notes-start-tag", "--notes-file") and rest:
            options[option] = rest.pop(0)
        else:
            unexpected(f"release create option {option}")
    if options.get("--repo") != REPOSITORY or options.get("--latest") not in ("true", "false"):
        unexpected(f"release create options {options}")
    # The notes come from a file, are generated, or both; a start tag is only for generated notes.
    notes_file, generate = options.get("--notes-file"), options.get("--generate-notes", False)
    if (notes_file is None and not generate) or ("--notes-start-tag" in options and not generate):
        unexpected(f"release create notes {options}")
    fault("create")
    if options.get("--verify-tag") and tag not in tags:
        print(f"tag {tag} doesn't exist in the repo {REPOSITORY}, aborting due to --verify-tag flag", file=sys.stderr)
        sys.exit(1)
    start = options.get("--notes-start-tag")
    if start is not None and start not in tags:
        http_error(400, "Invalid previous_tag parameter")
    if any(release["tag_name"] == tag for release in releases):
        http_error(422, "Validation Failed: already_exists")
    changes = f"compare/{start}...{tag}" if start else f"commits/{tag}"
    body = f"**Full Changelog**: https://github.com/{REPOSITORY}/{changes}"
    if notes_file is not None:
        # GitHub puts the notes it is given before the ones it generates.
        notes = Path(notes_file).read_bytes().decode("utf-8")
        body = f"{notes}\n\n{body}" if generate else notes
    release = {
        "tag_name": tag, "name": options.get("--title", tag), "draft": False, "prerelease": False, "immutable": True,
        "html_url": f"https://github.com/{REPOSITORY}/releases/tag/{tag}", "body": body,
    }
    releases.insert(0, release)
    save("releases", releases)
    # GitHub makes the only Release Latest whatever --latest says.
    if options["--latest"] == "true" or load("latest", None) is None:
        save("latest", tag)
    if "move-on-create" in faults:
        tags[tag] = faults["move-on-create"]
        save("tags", tags)
    print(release["html_url"])


match args:
    case ["api", *rest]:
        api(rest)
    case ["release", "create", *rest]:
        release_create(rest)
    case ["workflow", "run", *rest]:
        workflow_run(rest)
    case _:
        unexpected(f"command {args}")
FAKE
chmod +x "$scratch/bin/gh"

# fresh_gh: the stand-in gh without tags, releases, runs or faults, and with an empty log.
fresh_gh() {
    rm -rf "$fake"
    mkdir "$fake"
    : > "$fake/log"
}
fake_gh() { FAKE_GH=$fake "$scratch/bin/gh" _fake "$@"; }
with_gh() { env -u GH_TOKEN -u GITHUB_TOKEN FAKE_GH="$fake" FAKE_GH_REPO="$repo" PATH="$scratch/bin:$PATH" "$@"; }

# logged: the calls that the stand-in gh got, one per line, with a run's temporary folder shown as $RUNNER_TEMP and a
# tag's date as DATE.
logged() {
    sed -e "s|$scratch/state/runner-temp|\$RUNNER_TEMP|g" -e 's/tagger\[date\]=[0-9TZ:-]*/tagger[date]=DATE/g' \
        "$fake/log"
}

# calls_are DESCRIPTION LINE...: the stand-in gh got exactly these calls.
calls_are() {
    local description=$1 actual expected
    shift
    actual=$(logged)
    expected=$(if [ $# -gt 0 ]; then printf '%s\n' "$@"; fi)
    if [ "$actual" = "$expected" ]; then
        pass "$description"
    else
        fail "$description: the calls were"$'\n'"$actual"$'\n'"        instead of"$'\n'"$expected"
    fi
}

# released_is EXPECTED: release.sh released prints the versions EXPECTED, from the stand-in's releases.
released_is() {
    local expected=$1 actual status=0
    : > "$fake/log"
    actual=$(with_gh "$release" released owner/sonoff2hue 2> /dev/null | paste -sd ' ' -) || status=$?
    if [ $status -eq 0 ] && [ "$actual" = "$expected" ]; then
        pass "released prints '$expected'"
    else
        fail "released should print '$expected', but printed '$actual' (exit code $status)"
    fi
}

fresh_gh
accepted "owner/sonoff2hue has no GitHub Release of a version vX.Y.Z yet." with_gh "$release" released owner/sonoff2hue
calls_are "released asks for every page of the releases, 100 at a time" \
    "api --paginate 'repos/owner/sonoff2hue/releases?per_page=100'"
released_is ""
for release_args in "v1.1.0" "v2.0.0 draft" "v1.5.0 prerelease" "v1.0.10" "v1.2" "1.3.0" "v1.4.0-rc.1" "v01.5.0" \
    "latest" "v1.0.4" "V1.6.0"; do
    # shellcheck disable=SC2086 # the tag and its kinds
    fake_gh release $release_args
done
released_is "1.0.4 1.0.10 1.1.0"
fake_gh pages 2
released_is "1.0.4 1.0.10 1.1.0"
accepted "owner/sonoff2hue has GitHub Releases of the versions 1.0.4 1.0.10 1.1.0." \
    with_gh "$release" released owner/sonoff2hue
fake_gh fault list 500 "Server Error"
refused "Couldn't list the GitHub Releases of owner/sonoff2hue (gh exited with 1). No GitHub Release was created; run \
the job again." with_gh "$release" released owner/sonoff2hue
fresh_gh
fake_gh answer '{"message": "Not Found"}'
refused "Couldn't read the GitHub Releases of owner/sonoff2hue: gh's answer isn't a list of releases: {\"message\": \
\"Not Found\"}." with_gh "$release" released owner/sonoff2hue
fake_gh answer '[{"tag_name": "v1.0.0"}] <html>'
refused "Couldn't read the GitHub Releases of owner/sonoff2hue: gh's answer isn't JSON (" \
    with_gh "$release" released owner/sonoff2hue
fake_gh answer ''
refused "Couldn't read the GitHub Releases of owner/sonoff2hue: gh printed nothing." \
    with_gh "$release" released owner/sonoff2hue
for arguments in "" "sonoff2hue" "owner/sonoff2hue/x" "owner/sonoff2hue other/repository" "owner/sonoff 2hue"; do
    # shellcheck disable=SC2086 # one argument per word
    usage_error with_gh "$release" released $arguments
done

echo
echo "The workflows: who may write, and what runs"
# Prints a line "PASS <check>" or "FAIL <check>: <why>" per check of the structure of release.yml and tag.yml, and of
# what tag.yml needs from ci.yml.
cat > "$scratch/structure.py" << 'PY'
import sys

import yaml


def load(path):
    with open(path, encoding="utf-8") as file:
        return yaml.safe_load(file)


workflow, tag_workflow, ci = (load(path) for path in sys.argv[1:])


def check(ok, description, why=""):
    print(f"PASS {description}" if ok else f"FAIL {description}: {why}")


def triggers(workflow):
    # YAML 1.1 reads the key on as true.
    return workflow.get("on", workflow.get(True))


def one_at_a_time(concurrency):
    return (isinstance(concurrency, dict) and isinstance(concurrency.get("group"), str)
            and "${{" not in concurrency["group"] and concurrency.get("cancel-in-progress") is False)


def writes(permissions):
    if isinstance(permissions, dict):
        return any(value == "write" for value in permissions.values())
    return permissions == "write-all"


def condition(job):
    # A job's if, without ${{ }} and with single spaces.
    text = str(job.get("if", "")).strip()
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2]
    return " ".join(text.split())


def expressions_in_scripts(workflow):
    return [step.get("name") for job in workflow.get("jobs", {}).values() for step in job.get("steps", [])
            if "${{" in step.get("run", "")]


check(triggers(workflow) == {"push": {"tags": ["v*.*.*"]}, "workflow_dispatch": None},
      "it runs only for pushed tags v*.*.* and when it is run, without inputs", triggers(workflow))
check(workflow.get("permissions") == {"contents": "read"}, "its token may only read by default",
      workflow.get("permissions"))
concurrency = workflow.get("concurrency")
check(one_at_a_time(concurrency),
      "one release at a time: one concurrency group, which never cancels a run in progress", concurrency)
jobs = workflow.get("jobs", {})
writers = [name for name, job in jobs.items() if writes(job.get("permissions"))]
check(writers == ["release"] and jobs["release"]["permissions"] == {"contents": "write"},
      "only the GitHub Release job may write, and only the repository's contents", writers)
release = jobs.get("release", {})
check(release.get("needs") in ("check", ["check"]) and "if" not in release,
      "the GitHub Release job runs only once the check job has passed", (release.get("needs"), release.get("if")))
check(release.get("steps") and all("uses" not in step for step in release["steps"]),
      "the GitHub Release job runs no action, so it checks nothing out", release.get("steps"))
checkout = (jobs.get("check", {}).get("steps") or [{}])[0]
check(str(checkout.get("uses", "")).startswith("actions/checkout@")
      and checkout.get("with") == {"ref": "${{ github.sha }}", "fetch-depth": 0, "persist-credentials": False},
      "the check job checks out the tagged commit, github.sha, with main's history", checkout)
check(jobs and not expressions_in_scripts(workflow),
      "no script has an expression in it: values reach the scripts through env", expressions_in_scripts(workflow))

on = triggers(tag_workflow)
check(on == {"workflow_run": {"workflows": ["CI"], "types": ["completed"], "branches": ["main"]}}
      and ci.get("name") == "CI",
      "Tag the release runs only when CI, ci.yml's workflow, completes on main", (on, ci.get("name")))
check(isinstance(on, dict) and not {"pull_request", "pull_request_target"} & set(on),
      "Tag the release has no pull_request trigger", on)
check(isinstance(triggers(ci), dict) and (triggers(ci).get("push") or {}).get("branches") == ["main"],
      "CI runs for pushes to main", triggers(ci))
check(tag_workflow.get("permissions") == {"contents": "read"}, "Tag the release's token may only read by default",
      tag_workflow.get("permissions"))
concurrency = tag_workflow.get("concurrency")
check(one_at_a_time(concurrency) and concurrency["group"] != (workflow.get("concurrency") or {}).get("group"),
      "Tag the release runs one at a time: a concurrency group of its own, which never cancels a run in progress",
      concurrency)
jobs = tag_workflow.get("jobs", {})
writers = [name for name, job in jobs.items() if writes(job.get("permissions"))]
check(writers == ["tag"] and jobs["tag"]["permissions"] == {"contents": "write", "actions": "write"},
      "only the tag job may write: the repository's contents, for the tag, and its Actions, for the run", writers)
tag, checker = jobs.get("tag", {}), jobs.get("check", {})
check(tag.get("steps") and all("uses" not in step for step in tag["steps"]),
      "the tag job runs no action, so it checks nothing out", tag.get("steps"))
check(condition(checker)
      == "github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.event == 'push'",
      "the check job runs only after CI passed on a push", checker.get("if"))
check(tag.get("needs") in ("check", ["check"]) and condition(tag) == "needs.check.outputs.tag == 'true'",
      "the tag job runs only once the check job has passed and found a commit to tag",
      (tag.get("needs"), tag.get("if")))
checkout = (checker.get("steps") or [{}])[0]
check(str(checkout.get("uses", "")).startswith("actions/checkout@")
      and checkout.get("with")
      == {"ref": "${{ github.event.workflow_run.head_sha }}", "fetch-depth": 0, "persist-credentials": False},
      "the check job checks out the commit that CI passed on, with main's history and without credentials", checkout)
check(set(checker.get("outputs") or {}) == {"version", "sha", "tag"},
      "the check job hands over the version, the commit and whether to tag it", checker.get("outputs"))
check(jobs and not expressions_in_scripts(tag_workflow),
      "no script of Tag the release has an expression in it", expressions_in_scripts(tag_workflow))
PY
# structure_checks: the checks of structure.py, as checks of this script.
structure_checks() {
    local line
    while IFS= read -r line; do
        case $line in
            "PASS "*) pass "${line#PASS }" ;;
            *) fail "${line#FAIL }" ;;
        esac
    done < <("$python" "$scratch/structure.py" "$workflow" "$tag_workflow" "$ci_workflow" 2>&1 \
        || echo "FAIL structure.py exited with $?")
}
structure_checks

echo
echo "The Release workflow, run as GitHub Actions runs it, against the stand-in gh"
# Runs a job of a workflow as GitHub Actions runs it on ubuntu-latest: each step's script with
# `bash --noprofile --norc -eo pipefail`, with the workflow's, the job's and the step's env, the github context of
# $STATE/github.json and the outputs of the jobs before it in $STATE/needs.json, until a step fails. A job runs only if
# the jobs it needs have passed and its if holds, and a step only if its if holds. It only runs what release.yml and
# tag.yml use: run steps, actions/checkout (the scratch repository is the checkout already), and the expressions below,
# which it substitutes in env, outputs and scripts as GitHub does, and compares in conditions; anything else exits
# with 2. It prints the steps' output, then "result: success", "result: skipped" or "result: failure in <step>", and
# adds the job's result and outputs to $STATE/needs.json.
cat > "$scratch/simulate.py" << 'PY'
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import yaml

workflow_path, job_id, workspace, state = sys.argv[1:]
state = Path(state)
with open(workflow_path, encoding="utf-8") as file:
    workflow = yaml.safe_load(file)
github = json.loads((state / "github.json").read_text(encoding="utf-8"))
needs_path = state / "needs.json"
needs = json.loads(needs_path.read_text(encoding="utf-8")) if needs_path.exists() else {}
job = workflow["jobs"][job_id]
outputs = {}


def harness_error(message):
    print(f"simulate: {message}", flush=True)
    sys.exit(2)


def lookup(name):
    run = github.get("event", {}).get("workflow_run", {})
    match name.split("."):
        case ["github", "sha" | "ref" | "repository" | "token" | "event_name" as key]:
            return github[key]
        case ["github", "event", "workflow_run", key] if key in run:
            return str(run[key])
        case ["steps", step, "outputs", key]:
            return outputs.get(step, {}).get(key, "")
        case ["needs", other, "outputs", key]:
            return needs.get(other, {}).get("outputs", {}).get(key, "")
    harness_error(f"no value for {name}")


def evaluate(value):
    if isinstance(value, bool):
        return str(value).lower()
    return re.sub(r"\$\{\{\s*(.*?)\s*\}\}", lambda match: lookup(match.group(1)), str(value))


def holds(condition):
    # Only what the workflows use: values compared with strings, joined by &&. GitHub compares strings without
    # regard to case.
    text = str(condition).strip()
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2]
    comparisons = [re.fullmatch(r"\s*([\w.-]+)\s*==\s*'([^']*)'\s*", part) for part in text.split("&&")]
    if not all(comparisons):
        harness_error(f"the condition {condition!r} isn't supported")
    return all(lookup(name).casefold() == value.casefold() for name, value in (match.groups() for match in comparisons))


def read_outputs(path):
    found, lines = {}, path.read_text(encoding="utf-8").splitlines()
    while lines:
        line = lines.pop(0)
        if "<<" in line and ("=" not in line or line.index("<<") < line.index("=")):
            name, delimiter = line.split("<<", 1)
            value = []
            while lines[0] != delimiter:
                value.append(lines.pop(0))
            lines.pop(0)
            found[name] = "\n".join(value)
        elif "=" in line:
            name, value = line.split("=", 1)
            found[name] = value
        elif line:
            harness_error(f"can't read the output line {line!r}")
    return found


for unsupported in ("defaults", "strategy", "container", "services", "environment"):
    if unsupported in job or (unsupported == "defaults" and unsupported in workflow):
        harness_error(f"{unsupported} isn't supported")
# Without a status function in its if, a job runs only if every job it needs has passed.
needed = job.get("needs", [])
needed = [needed] if isinstance(needed, str) else needed
if any(needs.get(other, {}).get("result") != "success" for other in needed) or ("if" in job and not holds(job["if"])):
    needs[job_id] = {"result": "skipped", "outputs": {}}
    needs_path.write_text(json.dumps(needs), encoding="utf-8")
    print("result: skipped", flush=True)
    sys.exit(0)
runner_temp = state / "runner-temp"
runner_temp.mkdir(exist_ok=True)
inherited = ("GITHUB_", "RUNNER_", "ACTIONS_", "GH_")
base = {key: value for key, value in os.environ.items() if not key.startswith(inherited)}
base.update(CI="true", GITHUB_ACTIONS="true", GITHUB_EVENT_NAME=github["event_name"], GITHUB_REF=github["ref"],
            GITHUB_SHA=github["sha"], GITHUB_REPOSITORY=github["repository"], GITHUB_SERVER_URL="https://github.com",
            GITHUB_WORKSPACE=workspace, GITHUB_STEP_SUMMARY=str(state / "summary.md"), RUNNER_TEMP=str(runner_temp))
result = "success"
for number, step in enumerate(job["steps"], 1):
    name = step.get("name") or step.get("uses") or "run"
    for unsupported in ("shell", "working-directory", "continue-on-error"):
        if unsupported in step:
            harness_error(f"{unsupported} in the step {name} isn't supported")
    if "if" in step and not holds(step["if"]):
        print(f"== {name}: skipped", flush=True)
        continue
    if "uses" in step:
        if not step["uses"].startswith("actions/checkout@"):
            harness_error(f"the action {step['uses']} isn't supported")
        inputs = {key: evaluate(value) for key, value in (step.get("with") or {}).items()}
        print(f"== {name}: {inputs}", flush=True)
        continue
    print(f"== {name}", flush=True)
    env = dict(base)
    for scope in (workflow, job, step):
        env.update({key: evaluate(value) for key, value in (scope.get("env") or {}).items()})
    script = state / f"{job_id}-{number}.sh"
    script.write_text(evaluate(step["run"]), encoding="utf-8")
    output = state / f"{job_id}-{number}.output"
    output.write_text("", encoding="utf-8")
    env["GITHUB_OUTPUT"] = str(output)
    completed = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)], cwd=workspace,
                               env=env, stdout=sys.stdout, stderr=subprocess.STDOUT, check=False)
    if "id" in step:
        outputs[step["id"]] = read_outputs(output)
    if completed.returncode != 0:
        result = f"failure in {name}"
        break
needs[job_id] = {"result": result.split(" ")[0], "outputs": {}}
if result == "success":
    needs[job_id]["outputs"] = {key: evaluate(value) for key, value in (job.get("outputs") or {}).items()}
needs_path.write_text(json.dumps(needs), encoding="utf-8")
print(f"result: {result}", flush=True)
PY
state=$scratch/state
workspace=$scratch/repo
mkdir -p "$workspace/.github/scripts"
cp "$release" "$workspace/.github/scripts/release.sh"

# job WORKFLOW JOB: runs the job of the workflow, as simulate.py does, and prints its result. Its output goes to
# $state/JOB.log.
job() {
    local status=0
    with_gh "$python" "$scratch/simulate.py" "$1" "$2" "$workspace" "$state" > "$state/$2.log" 2>&1 || status=$?
    if [ $status -gt 1 ]; then
        echo "error: simulate.py exited with $status"
    else
        sed -n 's/^result: //p' "$state/$2.log" | tail -n 1
    fi
}

# run_workflow REF SHA [EVENT]: GitHub runs release.yml for REF, which names SHA, on a push (the default) or a run by
# hand (workflow_dispatch): the check job, then the GitHub Release job if the check job passed. Sets $check and
# $created to their results, or "skipped".
run_workflow() {
    rm -rf "$state"
    mkdir "$state"
    printf '{"event_name": "%s", "ref": "%s", "sha": "%s", "repository": "owner/sonoff2hue", "token": "a-token"}\n' \
        "${3:-push}" "$1" "$2" > "$state/github.json"
    : > "$fake/log"
    check=$(job "$workflow" check)
    created=$(job "$workflow" release)
}

# run_release REF SHA VERSION LATEST PREVIOUS: GitHub runs only the GitHub Release job, with these outputs of the check
# job, as when only the failed jobs of a run are run again. Sets $created to its result.
run_release() {
    rm -rf "$state"
    mkdir "$state"
    printf '{"event_name": "push", "ref": "%s", "sha": "%s", "repository": "owner/sonoff2hue", "token": "a-token"}\n' \
        "$1" "$2" > "$state/github.json"
    printf '{"check": {"result": "success", "outputs": {"version": "%s", "latest": "%s", "previous": "%s"}}}\n' \
        "$3" "$4" "$5" > "$state/needs.json"
    : > "$fake/log"
    check=success
    created=$(job "$workflow" release)
}

# outcome DESCRIPTION CHECK CREATED CREATE [MESSAGE]: the last run's check job ended as CHECK and its GitHub Release job
# as CREATED; the stand-in gh got the call `release create CREATE`, or no such call for "none"; and MESSAGE, if given,
# is in the jobs' output.
outcome() {
    local description=$1 expected_check=$2 expected_created=$3 create=$4 message=${5:-} calls problems=""
    calls=$(logged | sed -n 's/^release create //p')
    if [ "$create" = none ]; then create=""; fi
    [ "$check" = "$expected_check" ] || problems+=$'\n'"        the check job: $check, not $expected_check"
    [ "$created" = "$expected_created" ] \
        || problems+=$'\n'"        the GitHub Release job: $created, not $expected_created"
    [ "$calls" = "$create" ] || problems+=$'\n'"        gh release create: '$calls', not '$create'"
    if [ -n "$message" ] && ! grep -qF -- "$message" "$state"/*.log; then
        problems+=$'\n'"        the output lacks: $message"
    fi
    if [ -z "$problems" ]; then
        pass "$description"
    else
        fail "$description:$problems"$'\n'"$(cat "$state"/*.log | sed 's/^/        | /')"
    fi
}

# options VERSION LATEST [START]: the options that the GitHub Release job gives gh release create for generated notes.
options() {
    printf "v%s --repo owner/sonoff2hue --verify-tag --target %s --title 'sonoff2hue %s' --latest=%s --generate-notes" \
        "$1" "$sha" "$1" "$2"
    if [ $# -gt 2 ]; then printf ' --notes-start-tag v%s' "$3"; fi
}
# notes_options VERSION LATEST: the same for the notes of a release pull request.
notes_options() {
    printf "v%s --repo owner/sonoff2hue --verify-tag --target %s --title 'sonoff2hue %s' --latest=%s" \
        "$1" "$sha" "$1" "$2"
    # shellcheck disable=SC2016 # the run's temporary folder, as logged shows it
    printf ' --notes-file $RUNNER_TEMP/notes.md'
}

fresh_gh
fake_gh tag v1.0.0 "$first"
sha=$first
run_workflow refs/tags/v1.0.0 "$first"
outcome "the first release, v1.0.0, is Latest and has notes from the start" success success "$(options 1.0.0 true)" \
    "Its tag v1.0.0 names $first. Latest release: v1.0.0."
if grep -qxF "The GitHub Release: [sonoff2hue 1.0.0](https://github.com/owner/sonoff2hue/releases/tag/v1.0.0)" \
    "$state/summary.md"; then
    pass "the job summary links the GitHub Release"
else
    fail "the job summary: $(cat "$state/summary.md" 2> /dev/null)"
fi
run_workflow refs/tags/v1.0.0 "$first"
outcome "a run again leaves the GitHub Release of v1.0.0 as it is" success success none \
    "v1.0.0 has a GitHub Release already, sonoff2hue 1.0.0, https://github.com/owner/sonoff2hue/releases/tag/v1.0.0: \
this run leaves it as it is."

fake_gh tag v1.0.1 "$second"
sha=$second
run_workflow refs/tags/v1.0.1 "$second"
outcome "v1.0.1 after v1.0.0: Latest, with notes from v1.0.0" success success "$(options 1.0.1 true 1.0.0)"

fresh_gh
fake_gh tag v1.0.1 "$second"
for release_args in "v2.0.0 draft" "v1.5.0 prerelease" "v1.2" "v1.0.0" "v01.4.0"; do
    # shellcheck disable=SC2086 # the tag and its kinds
    fake_gh release $release_args
done
fake_gh tag v1.0.0 "$first"
fake_gh tag v9.9.9 "$first"
fake_gh latest v1.0.0
run_workflow refs/tags/v1.0.1 "$second"
outcome "drafts, pre-releases, other tags and git tags alone count neither for Latest nor for the notes" success \
    success "$(options 1.0.1 true 1.0.0)"

fresh_gh
fake_gh tag v1.0.5 "$patch"
for release_args in v1.1.0 v1.0.4; do fake_gh release "$release_args" && fake_gh tag "$release_args" "$first"; done
fake_gh latest v1.1.0
sha=$patch
run_workflow refs/tags/v1.0.5 "$patch"
outcome "a lower patch released later, v1.0.5 after v1.1.0: not Latest, with notes from v1.0.4" success success \
    "$(options 1.0.5 false 1.0.4)" "Its tag v1.0.5 names $patch. Latest release: v1.1.0."

fresh_gh
for ref in refs/tags/v1.0.0-rc.1 refs/tags/v01.0.0; do
    run_workflow "$ref" "$first"
    outcome "${ref#refs/tags/} isn't a release tag: no GitHub Release, not even a call of gh" \
        "failure in Check that the tag is vX.Y.Z" skipped none "isn't a release tag."
    calls_are "${ref#refs/tags/}: gh isn't called"
done
run_workflow refs/tags/v9.9.9 "$first"
outcome "v9.9.9 on a commit whose manifest.json has 1.0.0: no GitHub Release" \
    "failure in Check that X.Y.Z is the version in manifest.json" skipped none \
    "The tag v9.9.9 doesn't match the version in the tagged commit's $manifest, \"1.0.0\"."
calls_are "v9.9.9: gh isn't called"
run_workflow refs/tags/v9.9.8 "$side"
outcome "v9.9.8 on a commit that isn't on main: no GitHub Release" \
    "failure in Check that the tagged commit is on main" skipped none "The tagged commit $side isn't on main."
calls_are "v9.9.8: gh isn't called"

fresh_gh
fake_gh tag v1.0.0 "$first"
fake_gh fault list 503 "Service Unavailable"
run_workflow refs/tags/v1.0.0 "$first"
outcome "the releases can't be listed: the check job fails, no GitHub Release" \
    "failure in Choose Latest and where the notes start" skipped none "Couldn't list the GitHub Releases"

fresh_gh
fake_gh tag v1.0.0 "$second"
sha=$first
run_workflow refs/tags/v1.0.0 "$first"
outcome "the tag was moved after it was pushed: no GitHub Release" success \
    "failure in Check that vX.Y.Z still names the checked commit" none \
    "v1.0.0 names $second now, not $first, which the checks passed: it was moved after it was pushed."
fresh_gh
run_workflow refs/tags/v1.0.0 "$first"
outcome "the tag was deleted after it was pushed: no GitHub Release" success \
    "failure in Check that vX.Y.Z still names the checked commit" none "Couldn't read the commit of v1.0.0."

fresh_gh
fake_gh tag v1.0.0 "$first"
fake_gh fault release 500 "Server Error"
run_workflow refs/tags/v1.0.0 "$first"
outcome "it can't be found out whether the tag has a GitHub Release: none is created" success \
    "failure in Create the GitHub Release" none "Couldn't find out whether v1.0.0 has a GitHub Release already."
fresh_gh
fake_gh tag v1.0.0 "$first"
fake_gh fault create 500 "Server Error"
run_workflow refs/tags/v1.0.0 "$first"
outcome "gh release create fails: the job fails and says so" success "failure in Create the GitHub Release" \
    "$(options 1.0.0 true)" "gh couldn't create the GitHub Release v1.0.0, for the reason above."
fresh_gh
fake_gh tag v1.0.0 "$first"
fake_gh fault move-on-create "$second"
run_workflow refs/tags/v1.0.0 "$first"
outcome "the tag was moved while its GitHub Release was created: the job fails and says so" success \
    "failure in Show the GitHub Release" "$(options 1.0.0 true)" \
    "v1.0.0 names $second now, not $first, which the checks passed: it was moved while its GitHub Release was created."

fresh_gh
fake_gh tag v1.0.0 "$first"
fake_gh tag v1.0.1 "$second"
fake_gh release v1.1.0
fake_gh tag v1.1.0 "$second"
fake_gh latest v1.1.0
sha=$second
run_release refs/tags/v1.0.1 "$second" 1.0.1 true 1.0.0
outcome "the GitHub Release job run again after v1.1.0's release: v1.0.1 isn't Latest" success success \
    "$(options 1.0.1 false 1.0.0)" "Not Latest: v1.1.0, a higher version, has a GitHub Release already."
fresh_gh
fake_gh tag v1.9.1 "$second"
fake_gh release v1.10.0
fake_gh latest v1.10.0
run_release refs/tags/v1.9.1 "$second" 1.9.1 true ""
outcome "versions compare as numbers: v1.9.1 isn't Latest after v1.10.0" success success "$(options 1.9.1 false)"
fake_gh fault list 502 "Bad Gateway"
fake_gh tag v1.9.2 "$second"
run_release refs/tags/v1.9.2 "$second" 1.9.2 true 1.9.1
outcome "the releases can't be listed to choose Latest: no GitHub Release" success \
    "failure in Create the GitHub Release" none "Couldn't list the GitHub Releases to choose the Latest one."

# unfit VERSION LATEST PREVIOUS: the GitHub Release job refuses these outputs of the check job for v1.0.0 before it
# calls gh.
unfit() {
    run_release refs/tags/v1.0.0 "$first" "$1" "$2" "$3"
    outcome "the check job's outputs '$1', '$2' and '$3' don't fit v1.0.0: no GitHub Release" success \
        "failure in Check that vX.Y.Z still names the checked commit" none "which don't fit refs/tags/v1.0.0."
    calls_are "the outputs '$1', '$2' and '$3': gh isn't called"
}
fresh_gh
fake_gh tag v1.0.0 "$first"
unfit 1.0 true ""
unfit 1.0.1 true ""
unfit 1.0.0 yes ""
unfit 1.0.0 true 01.0.0
unfit "" true ""

echo
echo "The Release workflow, run on a tag or on main, and the notes of a release pull request"
# released_1_0_0: the stand-in gh with the GitHub Release of v1.0.0, which is Latest, and its tag.
released_1_0_0() {
    fresh_gh
    fake_gh release v1.0.0
    fake_gh tag v1.0.0 "$first"
    fake_gh latest v1.0.0
}

# notes_are DESCRIPTION TAG TEXT: the stand-in's GitHub Release of TAG has exactly TEXT as its notes.
notes_are() {
    if "$python" -c '
import json
import sys

with open(sys.argv[1], encoding="utf-8") as file:
    releases = json.load(file)
sys.exit([release["body"] for release in releases if release["tag_name"] == sys.argv[2]] != [sys.argv[3]])
' "$fake/releases.json" "$2" "$3"; then
        pass "$1"
    else
        fail "$1: the GitHub Releases are $(cat "$fake/releases.json")"
    fi
}

release_by_hand() {
    fresh_gh
    fake_gh tag v1.0.0 "$first"
    sha=$first
    run_workflow refs/tags/v1.0.0 "$first" workflow_dispatch
    outcome "a run on the tag v1.0.0 releases it as its push does" success success "$(options 1.0.0 true)" \
        "Its tag v1.0.0 names $first. Latest release: v1.0.0."
}

release_on_main() {
    released_1_0_0
    fake_gh tag v1.1.0 "$noted"
    run_workflow refs/heads/main "$later" workflow_dispatch
    outcome "a run on main: no GitHub Release" "failure in Check that the tag is vX.Y.Z" skipped none \
        "refs/heads/main isn't a tag. Only a tag vX.Y.Z is released, when it is pushed or when the Release workflow is \
run on it (gh workflow run release.yml --ref vX.Y.Z). No GitHub Release was created."
    calls_are "a run on main: gh isn't called"
}

release_notes_file() {
    local contents="api 'repos/owner/sonoff2hue/contents/.github/release-notes/v1.1.0.md?ref=$noted'"
    released_1_0_0
    fake_gh tag v1.1.0 "$noted"
    sha=$noted
    run_workflow refs/tags/v1.1.0 "$noted"
    outcome "with .github/release-notes/v1.1.0.md: those notes, without generated ones and without a start for them" \
        success success "$(notes_options 1.1.0 true)" \
        "The notes: .github/release-notes/v1.1.0.md, as the tagged commit has it."
    if logged | grep -qxF "$contents -H 'Accept: application/vnd.github.raw+json'"; then
        pass "the notes are read in the tagged commit, with the contents API's raw media type"
    else
        fail "the notes weren't read in the tagged commit with the raw media type: $(logged)"
    fi
    notes_are "the GitHub Release's notes are exactly the file" v1.1.0 "$notes_text"
    # shellcheck disable=SC2016 # a line of the notes, as it is
    if grep -qxF -- '- `code`, "quotes", 100%, $HOME, %s and a \ backslash.' "$state/release.log"; then
        pass "the job shows the notes"
    else
        fail "the job doesn't show the notes: $(cat "$state/release.log")"
    fi
}

release_generated_notes() {
    released_1_0_0
    fake_gh tag v1.0.1 "$second"
    sha=$second
    run_workflow refs/tags/v1.0.1 "$second"
    outcome "without .github/release-notes/v1.0.1.md, a 404: notes that GitHub generates, from v1.0.0" success success \
        "$(options 1.0.1 true 1.0.0)" "The tagged commit has no .github/release-notes/v1.0.1.md, so GitHub generates"
    notes_are "the GitHub Release's notes are GitHub's" v1.0.1 \
        "**Full Changelog**: https://github.com/owner/sonoff2hue/compare/v1.0.0...v1.0.1"
}

release_notes_unreadable() {
    released_1_0_0
    fake_gh tag v1.1.0 "$noted"
    fake_gh fault contents 500 "Server Error"
    sha=$noted
    run_workflow refs/tags/v1.1.0 "$noted"
    outcome "the notes can't be read: no GitHub Release" success "failure in Create the GitHub Release" none \
        "Couldn't read .github/release-notes/v1.1.0.md in the tagged commit. No GitHub Release was created; run this \
job again."
}

release_by_hand
release_on_main
release_notes_file
release_generated_notes
release_notes_unreadable

echo
echo "Tag the release, run as GitHub Actions runs it after CI, against the stand-in gh"
# tag_context SHA CONCLUSION EVENT: the github context of a run of tag.yml after CI completed with CONCLUSION for an
# EVENT whose commit is SHA. main's newest commit is $later.
tag_context() {
    printf '{"event_name": "workflow_run", "ref": "refs/heads/main", "sha": "%s", "repository": "owner/sonoff2hue", ' \
        "$later"
    printf '"token": "a-token", "event": {"workflow_run": {"name": "CI", "event": "%s", "conclusion": "%s", ' "$3" "$2"
    printf '"head_branch": "main", "head_sha": "%s"}}}\n' "$1"
}

# run_tag SHA [CONCLUSION [EVENT]]: CI completed with CONCLUSION (default success) on main for an EVENT (default push)
# whose commit is SHA, and GitHub runs tag.yml: the check job, then the tag job. Sets $checked and $tagged to their
# results, or "skipped".
run_tag() {
    rm -rf "$state"
    mkdir "$state"
    tag_context "$1" "${2:-success}" "${3:-push}" > "$state/github.json"
    : > "$fake/log"
    checked=$(job "$tag_workflow" check)
    tagged=$(job "$tag_workflow" tag)
}

# run_tag_job VERSION SHA: GitHub runs only the tag job, with these outputs of the check job, as when only the failed
# jobs of a run are run again. Sets $tagged to its result.
run_tag_job() {
    rm -rf "$state"
    mkdir "$state"
    tag_context "$noted" success push > "$state/github.json"
    printf '{"check": {"result": "success", "outputs": {"version": "%s", "sha": "%s", "tag": "true"}}}\n' "$1" "$2" \
        > "$state/needs.json"
    : > "$fake/log"
    checked=success
    tagged=$(job "$tag_workflow" tag)
}

# tagged_as DESCRIPTION CHECK TAG MESSAGE CALL...: the last run of tag.yml ended its check job as CHECK and its tag job
# as TAG; MESSAGE, unless it is empty, is in the jobs' output; and the stand-in gh got exactly the calls CALL....
tagged_as() {
    local description=$1 expected_check=$2 expected_tag=$3 message=$4 actual expected problems=""
    shift 4
    actual=$(logged)
    expected=$(if [ $# -gt 0 ]; then printf '%s\n' "$@"; fi)
    [ "$checked" = "$expected_check" ] || problems+=$'\n'"        the check job: $checked, not $expected_check"
    [ "$tagged" = "$expected_tag" ] || problems+=$'\n'"        the tag job: $tagged, not $expected_tag"
    [ "$actual" = "$expected" ] \
        || problems+=$'\n'"        the calls of gh:"$'\n'"$actual"$'\n'"        instead of:"$'\n'"$expected"
    if [ -n "$message" ] && ! grep -qF -- "$message" "$state"/*.log; then
        problems+=$'\n'"        the output lacks: $message"
    fi
    if [ -z "$problems" ]; then
        pass "$description"
    else
        fail "$description:$problems"$'\n'"$(cat "$state"/*.log | sed 's/^/        | /')"
    fi
}

# tag_names DESCRIPTION TAG COMMIT: the stand-in's tag TAG names COMMIT, or doesn't exist for "none".
tag_names() {
    local actual
    actual=$("$python" -c 'import json, sys; print(json.load(open(sys.argv[1])).get(sys.argv[2], "none"))' \
        "$fake/tags.json" "$2")
    if [ "$actual" = "$3" ]; then
        pass "$1"
    else
        fail "$1: $2 names $actual, not $3"
    fi
}

# The calls of gh that the workflow makes, as the stand-in logs them. Its first tag object is 000…001.
read_tag() { printf 'api repos/owner/sonoff2hue/commits/refs/tags/v%s --jq .sha' "$1"; }
list_releases="api --paginate 'repos/owner/sonoff2hue/releases?per_page=100'"
tag_object() { # VERSION COMMIT
    printf "api --method POST repos/owner/sonoff2hue/git/tags -f tag=v%s -f 'message=sonoff2hue %s' -f object=%s" \
        "$1" "$1" "$2"
    printf " -f type=commit -f 'tagger[name]=github-actions[bot]'"
    printf " -f 'tagger[email]=41898282+github-actions[bot]@users.noreply.github.com' -f 'tagger[date]=DATE' --jq .sha"
}
tag_ref() { # VERSION
    printf 'api --method POST repos/owner/sonoff2hue/git/refs -f ref=refs/tags/v%s -f sha=%040d' "$1" 1
}
release_run() { printf 'workflow run release.yml --repo owner/sonoff2hue --ref v%s' "$1"; }

tag_after_failed_ci() {
    released_1_0_0
    run_tag "$noted" failure
    tagged_as "CI failed on the release commit: neither job runs" skipped skipped ""
}

tag_after_cancelled_ci() {
    released_1_0_0
    run_tag "$noted" cancelled
    tagged_as "CI was cancelled on the release commit: neither job runs" skipped skipped ""
}

tag_after_pull_request() {
    released_1_0_0
    run_tag "$noted" success pull_request
    tagged_as "CI passed for a pull request from a branch called main: neither job runs" skipped skipped ""
}

tag_without_notes() {
    released_1_0_0
    run_tag "$patch"
    tagged_as "a commit without the notes of its version: no tag, and gh isn't called" success skipped \
        "Not a release commit: $patch has no .github/release-notes/v1.0.5.md for the version in its manifest.json, \
1.0.5. Nothing was tagged."
}

tag_bad_version() {
    released_1_0_0
    run_tag "$short"
    tagged_as "a version that isn't X.Y.Z: the check fails" "failure in Read the version in manifest.json" skipped \
        "The version in $manifest in the commit $short, \"1.1\", isn't X.Y.Z"
}

tag_release_commit() {
    released_1_0_0
    run_tag "$noted"
    tagged_as "the release commit of 1.1.0: tagged v1.1.0, then the Release workflow runs on the tag" success success \
        "Tagged $noted v1.1.0." "$(read_tag 1.1.0)" "$list_releases" "$(tag_object 1.1.0 "$noted")" \
        "$(tag_ref 1.1.0)" "$(release_run 1.1.0)"
    tag_names "the tag v1.1.0 names the release commit, not main's newest commit" v1.1.0 "$noted"
    if grep -qxF "The tag: [v1.1.0](https://github.com/owner/sonoff2hue/tree/v1.1.0), on $noted." "$state/summary.md" \
        && grep -qxF "The Release workflow creates its GitHub Release: [its runs]\
(https://github.com/owner/sonoff2hue/actions/workflows/release.yml)." "$state/summary.md"; then
        pass "the job summary shows the tag and links the Release workflow's runs"
    else
        fail "the job summary: $(cat "$state/summary.md" 2> /dev/null)"
    fi
}

tag_already_on_commit() {
    released_1_0_0
    fake_gh tag v1.1.0 "$noted"
    run_tag "$noted"
    tagged_as "CI ran again on the release commit after it was tagged: no tag" success skipped \
        "v1.1.0 names this commit already. Nothing was tagged. If the Release workflow didn't run for it, run it on \
the tag: gh workflow run release.yml --ref v1.1.0" "$(read_tag 1.1.0)"
}

tag_on_earlier_commit() {
    released_1_0_0
    fake_gh tag v1.1.0 "$noted"
    run_tag "$later"
    tagged_as "a commit after the release commit, which still has its notes: no tag" success skipped \
        "v1.1.0 names $noted, a commit before this one, so this isn't the release commit of 1.1.0. Nothing was \
tagged." "$(read_tag 1.1.0)"
}

tag_on_later_commit() {
    released_1_0_0
    fake_gh tag v1.1.0 "$later"
    run_tag "$noted"
    tagged_as "the tag names a commit after this one, as when CI passed there first: the check fails" \
        "failure in Look for the release notes and the tag" skipped \
        "v1.1.0 names $later, which is neither this commit, $noted, nor one before it, so this commit can't be \
released as 1.1.0. Nothing was tagged." "$(read_tag 1.1.0)"
}

tag_on_other_commit() {
    released_1_0_0
    fake_gh tag v1.1.0 "$side"
    run_tag "$noted"
    tagged_as "the tag names a commit off main: the check fails" "failure in Look for the release notes and the tag" \
        skipped "v1.1.0 names $side, which is neither this commit" "$(read_tag 1.1.0)"
}

tag_unreadable() {
    released_1_0_0
    fake_gh fault commit 500 "Server Error"
    run_tag "$noted"
    tagged_as "whether the tag exists can't be found out: the check fails" \
        "failure in Look for the release notes and the tag" skipped \
        "Couldn't find out whether v1.1.0 exists. Nothing was tagged; run this job again." "$(read_tag 1.1.0)"
}

tag_off_main() {
    released_1_0_0
    run_tag "$stray"
    tagged_as "a release commit that isn't on main: the check fails" "failure in Check that the commit is on main" \
        skipped "The tagged commit $stray isn't on main." "$(read_tag 1.2.0)"
}

tag_not_higher() {
    released_1_0_0
    fake_gh release v1.2.0
    run_tag "$noted"
    tagged_as "1.1.0 after the GitHub Release of 1.2.0: the check fails" \
        "failure in Check that X.Y.Z is higher than every released version" skipped \
        "1.1.0 isn't higher than every released version: 1.2.0 is released." "$(read_tag 1.1.0)" "$list_releases"
}

tag_releases_unlisted() {
    released_1_0_0
    fake_gh fault list 503 "Service Unavailable"
    run_tag "$noted"
    tagged_as "the GitHub Releases can't be listed: the check fails" \
        "failure in Check that X.Y.Z is higher than every released version" skipped \
        "Couldn't list the GitHub Releases of owner/sonoff2hue" "$(read_tag 1.1.0)" "$list_releases"
}

tag_pushed_meanwhile() {
    released_1_0_0
    fake_gh fault push-before-ref "$noted"
    run_tag "$noted"
    tagged_as "the tag was pushed on the commit since the check: the Release workflow isn't run a second time" \
        success success \
        "v1.1.0 names $noted already: it was pushed since the check job looked, and its push ran the Release workflow. \
If it didn't, run it on the tag: gh workflow run release.yml --ref v1.1.0" \
        "$(read_tag 1.1.0)" "$list_releases" "$(tag_object 1.1.0 "$noted")" "$(tag_ref 1.1.0)" "$(read_tag 1.1.0)"
}

tag_pushed_elsewhere_meanwhile() {
    released_1_0_0
    fake_gh fault push-before-ref "$later"
    run_tag "$noted"
    tagged_as "the tag was pushed on another commit since the check: the tag job fails" success \
        "failure in Tag the commit vX.Y.Z" \
        "v1.1.0 names $later, not $noted: it was pushed on another commit since the check job looked." \
        "$(read_tag 1.1.0)" "$list_releases" "$(tag_object 1.1.0 "$noted")" "$(tag_ref 1.1.0)" "$(read_tag 1.1.0)"
}

tag_object_fails() {
    released_1_0_0
    fake_gh fault tags 500 "Server Error"
    run_tag "$noted"
    tagged_as "the tag object can't be created: no tag, and the Release workflow doesn't run" success \
        "failure in Tag the commit vX.Y.Z" "Couldn't create the tag object of v1.1.0, for the reason above." \
        "$(read_tag 1.1.0)" "$list_releases" "$(tag_object 1.1.0 "$noted")"
}

tag_ref_fails() {
    released_1_0_0
    fake_gh fault refs 500 "Server Error"
    run_tag "$noted"
    tagged_as "the tag's ref can't be created: no tag, and the Release workflow doesn't run" success \
        "failure in Tag the commit vX.Y.Z" "Couldn't create the tag v1.1.0, for the reason above." \
        "$(read_tag 1.1.0)" "$list_releases" "$(tag_object 1.1.0 "$noted")" "$(tag_ref 1.1.0)" "$(read_tag 1.1.0)"
    tag_names "the tag's ref can't be created: v1.1.0 doesn't exist" v1.1.0 none
}

tag_run_fails() {
    released_1_0_0
    fake_gh fault dispatch 500 "Server Error"
    run_tag "$noted"
    tagged_as "the Release workflow can't be run: the tag job fails and says how to run it" success \
        "failure in Run the Release workflow on vX.Y.Z" "Couldn't run the Release workflow on v1.1.0, for the reason \
above. Run it on the tag: gh workflow run release.yml --ref v1.1.0" \
        "$(read_tag 1.1.0)" "$list_releases" "$(tag_object 1.1.0 "$noted")" "$(tag_ref 1.1.0)" "$(release_run 1.1.0)"
}

# unfit_for_tag VERSION SHA: the tag job refuses these outputs of the check job before it calls gh.
unfit_for_tag() {
    run_tag_job "$1" "$2"
    tagged_as "the check job's outputs '$1' and '$2' aren't a version and a commit: no tag" success \
        "failure in Tag the commit vX.Y.Z" "which aren't a version X.Y.Z and a commit. Nothing was tagged."
}

tag_unfit_outputs() {
    released_1_0_0
    unfit_for_tag 1.1 "$noted"
    unfit_for_tag 1.1.0 "${noted:0:7}"
    unfit_for_tag "" ""
}

tag_after_failed_ci
tag_after_cancelled_ci
tag_after_pull_request
tag_without_notes
tag_bad_version
tag_release_commit
tag_already_on_commit
tag_on_earlier_commit
tag_on_later_commit
tag_on_other_commit
tag_unreadable
tag_off_main
tag_not_higher
tag_releases_unlisted
tag_pushed_meanwhile
tag_pushed_elsewhere_meanwhile
tag_object_fails
tag_ref_fails
tag_run_fails
tag_unfit_outputs

echo
if [ $failures -eq 0 ]; then
    echo "PASS: all $checks checks passed."
else
    echo "FAIL: $failures of $checks checks failed."
    exit 1
fi
