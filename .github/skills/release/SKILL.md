---
name: release
description: Releases a new version of sonoff2hue, X.Y.Z or the next patch, minor or major version. It checks main, chooses and checks the version, writes the release notes, raises the manifest's version and opens the release pull request; merging that pull request tags and publishes the GitHub Release. "check X.Y.Z" checks and records a published release. Use only when asked to release a version.
argument-hint: "[X.Y.Z | patch | minor | major | check X.Y.Z] [--dry-run]"
disable-model-invocation: true
---

# Release a new version of sonoff2hue

This is the release procedure of sonoff2hue (decisions 29, 37 and 38 in `.github/copilot-instructions.md`, the project
memory, whose rules apply throughout). The `/release` command runs it, in VS Code and in Copilot CLI. The *Release*
custom agent runs it too, there and on GitHub as Copilot cloud agent.

## How a version is published

A version is a GitHub Release, "sonoff2hue X.Y.Z", made from a tag `vX.Y.Z` on a commit of main whose
`custom_components/sonoff2hue/manifest.json` has the version `X.Y.Z`. The Marketplace installs the Release from its
tag's source archive, and shows the Release's notes in Home Assistant's update dialog.

1. The release pull request raises the manifest's version and adds the release notes,
   `.github/release-notes/vX.Y.Z.md`. Nothing else. The notes file marks the release commit: a raised version without
   it releases nothing.
2. The owner merges it.
3. When *CI* has passed on main, *Tag the release* (`.github/workflows/tag.yml`) tags that commit `vX.Y.Z` and runs
   *Release* (`.github/workflows/release.yml`) on the tag.
4. *Release* checks the tag again and publishes the GitHub Release, with the notes file as its notes.

**Releases are immutable.**
- A published Release's tag can never be moved or deleted.
- The name of a deleted Release's tag can never be used again.
- So never create, move or delete a tag or a GitHub Release yourself, and never delete a Release. A bad release is fixed
  with the next patch release.

**Release in rising versions.** The Marketplace offers the newest Release, not the highest version. So a version must
be higher than every released version.

## The request

`/release [VERSION] [--dry-run]`, where VERSION is:

- `X.Y.Z`: exactly that version, three numbers without leading zeros and without a suffix, such as `1.2.0`;
- `patch`, `minor` or `major`: the next such version after the highest released one;
- nothing: you propose the version from the changes, by the rules of step 3.

`--dry-run` goes through steps 1 to 6 and stops. Its pull request is a draft titled "Release X.Y.Z (dry run)", to be
closed without merging. It adds the notes but leaves the manifest's version as it is, so that it releases nothing even
if it is merged by mistake.

`/release check X.Y.Z` releases nothing: it goes through step 9 alone, for a version that is already published, and
reports, step 10. Use it after the owner has merged a release pull request that a session on GitHub prepared.

Anything else in the request is a question for the owner, not a reason to change the procedure.

## Where you run

- **On GitHub, as Copilot cloud agent.** You work on the branch that the session gave you, and you can only push to
  it. You can't merge, push to main, push tags or publish anything. Go through steps 1 to 6, then end with the report
  of step 10; for `check`, step 9 and the report. The owner merges, and the merge publishes the release.
  - A tool call that Copilot's safety review doesn't approve waits for the owner in the session's conversation, and
    the session pauses if nobody answers.
  - So use git, the repository's scripts and the GitHub MCP server's read tools, and change the pull request only with
    your progress reports, which push your commits and write its description.
  - Don't edit the pull request with any other tool, its title included.
- **In VS Code or Copilot CLI,** on the owner's machine, with git and gh signed in as the owner. Go through every step.
  Ask the owner before step 7, the merge, which can't be undone.

The repository's rules for commits and pull requests hold in both:
- a commit message's subject has at most 72 characters, starts with a capital letter and an imperative verb ("Add",
  not "Added"), and has no trailing period, with a blank second line, as `.github/scripts/check-commits.sh` checks;
- locally, commit messages and pull request texts are written to files and passed with `git commit -F` and
  `--body-file`, and commits aren't GPG-signed: `git commit --no-gpg-sign`;
- every commit message ends with `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`;
- nothing from the owner's home or this machine goes into the repository, the notes or the pull request: no names of
  lights, rooms or switches, addresses, ids, keys or local paths.

## 1. Read the state

- `git fetch origin --tags`, so that `origin/main` and the tags are current. If the clone is shallow, fetch main's
  history too (`git fetch --unshallow origin`, or `git fetch origin main --depth=1000`).
- **The released versions** are the tags `vX.Y.Z` of the published GitHub Releases, neither drafts nor
  pre-releases, as *Release* counts them: `.github/scripts/release.sh released abpostelnicu/sonoff2hue-homeassistant`.
  On GitHub, gh's token isn't allowed to list the Releases (HTTP 403): use the GitHub MCP server's `list_releases`
  instead. A git tag alone isn't a release.
- **The previous release**, P, is the highest released version.
- **main's manifest version:** `custom_components/sonoff2hue/manifest.json` at `origin/main`. Normally it is P.
- **The minimum Home Assistant version:** `hacs.json` at `origin/main`.

## 2. Check that main can be released

- **CI and Validate** must have succeeded on main's newest commit. Use `gh run list --branch main --limit 10 --json
  headSha,workflowName,status,conclusion`, or the GitHub MCP server's Actions tools. If not, stop: report what failed,
  and don't prepare a release.
- **The beta canary** (*Beta*, `beta.yml`): note its last result. A failure isn't a blocker, but say so in the report.
- **Open pull requests** (`gh pr list`): list them in the report and in the release pull request.
  - Locally, ask the owner whether one should go in first.
  - On GitHub, release main as it is.
- **Locally,** main in the main checkout must equal `origin/main` and be clean. Work in a worktree of your own, never in
  the main checkout.

## 3. Choose the version

| The request | The version | It must |
|---|---|---|
| `X.Y.Z` | X.Y.Z | match `^(0\|[1-9][0-9]*)\.(0\|[1-9][0-9]*)\.(0\|[1-9][0-9]*)$`, be higher than P, and have no tag `vX.Y.Z` yet (`git ls-remote --tags origin vX.Y.Z` prints nothing) |
| `patch` | P with its third number raised | have no tag yet |
| `minor` | P's second number raised, the third 0 | have no tag yet |
| `major` | P's first number raised, the others 0 | have no tag yet |
| nothing | from the changes of step 4 | as for `X.Y.Z` |

Refuse a version that isn't higher than P, and stop. If a tag `vX.Y.Z` exists without a Release, it is left over from a
refused or failed release: stop and report it, as the owner has to decide.

**Choosing it yourself,** by what users of 1.x see:
- **major:** they must do something when they update. For example, the minimum Home Assistant version rises,
  entities or their unique ids are removed or change, the data of the bus events `sonoff2hue_press` and
  `sonoff2hue_toggle` or the Press event's type changes, a setting is removed, or a configuration is migrated without a
  way back.
- **minor:** new things that they can use, and nothing they must change.
- **patch:** fixes only.

Changes for contributors alone don't count: CI, tests, development tools, this memory. If there are no changes for
users since P, say so and stop, unless the owner asked for a version explicitly.

Locally, show the owner the version and why, and go on when they agree. On GitHub, go on, and explain the choice in the
pull request.

## 4. Collect the changes since P

- `git log --no-merges --format='%h %s' vP..origin/main`.
- The pull requests merged since P was published: `gh pr list --state merged --search "merged:>=YYYY-MM-DD" --json
  number,title,mergedAt`, with the date of P's Release.
- The memory's decisions and Status items added since P.
- The users' side: `git diff vP..origin/main -- README.md custom_components/sonoff2hue/translations/en.json
  custom_components/sonoff2hue/manifest.json hacs.json`.

Sort them into:
- new for users;
- fixed;
- what users must know or do;
- for contributors only, which the notes leave out.

Read the changes themselves where a title doesn't say what users notice.

## 5. Write the release notes

Write `.github/release-notes/vX.Y.Z.md`. It becomes the GitHub Release's notes, exactly as written, and Home
Assistant's update dialog shows it. Use this shape, and leave out empty sections:

```markdown
One or two sentences: what this release brings.

## What's new

- …

## Fixes

- …

## Before you update

- … what users must know or do: a higher minimum Home Assistant version, a changed entity, a manual step.

**Full changelog:** https://github.com/abpostelnicu/sonoff2hue-homeassistant/compare/vP...vX.Y.Z
```

**How to write them:**
- For users: what they see and what they can do, with the UI's labels in italics as `en.json` and Home Assistant
  show them.
- No decision numbers, module names, test names or pull request numbers.
- Short, plain sentences, in the README's style.
- Facts only: every line must hold for the code at `origin/main`.

## 6. Open the release pull request

**The branch:**
- On GitHub, your session's branch.
- Locally, a worktree: `git worktree add .worktrees/release-vX.Y.Z -b release/vX.Y.Z origin/main`, with the `.venv`
  link that "How to work in this repository" describes.

**The changes, and nothing else,** so that what was tested on main is what gets released:
1. `custom_components/sonoff2hue/manifest.json`: `"version": "X.Y.Z"`, the only line that changes. A dry run leaves it
   as it is.
2. `.github/release-notes/vX.Y.Z.md`: the notes.

The memory isn't changed here: step 9 records the release once it is published.

**The checks:**
- Run `scripts/check`. On macOS, mypy needs `--platform linux`, as the memory's Environment observations say: run
  the other steps and `.venv/bin/python -m mypy --platform linux`.
- On GitHub, multicast doesn't get through in the session, so one test of the mDNS socket times out there:
  - run `scripts/check --deselect tests/engine/test_mdns.py::test_the_group_reaches_the_socket_and_its_queries_go_out_from_the_interface`;
  - say so in the pull request: its *CI* runs every test.
- Run `.github/scripts/check-commits.sh origin/main..HEAD`.

**The commit:** one, titled `Release X.Y.Z`. Its body says that it raises the version and adds the notes, and that
merging it publishes the GitHub Release "sonoff2hue X.Y.Z" once *CI* passes on main. A dry run's commit is titled
`Draft the notes of release X.Y.Z`.

**The pull request:**
- Its title is `Release X.Y.Z`.
- Its body, written to a file, has:
  - the notes;
  - the results of step 2;
  - the pull requests left open;
  - how the version was chosen;
  - the line **Merging this publishes the release, which can't be undone.**
- On GitHub, your session has opened the pull request. Write this body with your progress report, and leave it a
  draft. Copilot gives it its title, which you don't change. Tell the owner what is left:
  - approve its workflow runs, if GitHub asks;
  - mark it ready for review;
  - rename it `Release X.Y.Z` if Copilot titled it otherwise;
  - merge it with *Squash and merge*, which makes it the single commit `Release X.Y.Z` on main.
- Locally, `gh pr create --title "Release X.Y.Z" --body-file <file>`.

**A dry run** keeps the pull request a draft and starts its body with **Dry run: close this pull request without
merging it.** Its title is "Release X.Y.Z (dry run)" (on GitHub, the owner renames it if Copilot titled it
otherwise). Its body says which line of the manifest the release would change. Stop there and report.

On GitHub, you're done: go to step 10.

## 7. Merge (locally)

1. Wait for every check: `gh pr checks <number> --watch --interval 30`. The ruleset requires *Lint*, *Test*,
   *End-to-end*, *All commits pass*, *hassfest* and *HACS*. If one fails, stop and report: don't change anything but
   the version and the notes to make it pass.
2. Ask the owner: "Release X.Y.Z from <commit>? Merging publishes it, and a release can't be undone." Merge only after
   a clear yes.
3. Merge by fast-forwarding main, as the repository does:
   - `git fetch origin && git merge-base --is-ancestor origin/main <commit> && git push origin <commit>:main`.
   - If main has moved meanwhile, rebase the branch with `git -c commit.gpgsign=false rebase origin/main`, push it, and
     wait for the checks again.

## 8. Follow the publication (locally)

The merge starts *CI* on main. When it passes, *Tag the release* tags the commit and runs *Release*.

1. `gh run list --workflow tag.yml --limit 3` and `gh run watch <id>`, then the same for `release.yml`.
2. If *CI* fails or is cancelled on the release commit, re-run it (`gh run rerun <id> --failed`) before anything else
   is merged. Otherwise the next commit whose *CI* passes is tagged instead.
3. If *Tag the release* refuses, its log says why, and nothing is published: report it.
4. If *Release* fails after the tag exists, re-run its failed jobs; the workflow is built for that. Never move or
   delete the tag of a published Release.

## 9. Check the release

Locally after step 8, and for `/release check X.Y.Z` locally or on GitHub. The release commit is the commit of main
that added the notes: `git log -1 --format=%H --diff-filter=A origin/main -- .github/release-notes/vX.Y.Z.md`. On
GitHub, where gh's token isn't allowed to read the Releases, read the Release, the tag and the commit with the GitHub
MCP server's tools, and report a check that can't run there, such as the attestation, as not run, for the owner to run
locally.

- **The Release:** `gh release view vX.Y.Z --json name,tagName,isDraft,isPrerelease,publishedAt,url,body`.
  - Its name is "sonoff2hue X.Y.Z".
  - It's neither a draft nor a pre-release.
  - Its notes are the notes file.
- **Immutable:** `gh api repos/abpostelnicu/sonoff2hue-homeassistant/releases/tags/vX.Y.Z --jq .immutable` is
  `true`.
- **Latest:** `.../releases/latest --jq .tag_name` is `vX.Y.Z`.
- **The tag:** `.../commits/refs/tags/vX.Y.Z --jq .sha` is the release commit.
- **The attestation:** `gh release verify vX.Y.Z` passes.
- **What the Marketplace installs:**
  1. Download `https://github.com/abpostelnicu/sonoff2hue-homeassistant/archive/refs/tags/vX.Y.Z.zip` to a temporary
     folder.
  2. Check that its `custom_components/sonoff2hue/` holds exactly the files of `custom_components/sonoff2hue` at the
     tag (`git ls-tree -r --name-only vX.Y.Z custom_components/sonoff2hue`), each the same by SHA-256 as
     `git show vX.Y.Z:<path>`.
  3. Delete the download.
- **Record it** in a pull request of its own, with one commit titled `Record release X.Y.Z`, as release 1.0.0 was
  recorded. In the memory:
  - a Verification log entry with the runs of *CI*, *Tag the release* and *Release*, and these checks;
  - a Status item after the last one: `- [x] Release X.Y.Z: the tag vX.Y.Z on <commit> and its immutable GitHub
    Release "sonoff2hue X.Y.Z", Latest (Verification log)`.

## 10. Report

**What it covers:**
- the version and why;
- the pull request;
- the checks of step 2, and anything odd.

**On GitHub, and after a dry run:** what the owner does next, as step 6 says, and that the merge publishes the release
once *CI* passes on main. Once it is published, `/release check X.Y.Z` checks and records it.

**Locally, and for `check`:**
- the Release's link;
- the tag's commit;
- whether it is immutable and Latest;
- the notes;
- the checks that couldn't run;
- the recording pull request.

## When something doesn't fit

Stop and report what you found, rather than improvising: a published release can't be taken back. In particular, don't
work around a failing check, a refused version, an existing tag or a Release that already exists.
