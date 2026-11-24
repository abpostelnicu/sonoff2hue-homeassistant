#!/usr/bin/env bash
# Tests of release.sh and of the Release workflow (decision 29), without a network: made-up refs and versions, a scratch
# git repository, and a stand-in for gh that answers as GitHub would. The workflow's two jobs run as GitHub Actions runs
# them: their steps' scripts from release.yml, in order, with their env, outputs and job outputs, until one fails.
# CI's Test job and scripts/check run it. It needs bash 3.2 or later, git, python3, and PyYAML in PYTHON (default: the
# .venv that scripts/setup makes).
set -euo pipefail

root=$(cd "$(dirname "$0")/../.." && pwd)
release=$root/.github/scripts/release.sh
workflow=$root/.github/workflows/release.yml
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
    refs/tags/v1.2.3/x "refs/tags/v1.2.3 " "refs/tags/v1.2.3"$'\n' refs/heads/main refs/heads/v1.2.3 \
    refs/pull/1/merge v1.2.3; do
    refused "isn't a release tag" "$release" version "$ref"
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
scratch_git init -q -b main
first=$(commit_manifest "$(version_manifest 1.0.0)")
second=$(commit_manifest "$(version_manifest 1.0.1)")
patch=$(commit_manifest "$(version_manifest 1.0.5)")
scratch_git switch -q -c side "$first"
side=$(commit_manifest "$(version_manifest 9.9.8)")
none=$(commit_manifest '{"domain": "sonoff2hue", "name": "sonoff2hue"}')
number=$(commit_manifest '{"domain": "sonoff2hue", "version": 1.0}')
newline=$(commit_manifest '{"domain": "sonoff2hue", "version": "1.0.0\n"}')
list=$(commit_manifest '["1.0.0"]')
broken=$(commit_manifest '{"version": "1.0.0",}')
scratch_git rm -q "$manifest"
scratch_git commit -q -m "No manifest"
missing=$(scratch_git rev-parse HEAD)
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
# A stand-in for gh that answers the calls of release.sh and release.yml as GitHub would, from the state in $FAKE_GH:
# the tags, the releases (newest first), the Latest one and faults to inject. It logs each call's arguments to
# $FAKE_GH/log. `gh _fake ...` sets the state up. An unexpected call or --jq filter exits with 2.
cat > "$scratch/bin/gh" << 'FAKE'
#!/usr/bin/env python3
import json
import os
from pathlib import Path
import shlex
import sys

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


args = sys.argv[1:]
tags = load("tags", {})
releases = load("releases", [])
faults = load("faults", {})


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
        case ["fault", "move-on-create", commit]:
            faults["move-on-create"] = commit
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


def api(args):
    paginate = "--paginate" in args
    args = [arg for arg in args if arg != "--paginate"]
    jq = None
    if "--jq" in args:
        index = args.index("--jq")
        jq = args[index + 1]
        del args[index : index + 2]
    if len(args) != 1:
        unexpected(f"api arguments {args}")
    endpoint = args[0]
    prefix = f"repos/{REPOSITORY}/"
    if not endpoint.startswith(prefix):
        http_error(404, "Not Found")
    path = endpoint.removeprefix(prefix)
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
    if jq is None:
        print(json.dumps(value))
    elif jq in JQ:
        for line in JQ[jq](value):
            print(line)
    else:
        unexpected(f"--jq {jq}")


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
        elif option in ("--repo", "--target", "--title", "--notes-start-tag") and rest:
            options[option] = rest.pop(0)
        else:
            unexpected(f"release create option {option}")
    if options.get("--repo") != REPOSITORY or options.get("--latest") not in ("true", "false"):
        unexpected(f"release create options {options}")
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
    release = {
        "tag_name": tag, "name": options.get("--title", tag), "draft": False, "prerelease": False, "immutable": True,
        "html_url": f"https://github.com/{REPOSITORY}/releases/tag/{tag}",
        "body": f"**Full Changelog**: https://github.com/{REPOSITORY}/{changes}",
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
    case _:
        unexpected(f"command {args}")
FAKE
chmod +x "$scratch/bin/gh"

# fresh_gh: the stand-in gh without tags, releases or faults, and with an empty log.
fresh_gh() {
    rm -rf "$fake"
    mkdir "$fake"
    : > "$fake/log"
}
fake_gh() { FAKE_GH=$fake "$scratch/bin/gh" _fake "$@"; }
with_gh() { env -u GH_TOKEN -u GITHUB_TOKEN FAKE_GH="$fake" PATH="$scratch/bin:$PATH" "$@"; }

# calls_are DESCRIPTION LINE...: the stand-in gh got exactly these calls.
calls_are() {
    local description=$1 actual expected
    shift
    actual=$(cat "$fake/log")
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
echo "The workflow: who may write, and what runs"
# Prints a line "PASS <check>" or "FAIL <check>: <why>" per check of release.yml's structure.
cat > "$scratch/structure.py" << 'PY'
import sys

import yaml

with open(sys.argv[1], encoding="utf-8") as file:
    workflow = yaml.safe_load(file)


def check(ok, description, why=""):
    print(f"PASS {description}" if ok else f"FAIL {description}: {why}")


# YAML 1.1 reads the key on as true.
triggers = workflow.get("on", workflow.get(True))
check(triggers == {"push": {"tags": ["v*.*.*"]}}, "it runs only for pushed tags v*.*.*", triggers)
check(workflow.get("permissions") == {"contents": "read"}, "its token may only read by default",
      workflow.get("permissions"))
concurrency = workflow.get("concurrency")
check(isinstance(concurrency, dict) and isinstance(concurrency.get("group"), str)
      and "${{" not in concurrency["group"] and concurrency.get("cancel-in-progress") is False,
      "one release at a time: one concurrency group, which never cancels a run in progress", concurrency)
jobs = workflow.get("jobs", {})


def writes(permissions):
    if isinstance(permissions, dict):
        return any(value == "write" for value in permissions.values())
    return permissions == "write-all"


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
scripts = [(name, step) for name, job in jobs.items() for step in job.get("steps", []) if "run" in step]
check(scripts and all("${{" not in step["run"] for _, step in scripts),
      "no script has an expression in it: values reach the scripts through env",
      [step.get("name") for _, step in scripts if "${{" in step["run"]])
PY
while IFS= read -r line; do
    case $line in
        "PASS "*) pass "${line#PASS }" ;;
        *) fail "${line#FAIL }" ;;
    esac
done < <("$python" "$scratch/structure.py" "$workflow" 2>&1 || echo "FAIL structure.py exited with $?")

echo
echo "The workflow, run as GitHub Actions runs it, against the stand-in gh"
# Runs a job of release.yml as GitHub Actions runs it on ubuntu-latest: each step's script with
# `bash --noprofile --norc -eo pipefail`, with the workflow's, the job's and the step's env, the github context of
# $STATE/github.json and the outputs of the jobs before it in $STATE/needs.json, until a step fails. It only runs what
# release.yml uses: run steps, actions/checkout (the scratch repository is the checkout already) and the expressions
# below, which it substitutes in env, outputs and scripts as GitHub does; anything else exits with 2. It prints the
# steps' output, then "result: success" or "result: failure in <step>", and adds the job's result and outputs to
# $STATE/needs.json.
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


def resolve(match):
    match match.group(1).split("."):
        case ["github", "sha" | "ref" | "repository" | "token" as name]:
            return github[name]
        case ["steps", step, "outputs", name]:
            return outputs.get(step, {}).get(name, "")
        case ["needs", other, "outputs", name]:
            return needs.get(other, {}).get("outputs", {}).get(name, "")
    harness_error(f"no value for the expression {match.group(0)}")


def evaluate(value):
    if isinstance(value, bool):
        return str(value).lower()
    return re.sub(r"\$\{\{\s*(.*?)\s*\}\}", resolve, str(value))


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


for unsupported in ("defaults", "if", "strategy", "container", "services", "environment"):
    if unsupported in job or (unsupported == "defaults" and unsupported in workflow):
        harness_error(f"{unsupported} isn't supported")
inherited = ("GITHUB_", "RUNNER_", "ACTIONS_", "GH_")
base = {key: value for key, value in os.environ.items() if not key.startswith(inherited)}
base.update(CI="true", GITHUB_ACTIONS="true", GITHUB_REF=github["ref"], GITHUB_SHA=github["sha"],
            GITHUB_REPOSITORY=github["repository"], GITHUB_WORKSPACE=workspace,
            GITHUB_STEP_SUMMARY=str(state / "summary.md"))
result = "success"
for number, step in enumerate(job["steps"], 1):
    name = step.get("name") or step.get("uses") or "run"
    for unsupported in ("if", "shell", "working-directory", "continue-on-error"):
        if unsupported in step:
            harness_error(f"{unsupported} in the step {name} isn't supported")
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

# job JOB: runs the job of release.yml, as simulate.py does, and prints its result. Its output goes to $state/JOB.log.
job() {
    local status=0
    with_gh "$python" "$scratch/simulate.py" "$workflow" "$1" "$workspace" "$state" > "$state/$1.log" 2>&1 || status=$?
    if [ $status -gt 1 ]; then
        echo "error: simulate.py exited with $status"
    else
        sed -n 's/^result: //p' "$state/$1.log" | tail -n 1
    fi
}

# run_workflow REF SHA: GitHub runs the workflow for a push of REF that names SHA: the check job, then the GitHub
# Release job if the check job passed. Sets $check and $created to their results, or "skipped".
run_workflow() {
    rm -rf "$state"
    mkdir "$state"
    printf '{"ref": "%s", "sha": "%s", "repository": "owner/sonoff2hue", "token": "a-token"}\n' "$1" "$2" \
        > "$state/github.json"
    : > "$fake/log"
    check=$(job check)
    created=skipped
    if [ "$check" = success ]; then created=$(job release); fi
}

# run_release REF SHA VERSION LATEST PREVIOUS: GitHub runs only the GitHub Release job, with these outputs of the check
# job, as when only the failed jobs of a run are run again. Sets $created to its result.
run_release() {
    rm -rf "$state"
    mkdir "$state"
    printf '{"ref": "%s", "sha": "%s", "repository": "owner/sonoff2hue", "token": "a-token"}\n' "$1" "$2" \
        > "$state/github.json"
    printf '{"check": {"result": "success", "outputs": {"version": "%s", "latest": "%s", "previous": "%s"}}}\n' \
        "$3" "$4" "$5" > "$state/needs.json"
    : > "$fake/log"
    check=success
    created=$(job release)
}

# outcome DESCRIPTION CHECK CREATED CREATE [MESSAGE]: the last run's check job ended as CHECK and its GitHub Release job
# as CREATED; the stand-in gh got the call `release create CREATE`, or no such call for "none"; and MESSAGE, if given,
# is in the jobs' output.
outcome() {
    local description=$1 expected_check=$2 expected_created=$3 create=$4 message=${5:-} calls problems=""
    calls=$(sed -n 's/^release create //p' "$fake/log")
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

options() { # VERSION LATEST [START]: the options that the GitHub Release job gives gh release create
    printf "v%s --repo owner/sonoff2hue --verify-tag --target %s --title 'sonoff2hue %s' --generate-notes --latest=%s" \
        "$1" "$sha" "$1" "$2"
    if [ $# -gt 2 ]; then printf ' --notes-start-tag v%s' "$3"; fi
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
if [ $failures -eq 0 ]; then
    echo "PASS: all $checks checks passed."
else
    echo "FAIL: $failures of $checks checks failed."
    exit 1
fi
