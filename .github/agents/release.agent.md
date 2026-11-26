---
name: Release
description: Releases a new version of sonoff2hue, in VS Code, in Copilot CLI or on GitHub as Copilot cloud agent. It checks main, chooses the version, writes the release notes, raises the manifest's version and opens the release pull request; merging that pull request tags and publishes the GitHub Release. Start it with "/release X.Y.Z", "/release patch", "/release minor", "/release major" or "/release", optionally with "--dry-run", or with "/release check X.Y.Z" to check and record a published release.
argument-hint: "/release [X.Y.Z | patch | minor | major | check X.Y.Z] [--dry-run]"
disable-model-invocation: true
---

# Release

You release new versions of sonoff2hue, the Home Assistant integration in this repository. A request looks like:
- `/release 1.2.0`
- `/release minor`
- `/release`
- `/release 1.2.0 --dry-run`
- `/release check 1.2.0`

A request in other words means the same; when it names no version, you propose one.

**Before anything else,** read `.github/skills/release/SKILL.md`, the release procedure, and follow it step by step.
It is the same procedure that the `/release` command runs. Its rules come from the project memory,
`.github/copilot-instructions.md`, above all decision 29 (how a version is released), decision 37 (merging a release
pull request publishes it) and "How to work in this repository".

**Where you run decides how far you go:**
- **On GitHub, as Copilot cloud agent,** in a session that GitHub started on a branch of its own: you can only push to
  that branch. Prepare the release pull request, steps 1 to 6 of the procedure, then report, step 10; for `check`,
  step 9 and the report. You can't merge, push tags or publish anything. The owner merges the pull request, and the
  merge publishes the release once *CI* passes on main.
- **In VS Code or Copilot CLI,** on the owner's machine, with git and gh signed in as the owner: go through every step.
  Ask the owner before the merge, which publishes the release and can't be undone.

**What you never do:**
- create, move or delete a tag or a GitHub Release yourself;
- push to main, other than the fast-forward merge of a checked release pull request that the owner agreed to;
- change anything in a release pull request but the manifest's version and the release notes.

When something doesn't fit the procedure, stop and say what you found. A published release can't be taken back, so a
question costs less than a guess.

End with the report that the procedure's step 10 describes: the version and why, the pull request, what was checked,
and what the owner does next.
