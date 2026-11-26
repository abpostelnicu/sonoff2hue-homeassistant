# sonoff2hue for Home Assistant: Copilot instructions and project memory

This file is the project's GitHub Copilot memory. Copilot loads `.github/copilot-instructions.md` automatically
for chat, agent and code-review requests in this repository. It records what was asked, what was learned, what was
decided and why, and where the work stands. Every step is also recorded as a git commit.

Keep it current. When you make a decision, learn something about Home Assistant, the devices or the APIs, verify
something or finish a step, update the matching section below in the same commit as the change. Append; don't
renumber decisions.

**This repository is public.** Never put secrets here (Hue application keys, eWeLink device keys, certificate pins),
and nothing about the user's own network or devices: no host names, addresses, device ids, serial numbers or SSH host
keys. Tests, examples and this file use made-up device ids and keys, such as `1000abcdef`, and documentation addresses,
such as 192.0.2.10.

## Project

`sonoff2hue-homeassistant` is a Home Assistant custom integration, domain `sonoff2hue`, for Home Assistant 2026.11 and
later. When a wall switch wired to a Sonoff MINIR4M in detached relay mode is operated, it toggles the Philips Hue
lights, rooms and zones mapped to that MINIR4M. It listens passively, with an mDNS socket of its own, for the MINIR4M's
eWeLink LAN mDNS broadcasts, and switches the lights through the Hue bridge's local CLIP v2 API. Home Assistant's own UI
sets it up and shows what it does: a setup flow, a subentry per switch, devices and entities, repairs, the logbook and
diagnostics.

It stands alone (decision 30). Python's standard library and the libraries that Home Assistant ships decide edge cases,
and the product rules (Behavior) are its own specification. The mDNS layer is the exception: it keeps exactly its
behavior, with string rules of its own, and never relies on Home Assistant's mDNS.

## Goal (from the user)

On 2026-11-23 the user asked for a Home Assistant integration like SonoffLAN that toggles Hue lights from MINIR4M wall
switches, for Home Assistant 2026.11 and later only. The user's choices, made when the plan was approved that day:

- A Python custom integration, which runs in Home Assistant's own process.
- Its own public repository, `abpostelnicu/sonoff2hue-homeassistant`, under the Apache-2.0 license.
- Hue controlled directly: its own link-button pairing, the pinned certificate and a warm connection. Home Assistant's
  Hue integration and its light entities aren't used.
- Home Assistant's own UI:
  - a setup flow and one config subentry per switch;
  - entities, repairs, logbook entries and diagnostics;
  - no custom panel.
- A one-time import of an existing `sonoff2hue.yaml`.
- The live test runs on the user's Home Assistant OS.

> The interface must use Home Assistant components. (2026-11-24)

Asked what that covers, the user chose Home Assistant's own UI only: dialogs built from Home Assistant's form controls,
devices and entities, repairs, the logbook and diagnostics, and no custom panel or web page. The user didn't choose Home
Assistant's entity and area pickers for the lights, its Hue integration for switching them, or SonoffLAN for reading
the switches, so those stay as above.

On 2026-11-25 the user chose:

- the logbook's entries for presses: Home Assistant's own, the switch's *Press* and *Last action* (decision 26);
- a standalone, Pythonic integration: Python's standard library and the libraries that Home Assistant ships, with the
  product rules kept and nothing that reproduces another platform's rules (decision 30);
- an mDNS layer of the integration's own, which never relies on the mDNS that Home Assistant or Home Assistant OS
  provides, and keeps exactly its behavior (decision 30).

## How to work in this repository

- Run `scripts/setup` once. It uses uv to create `.venv` with Python 3.14 and the pinned tools of
  `requirements_test.txt`, Home Assistant included. Then `scripts/check` runs what CI runs: `ruff check`,
  `ruff format --check`, `mypy` (strict, on `custom_components` and `tools`), the Release workflow's tests
  (`.github/scripts/release-test.sh`) and `pytest`. Arguments of `scripts/check` go to pytest. `scripts/e2e` runs the
  end-to-end test (decision 27), and `scripts/develop` a local Home Assistant with the fake Hue bridge and the simulated
  wall switch (decision 18).
- `custom_components/sonoff2hue/engine/` is the integration's engine. Its modules import nothing from Home Assistant,
  which `tests/test_own_mdns.py` checks; the rest of `custom_components/sonoff2hue` adapts them to Home Assistant. Keep
  that split.
- Keep the product rules (Behavior): how presses are detected and lights toggled, the timings, pairing and discovery,
  the switches' limits and their messages, the log lines and their levels, and the flows, entities, repairs, bus events
  and diagnostics. For text, JSON, base64, addresses, HTTP and YAML, use Python's standard library and the libraries
  that Home Assistant ships (aiohttp, yarl, cryptography, PyYAML, probatio, ifaddr), and test the integration's own
  behavior with hand-written pytest cases (decision 30).
- The mDNS layer keeps exactly its behavior for every input: `engine/dns.py`, `engine/mdns.py`, the record part of
  `engine/ewelink.py` (`EwelinkAnnouncement`, `announcements`, `probing_device_ids`, `try_get_device_id`,
  `_find_address` and `_sender`) and the mDNS part of Hue discovery (`_discover_mdns` in `engine/hue_setup.py`). A
  change to it must keep its recorded cases, `tests/engine/mdns_packets.json` and `mdns_text.json`, and its tests
  passing with their expectations unchanged: `test_mdns_packets.py`, `test_mdns_text.py`, `test_dns.py`, `test_mdns.py`,
  `test_lan_monitor.py`, the record tests of `test_ewelink.py` and the discovery tests of `test_hue_setup.py`. Only
  names, ids and docstrings may change in them.
- Never rely on Home Assistant's mDNS: no python-zeroconf, no `zeroconf` or `network` integration, no resolver that
  answers `.local` names through them, no zeroconf or HomeKit matcher in the manifest, and no Home Assistant shared HTTP
  session for anything that may need mDNS. `tests/test_own_mdns.py` checks the imports and the manifest.
- Never run two listeners that toggle the lights for the same switch: every press would toggle twice and leave the
  lights as they were.
- Stay listen-only towards the switches: the only packets sent are standard mDNS queries.
- Tests must not need the network, a Hue bridge or a MINIR4M. Use the packet builders in `tests/engine/packets.py`,
  the fake Hue bridge in `tools/fake_hue` (on 127.0.0.1), `InMemoryBridge` in `tests/hue.py`, and the fake network and
  clock of `tests/engine/fakes.py`.
- Log the way Home Assistant does: a module-level `_LOGGER` and lazy `%s` arguments (ruff's G rules), with the
  existing lines' wording. Trace-level lines (repeated broadcasts, malformed packets) use level 5, below DEBUG.
- Diagnostics and logs never show the Hue application key, device keys or the certificate pin. Never commit a
  `sonoff2hue.yaml`; `.gitignore` excludes it.
- Docstrings, comments, test names and messages describe the integration on its own terms; decisions are named by their
  numbers.
- Make one descriptive commit per step, and every commit must pass `scripts/check` on its own. Check the whole branch
  with `.github/scripts/check-commits.sh origin/main..HEAD`, which checks each commit's message by the rules below and
  its whitespace errors and conflict markers (`git diff --check`), and with
  `.github/scripts/test-each-commit.sh origin/main..HEAD`, which runs `scripts/check` for each commit in a temporary
  worktree.
- Commit messages: a subject of at most 72 characters, a capital first letter, an imperative verb ("Add", not "Added"
  or "Adds"), no trailing period, and a blank second line. No fixup!/squash!/WIP commits and no whitespace errors.
  Commit with `--no-gpg-sign` (the user's preference) and end the message with
  `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`. The global git config signs commits, so
  rebase with `git -c commit.gpgsign=false rebase …`, or a rebase opens a passphrase prompt.
- Write commit messages and pull requests' titles and bodies to files, and pass them with `git commit -F` and
  `gh pr create --body-file`. macOS's /bin/bash 3.2 misparses `$(cat <<'EOF' …)` when the text has an apostrophe, and
  has run backtick spans in it as commands. Never paste a local command's output into them, such as a test log: it
  shows the checkout's path, which is private. GitHub keeps every revision of a pull request's description in its
  public edit history, and only the owner can delete one, on the web page.
- Stage your own files by explicit path; never use `git add -A` or `git commit -a`, which would pick up another
  agent's unfinished edits. Agents working in parallel each use their own git worktree under `.worktrees/` (add it to
  `.git/info/exclude`) and their own branch (decision 13). They don't use `git stash`, whose list all worktrees share.
- Push new commits to a branch, open a pull request and wait for the checks. Then merge by fast-forwarding `main`
  (`git push origin <sha>:main`), which keeps the commit hashes; GitHub then marks the pull request as merged. A
  ruleset on `main` (id 28003079) requires *Lint*, *Test*, *All commits pass*, *hassfest*, *HACS* and *End-to-end*;
  repository admins can bypass it. It matches the checks by their job names, so rename such a job only together with
  the ruleset.
- CI (`.github/workflows/ci.yml`) runs *Lint* (ruff, the formatter, mypy), *Test* (pytest with coverage, and
  `release-test.sh`) and *End-to-end* (decision 27: `scripts/e2e`, a real Home Assistant with the integration, the fake
  Hue bridge and the simulated wall switch) on `main` and on the merge result of each pull request. *Commits*
  (`commits.yml`) checks every commit of a pull request on its own: the message rules and `git diff --check`, then
  `scripts/check` per commit, summed up by *All commits pass*. *Validate* (`validate.yml`) runs *hassfest* and *HACS*
  (decision 16). They install uv 0.13.7 with `astral-sh/setup-uv@v10.4.0` (it publishes only full-version tags) and
  run `scripts/setup`, with uv's cache keyed on `requirements_test.txt`, and for *End-to-end* on `requirements_dev.txt`
  too. A failed *End-to-end* keeps Home Assistant's and the fake bridge's logs as the artifact `end-to-end-logs`. Run
  `scripts/e2e` before pushing a change to how the integration sets up, runs or toggles.
- *Beta* (`beta.yml`, decision 35) is a canary for the next Home Assistant release. Every Monday, on demand and on a
  pull request that changes it, it runs mypy and the tests against the newest `pytest-homeassistant` on PyPI, betas
  included, and its summary names the Home Assistant version. No ruleset requires it. When it fails, its log shows
  whether the install, mypy or a test failed: fix the integration in a pull request before that release ships, so that
  it works on the pinned release, which CI tests, and on the beta, which `gh workflow run beta.yml --ref <branch>`
  tests. GitHub notifies whoever last changed its `cron` line of its scheduled runs.
- Releases (decisions 29 and 37) come from a release pull request or from a tag pushed by hand. **A release pull
  request** raises `version` in `custom_components/sonoff2hue/manifest.json` to X.Y.Z and adds the release's notes,
  written for users, as `.github/release-notes/vX.Y.Z.md`, the file that marks a release commit. Once it is merged and
  *CI* passes on that commit on `main`, *Tag the release* (`tag.yml`) tags it `vX.Y.Z` and runs *Release* on the tag,
  and the GitHub Release takes that file as its notes. CI must pass on the release commit itself: if it fails there,
  or a newer push to `main` cancels it, the next commit whose CI passes gets the tag instead, with the changes merged
  after the release commit. So re-run CI on the release commit (`gh run rerun <id>`, or *Re-run jobs*) before merging
  anything else. **By hand,** tag the merged commit on `main` with `vX.Y.Z` (`git tag -a vX.Y.Z -m "sonoff2hue X.Y.Z"`)
  and push the tag. Either way the *Release* workflow checks the tag and creates the GitHub Release "sonoff2hue
  X.Y.Z", from which the Marketplace installs. Release in rising versions: the Marketplace offers the newest Release,
  not the highest version. Releases are immutable: a published Release's tag can't be moved or deleted, and a deleted
  Release's tag name can never be used again, so never delete a Release; fix a bad release with the next patch.
  Merging a release pull request, and any tag `vX.Y.Z` on `main` whose `version` is `X.Y.Z`, release for real. A tag
  pushed for a test must fail one of the checks before the Release (another version, a commit that isn't on `main`, a
  suffix) and is deleted right afterwards. `.github/scripts/release-test.sh` tests the checks and both workflows;
  `scripts/check` and CI's *Test* job run it.
- The Marketplace's catalog (decision 31): hacs/default#14951 adds sonoff2hue to it. While it waits, don't comment on
  it, push to it or open another one, as `hacs-bot` asks. If a reviewer sets it to draft, fix what they ask in this
  repository, and mark it ready for review again.

## Layout

| Path | Contents |
|------|----------|
| `custom_components/sonoff2hue` | The integration (decision 16): `manifest.json`; `const.py`, the domain, the config entry's keys (`CONF_BRIDGE_IP`, `CONF_BRIDGE_ID`, `CONF_APP_KEY`, `CONF_CERTIFICATE_SHA256`) and title (`ENTRY_TITLE`), a switch subentry's type and keys (`SUBENTRY_SWITCH`, `CONF_DEVICE_ID`, `CONF_NAME`, `CONF_DEVICE_KEY`, `CONF_LIGHTS`, `CONF_GROUPS`), and the bus events with their data keys; `__init__.py`, which loads the remembered resources and starts the runtime, adds the update listener and `async_track_devices`, forwards `PLATFORMS` and unloads them, deletes the remembered resources with the entry (`async_remove_entry`), and declares `Sonoff2HueConfigEntry`; `translations/en.json`, every text (custom integrations ship no `strings.json`); `icons.json`, the entities' icons; `brand/`, the integration's icons (`icon.png`, `icon@2x.png`) |
| `custom_components/sonoff2hue/config_flow.py` | The setup flow with reconfigure and reauth (decision 22): it finds or takes the bridge's address and pairs it with the engine's `hue_setup`, or imports a `sonoff2hue.yaml` with `yaml_import`. It declares the `switch` subentry type, whose flow is `switch_flow.py` |
| `custom_components/sonoff2hue/switch_flow.py` | The switch subentry flow (decision 24): it adds and edits the switches with the engine's `switch_form`, `switch_rules` and `picker`, and offers a room or zone for a switch's single lights with `group_offer` (decision 34). From the loaded entry's runtime (decision 23) it takes the devices seen on the LAN (`lan_registry`) and the factory of Hue clients, and the switches and the bridge from `config_from_entry` |
| `custom_components/sonoff2hue/runtime.py`, `repairs.py`, `remembered.py` | The config entry's runtime (decision 23): `Sonoff2HueData`, which is `entry.runtime_data`, with the engine's parts that live as long as the entry (the mDNS listener that the LAN monitor runs, the LAN registry, the status tracker, the activity buffer), the remembered resources, and the bridge supervisor of the current configuration (`config_from_entry`); the dispatcher signals; the bus events; the repair issues; the renames saved in the switches' subentries; the switch test (`async_test_toggle`); the search for the bridge, which every supervisor gets (`new_bridge_search`), and the new address of a bridge that moved (decision 33). `repairs.py` has the repair flow that leads to the reconfigure flow, its text with the issue's placeholders. `remembered.py`: `RememberedResources`, the resource that each entry last found, in the entry's store (decision 32), and `async_remove` |
| `custom_components/sonoff2hue/entity.py` and the platforms | The devices and entities (decision 25). `entity.py` has what they share: the bridge's device and each switch's (`bridge_identifier`, `switch_of`); `Sonoff2HueEntity`, named by its translation key, which can follow the status tracker's snapshots (`_follow_status`); `BridgeEntity`, on the bridge's device; `WallSwitchEntity`, on a switch's device, which follows its subentry; `async_add_switch_entities`, which adds a platform's entities for each `switch` subentry, now and when one is added; and `async_track_devices`, which keeps the devices' identifiers and names and the entities' unique ids in step with a new bridge id, device id or title. The platforms: `sensor.py` (Status, eWeLink devices seen, Last action), `binary_sensor.py` (Reachable, Online), `event.py` (Press) and `button.py` (Test toggle) |
| `custom_components/sonoff2hue/diagnostics.py` | The config entry's diagnostics (decision 26): `async_get_config_entry_diagnostics` gives the configuration that the supervisor runs, the status snapshot, the LAN registry's devices with `online` and the activity buffer's last 1,000 entries, in plain JSON, through `async_redact_data` with `TO_REDACT`, the keys of the secrets. The integration has no `logbook.py`: the logbook shows the entities' changes |
| `custom_components/sonoff2hue/engine`, the mDNS layer | The integration's own mDNS (decision 30). `dns.py`: DNS messages, parsed and built (`DnsMessage`, `build_query`), with names and TXT keys compared by the layer's case rule (`case_key`, `equals_ignoring_case`, `starts_with_ignoring_case`, `ends_with_ignoring_case`) and a name's length in UTF-16 code units. `mdns.py`: `MdnsListener` with its started callbacks and packet handlers, `MdnsPacket` and `TRACE`; the UDP 5353 socket (`open_multicast_transport`, `open_multicast_socket`); the interfaces (`interface_states`, from ioctls and ifaddr; `multicast_interfaces`; `local_addresses`); network changes (`watch_network_changes`, netlink or polling; `address_changes`). The record part of `ewelink.py`, with its rules for white space, device ids and `seq`, and `_discover_mdns` in `hue_setup.py` |
| `custom_components/sonoff2hue/engine`, eWeLink and presses | `ewelink.py`, besides the records: the payload (`read_params`, `decrypt`, `encrypt`, with `base64`, `json` and cryptography). `press.py`: `PressDetector`, `SwitchTracker` and `summarize` |
| `custom_components/sonoff2hue/engine`, Hue | `hue_address.py`: the bridge's address (`BridgeUrl`, which tells an IP address from a host name, and `create_base_url`), with `ipaddress` and yarl. `hue_client.py`: `HueClient`, the CLIP v2 client, on `AiohttpTransport`, a connector of its own with the pin (`_CertificateCheck`, an `aiohttp.Fingerprint`); `HttpAnswer`, `HueApiError`, `CERTIFICATE_MISMATCH` and `PAIR_AGAIN`; `matches_pin`, the pin's rule for a certificate read elsewhere. `hue_setup.py`: discovery (`discover`, with mDNS, Signify's endpoint or both), the bridge's identity and certificate (`get_bridge_info`), and pairing (`pair`, `build_device_type`). `targets.py`: the target resolver (`resolve`, and `resolve_switch` with the remembered resources: `EntryKey`, `RememberedResource`, `Renamed`, decision 32), the name rule that the picker and the switch rules share (`name_key`, `refers_to`, `split_kind`), and the picker's spelling of a resource (`encode_light`, `encode_group`). `toggler.py`: `HueToggler`, with `ToggleResult` and `TargetFailure` |
| `custom_components/sonoff2hue/engine`, running | `runner.py`: the runner and `RunnerObserver` (decision 14), and the switches resolved each on its own (`resolve_bindings`, `resolve_switches`, `ResolvedSwitches`, decision 32). `supervisor.py`: `BridgeSupervisor`, which runs the runner for one configuration, and `create_hue_client` (decision 21); `BridgeSearch`, with its `BridgeFinder` and `BridgeReader`, which looks for the bridge at a new address, and `BridgeMoved`, what it reports (decision 33). `status.py`: the `StatusTracker`, the state that the entities and diagnostics show (decision 21) |
| `custom_components/sonoff2hue/engine`, the LAN view | `registry.py` (`DeviceRegistry`), `lan_monitor.py` (`LanMonitor`, which runs the config entry's mDNS listener) and `activity.py` (`ActivityBuffer`, a `logging.Handler`) (decision 17) |
| `custom_components/sonoff2hue/engine`, the settings and the switches | `config.py`: `ConfigError`, `HueSettings`, `SwitchSettings` and `EngineConfig` with `find_switch` and `validate_for_run`. `switch_rules.py`: the rules a switch meets before it is saved, and the picker's resources; `switch_form.py`: the switch form's own checks; `picker.py`: the picker of lights, rooms and zones (decision 20); `group_offer.py`: the room or zone that the switch dialog offers for a switch's single lights, or the zone to create of them, and the switch with it in their place (decision 34). `yaml_import.py`: a pasted `sonoff2hue.yaml`, read with PyYAML and a probatio schema (decision 30) |
| `tests/engine` | The engine's tests: `test_dns.py`, `test_mdns.py`, `test_ewelink.py`, `test_press.py`, `test_registry.py`, `test_lan_monitor.py`, `test_activity.py`, `test_config.py`, `test_targets.py`, `test_toggler.py`, `test_hue_address.py`, `test_hue_client.py`, `test_hue_setup.py`, `test_runner.py`, `test_supervisor.py`, `test_status.py`, `test_picker.py`, `test_switch_form.py`, `test_switch_rules.py`, `test_group_offer.py` and `test_yaml_import.py`, one per module. `packets.py` builds raw DNS packets, MINIR4M broadcasts and `MdnsPacket`s; `fakes.py` has `FakeNetwork`, fake multicast transports for `MdnsListener`, and `VirtualTime`, which makes the event loop's clock jump to the next timer whenever the loop would wait. The mDNS layer's recorded cases (decision 30): `mdns_packets.json`, 36 packets with the message, announcements and probes read from each or its parse error, which `test_mdns_packets.py` replays; and `mdns_text.json`, the layer's white space and case mappings for every code point, which `test_mdns_text.py` replays |
| `tests`, the fakes | `hue.py` runs the fake Hue bridge on 127.0.0.1 with a manual clock and a new certificate, or one that it is given (`FakeHueHost.start`'s `certificate`), and has `InMemoryBridge`, which answers and records requests without sockets. `fakes.py`: `Lan`, the entry's listener on `FakeNetwork` with the broadcasts of MINIR4Ms in plain text or encrypted; `Bridge`, an `InMemoryBridge` with lights, a room, names that a test may change, lights and a room that a test may delete (not listed, nor among the zones' children, and answered 404), the zones that `POST /clip/v2/resource/zone` creates, faults (for one method, if given), held answers and counts of the clients made and closed, and addresses that it moved away from (`moved_from`), where no request gets an answer; `Finder`, the runtime's search for the bridge, with the bridges that it lists and what reading each gives (`bridge_info()`); `switch()` and `paired()`, a switch subentry and an entry. `conftest.py`: the fixtures `fake_hue` and `fake_hue_pressed`, which enable sockets, `lan` and `bridge`, which put `Lan` and `Bridge` in place of `runtime.new_listener` and `runtime.create_hue_client`, and `finder`, which puts `Finder` in place of `runtime.new_bridge_search`. `platforms.py`: the entity ids on the bridge's device and on the switch "Hall" of `fakes.switch()`, and `start`, `settle` and `tick` |
| `tests`, the integration | `test_own_mdns.py`, the guard (decision 30): it reads the integration's modules with `ast`, and its manifest, and fails on an import of python-zeroconf, Home Assistant's `zeroconf` or `network` integration or `aiohttp_asyncmdnsresolver`, on an engine module that imports Home Assistant, and on a manifest with a zeroconf or HomeKit matcher or a dependency on `zeroconf` or `network`. `test_init.py`, `test_config_flow.py`, `test_switch_flow.py` and `test_runtime.py` run the integration in Home Assistant, with pytest-homeassistant's `hass` fixture and `MockConfigEntry`; `test_entity.py`, `test_sensor.py`, `test_binary_sensor.py`, `test_event.py` and `test_button.py` its devices and entities; `test_diagnostics.py` the diagnostics and that no secret reaches them or the log; `test_logbook.py` the real logbook, on the recorder; `test_brand.py` the brand images |
| `tests/tools` | `test_fake_hue.py`, the fake Hue bridge; `test_develop.py`, the development configuration folder and the pins of `requirements_dev.txt` |
| `tools/fake_hue` | The fake Hue bridge (decision 12): `bridge.py` (state, with its renames and deletions, `Fault`s), `server.py` (endpoints, HTTPS, a certificate of its own or one that it is given), `__main__.py` (`python -m tools.fake_hue`, settings `FAKE_HUE_BIND`, `FAKE_HUE_PORT`, `FAKE_HUE_LINK_BUTTON`) |
| `tools/develop` | The development Home Assistant's configuration folder (decision 18): `python -m tools.develop <folder>` copies `configuration.yaml` once and writes Home Assistant's HTTP store, `.storage/http`, on 127.0.0.1 (port 8123 or `SONOFF2HUE_DEV_PORT`), and refuses a port in use |
| `tools/e2e` | The end-to-end test (decision 27), `python -m tools.e2e`, which `scripts/e2e` runs: `__init__.py` (the switch, the light's name after the rename, and the targets and PUTs to expect), `hass.py` (Home Assistant's process, started and restarted as `scripts/develop` does, its onboarding, and its REST and WebSocket APIs), `devices.py` (the fake bridge's process and the simulated switch), `checks.py` (the report of the checks, and the bus events received), `scenario.py` (the steps and their checks) and `__main__.py` (free ports, and the work folder `.dev/e2e`) |
| `tools/send-ewelink-announcements.py` | The simulated wall switch: a script on the standard library that sends, for each seq, the mDNS response with which a MINIR4M in DIY mode announces its state (PTR and TXT answers, SRV and A records), 1.5 s apart and with multicast TTL 0. ruff and mypy check it, with only N999 ignored for its name |
| `scripts` | `setup` (the virtual environment) and `check` (lint, formatting, types, the release tests and the tests); `develop` (decision 18) runs a local Home Assistant with this checkout's integration from `.dev/venv` (`requirements_dev.txt`), and the fake Hue bridge and the simulated wall switch from `.venv`, with its commands `run`, `hass`, `fake-hue`, `setup`, `press` and `lights`; its state is in `.dev/`, which git ignores, and `SONOFF2HUE_DEV_CONFIG` chooses another configuration folder than `.dev/config`; `e2e` (decision 27) runs the end-to-end test, `tools/e2e` |
| `.github/scripts` | `check-commits.sh`: each commit's message and `git diff --check`. `test-each-commit.sh`: `scripts/check` per commit, in a temporary worktree. `release.sh`: the release checks, the version in a commit's manifest (`manifest-version`), the released versions, Latest, where the notes start, and whether a version is higher than every released one (`check-higher`). `release-test.sh`: its tests, and the jobs of `release.yml` and `tag.yml` run as GitHub runs them, their conditions included, against a stand-in `gh` and a scratch repository; then the mistakes that these checks catch, each made in a copy of the workflows and `release.sh` (decision 37) |
| `.github/copilot-instructions.md` | This file: the project memory, which Copilot loads for every request in this repository |
| `.github/workflows` | `ci.yml` (*Lint*, *Test*, *End-to-end*); `commits.yml` (per-commit checks, *All commits pass*); `validate.yml` (*hassfest* and *HACS*, the validation the Marketplace requires, decision 16); `release.yml` (*Release*, decisions 29 and 37, for a tag `vX.Y.Z`, pushed or run on: *Check the tag* checks it and chooses Latest and the notes' start; *GitHub Release* creates the GitHub Release "sonoff2hue X.Y.Z", with the tagged commit's `.github/release-notes/vX.Y.Z.md` as its notes, or GitHub's without it); `tag.yml` (*Tag the release*, decision 37, after every *CI* run on `main`: *Check the release commit* finds a release commit that isn't tagged yet, and *Tag and release* tags it `vX.Y.Z` and runs *Release* on the tag); `beta.yml` (*Beta*, decision 35: *Test (Home Assistant beta)*, mypy and the tests against the newest `pytest-homeassistant`, betas included, weekly, on demand and on pull requests that change it; no ruleset requires it) |
| `.github/release-notes` | The notes of the releases (decision 37): `vX.Y.Z.md`, which a release pull request adds with the version. It marks the release commit, and its GitHub Release takes it as its notes, as it is. None yet |
| `.vscode` | The VS Code debug setup (decision 18): `launch.json` runs Home Assistant and the fake Hue bridge under debugpy, alone or together; `tasks.json` runs `scripts/develop setup`, `press` and `lights`; `extensions.json` recommends the extensions. `.gitignore` keeps everything else in `.vscode/` out |
| `README.md` | The users' documentation, which the Marketplace and GitHub show (decision 28): what the integration does and how, its requirements, the installation, which the Marketplace leaves out from Home Assistant 2026.12, the setup with the import of a `sonoff2hue.yaml` and its format, the switches and their device keys, the devices and entities, presses in automations, the repairs, the activity, log and diagnostics, troubleshooting and limitations; then *Development* (decisions 18, 27 and 30) and *Releases* (decision 29) for contributors |
| The root | `hacs.json` (the Marketplace's name and minimum Home Assistant version), `pyproject.toml` (ruff, mypy, pytest and coverage), `requirements_test.txt` (the pinned tools, Home Assistant and `types-PyYAML`), `requirements_dev.txt` (decision 18), `LICENSE` (Apache-2.0) and `.gitignore` |

## Behavior

The integration's own specification: the rules that its code and tests keep. Decisions give the reasons.

### The mDNS layer

The integration's own mDNS, which keeps exactly this behavior for every input (decision 30):

- `engine/dns.py` and `engine/mdns.py`;
- the record part of `engine/ewelink.py`: `EwelinkAnnouncement`, `announcements`, `probing_device_ids`,
  `try_get_device_id`, `_find_address` and `_sender`;
- the mDNS part of Hue discovery, `_discover_mdns` in `engine/hue_setup.py`.

**It never relies on Home Assistant's mDNS.** `tests/test_own_mdns.py` checks the first two points.

- No python-zeroconf, no Home Assistant `zeroconf` or `network` integration, and not the zeroconf instance that Home
  Assistant shares.
- No `zeroconf` (or `homekit`) matcher in the manifest, and no `zeroconf` or `network` in its `dependencies` or
  `after_dependencies`.
- No Home Assistant shared HTTP session for anything that needs mDNS: that session's resolver answers `.local` names
  through Home Assistant's zeroconf.
  - The Hue client keeps its own connector and the system's resolver.
  - Cloud discovery may use the shared session, as it asks a public host name.
- No need for the copies that the Supervisor's multicast plugin relays: the socket hears the LAN on its own
  interfaces, and takes no address from a copy (decision 19).

**Socket**

- IPv4 UDP 5353 with SO_REUSEADDR and SO_REUSEPORT, multicast TTL 255 and loopback on.
- Bound to 224.0.0.251 on Linux and to 0.0.0.0 elsewhere (decision 19).
- It joins the group on each chosen address and skips a join that fails. It fails with "Network is down" when it joined
  none.
- Queries go out on every joined interface.

**Interfaces**, in index order:

- Up with IFF_UP and IFF_RUNNING, unless the ethtool link check reports no link for a hardware type that gets the
  check.
- Multicast with IFF_MULTICAST or IFF_ALLMULTI; loopback by its ARPHRD type, else IFF_LOOPBACK.
- IPv4 addresses come from ifaddr. An alias such as `eth0:1` counts for `eth0`; `127.` addresses and repeats are left
  out.
- Outside Linux, every interface with an address counts.
- This host's own addresses are all interfaces' IPv4 addresses at each (re)start.

**Network changes**

- On Linux: a netlink route socket on the IPv4 address group. Each RTM_NEWADDR or RTM_DELADDR counts, a datagram is
  read up to NLMSG_DONE or NLMSG_ERROR, and ENOBUFS counts as a change.
- Elsewhere, or when netlink can't be used, the addresses are polled every 5 s.
- A change is debounced 3 s, then restarts the socket ("Network configuration changed; rejoining the mDNS multicast
  group.").

**Listener**

- After a socket error it logs "mDNS socket error (…); retrying in 5s." and retries 5 s later.
- Started callbacks:
  - start eagerly, in order, and at once for a socket that is already open;
  - are cancelled with their socket;
  - removing one cancels it and waits for it.
- A failing callback or packet handler is logged, and nothing stops.
- A malformed packet is dropped at trace level (5).
- A packet from one of this host's addresses is marked as such (`MdnsPacket.from_this_host`).

**DNS parsing**

- A 12-byte header, then the questions, answers, authority and additional records.
- Compressed names with at most 32 pointers; names of more than 1024 characters are refused.
- A, AAAA, PTR, SRV and TXT are read; other types are kept as numbers.
- TXT strings:
  - each is split at its first `=`;
  - empty ones are skipped, and a key without `=` gets "";
  - the first occurrence of a key is kept, and keys compare without regard to case.
- Text is UTF-8 with replacement. The `DnsFormatError` messages stay the same: "DNS packet shorter than header.", "DNS
  packet truncated.", "TXT string overruns record.", "Too many DNS name compression pointers.", "Unsupported DNS label
  type." and "DNS name too long.".
- A query has id 0, one question, class IN and no unicast-response bit.

**eWeLink records**

- Responses only, from the answer and additional records: TXT records with TTL > 0 named
  `<instance>._ewelink._tcp.local`, whose instance starts with `ewelink`.
- The id:
  - TXT `id` unless it is blank, else the instance from character 8;
  - trimmed and lowercased.
- Then `seq`, `type`, `encrypt` (`true` without regard to case), `iv`, and `data1`..`data4` concatenated.
- The address:
  - the A record named by the SRV record of the same name;
  - else the sender, unless the sender is this host.
- A probe is a query whose authority records are named `eWeLink_<id>…`; each id counts once, in order.

**Hue discovery's mDNS**

- Its own listener for 5 s, with PTR `_hue._tcp.local` at 0, 1 and 3 s.
- It reads TXT records with TTL > 0 named `*._hue._tcp.local`, and takes `bridgeid` from the TXT record.
- The address:
  - the A record named by the SRV record of the same name, else the sender;
  - an answer from this host without an A record lists no bridge.
- The first answer for an address is kept.

**Its own string rules.** These differ from Python's for unusual input. They are small private helpers, documented as
protocol rules:

- **Names and TXT keys** compare by each character's simple uppercase form, where a non-ASCII character never becomes
  an ASCII one (`dns.case_key`).
  - 'ü' matches 'Ü'.
  - The Kelvin sign, the long s and the dotless i match no ASCII letter, and 'ß' matches only itself.
  - Against the fixed ASCII patterns (`ewelink`, `ewelink_`, `._ewelink._tcp.local`, `._hue._tcp.local`), plain ASCII
    case-insensitivity gives the same results, which was checked for every code point on 2026-11-25.
- **White space** is U+0009–U+000D, U+0020, U+0085, U+00A0, U+1680, U+2000–U+200A, U+2028, U+2029, U+202F, U+205F and
  U+3000 (`str.strip()` with that set). U+001C–U+001F aren't white space here, though Python's `isspace()` counts them.
- **A device id** is lowercased one character to one character: 'İ' stays, and there's no final-sigma rule.
- **`seq`** is a signed 64-bit decimal: an optional sign and ASCII digits, with optional ASCII white space around them,
  and trailing NULs ignored. Anything else gives no seq, such as `1_2`, Arabic-Indic digits or a leading no-break
  space.
- **A name's length** counts UTF-16 code units, dots included.

**Tests that pin it:**

- the 36 recorded packet cases (`tests/engine/mdns_packets.json`, replayed by `test_mdns_packets.py`);
- the per-code-point cases of its string rules (`tests/engine/mdns_text.json`, replayed by `test_mdns_text.py`);
- the tests of `test_dns.py`, `test_mdns.py` and `test_lan_monitor.py`;
- the record tests of `test_ewelink.py` and the discovery tests of `test_hue_setup.py`.

Their expectations don't change; only names, ids and docstrings may.

### The product rules

Outside the mDNS layer, Python's rules apply: `strip()` trims and a blank value is `not s or s.isspace()`; names and ids
compare with `casefold()` and sort by `str.casefold`; lengths are `len()`; a control character is one of category Cc;
JSON is `json` with plain values compared with `==`; base64 is `base64.b64decode(validate=True)`; numbers in text are
`int()`.

| Area | Rule |
|---|---|
| Queries | PTR `_ewelink._tcp.local` 0, 1 and 3 s after each (re)start of the socket (waits of 0, 1 and 2 s; a wait of 0 isn't awaited, so the first query goes out as the socket opens). Then the LAN monitor looks every 30 s (decision 36): it asks when 2 min have passed since its last query, and at each look while a configured switch that it heard before is late, last heard more than 2 min 30 s ago but within the 5 min of Online. Each query starts the 2 min again, so a look sends one query at most: 123 s, 243 s, … while every switch answers, and after a lost answer every 30 s until the switch is heard, or offline. The runner repeats it every 30 s while a mapped switch is unseen, warning "Not seen on the LAN yet: …" |
| Rejoin | A probe resets that device's baseline; the runner logs "… (re)joined the network." for a mapped switch |
| Payloads | The params are a JSON object in `data1`..`data4`: plain in DIY mode, else AES-128-CBC/PKCS7 with the key MD5(device key, stripped) and a 16-byte IV, both base64, decoded as UTF-8 with replacement. Without a device key an encrypted payload isn't read. Reading never raises; its errors: "encrypted payload has no iv", "iv is not valid base64", "iv has N bytes instead of 16", "payload is not valid base64", "decryption failed, check the deviceKey", "payload is not valid JSON" (", check the deviceKey" when encrypted) and "payload is not a JSON object" |
| Press rule | Per device, keyed by its casefolded id: Baseline (the first broadcast since a start, a reset or a rejoin), Duplicate (the same `seq`, or without one the same IV and data), SequenceReset (a lower `seq`, recorded as the new state), then, when both payloads were read, RelayChanged (a relay's `switch` changed without regard to case: `switches[]` by `outlet`, read with `int()`, else by place, and the top-level `switch`) and SettingsChanged (one of `sledOnline`, `swMode`, `swCtrlReverse`, `relaySeparation`, `configure`, `pulses`, `startup`, `pulse`, `pulseWidth`, `lock` changed, compared with `==`), else Press. Detached mode is `relaySeparation` 1. A summary writes the relays and `relaySeparation`, `swMode`, `triggerType`, `rssi` and `fwVersion` as `json.dumps` writes them |
| Targets | Names or ids, without regard to case or to white space around them (`targets.name_key`: `strip()` and `casefold()`), with an optional `room:`/`zone:` prefix in any case; an empty entry names nothing, and a resource without a name matches by its id. Exactly one match is required, and a room or zone without `grouped_light` is an error ("…: room '…' has no lights."). Duplicates are removed by (kind, id). The messages list the known names, distinct, quoted and sorted by `str.casefold`, or "(none)": "…: no Hue light named '…'. Known lights: …", "…: N lights are named '…'; use the light id instead (…).", "…: no Hue room or zone named '…'. Known: …" and "…: '…' matches …; prefix it with 'room:' or 'zone:'." With the remembered resources (decision 32), an entry that matches no resource, or several, resolves to the resource that it found before while the bridge has it, of the same kind and id; it is a rename when the entry no longer names that resource, which an entry that names it by its id always does, and the entry's new spelling is the picker's. An entry that resolves neither way is missing, with the message above. A target's label is `light '<name>'` or `group '<name>'`, followed by its id, the light's or the room's or zone's own, when another resource of its kind has the same name by `name_key` (another light, for a light; another room, or zone, for a room or zone), as the switch dialog lists them: `light 'Ceiling' (<id>)` (decision 32). The log lines, the status, the diagnostics and the bus events name targets by their labels |
| Toggle | If any target is on, all go off; otherwise all go on. It reads the light and/or grouped_light states, only the kinds its targets need, with one retry after 300 ms on a transient error. PUTs are sequential, targets already in the wanted state are skipped, and a target that the bridge doesn't switch counts as failed and is logged ("Could not switch … on: …"), with the status of the bridge's answer (`ToggleResult.failures`, `TargetFailure`): 404 means that the bridge no longer has that resource (decision 32) |
| Runner | Each switch's names are resolved on its own (decision 32): its binding has the targets that resolve and the resolver's message for each entry that resolves to nothing, which never stops another switch. The names are resolved again every 10 s while the bridge's errors are transient ("Hue bridge at … not usable yet (…); retrying in 10s.", on the supervisor's logger). A switch with no target left still reports its presses, and each toggle fails without a request, with "None of the lights, rooms and zones of this switch is on the Hue bridge." (`NO_TARGETS`), logged as "…: could not toggle the lights: …". Toggles of one switch are serialized, in press order. A press that waited more than 5 s since it was received is dropped ("…: dropped a press that waited more than 5s for the bridge."). Keep-alive `GET /clip/v2/resource/bridge` at once, then every 45 s. Reachability is reported on changes only ("Hue bridge unavailable: …", "Hue bridge reachable again."), and a rejected key (401/403) right after it. Stopping stops the queries, the keep-alive and every toggle before anything is awaited, and waits for them (decision 14). New targets and problems of the switches reach the running runner without a restart (`update_targets`, decision 32): each switch keeps its lock, its warnings and its tracker's baseline, a toggle that a press started before goes on with the targets that it had, and "<switch> -> <targets>" is logged again for a switch whose targets changed. A toggle in which the bridge answered 404 for some targets reports them right after its outcome (`on_targets_gone`). Its lines: "<switch> -> <targets>", "(none)" for no targets, "…: no deviceKey configured, …", "Listening for wall switch presses.", "…: online at … (seq …; …).", "…: wall switch operated (seq N).", "…: turned ON … in N ms (1 already on, 1 failed).", "…: could not toggle the lights: …", "…: ignored, relay changed (…).", "…: ignored, settings changed (…).", "…: ignored, device restarted (…).", "…: duplicate broadcast (seq N)." at trace level, "Ignoring unconfigured eWeLink device … at …." at debug level once per device, and once per change "…: cannot read the LAN payload (…); falling back to sequence numbers only." and "… is not in detached relay mode, so the wall switch cuts power to the lights. Enable 'Detach relay' in the eWeLink app." A switch is named "Name (device id)", or by its device id without a name, and a missing address or `seq` reads `None`, as in "Hall (1000abcdef): online at None (seq None; payload not decoded)." |
| HTTP | 5 s total and 4 s connect timeouts. A connector of its own, never Home Assistant's shared session, keeps idle connections 300 s and resolves names with the system's resolver (`aiohttp.ThreadedResolver`). Header `hue-application-key` (printable ASCII, else a `ConfigError`), aiohttp's default headers, and a JSON body. URLs are built with yarl; an id is one path segment, percent-encoded with its dots, so `..` stays an id. The pin is the SHA-256 of the leaf certificate's DER, as upper-case hex without colons or white space; a malformed pin matches no certificate. `matches_pin` compares a certificate's SHA-256 that `get_bridge_info` read with a pin by the same rule (decision 33). A 2xx with `errors[]` is a failure ("The Hue bridge reported errors for PUT /…: …"), 401/403 means the key was rejected ("The Hue bridge rejected the application key (…); reconfigure the sonoff2hue integration to pair the bridge again."), and another status is "Hue bridge returned 404 Not Found for GET /…: …". Transient means no answer, or 429, 500, 502, 503 or 504. A body is decoded by its charset with Python's codecs, as UTF-8 without one or for one that Python has no text codec for, invalid bytes replaced and a byte order mark dropped; a missing reason phrase is `HTTPStatus`'s. A CLIP v2 answer is read with `json`, a null counting as missing; a malformed one, such as a resource without an id, is the transient `HueApiError` "Unexpected response from the Hue bridge for '<type>'.". A light, room or zone without a name is named by its id. The switch rules, the runner and the switch test read the lights, rooms and zones together (`get_lights_and_groups`: `GET /clip/v2/resource/light`, `room` and `zone`, in that order), each room or zone with the lights it holds: the lights among its children, those that a device among them owns (each light's `owner`), and those of a room or zone among them, once even when groups refer to each other; a child of another kind holds none. Only these reads take owners and children, which have to be well-formed there; the toggle's and the keep-alive's reads ignore them (decision 34). `create_zone` sends `POST /clip/v2/resource/zone` with `{"children":[{"rid":"<light id>","rtype":"light"},…],"metadata":{"name":"…","archetype":"other"}}`, the lights in the order given; the answer's `data` names the new zone (`rtype` `zone`), which is then read with `GET /clip/v2/resource/zone/{id}`, up to 5 times 0.5 s apart until it lists its grouped light, and returned as the last reading has it, with the lights among its children. An answer that names no zone, or a reading without it, is the transient `HueApiError` "Unexpected response from the Hue bridge for 'zone'."; a refusal is the bridge's status and errors, as for any request. Without an answer: "Hue bridge not reachable: <reason> (<host>:<port>)" with the system's words (`os.strerror`), such as "Connection refused", and "Hue bridge request timed out."; a failed TLS handshake or another certificate is `CERTIFICATE_MISMATCH` |
| Supervisor | Its checks in order, each with its state, message and problems: not paired (`not_configured`, "Pair the Hue bridge to get started: reconfigure the sonoff2hue integration."), no switch (`not_configured`, "Add a switch: pick a MINIR4M and the Hue lights it should toggle."), `validate_for_run`'s problems (`invalid_config`, "The configuration has errors."), a client that can't be made (`invalid_config`, "The Hue settings in the config can't be used."), then `connecting` ("Connecting to the Hue bridge at …."). It pings the bridge every 10 s while its errors are transient, showing "Waiting for the Hue bridge at …: …" and warning once ("Hue bridge at … not reachable (…); retrying every 10 s."), then resolves the names. An entry that resolves to nothing is its switch's problem, warned about with the resolver's message, and doesn't stop the bridge (decision 32). The names resolve with the remembered resources, and each resolution is reported on the event loop's next turn (`on_resolved`): what each entry found and the renames. While the runner runs, the switches are resolved again every 60 s while one of them has a problem, else every 15 minutes, and at once after a toggle found targets gone; the new targets and problems go to the running runner and to the status, without a restart, each problem that the last resolution didn't have is warned about, and the resolution is reported as at the start, so that a rename found then is saved and restarts the bridge once. A bridge error while doing so changes nothing ("The lights, rooms and zones stay as they were: the Hue bridge didn't list them (…)." at debug level), and the keep-alive goes on reporting the bridge's reachability. A rejected key (`error`, "The Hue bridge no longer accepts sonoff2hue's application key. Reconfigure the sonoff2hue integration to pair the bridge again.") and any other refusal (`error`, its message) wait for the next change. Without a pin it warns "hue.certificateSha256 is not set, so the bridge certificate is not verified. Reconfigure the sonoff2hue integration to pair the bridge again and pin it." before running. Running: "1 wall switch toggles 2 Hue lights, rooms and zones.", or with what is missing, "2 wall switches toggle 5 Hue lights, rooms and zones; 1 is missing." ("…; 2 are missing."), whose problems are then every switch's, in order. An unexpected stop is logged with its traceback ("The bridge stopped unexpectedly; restarting it in 30 s.") and shown as "The bridge stopped unexpectedly: …", and the bridge starts again 30 s later. A change of the configuration logs "Configuration changed; restarting the bridge.", except in that wait, and restarts it once the run before it has ended (decision 23). While the bridge is unreachable, as the supervisor waits for it and as the runner reports it, the supervisor looks for it on the network with its `BridgeSearch` (decision 33), when its address is an IP address, not a host name, and the configuration has its bridge id. 60 s after the first failure it logs "The Hue bridge at … has been unreachable for 60 s; looking for it on the network." at info level, the waiting message ends "…; looking for it on the network." (a trailing period dropped), and the search asks the sources that are due: mDNS at most every 5 minutes and Signify's endpoint at most every 15, by the times that the search keeps, which every supervisor of the config entry shares, and in one search those due within a millisecond of each other, as the sums of their intervals on the loop's float clock can round apart. A search that finds nothing logs nothing. The bridge answering, also with a refusal, or the supervisor's stop ends the search, and cancels one under way. A bridge listed at another address than the bridge's own, by host and port, with the bridge's id or without one (`casefold()`), those with the id first, is read with `get_bridge_info`, without the application key, and its id compared again. With a pin, the first whose certificate is the pinned one (`matches_pin`) is reported once (`on_bridge_moved`, a `BridgeMoved`), on the event loop's next turn, and the search ends; one with another certificate is warned about, once per address ("A device with the Hue bridge's id … answers at …, but its certificate isn't the pinned one, so sonoff2hue doesn't use that address. If the bridge was replaced or reset, reconfigure the sonoff2hue integration to pair the bridge again."). Without a pin, the first goes to the status (`set_bridge_found`), warned about once ("The Hue bridge … answers at … instead of …, but its certificate isn't pinned, so sonoff2hue doesn't move there by itself; reconfigure the sonoff2hue integration to pair the bridge again there."), and the search goes on. A search that fails unexpectedly is logged with its traceback ("Looking for the Hue bridge on the network failed."), and the next comes at its rate. Stopping stops the runner and the search and closes the client; the listener goes on |
| Status | The service's state, message and problems, and "since", which moves only when one of them changes. Any state but running forgets the resolved targets and problems; running again publishes when a switch's targets or problems changed, and moves "since" only as above. The bridge's reachability, its message while unreachable and the time of each report, and, without a pin, where a bridge with its id answered while it was unreachable (`found_at`, decision 33), which a report that it is reachable forgets, all shown only while paired; a new configuration resets them only when the address, key or pin changes. Per switch, in the configuration's order: its LAN device (online for 5 min), the targets and its problems (the resolver's messages for its entries that resolve to nothing, decision 32), and the last action and error, kept across configuration changes; a toggle that some targets failed also records "N of M lights could not be switched.", and a dropped press "A press was dropped because it waited more than 5 seconds for the Hue bridge." Changes reach the listeners together, 500 ms after the first; a LAN device's only when it is a configured switch's |
| Pairing | `GET /api/0/config` gives the bridge's `bridgeid`, `name`, `modelid` and `swversion`, read with `json`, and the pin, from the certificate of the same connection. No answer is "No Hue bridge answered at …: <reason>", such as "Connection refused (192.0.2.10:443)", "The request timed out." or "HTTP 404 Not Found"; an answer without a `bridgeid` is "The device at … does not look like a Hue bridge." `POST /api` with `{"devicetype":"sonoff2hue#<host>","generateclientkey":true}`, where `<host>` is what `str.isalnum` keeps of the host name up to its first dot, at most 19 characters, else `host`: every 2 s for 60 s. A `success` with a `username` is the key; error 101 means keep waiting; another error is a refusal ("The Hue bridge refused pairing: … (error N).", "no description" without one); a timeout, a failed connection or an answer of another shape is tried again ("the request timed out", "bridge not reachable: …", "unexpected response (HTTP N): …"), each attempt reported. A changed certificate aborts ("The TLS certificate of … changed during pairing. Make sure this address belongs to your Hue bridge and pair it again."). After 60 s: "The link button was not pressed within 60 seconds. Try again." |
| Discovery | For 5 s, side by side: mDNS (The mDNS layer) and `https://discovery.meethue.com/` on Home Assistant's shared session, or one of them alone (`discover`'s `mdns` and `cloud`), as the supervisor's search asks them, each at its own rate (decision 33). The cloud's answer is read with `response.json(content_type=None)`: each entry whose `internalipaddress` `ipaddress` reads, written as it writes it, with its `id` if that is a text. A failure, or an answer that isn't a list of objects, is logged at debug level and adds no bridge. The bridges are listed by address, each as the first answer for that address gave it, with its source ("mDNS" or "discovery.meethue.com"), sorted by their addresses as text |
| Bridge address | Stripped, then an IPv4 address as `ipaddress` reads it; an IPv6 address, compressed, with a zone of letters, digits and `._~-`, in brackets when a port follows; or a host name whose labels, in the ASCII form that yarl's IDNA gives an internationalized name, hold 1-63 letters, digits and hyphens, neither first nor last a hyphen, at most 253 characters in all, the last label not all digits. A port is 1-65535 in ASCII digits, else 443. Anything else, such as a scheme, user info, a path, a query, a fragment, spaces, `192.168.1`, a name with `_` or a trailing dot, or `bridge:`, is refused with "'…' is not a valid Hue bridge address. Use a host name or IP address, optionally with a port, for example 192.168.1.20 or bridge.local:8443.", and an empty one with "The Hue bridge address is empty.": `hue_address.create_base_url` raises `ConfigError` with these messages. Messages about the bridge name it as `<host>:<port>`, a host name lowercased and an IPv6 address in brackets. `BridgeUrl.is_ip_address` tells an IPv4 or IPv6 address, which a bridge's new address may replace, from a host name, which it never does (decision 33) |
| Setup flow | The 5 s search; the address checked as above; the bridge read, with its messages; the bridge and the certificate to pin shown while the link button is awaited; the pairing's attempts ("The bridge did not answer (…); still trying." while it doesn't answer); its outcomes, messages and log lines ("Pairing with the Hue bridge … at …: waiting for its link button.", "Pairing with the Hue bridge … at … gave up: its link button wasn't pressed within 60 seconds. Start the pairing again in the sonoff2hue setup.", "Paired with the Hue bridge … at …; saved its credentials to the configuration."); *Retry* reads the bridge again at the same address. The bridge is saved with the switches untouched. An imported `sonoff2hue.yaml` gets the run's checks (`validate_for_run`) and its first connection to the bridge (the supervisor's client and keep-alive), with their messages (decision 22) |
| Switch picker | A light is saved by its name if no other light has that name, else by its id; a room or zone as `room:<name>` or `zone:<name>` if its name is unique among its kind, else by its id. Names compare by `targets.name_key`, the resolver's rule, so the picker and the resolver never disagree about an entry. Entries that still resolve to a selected resource keep their spelling and are saved once; entries that don't resolve stay unless removed. A selection keys resources by kind and id. The lists are ordered by the casefolded name, resources of one name in the bridge's order. Typed entries are split at commas (`split_list`) |
| Room or zone offer | For a switch that would be saved with 2 or more single lights: the distinct lights that its `lights` entries name, each entry naming exactly one, in the entries' order, against the lists that the rules' check read from the bridge (`SwitchWrite.lists`). The room or zone whose lights are exactly those, rooms first, each kind by name as the picker lists them, is offered, unless one of the switch's `groups` entries names a room or zone of exactly those lights, which leaves nothing to offer. Else a zone of them is: named after the switch, its stripped name or else its device id, cut to the bridge's 32 characters and stripped again, with " 2", " 3" and so on, the name cut shorter to fit, when a room or zone has that name or id already (`targets.name_key`). The room or zone, or the new zone, takes the place of the lights it holds: the `lights` entries that name one of them go, and the picker's entry for it, `room:<name>` or `zone:<name>`, or its id when another of its kind has its name, joins `groups` unless an entry names it already; the name, device and key stay as given (decision 34) |
| Switch form | Before the switch rules: a device chosen or typed (and stripped), of 4-40 ASCII letters and digits, not used by another switch (compared with `casefold()`); a name of at most 64 characters once stripped; at least one target; a new key when the current one is replaced. All its messages at once, in that order. It gives the rules the name or None, the key None to keep it, "" to remove it, else stripped, and typed entries split at commas. The rules' errors are joined per field (`field_errors`), the picker's field taking those of `targets`, `lights` and `groups`, the typed entries a field each |
| Switch validation | After `strip()`: a name of at most 64 characters, without control characters; a device id of 4-40 ASCII letters and digits, after `lower()`; a key of at most 128 characters, without white space (`isspace()`) or control characters. Each list holds at most 50 entries, counted before duplicates are removed by casefold, each of 1-200 characters without control characters. At least one target. The messages, in this order, under the keys `name`, `deviceId`, `deviceKey`, `lights`, `groups` and `targets`: "Use at most 64 characters.", "The name contains control characters.", "Choose a device or enter its device id.", "A device id has 4 to 40 letters and digits, e.g. 1000abcdef.", "A device key has at most 128 characters and no spaces.", "Use at most 50 entries.", "Each entry needs 1 to 200 characters." and "Choose at least one light, room or zone.". A new switch's device id must be free (`SwitchConflictError`, "Device … is already configured. Edit that switch instead."), and a changed switch must exist (`SwitchNotFoundError`, "No switch with device id … is configured.") and may not take another's id (`SwitchConflictError`, "Device … is already configured by another switch."); both are checked before and again after the bridge is asked. The entries are checked against the bridge when it is paired and reachable, and only warned about otherwise ("The Hue bridge is not paired yet, so the light names were not checked.", "The Hue bridge could not be reached, so the light names were not checked (…).", "The Hue settings in the config can't be used, so the light names were not checked (…)."). A key of None keeps the current one, "" removes it, and a changed device id drops a kept key |
| Switch dialogs | A step at a time (decision 24): identify-by-press, where a device event is a press when its last event is a press at a time the device didn't have, with `switch_form.identify`'s messages ("Selected …: it reported a press.", or the switch that uses it); the device list in its order (unused first, the latest press first, then by id), each device with its id, address, type, *Online* or *Offline*, its last press as a relative time ("just now", "12 s ago", "3 min ago", "2 h ago", "yesterday 21:04" or a date) and the switch that uses it; the device id checked by the form; the picker's lights with their state, rooms and zones with "no lights", the id beside a repeated name, and entries that don't match the bridge kept until removed; typed entries split at commas; the key kept, replaced or removed, with the warning; the form's checks, then the rules', their messages on their fields; then, for a switch of 2 or more single lights that the bridge's lists named, picked or typed, the offer (decision 34): *Use room "…"* or *Use zone "…"* (`group_offer`), or *Create zone "…" on the bridge* (`zone_offer`), and *Keep the lights*, each of which the rules check again before saving, without another offer; a zone that the bridge doesn't create shows the bridge's message (`zone_failed`), with *Try again*, which checks the switch and offers again, and *Keep the lights*; "Added …" and "Saved …" |
| YAML import | `yaml_import.parse(text)` reads the text with PyYAML, `CSafeLoader` when PyYAML has libyaml, else `SafeLoader`, with null (empty, `~`, `null`, `Null`, `NULL`) as the only implicit value, so `yes`, `0x10`, `010`, `1e3` and dates stay the text written. It refuses, in this order: what PyYAML can't read, with its problem, such as a character that YAML refuses ("unacceptable character #x0007: …"); a second document ("only one document is allowed, but another one starts here."); a tag other than `!!str` ("the tag !!int isn't supported here. Write the value without the tag."); nesting deeper than 32 levels ("the text nests more than 32 levels deep here."); a key that isn't a single value or that its mapping repeats ("duplicate key '…'."); what doesn't fit the probatio schema, top-level `hue` and `switches`, `hue` with `bridgeIp`, `bridgeId`, `appKey` and `certificateSha256`, a switch with `name`, `deviceId`, `deviceKey`, `lights` and `groups` ("unknown key '…' under 'hue'. Valid keys are bridgeIp, bridgeId, appKey, certificateSha256 (case-sensitive).", "expected 'key: value' lines here.", "expected a list here: '- item' lines, or [a, b].", "expected a single value here, not a list or 'key: value' lines.", and for a null entry of `lights` or `groups`, at the entry's value or right after a lone `-`, "an entry of 'lights' is empty. Write the light's name or id, or remove the '-'." or "an entry of 'groups' is empty. Write the room's or zone's name or id, or remove the '-'."). The first problem in the text is reported as "Config, line L, column C: …", 1-based, columns in characters, such as "Config, line 4, column 9: an entry of 'lights' is empty. Write the light's name or id, or remove the '-'.". Null sections, lists and values become empty, and an empty `switches` entry is refused, without a line and column ("Config: switches[N] is empty. Remove the stray '-' or fill in the entry."). `SwitchSettings.lights` and `groups` are `Sequence[str]`. The setup flow then runs `validate_for_run`: at least one switch, each with a `deviceId` that no other has, at least one entry in `lights` or `groups` and no blank one ("empty light/group name.", for a text such as `' '`); "Invalid configuration:" with one "  - …" line per problem, such as "switches[1] ('Hall'): deviceId is missing." |
| LAN registry | Per device: address, type, encrypted, seq, first and last seen, last event and its time, last press, detached, summary, decode error. A broadcast without an address, type or seq keeps the earlier one, and one that can't be decoded keeps the summary and detached mode; the decode error is always the last broadcast's. A Duplicate only moves the time last seen. Every broadcast but a Duplicate is reported, and a device's first. Devices are keyed and ordered by their casefolded ids. Online means heard within the last 5 min. The time of the last packet counts any packet |
| LAN monitor | Runs as long as the config entry, also before a switch is mapped, with a tracker of its own; a configuration change gives it a new one, for new device keys, and the switches that can be late (Queries). The registry is listening while the listener runs, also while its socket retries. An unexpected stop of the listener is logged at error level as "The LAN monitor stopped; restarting it in 10 s.", and it starts again 10 s later |
| Activity | The last 1,000 log entries of the integration's loggers, at the levels that they let through: id, time (UTC), level (`trace` below debug, `debug`, `information`, `warning`, `error`, `critical`), category (the logger's name after its last dot), message, and the exception as "Type: message", with its Python class name, without the traceback, which stays in Home Assistant's log. `get(after, limit)` gives the newest `limit` entries after `after`, oldest first; a limit of 0 gives none |
| Runtime | What lives as long as the config entry: the LAN monitor with its listener, the device registry, the status tracker and the activity buffer. A change of the configuration restarts only the bridge supervisor: the running supervisor logs "Configuration changed; restarting the bridge." and stops, the LAN monitor takes a new tracker and the status the new configuration, and the next run starts once the last one has ended. A change of a title alone restarts nothing (decision 23). The remembered resources (decision 32) are the entry's store, `sonoff2hue.<entry id>`, version 1, written atomically and tagged with the bridge id (compared without regard to case): loaded before the runtime starts, without another bridge's and those of switches and entries that the configuration doesn't have, and "Can't read the remembered lights, rooms and zones (…); starting without them." when the store can't be read; each resolution's taken; forgotten for a switch or entry that a change removes, and all of them when the entry is paired with another bridge; written 10 s after a change, at once when the entry unloads, and at Home Assistant's final write; deleted with the entry. A rename is saved in its switch's subentry (`async_update_subentry`), the entry replaced by the picker's spelling, or dropped when another entry already is that, only while the subentry still has the entry and the resolution's configuration is the current one. The renames of one resolution are saved together, so the configuration changes once and the supervisor restarts once, and each is logged: "…: light 'Ceiling' was renamed to 'Ceiling lamp' on the Hue bridge; saved the new name." The search for the bridge (decision 33) is the entry's, made with the runtime and given to every supervisor (`new_bridge_search`): the engine's discovery for 5 s, with Home Assistant's shared session for Signify's endpoint, as the setup flow searches, and `get_bridge_info` for each bridge that it lists. A bridge that the supervisor found at another address with its pin is saved as the entry's `bridge_ip` (`async_update_entry`), only while the report's configuration is the current one, and logged at warning level ("The Hue bridge … moved from … to …; saved its new address."); the configuration then changes once, which restarts the supervisor once, and the remembered resources stay, as the bridge id is the same |
| Logging | Each line on its module's logger (`custom_components.sonoff2hue.<module>`), whose name after the last dot is the category, at its fixed level; trace at level 5. The activity buffer takes every record of the integration's loggers that their levels let through, and the levels are Home Assistant's |
| Switch test | The switch is found by its id, stripped, without regard to case, else "No switch with device id … is configured."; the bridge must be paired, else "Pair the Hue bridge first: reconfigure the sonoff2hue integration."; a client of its own for the settings as they are, whose `ConfigError` is the answer, closed afterwards; the targets resolved as the runner resolves them (decision 32), with the remembered resources, from the bridge's lights and groups at that moment: an entry that resolves to nothing is skipped, and when none resolves, the test fails with their problems joined with spaces; the toggle with `HueToggler` on the `sonoff2hue` logger, the line "…: test from Home Assistant turned ON ….", or with what it skipped "…: test from Home Assistant turned ON …; 1 is missing: <the problem without the switch's name>" (problems joined with "; "), and the result: turned on, changed, unchanged, failed, the milliseconds, the targets and the problems; a bridge error's message. The test reaches neither the status nor the runner's reports. *Test toggle* raises the message as Home Assistant's translated error `test_toggle_failed`, whose text is the message (decision 36) |
| Fake Hue bridge | Three lights, each owned by a device of its own, a room whose children are the devices of two of them and a dimmer switch, which owns no light, and a zone whose children are the three lights, with fixed ids, the Hue API calls that sonoff2hue makes (`GET /api/0/config`, `POST /api`, `GET /clip/v2/resource/{light,room,zone,grouped_light,bridge}`, also by id (`…/{type}/{id}`), `PUT /clip/v2/resource/{light,grouped_light}/{id}`, and `POST /clip/v2/resource/zone`, which creates a zone of its lights with a grouped light, the ids `FakeIds.group(3)` and `FakeIds.grouped_light(3)` on, and refuses with 400 what a bridge would: a body that isn't JSON, no metadata, a name that isn't 1 to 32 characters, no archetype of a room or zone, children that aren't a list of its lights), the 30 s link-button window, and plain-text test endpoints under `/_fake/` (`link-button`, `lights`, `puts`, `posts`, which records each POST of a zone as `puts` records PUTs, `requests`, `reset`, which also removes the zones that POSTs created, and `rename/{id}?name=…` and `delete/{id}` (decision 32), which change a light, room or zone as the Hue app does, until the next start: a renamed one keeps its id, its owner and its children, and a room's or zone's new name has at most 32 characters; a deleted light goes with its device, out of the rooms' and zones' children, and a deleted room or zone with its grouped light, so that their PUTs and GETs get 404; a zone created after a deletion still gets a number of its own, and `reset` keeps the renames and deletions of the bridge's own lights, room and zone), over HTTPS with a certificate made at every start, or one given to `start` (`certificate`), so that two fake bridges present one certificate and the bridge id, as a bridge does at its old address and its new one (decision 33). It reads bodies with `json` and its settings with `ipaddress` and `int()`. Tests add `Fault`s: a status, body or delay for matching requests (decision 12) |

### Design choices for Home Assistant

- **One mDNS socket per config entry.** One UDP 5353 socket serves both the LAN monitor and the runner (decision 8).
  Each keeps its own tracker, reset with each (re)start of the socket, so their baselines and press decisions are
  independent.
- **The socket bound to the group on Linux** (decision 19). It gets every multicast packet and sends its queries from
  its interfaces' addresses, but no unicast, so it never takes a unicast reply meant for Home Assistant's zeroconf,
  which shares the port. macOS can't send from such a socket, so there it is bound to 0.0.0.0.
- **No address from a relay's copy** (decision 19). A broadcast without SRV and A records gives its sender as the
  device's address only when the sender isn't one of this host's own IPv4 addresses; otherwise the LAN registry keeps
  the address it knew. On Home Assistant OS, the Supervisor's multicast plugin repeats every broadcast from its
  `hassio` bridge, and the update after a press has no address records. Hue bridge discovery lists no bridge for an
  answer without an A record from this host.
- **One bridge.** A single config entry (`single_config_entry`) for the one Hue bridge, with the switches as its
  subentries. Reconfigure pairs it again or pairs another bridge, and deleting the entry deletes its switches.
- **Home Assistant's log levels.** Home Assistant's default level, warning, hides the info lines. The activity buffer,
  which diagnostics show, records the integration's lines at the levels that Home Assistant's `logger` sets: warnings by
  default, info and debug lines once debug logging is on for the integration or `logger:` asks for them. The runtime
  never changes a log level (decision 23).
- **The logbook.** It has Home Assistant's own entries for each press, the switch's *Press* and the change of its
  *Last action*, with the details in their attributes; a press whose outcome is the one before shows as *Press* alone.
  The runner's lines are in Home Assistant's log at its levels and in the diagnostics' activity (decision 26).
- **Messages that say how to pair the bridge** name the integration's reconfigure flow (`PAIR_AGAIN`), and the setup
  flow's import says "pair the bridge again", as the integration doesn't exist yet (decisions 21 and 22).
- **The switch dialogs** (decision 24): identify-by-press waits 60 seconds and starts only when chosen; a device of
  another switch can be chosen from the device list and is then refused with the form's message, as Home Assistant's
  options can't be disabled; the list's times don't refresh; an edited switch keeps its device with *Keep device …*;
  when the bridge can't list its lights, a menu comes before typing them; the rules' warnings are confirmed before
  saving (*Save without checking?*); a conflict or a deleted switch ends the dialog; and an unexpected failure shows
  its own message.
- **Repairs and reauthentication** (decision 23): reauthentication starts when the bridge rejects the key, and repair
  issues show what the status reports as problems: the configuration's problems, a switch that isn't detached or whose
  payload can't be read, a switch whose lights, rooms or zones are missing (decision 32), a certificate that isn't
  pinned or has changed, and a bridge without a pin that answers at another address (decision 33). The last three lead
  to the reconfigure flow. The bus events `sonoff2hue_press` and `sonoff2hue_toggle` carry each press and its outcome
  for automations.
- **The entities** (decision 25) show the status and the LAN registry. Status's states have short names, and Reachable
  is unknown both before the bridge is first checked and while it isn't paired. Last action shows the newer of the
  switch's last action and its last error. Press fires for the presses that the bridge acts on, and keeps its last one
  across a restart; Online's `last_press_at` counts every press that the LAN heard. The entities follow the status, the
  LAN registry and timers of their own.

## Home Assistant's UI

Where each part of the integration lives in Home Assistant's own UI (the user's choices of 2026-11-23 and 2026-11-24).

| What | Where |
|---|---|
| Setting up | Settings → Devices & services → *Add integration* → sonoff2hue: the setup flow (`config_flow.py`, decision 22), which finds the bridge or takes its address and pairs it, or imports a `sonoff2hue.yaml`. The entry's *Reconfigure* pairs again or pairs another bridge, with the switches kept; reauthentication, when the bridge rejects the key, pairs the same bridge again |
| The switches | *Add switch* on the integration's page and *Edit switch* on each switch: the switch subentry flow (`switch_flow.py`, decision 24), which finds the MINIR4M by a press of its wall switch, among the devices seen on the LAN or by its id, then takes the name, the bridge's lights, rooms and zones and the device key, checked and saved by the switch rules. The integration's page lists the switches, and Home Assistant's own UI deletes one |
| The integration's state | `sensor` **Status** on the bridge's device (`sensor.hue_bridge_status`): an enum of `not_configured`, `invalid_config`, `connecting`, `running` and `error`, with `message`, `problems` and `since` (decision 25); the problems are also repairs (decision 23) |
| The bridge | The bridge's device, "Hue bridge", identified by the bridge id; its `binary_sensor` **Reachable** (`binary_sensor.hue_bridge_reachable`, connectivity, diagnostic), unknown until it is checked and while the bridge isn't paired, with `address`, `message` and `checked_at` (decision 25) |
| The LAN | `sensor` **eWeLink devices seen** (`sensor.hue_bridge_ewelink_devices_seen`, diagnostic, in devices), with `listening` and `last_packet_at` (decision 25) |
| Each switch | A device per `switch` subentry, named by its title, "SONOFF" "MINIR4M", with the device id as its serial number: `event` **Press** (button, event type `press`, `seq`), `sensor` **Last action** (`turned_on`, `turned_off`, `failed` or `dropped`; `at`, `changed`, `unchanged`, `failed`, `elapsed_ms`, `targets`, `message`), `binary_sensor` **Online** (connectivity, diagnostic: heard within 5 minutes; `address`, `last_seen_at`, `last_press_at`, `seq`, `encrypted`, `has_device_key`, `detached`, `decode_error`) and `button` **Test toggle** (config) (decision 25); its warnings are also repairs (decision 23) |
| Problems | Repairs (decision 23): *The configuration has problems*, *… is not in detached relay mode*, *Can't decode the broadcasts of …*, *Some lights, rooms or zones of … are missing* (decision 32), *The Hue bridge's certificate is not pinned*, *The Hue bridge's certificate changed* and *The Hue bridge answers at another address* (decision 33); the last three lead to *Reconfigure*. Home Assistant's own *Authentication expired for sonoff2hue* comes with reauthentication |
| What happened | The logbook (Activity), also on the page of the switch's device, with Home Assistant's own entries for each press: the switch's **Press** ("Press") and the change of its **Last action** ("Turned on", "Turned off", "Failed" or "Dropped"), with the details in their attributes (decision 26). Home Assistant's log, with the integration's lines: warnings by default, and info and debug lines once debug logging is on for the integration. The diagnostics (`diagnostics.py`), with the activity's last 1,000 entries at those levels next to the configuration, the status and the LAN's devices (decision 26) |
| Automations | The trigger *Event received* on a switch's **Press**, and the bus events `sonoff2hue_press` and `sonoff2hue_toggle` (decision 23) |

## Research findings

Home Assistant 2026.11, checked on 2026-11-23 and 2026-11-24 in the developer documentation
(contributors.home-assistant.io), on PyPI and in the installed `homeassistant` 2026.11.3:

- `homeassistant` 2026.11.x requires Python >= 3.14.2.
- **Marketplace.** Home Assistant 2026.11 has a built-in Marketplace (Settings → Marketplace). It grew out of HACS
  and uses the same catalog and `hacs.json`, and 2026.11 blocks the HACS custom integration itself.
  - It installs only from public GitHub repositories with a `hacs.json` and a README in the root, and one integration
    in `custom_components/<domain>/`.
  - `homeassistant` in `hacs.json` is the minimum version, compared with AwesomeVersion. It is checked only for
    repositories with releases.
  - The manifest needs `version`. HACS also requires `documentation`, `issue_tracker`, `codeowners` and `name`, and
    checks `categories`: one to three, known ones. A missing `categories` only warns.
  - Brand images go in `custom_components/<domain>/brand/` (since 2026.3): `icon.png` at 256×256 and `icon@2x.png` at
    512×512, trimmed, with a transparent background. Home Assistant's own branding must not be used.
  - Custom integrations ship `translations/en.json` and no `strings.json`.
  - The catalog requires both the HACS action (`hacs/action`, category `integration`, nothing ignored) and hassfest
    (`home-assistant/actions/hassfest`).
  - The Marketplace refuses an invalid domain, a domain that another installed repository owns, an unloadable
    manifest, or more than 100 MiB or 50,000 files. A domain of a core integration needs the user's confirmation;
    `sonoff2hue` isn't one.
  - From Home Assistant 2026.12, the Marketplace leaves out a README's parts between
    `<!-- ha-marketplace:exclude-start -->` and the next `<!-- ha-marketplace:exclude-end -->`, the spaces inside them
    optional, or up to the end without one; markers in a code block or inline code are text. Its frontend does it
    before it renders the README (`withoutExcludedSections` in `src/panels/marketplace/tools/markdown.ts`,
    home-assistant/frontend#55571), first in 20261125.0, which 2026.12.0b0 pins. 2026.11's 20261028.8 shows the whole
    README, its sanitizer dropping the comments, and so does GitHub. Checked on 2026-11-25 in the frontend's source and
    the guide *Repository structure* on contributors.home-assistant.io.
- **Releases** (decision 29; checked on 2026-11-25 in the installed 2026.11.3's `components/marketplace`, and in the
  Marketplace guides *Releases and updates*, *Publish a custom integration* and *Get into the catalog* on
  contributors.home-assistant.io):
  - A version is the tag of a published GitHub Release; a tag alone isn't one. Without Releases, the Marketplace
    installs the default branch's newest commit, shown as its hash, and doesn't check `homeassistant` in `hacs.json`.
    The guides ask for the manifest's `version` to be the tag's version. After an install, the Marketplace refuses a
    manifest with another domain or without a version the loader accepts (CalVer, SemVer, SimpleVer, BuildVer or
    PEP 440).
  - The version on offer is the first Release in GitHub's list of releases, newest first, that is neither a draft nor
    a pre-release; it reads 30. That is neither the Release marked Latest nor the highest version. A pre-release
    reaches only users who turn on the repository's Pre-release switch, and only while it's newer than the latest
    regular release. A tag has to be a usable ref: no segment starting with a dot, and no `?`, `#`, `%`, `\` or white
    space.
  - Without `zip_release`, an integration is installed from its tag's source archive,
    `https://github.com/<owner>/<repository>/archive/refs/tags/<tag>.zip`: the Marketplace extracts
    `custom_components/<domain>/`, up to 100 MiB and 50,000 files, or else downloads the files one by one from the
    tag's tree. It reads `hacs.json` and the README at the tag too. Files marked `export-ignore` in `.gitattributes`
    aren't in the archive. With `zip_release` and a `filename` ending in `.zip`, it downloads that asset of the Release
    instead, which the guide recommends only when the release workflow builds files. This integration builds
    nothing, so its Releases have no assets and `hacs.json` sets neither key.
  - The update entity shows the bodies of the Releases, the notes, from the installed version up to the one on offer,
    each headed by its tag and the Release's name if that differs. Users can install an older version too, picked
    from the Releases, so they are kept. When a version's Release or tag is gone, the Marketplace falls back to the
    version it would install by default.
  - The catalog, `hacs/default`, takes a repository only with at least one full Release, published after hassfest and
    the HACS action pass. The catalog's data is made every 4 hours and the Marketplace reads it every 6 hours; a
    repository added from a link asks GitHub itself every 48 hours, and only with a GitHub connection. The My link
    `marketplace_repository` (new in 2026.11; `hacs_repository` reaches older versions too) opens a repository in the
    user's Marketplace and offers to add it.
  - HACS's own documentation says the same: "Just publishing tags is not enough, you need to publish releases", and
    `zip_release` is for integrations only.
- **Testing.** `pytest-homeassistant-custom-component` stopped at 0.13.375, for Home Assistant 2026.10.4, and its
  README says it's broken. Home Assistant now publishes `pytest-homeassistant` from `packages/pytest-homeassistant`
  in home-assistant/core.
  - Its version is the Home Assistant version: 2026.11.3 pins `homeassistant==2026.11.3`, pytest 9.0.3,
    pytest-asyncio 1.4.0 and pytest-socket 0.8.1.
  - It needs `asyncio_mode = "auto"`, `-p no:respx` and `pythonpath = ["."]`.
  - The `hass` fixture enables custom integrations, and helpers such as `MockConfigEntry` come from
    `pytest_homeassistant.common`.
- **asyncio servers in a test's event loop** (2026-11-26; in Python 3.14.10's `asyncio`, on CPython's `3.14` and `main`
  branches, and in pytest-homeassistant 2026.11.3). pytest-homeassistant runs every test's loop in debug mode
  (`enable_event_loop_debug`, autouse), and its `hass` fixture fails a test, at its teardown, for each report to the
  loop's exception handler meanwhile. In debug mode, and in 3.14 only then, a selector loop reports an incoming
  connection whose transport couldn't be made, or whose TLS handshake failed: "Error on transport creation for incoming
  connection". So a server in the test's own loop, such as the fake Hue bridge, can fail a test in two ways.
  - A connection is accepted in one turn of the loop and gets its transport in the next. If `Server.close()` runs in
    between, `Server._attach` fails its assertion (`AssertionError`), the accepted socket stays open, and the half-made
    transport raises `TypeError` when it is collected (gh-109564). The fix, on the `3.14` branch since 2026-10-29,
    closes such a connection instead; 3.14.10 and CI's 3.14.4 lack it. Removing the listening sockets' readers
    (`loop.remove_reader`) and letting the loop turn once before `close()` leaves no connection between the two.
  - A client that leaves while the server's side of its TLS handshake runs, as one cancelled while it connects does,
    fails the handshake with `ConnectionResetError`. When the server closes its side first (`Server.abort_clients()`,
    new in Python 3.13), the handshake ends quietly.
  - On CPython's `main` branch, a transport that couldn't be made is also reported without debug mode, and its socket
    closed (gh-155934).
- **Config subentries.**
  - `ConfigFlow.async_get_supported_subentry_types` maps each type to a `ConfigSubentryFlow`. A subentry flow starts
    only with `user` or `reconfigure`, with no discovery and no reauthentication.
  - `self._get_entry()` and `self._get_reconfigure_subentry()` give the entry and the subentry.
  - Adding, updating or removing a subentry fires the entry's update listeners (`_async_save_and_notify`). With an
    update listener on the entry, use `async_update_and_abort` rather than `async_update_reload_and_abort` (The runtime,
    below).
  - Entities pass `config_subentry_id`. Texts go under `config_subentries.<type>`, with `entry_type` and
    `initiate_flow`.
- **Libraries.** `homeassistant` 2026.11.3 requires aiohttp 3.14.5, cryptography 50.0.2, PyYAML 6.0.3, ifaddr 0.2.0 and
  zeroconf 0.152.0 itself, so the manifest needs no `requirements`. voluptuous 0.16.0 is installed with it.
- **Dependencies and schemas** (the skeleton, decision 16; checked on 2026-11-24 in the installed 2026.11.3):
  - `ifaddr` and `yarl` are requirements of the `homeassistant` package too, so the engine needs no `network`
    dependency; core integrations list `network` because they call its API (`async_get_adapters`). Home Assistant sets
    up `network` and `zeroconf` in stage 0 of its start, before any config entry, so `after_dependencies` couldn't
    change the order either. python-zeroconf binds UDP 5353 with SO_REUSEADDR and SO_REUSEPORT, as the engine's
    socket does.
  - Home Assistant 2026.11 itself imports no voluptuous: its schemas are `probatio` 0.13.0, a requirement of
    `homeassistant` with voluptuous's API, and flows serialize `data_schema` with `probatio.to_field_list`.
    voluptuous 0.16.0 comes only with annotatedyaml and hass-nabucasa.
  - `ConfigEntry[_DataT]` is generic, and `runtime_data` has its type.
  - With `single_config_entry`, Home Assistant aborts a flow that starts while the entry exists, or that finishes after
    another one added it, with `single_instance_allowed` in its own translation domain (`homeassistant`), so the
    integration needs no string for it. A flow that adds the entry also aborts the others in progress.
  - pytest-homeassistant 2026.11.3 has no check that flows' strings exist; core's `check_translations` fixture isn't
    part of it.
- **hassfest** (2026.11.3; read in core's `script/hassfest` and `packages/hassfest` and run from a checkout of that tag
  on 2026-11-24):
  - `home-assistant/actions/hassfest@master` (its `2026.11.0` tag is the same) runs the image
    `ghcr.io/home-assistant/hassfest`, which core builds for every beta and stable release; `latest` was 2026.11.3. It
    validates each `custom_components/*/manifest.json` with `--action validate`. Warnings don't fail it.
  - The manifest of a custom integration: `domain`, `name`, `codeowners`, `iot_class`, `documentation` over https and
    not on home-assistant.io, and `version` (an error without it), with the keys sorted as domain, name, then
    alphabetical. `categories` (one to three, the primary first, such as `lighting` or `lighting.lighting_systems`) and
    `connectivity` are optional; values that `homeassistant/strings.json` doesn't define only warn. A requirement that
    Home Assistant itself depends on is an error: "Requirement … is a dependency of Home Assistant itself and must not
    be listed in the manifest of a custom integration."
  - `translations/en.json` is checked against the strings schema: no URLs, HTML or leading and trailing spaces.
    `config_flow: true` needs `config_flow.py`, `async_setup` needs a `CONFIG_SCHEMA` (a warning for custom
    integrations), registered services need `services.yaml`, and an import of `homeassistant.components.<x>` needs
    `<x>` in `dependencies` or `after_dependencies`. The quality scale and `integration_type` checks are for core
    only. Brand images aren't checked.
- **Config flows** (the setup flow, decision 22; checked on 2026-11-24 in the installed 2026.11.3 and in the frontend
  it requires, `home-assistant-frontend` 20261028.8):
  - A progress step is `async_show_progress(step_id, progress_action, description_placeholders, progress_task)`. When
    its task ends, the flow manager runs the step again, and the step returns `async_show_progress_done(next_step_id)`;
    the public `async_configure`, which the frontend's fetch calls, goes on to that step at once. The text is
    `config.progress.<progress_action>` with the placeholders, the header `config.step.<step_id>.title`.
  - A progress step can change its text. `async_notify_flow_changed()` fires `data_entry_flow_progressed`, and the
    frontend fetches the flow, which runs the current step again without input; the manager fires it too when a
    progress step comes back with another `progress_action` or other placeholders. `async_update_progress(0…1)` fires
    `data_entry_flow_progress_update`, which the frontend shows as a percentage ring until the next progressed event.
  - `hass.async_create_task` starts a task eagerly unless `eager_start=False`, so a task can run, or even end, inside
    the step that creates it.
  - A flow is removed when its dialog is closed, when it's aborted, and when another flow adds the single entry. The
    manager then cancels the current progress task and calls `async_remove`. A step that is still awaiting when its
    flow is removed has its result dropped (`UnknownFlow`), and a task that it starts isn't cancelled.
    `hass.async_block_till_done()` doesn't wait for tasks that are being cancelled.
  - `async_set_unique_id` aborts with `already_in_progress` (in the `homeassistant` translation domain) when another
    flow that isn't a reauth has that unique id. `_abort_if_unique_id_mismatch(reason=…, description_placeholders=…)`
    is for reauth and reconfigure. `async_update_reload_and_abort` and `async_update_and_abort` take `unique_id`,
    `title`, and `data` or `data_updates` (merged), and abort with `reauth_successful` or `reconfigure_successful` in
    the `homeassistant` domain. With an update listener on the entry, `async_update_reload_and_abort` still schedules
    the reload and, for a custom integration, logs a report that this breaks in 2026.12.0.
  - Reauth: `entry.async_start_reauth` runs the flow's first step, `async_step_reauth(entry_data)`, at once, with the
    entry's unique id in the flow's context; the notification then waits for the user. Its forms get the entry's title
    as the `name` placeholder. The single-entry check doesn't apply to reauth and reconfigure flows, and an entry
    supports reconfigure when the flow has `async_step_reconfigure`.
  - Texts: menu options are `config.step.<id>.menu_options.<option>`, a new entry's text
    `config.create_entry.<description>` (else `default`), an abort's `config.abort.<reason>` in the result's translation
    domain. A select selector's options take `selector.<translation_key>.options.<value>` where it exists, else their
    label. hassfest also rejects a placeholder in single quotes (`'{x}'`) and a `data_description` key that isn't in
    `data`. A form, menu or progress step shown without a `step_id` is reported (it breaks in 2027.5.0).
  - Subentries: `ConfigEntry` builds them from `subentries_data` (`ConfigSubentryData`: `data`, `subentry_type`,
    `title`, `unique_id`), as `async_create_entry(subentries=…)` passes them, without checking the type against
    `async_get_supported_subentry_types` or their unique ids against each other, which adding one later does
    (`already_configured`). The declared types give the frontend its add buttons
    (`config_subentries.<type>.initiate_flow.user`) and reconfigure (`supports_reconfigure`, from the subentry flow's
    `async_step_reconfigure`); it lists every subentry, named by `config_subentries.<type>.entry_type`. A subentry flow
    of a type that isn't declared fails with `UnknownHandler`. So the import can create switch subentries before the
    subentry flow exists, but switches can be added or reconfigured only once `switch` is declared.
  - The flow API answers a new entry with its result without `data` and `context`, but with everything else, its
    `subentries` with their data included. `async_on_create_entry(result)` runs once the entry exists, built from
    those subentries, and what it returns is the answer, so it can drop them. A reconfigure or reauth flow ends in an
    abort, whose answer holds no data.
  - A form's errors are text in an `ha-alert`, where line breaks collapse into spaces; descriptions are markdown with
    `breaks`. A text selector with `multiline` is a text area. After an error, a form's fields are empty unless the
    flow suggests values.
- **Brand images** (2026-11-24: the developer documentation, the brands repository and the installed `brands`
  integration):
  - Home Assistant serves the `brand/` folder of an installed custom integration at
    `/api/brands/integration/<domain>/<image>`, before the brands CDN; a missing `icon@2x.png` falls back to
    `icon.png`. For integrations not installed yet, the Marketplace takes `custom_components/<domain>/brand/` from
    their repository, up to 2 MiB an image. The brands repository closes new custom integrations and is moving the
    existing ones out (its `CUSTOM_INTEGRATIONS_CLEANUP.md`).
  - Its image specification: PNG, compressed (lossless preferred), interlaced and transparent preferred, trimmed (no
    empty border), and icons square at 256×256 and 512×512.
- **The HACS action** (read in hacs/action and hacs/integration on 2026-11-24): `hacs/action@main` runs the image
  `ghcr.io/hacs/action:main`, HACS's own code with Home Assistant 2026.10.4. It reads the repository through the
  GitHub API at the pushed ref, for a pull request its head branch, so it can't check a private repository.
  - Its checks for an integration: `archived`; `brands` (`custom_components/<domain>/brand/icon.png` in the tree, else
    the domain in the brands repository's list); `description`; `hacsjson` (`name` required, and only its known keys);
    `information` (a README); `integration_manifest` (`codeowners`, `documentation`, `domain`, `issue_tracker`, `name`
    and `version`); `integration_categories` (known, unique, at most three; none only warns); `issues`; `license`
    (OSI-approved, by GitHub's SPDX id); `topics`. `images` is for plugins and themes only. One failed check fails it.
  - This repository's description, topics, issues and Apache-2.0 license already pass.
- **HTTP.** aiohttp's `TCPConnector` closes idle connections after 15 s by default (`keepalive_timeout`), and Home
  Assistant's shared connector (`HomeAssistantTCPConnector`) keeps that default. A 45 s keep-alive therefore needs a
  connector of its own, with a longer `keepalive_timeout`, to keep the TLS connection to the bridge warm.
  `aiohttp.Fingerprint` takes a SHA-256 digest of the leaf certificate's DER (`getpeercert(binary_form=True)`), the
  pin. It turns off certificate validation and checks the digest instead.
- **Logging.** A Python log record exists only when its logger is enabled for its level, and then reaches the handlers
  of every logger above it, whatever their levels. Home Assistant sets the root logger to WARNING (INFO with
  `--verbose`) and leaves its handlers at every level. Its `logger` integration sets levels per logger, and ignores
  `setLevel` on a logger it has a level for. So a handler on `custom_components.sonoff2hue` gets info lines only while
  that logger is at INFO, and Home Assistant's log then shows them too.
- **Running Home Assistant from a virtual environment**, for development (decision 18; measured on 2026-11-24 with
  2026.11.3 on the development machine):
  - Without `default_config` it still sets up `bootstrap.DEFAULT_INTEGRATIONS` (the frontend, `backup`, `marketplace`,
    `brands`, `automation` and more, and every base platform but `calendar` and `todo`) and their dependencies: 104
    integrations for a `configuration.yaml` with only `homeassistant`, `logbook` and `logger`.
  - At its first start in an environment with only the `homeassistant` package, as the test environment has it, it
    installed their requirements with `uv pip install --constraint homeassistant/package_constraints.txt`:
    home-assistant-frontend (a 117 MB wheel), hassil, home-assistant-intents and gazetteer-matcher (`conversation`),
    ha-ffmpeg, infrared-protocols, rf-protocols, mutagen (`tts`), PyTurboJPEG (`camera`), aiohasupervisor (`hassio`, an
    after-dependency), and pyotp and PyQRCode (the TOTP sign-in module, before logging is set up). That start took 17 s.
    With them installed beforehand, it initialized in about 2 s and installed nothing.
  - pymicro-vad (wheels up to cp312) and pyspeex-noise (an sdist only), which `assist_pipeline` requires, have no wheels
    for Python 3.14 and didn't build without a C++ compiler; Home Assistant tried each three times at every start, and
    `assist_pipeline` and `assist_satellite` failed. `--skip-pip-packages` skips a requirement with the warning
    "Skipping requirement … This may cause issues", and the integration then fails at its import, at once. Without the
    `ffmpeg` program and libturbojpeg, `ffmpeg` and `camera` log errors at every start.
  - It listens on TCP 8123 on every address (`0.0.0.0` and `::`). It also holds UDP 5353, although the `zeroconf`
    integration isn't set up ("Nothing to set up in stage zeroconf"): the shared aiohttp session's resolver
    (`HassAsyncDNSResolver`) creates python-zeroconf, which bound `*:5353`, the interface's IPv4 address and its IPv6
    link-local address. The engine's listener received next to it on the same machine.
  - The HTTP server's settings live in the store `.storage/http` (version 3, minor 1: `stable`, `pending` and
    `yaml_migration_done`). An `http:` section of `configuration.yaml` is moved there once, as a `pending` trial that
    Home Assistant reverts after five minutes, restarting, unless it is confirmed in its UI, and the repair
    `yaml_still_present_after_migration` stays while the section does. The variable `SETUP_PORT` only changes the
    default port.
  - Custom integrations: `loader.async_mount_config_dir` puts the configuration folder on `sys.path` only while it
    imports the namespace package `custom_components`, whose `__path__` is then recomputed from `sys.path`. So with the
    repository root on `sys.path` (`python -m homeassistant` run from it), `custom_components/sonoff2hue` is the
    checkout's, and the configuration folder's `custom_components` isn't searched: an integration there didn't load,
    and loaded when Home Assistant ran from the configuration folder. A domain in `configuration.yaml` that doesn't
    resolve is dropped without a log line. With the root also on `PYTHONPATH`, the integration was found twice.
  - A restart (from the UI, a service call or SIGHUP) ends the process with exit code 100, and nothing starts it again.
    It handles SIGINT and SIGTERM only once `async_start` has returned, after its startup jobs; a SIGINT before that
    raises `KeyboardInterrupt`, which abandons the start without saving.
- **debugpy** as the Python Debugger extension 2026.6.0 bundles it (1.8.20; read in its sources and run through its
  adapter on 2026-11-24):
  - Breakpoints are matched by the real path (`os.path.realpath`): one set in the checkout's file is hit when the module
    was imported through a symlink, but the stopped frame names the link's path, which VS Code opens as another file
    (unless `resolveSymlinks`).
  - Stop kills the process group with SIGKILL, unless the launch configuration has `"onTerminate": "KeyboardInterrupt"`,
    which the extension's schema doesn't list: then pydevd sends its own process SIGINT, and a second Stop kills it.
  - The extension resolves the configuration's `python` when it starts the adapter, after the `preLaunchTask`, which
    can still create it. `serverReadyAction` also reads the integrated terminal.
- **Reauthentication** (2026-11-24, in the installed 2026.11.3). `ConfigEntry.async_start_reauth` is a callback that
  returns at once while a reauth or reconfigure flow of the entry is in progress, and checks again under a lock in the
  task it starts. A reauth flow that shows a form also raises the repair issue
  `config_entry_reauth_<domain>_<entry_id>`.
- **The runtime** (decision 23; checked on 2026-11-25 in the installed 2026.11.3, in its frontend 20261028.8 on GitHub,
  and in the development Home Assistant):
  - **Update listeners.** Home Assistant calls them, each as a task that starts eagerly, after any change of the entry:
    its data, options, title, unique id or preferences, and a subentry added, changed or removed (`_async_update_entry`,
    `_async_save_and_notify`). A change that leaves everything as it was calls none. Removing a subentry first removes
    the devices and entities linked to it (`async_clear_config_subentry`), and the entities' objects with them. A
    device belongs to one entry and at most one of its subentries (`DeviceEntry.config_subentry_id`).
  - **Reloads.** With an update listener on the entry, `async_update_reload_and_abort` logs "has an update listener and
    should use it for scheduling a reload", which breaks in 2026.12.0, and still reloads. `async_update_and_abort`
    saves without a reload, and the update listener does the rest. An entry that isn't loaded has no listener and
    takes the new data when it is set up.
  - **Tasks.** The entry's background tasks are cancelled when it unloads, after its `async_on_unload` callbacks have
    started, and waited for up to 10 s; the callbacks also run when its setup fails. Home Assistant's stop cancels
    every background task, then fires `homeassistant_stop`, and doesn't wait for the tasks it cancelled; it doesn't
    unload the config entries. Cancelling a task again while it cleans up interrupts the cleanup, such as closing an
    aiohttp session.
  - **Issues.** `async_create_issue(..., config_entry_id=…)` links an issue to its entry: Home Assistant deletes it
    when the entry is removed and hides it while the entry is disabled. An issue that isn't persistent is inactive
    after a restart until it is created again, and creating an issue again unchanged fires nothing. A repair flow that
    ends with `async_create_entry` deletes its issue, one that aborts keeps it, and both can name
    `next_flow=(FlowType.CONFIG_FLOW, flow_id)`, a flow that the frontend opens next. Texts: `issues.<key>.title`, and
    `description` or, for a fixable issue, `fix_flow` with its steps and aborts. hassfest accepts the import of
    `homeassistant.components.repairs` in `repairs.py`, a platform of that name, without a dependency, and the frontend
    depends on `repairs`.
  - **Logbook.** It matches events to a device by `device_id` in their data, in its queries and in its live stream.
  - **Tests.** `freezer` also moves the event loop's clock (`time.monotonic`). A timer that is due exactly at the new
    time doesn't run: the frozen clock counts seconds since 1970, where the loop's resolution of 1 ns is lost, so the
    tests move it a microsecond more. `hass.async_block_till_done()` doesn't wait for background tasks.
- **Diagnostics** (decision 26; checked on 2026-11-25 in the installed 2026.11.3 and with hassfest 2026.11.3):
  - Home Assistant's download, `/api/diagnostics/config_entry/<entry id>`, calls the platform's
    `async_get_config_entry_diagnostics(hass, entry)` for any entry of the domain, loaded or not, and an entry's
    `runtime_data` is deleted once it unloads. The file holds the answer as `data`, next to Home Assistant's system
    information, the custom integrations' versions, the manifest, the setup times and the domain's repair issues with
    their placeholders. It is written with `ExtendedJSONEncoder`, which writes a datetime in ISO 8601 and anything it
    doesn't know, such as an enum that isn't a string, as an object with its type and `repr`. A device's page offers a
    download of its own only when the platform has `async_get_device_diagnostics`. The frontend depends on
    `diagnostics`, so every Home Assistant with its UI has them, without `default_config`.
  - `async_redact_data(data, keys)` replaces the values of the listed keys at any depth of dicts and lists with
    `**REDACTED**`, and leaves None and empty strings as they are.
  - hassfest, run on a custom integration alone (`--integration-path`), allows the import of
    `homeassistant.components.<x>` anywhere in it when it has a platform `<x>.py`, or a folder `<x>`, without
    `dependencies` or `after_dependencies`. A copy with `diagnostics.py` and `logbook.py` renamed failed with "Using
    component diagnostics but it's not in 'dependencies' or 'after_dependencies'", and the same for the logbook.
  - Tests: `hass_client`'s access token is valid for 30 minutes from the real time, so after `freezer.move_to` a later
    time its requests get 401, and while the clock is frozen aiohttp's access log fails on every request ("Error in
    logging", as `tm_gmtoff` is None).
- **The logbook** (decision 26; checked on 2026-11-25 in the installed 2026.11.3 and its frontend 20261028.8 on GitHub):
  - It is named Activity in 2026.11. It loads each integration's `logbook.py` with
    `async_process_integration_platforms`, whether the integration was set up before or after it, and
    `async_describe_events(hass, async_describe_event)` registers a domain and a function for each event type. The
    recorder stores the events, and an entry is described whenever it is read, from a `LazyEventPartialState` whose
    `data` is the event's data as stored, or in the live stream the event's own. A function that raises is logged
    ("Error with <domain> describe event for <type>") and its entry left out.
  - For a device's page, it reads the event types registered under the domains of the config entries that own the
    devices, and matches `device_id` in the event's data.
  - An entry is its `name`, `message`, `domain` and `when`, with the context's fields. The frontend shows one without an
    `entity_id` as its name, a space and its message, with the domain's brand icon.
  - Besides state changes and the entries of `logbook.log`, it lists only the event types that an integration's
    `logbook.py` describes, so the bus events of an integration without one are recorded but never listed. It lists
    what happened after its start time and before its end time, both left out.
  - Tests: the real logbook runs in pytest-homeassistant 2026.11.3 with `recorder_mock`, listed before `hass`, and the
    frontend marked as set up (`mock_component`), as `home-assistant-frontend` isn't installed; `logbook/get_events`
    reads it over the websocket API once `async_wait_recording_done` has stored the events. With the clock frozen, a
    test moves it on after the last change it expects and before the first, as both ends are left out. Entries of the
    same moment come in no fixed order: the logbook sorts by time alone, and the entities that one status snapshot
    changes write their states at the same moment, in the order in which they started following the status, which
    varies, as Home Assistant sets an entry's platforms up side by side (`asyncio.gather`). A test compares them
    without regard to their order.
- **Subentry flows** (the switch flow, decision 24; checked on 2026-11-25 in the installed 2026.11.3, in the sources
  of its frontend, 20261028.8, and in a development Home Assistant):
  - Progress steps work in subentry flows. The flow manager is the same as for config flows,
    `SubentryManagerFlowResourceView` answers the frontend's GET of a flow, and the frontend's dialog subscribes to
    `data_entry_flow_progressed` for any flow and fetches it again through the subentry API. The text is
    `config_subentries.<type>.progress.<action>` with the placeholders, under the step's title.
  - Texts are under `config_subentries.<type>`: `step.<id>` (with `sections.<name>`: `name`, `description`, `data` and
    `data_description`), `error`, `abort` (unless the result names a translation domain), `create_entry.<description>`
    (else `default`), `progress`, `entry_type`, shown under each subentry's title, and `initiate_flow`: `user` labels
    the add button on the integration's page and in each entry's menu, `reconfigure` the cog of each subentry.
    hassfest requires `entry_type` and `initiate_flow.user`. Select options take
    `selector.<translation_key>.options.<value>`.
  - The add button is shown whatever the entry's state (it is disabled only without an enabled entry), so a flow that
    needs the loaded entry checks it; core integrations abort with `entry_not_loaded`. Removing the entry doesn't abort
    its subentry flows, and neither does Home Assistant's stop. A task made with `hass.async_create_task` before the
    stop doesn't hold it up: its second stage waits only for newer tasks, and its fourth cancels older ones with a
    warning.
  - `ConfigEntry.supported_subentry_types` asks the registered flow handler, so it is empty until the config flow
    module is loaded: Home Assistant loads it with the integration, and a test loads it with
    `async_get_platform("config_flow")`.
  - When a flow is removed, closed in the dialog or aborted, the flow manager cancels the task of its progress step
    (`async_cancel_progress_task`) before it calls the flow's `async_remove`.
  - Unloading the entry doesn't abort its subentry flows. Its state is `UNLOAD_IN_PROGRESS` while its
    `async_unload_entry` and unload callbacks run; then Home Assistant deletes `runtime_data`, and only then is the
    state `NOT_LOADED`. So a flow checks that the entry is `LOADED` before it reads `runtime_data`, and
    `async_on_state_change` lets it follow a reload to the new runtime.
  - A step that a result names (a menu's option, a form, a progress step's next step) has to exist as
    `async_step_<id>`, or the flow fails with `UnknownStep`.
  - `async_update_and_abort` takes no description placeholders; `hass.config_entries.async_update_subentry` and
    `async_abort` do the same with them. A unique id that another subentry has aborts with `already_configured`,
    without a translation domain, when a subentry is added (in `async_finish_flow`) and when one is updated. The flow
    API answers a new subentry without its `data` and `context`.
  - Sections (`data_entry_flow.section(schema, {"collapsed": …})`) are sent as `expandable` with `expanded`. The
    frontend shows an error keyed by the section's name above it, and none for the fields in it.
    `add_suggested_values_to_schema` takes a section's values as a nested mapping, and the frontend fills a section's
    data from its fields' suggested values.
  - Conditional fields: `data_entry_flow.Optional` and `Required` take `visible`, a `FieldCondition`
    (`helpers/field_visibility.py`), also inside a section. The frontend hides the field while its condition fails,
    and the flow manager drops a hidden field's value before it validates the input.
  - Select selectors: without a `mode`, the frontend shows fewer than 6 options as radio buttons or checkboxes, and more
    as a dropdown; several choices in dropdown mode are chips that can be removed, with a searchable picker to add
    more. An option is a value and a label only, so none can be disabled.
  - The frontend submits a form only when its required fields are filled, and leaves out the fields whose value is
    empty, so a flow that has its own message for an empty value takes an optional field.
- **Entities and devices** (decision 25; checked on 2026-11-25 in the installed 2026.11.3, and on GitHub in its
  hassfest's `icons.py` and its frontend 20261028.8's logbook and device page):
  - **Devices.** `DeviceInfo` is a dataclass with keyword-only fields, read like the mapping it was. `via_device` is
    deprecated for `via_device_id`, a device's id, and breaks in 2027.8.0. A device belongs to one config entry and at
    most one of its subentries, which `async_add_entities(…, config_subentry_id=…)` gives the devices of its entities;
    the subentry must exist, else it raises. A device registered again from another subentry moves there, with a
    report that this raises from 2027.8. A new device without a name takes the entry's title. A helper linked to a
    device is among the device's entities (`async_entries_for_device`), with a config entry of its own.
  - **Event entities.** The button device class has standard event types, `press_start`, `press_end`,
    `long_press_start`, `long_press_end`, `multi_press_ongoing` and `multi_press_end`, none of them required; core
    integrations such as switchbot, govee_ble and bthome use `press`. The state is the time of the last event to the
    millisecond, made 1 ms later than the one before when they would be equal, with `event_type` and the event's
    attributes, and the entity restores them after a restart.
  - **Translations and icons.** An entity's `state` texts replace its device class's, `on` and `off` of a binary
    sensor too, and a sensor's `unit_of_measurement` can be a text of its own, which it may not also set in code.
    hassfest refuses a state's icon that is the entity's default icon.
  - **Logbook.** Every change of an entity's state reaches the logbook, an event entity's with its `event_type`. It
    leaves out only changes of the attributes alone, the domains counter, image and proximity, and sensors with a
    unit, a state class or a numeric device class; nothing else keeps an entity out, and an enum sensor can't take a
    unit or a state class for that. A device's page asks the logbook for its entities and for itself (`entity_ids`
    and `device_ids`), so it shows its entities' changes next to the events that name it. The frontend (20261028.8)
    writes an event entity's change as the text of its event type, such as "Press", and "detected an event" only
    without one, a button's as "pressed", and an enum sensor's as its state's text.
  - **Recorder.** Every state change is a row of `states`; `_unrecorded_attributes` keeps attributes out of its
    `state_attributes`, and a write that changes nothing is a `state_reported` event, which isn't recorded.
  - **Timers.** `async_track_point_in_utc_time` arms the timer again when it fires early, so its callback runs once
    the time it asked for has come.
- **Storage, subentries and removal** (decision 32; checked on 2026-11-26 in the installed 2026.11.3 and in
  pytest-homeassistant 2026.11.3):
  - **`Store`.** `homeassistant.helpers.storage.Store(hass, version, key, private=False, *, atomic_writes=False,
    encoder=None, max_readable_version=None, minor_version=1, read_only=False, serialize_in_event_loop=True)` keeps
    `<config>/.storage/<key>` as `{"version", "minor_version", "key", "data"}`. `async_load()` gives `None` without a
    file, else `data`; while a write is pending, a copy of the pending data. A file that isn't JSON is moved aside
    (`<key>.corrupt.<time>`) with a critical repair `storage_corruption_…`, and `None` comes back; a newer major
    version raises `UnsupportedStorageVersionError`, a `HomeAssistantError`; another version calls
    `_async_migrate_func`, which raises `NotImplementedError` unless a subclass has one. With `atomic_writes`, a
    write goes to a temporary file that then replaces the store.
  - **Saving.** `async_delay_save(data_func, delay)` calls `data_func` when it writes, `delay` seconds later on the
    event loop's clock (`loop.call_at`); a later call moves the write to its own time. It also listens for
    `EVENT_HOMEASSISTANT_FINAL_WRITE`, when a pending write is made, and schedules nothing while Home Assistant stops.
    `async_save(data)` writes at once and replaces a pending write. `async_remove()` drops a pending write, its timer
    and its final-write listener, and deletes the file.
  - **Tests.** pytest-homeassistant's `hass_storage` fixture, which its `hass` fixture uses, keeps the stores in a
    dictionary of their keys and written envelopes. Home Assistant's stop at the end of a test makes the pending
    writes (the final write), so a delayed save leaves no timer behind.
  - **`async_update_subentry(entry, subentry, *, data, title, unique_id)`** returns `False` and calls nobody when
    nothing changes. Otherwise it replaces the data with a `MappingProxyType` of it, moves `modified_at`, schedules
    the entries' save and calls the entry's update listeners, each as a task that starts eagerly
    (`hass.async_create_task`), so a listener runs until it first waits before the call returns. A unique id that
    another subentry has raises `AbortFlow("already_configured")`, and an unknown entry or subentry `UnknownEntry` or
    `UnknownSubEntry`.
  - **`async_remove_entry(hass, entry)`**, the integration's, runs when an entry is removed: after the entry has
    unloaded under its setup lock, its `async_on_unload` callbacks included, also for an entry that wasn't loaded,
    and before Home Assistant removes its devices, entities and issues. One that raises is logged ("Error calling
    entry remove callback …") and the removal goes on. An ignored entry doesn't call it.
- **Driving it through its API**, as the end-to-end test does (decision 27; checked on 2026-11-25 in the installed
  2026.11.3 and by running it):
  - **Onboarding.** `GET /api/onboarding` needs no sign-in and answers once the HTTP server runs, which starts when the
    frontend is set up, before the start is over. `POST /api/onboarding/users`, without a sign-in too, takes
    `client_id`, `name`, `username`, `password` and `language`, makes the owner and answers an `auth_code`. `POST
    /auth/token` with the form `grant_type=authorization_code`, the `code` and the same `client_id` gives an access
    token, valid for 30 minutes and also after a restart, and a refresh token. The REST and WebSocket APIs need nothing
    more. The next step, `POST /api/onboarding/core_config`, starts the setup flows of `google_translate`, `met`,
    `radio_browser` and `shopping_list`, which need the network and requirements of their own.
  - **Start.** `GET /api/core/state`, signed in, answers `{"state": "RUNNING", …}` once the start is over. The config
    entries are set up before that, and from then on Home Assistant handles SIGINT; an entry's background tasks, such
    as the supervisor, may still be starting.
  - **Flows.** `POST /api/config/config_entries/flow` with `{"handler": …}` starts a flow at `user`. `POST
    /api/config/config_entries/flow/<flow_id>` takes a step: a menu's with `{"next_step_id": …}`, a form's with its
    fields. `GET` on it runs the current step again without input, as the frontend fetches a flow. When a progress
    step's task ends, the flow manager runs the step again and fires `data_entry_flow_progressed` with the flow's
    `flow_id` if the step or its text changed; the entry is created only when the flow is fetched next, as the public
    `async_configure` goes past `progress_done`. A task that ends within milliseconds can have its announcement reach
    a WebSocket client before the answer to the request that started the step. Subentry flows are at
    `/api/config/config_entries/subentries/flow`, with the handler `[entry_id, subentry_type]` and, to reconfigure,
    `subentry_id`. A form's `data_schema` gives a select field's options as values and labels; the switch flow's
    lights, rooms and zones are numbered by their place in the bridge's lists, so a client picks them by label, as a
    user does. `GET /api/config/config_entries/entry?domain=…` lists the entries with their `state`, and the
    WebSocket command `config_entries/subentries/list` an entry's subentries with their type, title and unique id.
  - **Events.** The WebSocket command `subscribe_events` with an `event_type` sends each event of that type after the
    command's result, in the order they were fired. The owner may subscribe to any type.
  - **Entities.** The WebSocket command `config/entity_registry/list` lists every entity with its `entity_id`,
    `platform`, `translation_key`, `unique_id` and `config_subentry_id`, so a client finds an integration's entities
    by their translation keys. `GET /api/states/<entity_id>` gives the state with its attributes; an event entity's
    hold its `event_type` and the event's own, such as Press's `seq`.
  - **The integration's status.** The diagnostics download, `/api/diagnostics/config_entry/<entry_id>` (decision 26),
    holds `entry.runtime_data.status` as `data.status`: the status tracker's state at that moment, not the last
    snapshot it published 500 ms after a change, so it shows `running` at once, and `data.lan_devices` the LAN
    monitor's devices with their `seq`. Only an administrator may download it. Right after the integration's first
    setup it answers 404 for a moment: Home Assistant imports and registers an integration's diagnostics platform in
    the background, on `component_loaded`.
  - **Restart.** The `homeassistant.restart` action checks the configuration (`async_check_ha_config_file`), answers,
    then stops Home Assistant, which closes the WebSocket connections and exits with code 100: 1.1 s after the call on
    the development machine, and 2.4 s more to `RUNNING` again; on GitHub's runner 0.7 to 1.4 s, and 2.6 to 4.0 s in
    all.
  - **Log.** `GET /api/error_log`, for the owner, serves `home-assistant.log`. Home Assistant moves that file to
    `home-assistant.log.1` at each start (`backupCount=1`), so after the first start `home-assistant.log.1` is empty. A
    thread of its own writes the file (`QueueListener`), so a line can reach it after the event that the integration
    fired once it had logged the line. The development configuration's `logbook` depends on `recorder`, which writes
    `home-assistant_v2.db`.
  - **GitHub Actions.** `astral-sh/setup-uv@v10.4.0` doesn't prune its cache before it saves it (`prune-cache: false`),
    so the cache keeps the downloaded wheels; its key follows `cache-dependency-glob`. `actions/upload-artifact` v7
    (7.0.2) leaves out hidden files and folders, such as `.dev`, unless `include-hidden-files: true`. ubuntu-latest is
    the image `ubuntu-26.04`, whose own `/usr/bin/python3.14` (CPython 3.14.4) `uv venv --python 3.14` takes, so with
    `cache-python: true` setup-uv finds no managed Python to save and warns so at the end of each job, *Lint* and *Test*
    as well. The warning is harmless.

Home Assistant OS, measured by the mDNS spike (decision 19) on the user's host on 2026-11-25: Home Assistant OS 18.4
with kernel 6.18, Core 2026.11.3 on Python 3.14.10, in an x86_64 virtual machine. The spike ran the engine's listener in
Home Assistant's process for 3 minutes and logged what it saw:

- **UDP 5353.** The core container shares the host's network, so its `/proc/net/udp` and `udp6` list the host's
  sockets; only Home Assistant's own process sees them side by side, as the Terminal & SSH app has a network of its
  own. There were 14 on port 5353, and the engine's socket bound beside them at the first attempt:
  - the engine's, on 0.0.0.0, with the options as the kernel reported them: SO_REUSEADDR 1, SO_REUSEPORT 1,
    IP_MULTICAST_TTL 255, IP_MULTICAST_LOOP 1, IP_MULTICAST_ALL 1 and SO_RCVBUF 212,992;
  - Home Assistant's zeroconf's 3 (below), and no other socket of Home Assistant's process;
  - 10 of other processes, which the core container can't name, as their pids are in other namespaces: 5 on 0.0.0.0
    and 4 on `[::]`, one IPv4 and one IPv6 socket of a system user and the rest root's, and 1 bound to the `hassio`
    bridge's address. That one had dropped about 15,800 datagrams, 10 of them during the spike: its owner, presumably
    the multicast plugin, which sends with it, never reads it.
- **Interfaces and the multicast plugin.** The listener's choice of interfaces (decision 8) joined 224.0.0.251 on
  three: the LAN interface, and Docker's bridges `docker0` and `hassio`, which are up, multicast-capable and have IPv4
  addresses. It left out `lo` and 8 interfaces without an IPv4 address, the containers' veth ends among them. Home
  Assistant's own network settings had only the LAN interface enabled; the engine doesn't read them.
  - The Supervisor's multicast plugin repeats mDNS between the LAN and the `hassio` network, so every packet from the
    LAN arrives twice: the copy, a few milliseconds later, comes from the `hassio` bridge's address, one of this host's
    own. The tracker takes the copy as a duplicate.
  - The user's MINIR4M announces each new `seq` twice, about 1.1 s apart, as mDNS announces a changed record, so each
    press arrived 4 times: once as the press, then as 3 duplicates.
  - The answers to the PTR queries carry SRV and A records, so their copies still name the device's own address. A
    press's update carries only the TXT record, so the engine took its sender as the address, and for the plugin's
    copy that is the `hassio` bridge's. The LAN registry keeps the address of every broadcast, duplicates included,
    so it showed the bridge's address for the switch after each press. Since then the engine takes no address from a
    sender that is one of this host's own addresses (decision 19).
- **Home Assistant's zeroconf** had 3 sockets on UDP 5353, as python-zeroconf makes them when an enabled adapter has
  IPv6 (`IPVersion.All`): one on `[::]` that only receives, IPv4 too, and one bound to each of the LAN interface's
  addresses, its IPv4 and its IPv6 link-local one, that receive and send. All had SO_REUSEADDR 1, SO_REUSEPORT 1 and
  SO_RCVBUF 212,992. IP_MULTICAST_ALL read 0 on the IPv6 ones and 1 on the IPv4 one, which are Linux's defaults: a new
  AF_INET6 socket has 0 and an AF_INET socket 1 (checked on 6.18).
  - Multicast: Linux gives each multicast datagram to every socket bound to its port that accepts it, so the two stacks
    don't take packets from each other. A socket with IP_MULTICAST_ALL 0 accepts the group only on the interfaces it
    joined (`ip_mc_sf_allow`), so the engine's joins on `docker0` and `hassio` add nothing to what Home Assistant's
    zeroconf receives. During the spike it received 6,792 records, 6,771 of them from other hosts, with the same 21
    eWeLink devices as the engine's socket.
  - Unicast replies stay safe: Home Assistant sends its IPv4 queries from its socket bound to the LAN address, and Linux
    gives a unicast datagram to a socket bound to its exact address before any wildcard socket (`__udp4_lib_lookup`).
    They would be at risk with only the default adapter enabled and no IPv6: python-zeroconf then uses one socket on
    0.0.0.0:5353 (`InterfaceChoice.Default`), and the kernel may hand unicast replies meant for it to the engine's
    socket, another wildcard socket on the port. So on Linux the engine's socket is bound to the group (decision 19).
- **SonoffLAN** (domain `sonoff`), in local mode, browses `_ewelink._tcp.local.` with Home Assistant's zeroconf: its
  listener for that service is one of the record listeners of Home Assistant's instance, so it gets every record that
  instance receives. While the engine's socket was open, that listener stayed registered, and SonoffLAN's entities wrote
  their state 211 times in 3 minutes, within 5 s after each of the 5 presses. The two listen to the same switches side
  by side.
- **The Supervisor's proxy** to Core's API, `http://supervisor/core/api/…` with an app's `SUPERVISOR_TOKEN`, forwards
  GET and POST, and DELETE since Supervisor 2026.02, but only for apps with `homeassistant_api: true`; any other app
  gets 401, and the Supervisor logs "Not permitted API access" (`api/proxy.py`, `_check_access`, in 2026.11.0). The
  official Terminal & SSH app has `hassio_api` and `hassio_role: manager` but not `homeassistant_api` (`ssh/config.yaml`
  in home-assistant/app-ssh), so it can't add or delete config entries. Over its SSH, a custom integration can start
  from a line in configuration.yaml (with `async_setup` and a `CONFIG_SCHEMA`), or a long-lived access token of an
  administrator calls Core's REST API directly, at `http://homeassistant:<port>/api` from that app. `ha core logs`
  prints the last 100 records unless `-n` asks for more.
- **Log volume.** Home Assistant's log listener (`HomeAssistantQueueListener`) counts each logger's records at INFO and
  above, and when one reaches 200 within 5 minutes, it warns once: "Module … is logging too frequently. 200 messages
  since last count". The spike's warning per packet reached it in its first minute; the engine logs per packet at debug
  and trace level.

A UDP socket bound to the mDNS group, 224.0.0.251:5353, with SO_REUSEADDR and SO_REUSEPORT (decision 19; checked on
2026-11-25 on Linux 6.18 and on GitHub's macOS 26.7.2 runner, and read in Linux's `net/ipv4` and xnu's
`bsd/netinet`):

- **Linux.** It binds next to sockets on 0.0.0.0, and wherever a socket on 0.0.0.0 could bind.
  - It receives every datagram sent to the group on the interfaces it joined, as a socket on 0.0.0.0 does:
    `__udp_is_mcast_sock` accepts a socket bound to the datagram's destination, and the lookup finds it, whether it
    walks all the sockets of the port or, with more than 10 of them as on Home Assistant OS, the hash slots of the
    group's address and of 0.0.0.0.
  - Unicast never reaches it, as `__udp4_lib_lookup` leaves out the sockets bound to another address. Sockets bound
    to the same address with SO_REUSEPORT share unicast by a hash of the sender: 64 datagrams from 64 ports to
    127.0.0.1 went 32 and 32 to two sockets on 0.0.0.0, but all 64 to the one on 0.0.0.0 when the other was bound
    to the group.
  - Its multicast goes out from the address of the interface that IP_MULTICAST_IF names, and from its own port:
    `__inet_bind` leaves the source address unset for a multicast address, and `udp_sendmsg` then takes the
    interface's. It loops back to every socket on the port, itself included.
- **macOS.** The bind works, next to mDNSResponder too, and the socket receives the group and no unicast, but sending
  from it fails with EADDRNOTAVAIL ("Can't assign requested address"), also as root: xnu's `udp_output` sends from
  the bound address, and a multicast address isn't one of the host's.
- **What it gives up:** only unicast, which the engine doesn't need. Its queries are multicast, from port 5353 and
  without the unicast-response bit, so their answers must go to the group too (RFC 6762, section 6, which also has
  a querier ignore unicast answers it didn't ask for); in the spike, the multicast plugin repeated the eWeLink
  devices' answers to them.

Linux's interfaces and address changes, as the mDNS layer reads them (decision 8; Linux's netlink, `rtnetlink`
and `netdevice` interfaces, 2026-11-24):

- **Address changes.** A netlink route socket (`NETLINK_ROUTE`) bound to the group `RTMGRP_IPV4_IFADDR` gets an
  `RTM_NEWADDR` or `RTM_DELADDR` message for each IPv4 address added or removed; links and routes have groups of their
  own, which the socket doesn't join. A datagram can hold several messages, each aligned to 4 bytes, and ends with
  `NLMSG_DONE` or `NLMSG_ERROR` where it has one. When the socket's buffer overflows, `recv` fails with `ENOBUFS`:
  messages were lost, so the listener counts a change.
- **Interfaces.** `socket.if_nameindex()` lists them in index order. `SIOCGIFFLAGS` gives an interface's flags: it is
  up with both `IFF_UP` and `IFF_RUNNING`, and multicast-capable with `IFF_MULTICAST` or `IFF_ALLMULTI`.
  `SIOCGIFHWADDR` gives its hardware type (ARPHRD): 772 is loopback, 768 and 769 are tunnels. For Ethernet (1, 2),
  ProNET (4), ATM (19), SLIP and CSLIP (256 to 259), PPP (512), FDDI (774) and Wi-Fi (801 to 803), `ETHTOOL_GLINK`
  (through `SIOCETHTOOL`) tells whether the link is up, where the driver can tell, and an interface without a link isn't
  up. Without a hardware type, `IFF_LOOPBACK` tells loopback.
- **Addresses.** ifaddr (`getifaddrs`) gives the IPv4 addresses; one on an alias such as `eth0:1` belongs to `eth0`,
  the interface that `if_nametoindex` names.

aiohttp 3.14.5 and yarl, as the Hue client uses them (2026-11-24 and 2026-11-25):

- A small response's connection is released before the caller sees it (`response.connection` is None), so the
  certificate is read in `aiohttp.Fingerprint.check`, which the connector calls after every TLS handshake. asyncio
  reports a connection closed during the handshake as `ConnectionResetError`: a server that hangs up at once gives
  "Connection reset by peer", and a plain HTTP server a failed TLS handshake (`ClientSSLError`).
- With aiodns installed, as Home Assistant installs it, aiohttp's default resolver is c-ares (`AsyncResolver`).
  `aiohttp.ThreadedResolver` asks the system's resolver (`getaddrinfo`), which also answers `.local` names where the
  system can.
- yarl takes an IPv6 zone in brackets unencoded (`https://[fe80::1%eth0]/`), and aiohttp connects to `raw_host`. yarl
  maps an internationalized host name to its ASCII form with IDNA (UTS 46), lowercases it, and accepts hosts such as
  `1.2.3`, `-bridge` and `bridge_1`, which aren't host names; hence the integration's own check of the labels.
- `http.HTTPStatus(code).phrase` is the standard reason phrase of a status.

PyYAML and probatio, for the import (2026-11-25):

- Home Assistant 2026.11.3 ships PyYAML 6.0.3 with libyaml. Its `safe_load` turns `yes`, `on`, `~`, `0x10` and `010`
  into booleans, null and numbers; a loader whose only implicit resolver is null's keeps every other plain value as the
  text written.
- The C loader (`CSafeLoader`) and PyYAML's own (`SafeLoader`) refuse the same nodes at the same places; they differ in
  the texts of their problems, and libyaml takes a tab inside a plain value, which the pure-Python reader refuses. A
  `ReaderError`'s position counts bytes of the UTF-8 text in the C loader and characters in the other, and the C loader
  raises `UnicodeEncodeError` for a lone surrogate. With Python 3.14, 1,000 nested lists stop PyYAML's pure-Python
  composer and 100,000 the C one with a `RecursionError`.
- probatio 0.13.0 has voluptuous's API. `Maybe(X)` reports X's own error class, `DictInvalid`, `SequenceTypeInvalid`
  or `TypeInvalid`, which names the kind expected, where `Any(X, None)` reports `AnyInvalid`; an unknown key is an
  `ExtraKeysInvalid`; and a schema collects every error with its path (`MultipleInvalid`).

The eWeLink LAN protocol and the Hue API, from public sources:

- The MINIR4M is eWeLink UIID 138, like the MINIR3/MINIR4
  ([SonoffLAN devices.py](https://github.com/AlexxIT/SonoffLAN/blob/master/custom_components/sonoff/core/devices.py)).
  Each device publishes `eWeLink_<deviceid>._ewelink._tcp.local`, whose TXT record holds `id`, `type`, `seq`,
  `encrypt`, `iv` and `data1`..`data4`. The data is the device's params as JSON, encrypted with AES-128-CBC/PKCS7 and
  the key MD5(devicekey), or plain in DIY mode. On every state change the device re-announces the record with `seq` + 1.
- In detached mode, the press itself (`{"key":0}`) goes to the eWeLink cloud only. Over LAN, each press is a new state
  broadcast with a higher `seq` and an unchanged relay
  ([SonoffLAN #1227](https://github.com/AlexxIT/SonoffLAN/issues/1227),
  [#1880](https://github.com/AlexxIT/SonoffLAN/issues/1880)). SonoffLAN accepts instances that start with `ewelink`,
  case-insensitively; `ihost-*` and `zbbridgeu-*` aren't devices.
- Hue's local CLIP v2 API uses HTTPS with the header `hue-application-key`. The key is the `username` from
  `POST /api {"devicetype": …, "generateclientkey": true}` after the link button is pressed (error 101 until then).
  Rooms and zones have a `grouped_light` service that switches all their lights in one command. CLIP v2 can answer 2xx
  and still report errors, such as 207 for an unreachable light
  ([openhab/openhab-addons#15511](https://github.com/openhab/openhab-addons/issues/15511)).

A MINIR4M that stops receiving multicast, measured on the user's network on 2026-11-26: the recorder's history, and a
throwaway probe on a computer on the same subnet, which pinged the switch, sent it a unicast mDNS query every 20 s and
listened to the group.
- **The pattern:** one of the user's four switches went *Offline* about once an hour, 3 to 8 minutes past the hour, for
  1 to 9 minutes each time overnight. That means it went 5 to 14 minutes without answering. The other three switches
  never did.
- **During such a time,** for at least 20 minutes on 2026-11-26:
  - it answered none of the LAN monitor's multicast queries;
  - it answered every ping and every legacy unicast query, from a port other than 5353, 139 of 139;
  - it answered a direct unicast query sent from port 5353 (RFC 6762, section 5.5) by multicast, which every listener
    on the group received, the engine's socket too.
- **So** it still sends, its presses among it, but receives no multicast. That fits a station that misses the
  access point's group traffic after the group key is renewed, which some access points do every hour by default.
- **The catch-up queries** (decision 36) can't bridge it, as they are multicast. A direct unicast query to the switch's
  last address would. That needs a new kind of query from the mDNS layer, which decision 30 freezes, so it waits for
  the user's decision.

Hue rooms and zones in CLIP v2 (decision 34), checked on 2026-11-26 in public sources, as Hue's own API reference needs
a developer account: aiohue 4.9.2, the library of Home Assistant's Hue integration (home-assistant-libs/aiohue at
edfdc8a: `aiohue/v2/models/room.py`, `zone.py`, `device.py`, `light.py` and `controllers/groups.py`); openhue-api, an
OpenAPI description of CLIP v2 (openhue/openhue-api at 1ffc817: `src/room`, `src/zone`, `src/common`); and openHAB's
Hue binding (openhab/openhab-addons at 2cd1f77: the CLIP 2 DTOs `Resource.java`, `MetaData.java` and
`enums/Archetype.java`):

- **Rooms** group devices, and a device can be in one room only. A room's `children` are `{"rid","rtype"}` references
  of `rtype` `device` (aiohue: "Following children are allowed: device"). A device's `services` include its `light`
  services; aiohue's `Device.lights` is the set of them. A room's devices can include some without a light, such as
  accessories.
- **Zones** group services, and a service can be in several zones: in practice a zone's `children` are `light`
  references (aiohue's `ZoneController.get_lights` takes those). None of the sources has a zone holding a room or a
  device. The bridge's `bridge_home` holds rooms and the devices of no room.
- **Lights:** every light has an `owner`, the reference of its device (openhue-api's `ResourceOwned`: required).
- **Grouped services:** a room's or zone's `services` aggregate its children's, per service type: "This includes all
  services grouped in the group hierarchy given by child relation. This includes all services of a device grouped in
  the group hierarchy given by child relation." So the `grouped_light` switches the lights among the children, the
  lights of the devices among them, and those of the groups among them; sonoff2hue reads a room's or zone's lights by
  that rule, with the light answer's owners in place of `GET /clip/v2/resource/device`.
- Rooms and zones have the same schema, archetypes included (openhue-api: "Zone and Room schemas are strictly
  equivalent"; aiohue's `Zone` is a `Room`).
- **Creating a zone:** `POST /clip/v2/resource/zone` with `children` and `metadata`:
  `{"children":[{"rid":"<light id>","rtype":"light"}],"metadata":{"name":"TV Zone","archetype":"tv"}}` (openhue-api's
  example `NewTvZone`). aiohue's `ZonePost` is its `RoomPost`, whose `children` and `metadata` with `name` and
  `archetype` are all required, as in openhue-api's `RoomPost`; openhue-api's zone `POST` takes its room's `RoomPut`
  schema instead, which requires nothing, and calls the two strictly equivalent. aiohue's `create` sends the dataclass
  as JSON. The answer is 200 with
  `data`, a list of references, the new zone's (openhue-api). diyHue, an emulation of the bridge, answers
  `{"data":[{"rid":"<id>","rtype":"zone"}],"errors":[]}` (diyHue/diyHue at 88d0594,
  `BridgeEmulator/flaskUI/v2restapi.py`). The zone is then `GET /clip/v2/resource/zone/{id}`, whose `data` holds it.
- **The name:** 1 to 32 characters (openhue-api's `RoomPost`: `minLength` 1, `maxLength` 32; the other sources give no
  limit). JSON Schema counts characters, as `len()` does.
- **The archetype** is required, and is one of 40, the same for rooms and zones in all three sources: `living_room`,
  `kitchen`, `dining`, `bedroom`, `kids_bedroom`, `bathroom`, `nursery`, `recreation`, `office`, `gym`, `hallway`,
  `toilet`, `front_door`, `garage`, `terrace`, `garden`, `driveway`, `carport`, `home`, `downstairs`, `upstairs`,
  `top_floor`, `attic`, `guest_room`, `staircase`, `lounge`, `man_cave`, `computer`, `studio`, `music`, `tv`, `reading`,
  `closet`, `storage`, `laundry_room`, `balcony`, `porch`, `barbecue`, `pool` and `other`. aiohue reads an unknown one
  as `other`.
- **How many:** each room and zone is also a group of the older API, as an entertainment area is (`id_v1`
  `/groups/<n>`, in aiohue's recorded resources, `tests/fixtures/v2_resources.json`), and a bridge's capabilities in
  that API (`GET /api/<key>/capabilities`) count them together: groups `{"available": 41, "total": 64}` in
  peter-murray/node-hue-api's example (`docs/capabilities.md`). None of the sources gives the Hue Bridge Pro's number.
  A full bridge refuses; openhue-api lists 507 Insufficient Storage among the answers to `POST`. sonoff2hue shows the
  bridge's message rather than count.
- **The grouped light:** Home Assistant 2026.11.3's Hue integration waits up to 5 times 0.5 s for a new grouped
  light's room or zone, "due to a race condition where the grouped_light resource is created before the zone/room"
  (`homeassistant/components/hue/v2/group.py`). So the bridge makes the two close together; sonoff2hue reads a new
  zone again briefly in case it lists its grouped light a moment after it.
- **Quotes in the dialog's texts** (Home Assistant 2026.11.3 with frontend 20261028.8, whose bundle formats texts with
  `IntlMessageFormat`): ICU message format takes an apostrophe right before a brace as the start of quoted text.
  intl-messageformat 10.7.18, run with Node 26.13.1, formats `Use room '{group}'` as `Use room {group}`, and
  `Use room "{group}"` as `Use room "Living room"`. Core's `strings.json` files put no apostrophe before a
  placeholder, and quote one with double quotes, written `\"{subtype}\"` in their JSON 99 times. So the offer's
  texts quote names with double quotes, and the switch flow's tests refuse an apostrophe before a placeholder.

Finding a Hue bridge at a new address (decision 33), checked on 2026-11-26:

- **Signify's discovery endpoint** is rate limited. Hue's guide to bridge discovery and its page on rate limiting need a
  developer account (`developers.meethue.com/develop/application-design-guidance/hue-bridge-discovery/` and
  `/rate-limiting-status-code/` lead to its login). node-hue-api's author quoted the guide in
  peter-murray/node-hue-api#222 on 2022-08-01: "The rate limit of using the discovery endpoint is maximum one request
  per 15 minutes per client. Typically it should be much less than that because the discovered IP can be stored and only
  rechecked when a request times out (as the ip might have changed). More regular ip scanning can always be done with
  mDNS." Beyond the limit the endpoint answers 429 Too Many Requests, without headers that give the window or the
  requests left (the same issue). Makers of other Hue clients document one request per 15 minutes too: Hue Log's help
  page on the 429, and Chowmain's knowledge base for its Control4 driver, of 2026-01-11. The endpoint lists the bridges
  that registered from the same public address as the request, each with its internal address and id, which a bridge
  reports from time to time (Hue Log's help), so it may list a bridge's new address only some time after it moved.
  sonoff2hue's discovery takes a 429 as a failure, logged at debug level (Behavior, Discovery).
- **Home Assistant's own Hue integration** (2026.11.3, `homeassistant/components/hue/config_flow.py`) follows a bridge's
  new address from zeroconf: `async_step_zeroconf` takes the announcement's `bridgeid`, normalized by aiohue's
  `normalize_bridge_id` (lowercased, the `fffe` of a 16-character id dropped; home-assistant-libs/aiohue at `main`,
  `aiohue/util.py`), as the unique id, and `_abort_if_unique_id_configured(updates={CONF_HOST: discovery_info.host},
  reload_on_update=True)` gives the configured entry the new host and reloads it, on the TXT record's claim alone,
  without a certificate. It ignores IPv6 hosts.

GitHub, for the Release workflow (decision 29), checked on 2026-11-25 with gh 2.46.0, the REST API and GitHub's
documentation:

- The list of releases (`GET /repos/{owner}/{repo}/releases`) is ordered newest first by date, not by version:
  nodejs/node lists v24.22.3 (2026-11-11) before v26.13.0 (2026-10-28). So a lower version released after a higher one
  would become the Marketplace's version on offer.
- `gh api --paginate` merges the pages of a JSON array into one array. After an HTTP error, `gh api` prints the answer's
  body on stdout and `gh: <message> (HTTP <status>)` on stderr, and exits with 1. `releases/tags/<tag>` and
  `releases/latest` answer 404 when there is no such Release, and `commits/refs/tags/<tag>` answers 422 "No commit
  found for SHA: refs/tags/<tag>" for a missing tag.
- The notes that GitHub generates for `v1.0.0` (`POST /repos/{owner}/{repo}/releases/generate-notes`, which saves
  nothing) list every merged pull request, the new contributors, and a link to the commits up to the tag.
- `GET /repos/{owner}/{repo}/immutable-releases` answers `{"enabled": …, "enforced_by_owner": …}`, and `PUT` turns
  immutable releases on.
- An immutable Release locks only its tag and assets: its title and notes can still be edited, and whether it is a
  pre-release or Latest. Publishing one makes a release attestation of its tag, commit and assets, which
  `gh release verify` checks (GitHub's documentation, *Immutable releases*, read on 2026-11-25).
- A concurrency group keeps at most one run waiting by default, so a third run cancels the waiting one. GitHub Actions
  now has `queue: max` for up to 100 waiting runs, but actionlint 1.7.12, its newest release, rejects the key, so the
  workflow keeps the default.
- A tag push runs the workflow files of the tagged commit. A `push` trigger that names only branches doesn't run for
  tags, so *CI* and *Validate* don't run for a release tag.

GitHub, for releases from a merged release pull request (decision 37), checked on 2026-11-26 in GitHub's documentation
(*Events that trigger workflows*, *Triggering a workflow*, *Workflow syntax*, *Variables reference*, *Permissions
required for fine-grained personal access tokens*, and the REST API's *Git tags*, *Git references*, *Repository
contents* and *Workflows*), in its changelog of 2026-02-19, and with gh 2.106.0 against this repository, reading only:

- **What a workflow's `GITHUB_TOKEN` starts.** Events that the token causes create no workflow run, except
  `workflow_dispatch` and `repository_dispatch`, which always do, and a pull request that the token opens or updates,
  whose runs wait for an approval. So a tag ref that a workflow creates with the token, through the REST API too, runs
  nothing, not `release.yml`'s `push: tags` either, while a dispatch runs it.
- **`workflow_run`** starts a workflow when another one is requested, in progress or completed (`types`), whatever its
  conclusion, and only from the default branch's copy of the workflow file. Its `GITHUB_SHA` is the default branch's
  newest commit and `GITHUB_REF` the default branch, so the commit that the other workflow ran on is
  `github.event.workflow_run.head_sha`. `workflows` names the other workflow by its `name`, and `branches` filters the
  branch that it ran on, `head_branch`, which for a pull request's run is the pull request's own branch: only `event`
  tells a push to `main` from a pull request from a branch called `main`. The run object, the REST API's too, has
  `conclusion` (`success`, `failure`, `cancelled`, …), `event`, `head_branch`, `head_sha` and `run_attempt`. Among this
  repository's CI runs on `main` are failed ones and one that a newer push cancelled. A re-run completes again: only
  `requested` doesn't occur for a re-run. Chains stop after three levels. A workflow that it starts can write and read
  secrets even when the other one couldn't, so it mustn't run untrusted code.
- **A run on a tag** (`workflow_dispatch`): `GITHUB_REF` is the branch or tag that received the dispatch, fully formed
  (`refs/tags/<tag>`), and `GITHUB_SHA` its last commit. So a run on `vX.Y.Z` has the ref and the commit of the tag's
  push, and runs the tagged commit's workflow file; the file must also be on the default branch. The REST API
  (`POST /repos/{owner}/{repo}/actions/workflows/{file}/dispatches`, with `ref` a branch or tag) needs Actions write. It
  answers 204, or since 2026-02-19 200 with `workflow_run_id`, `run_url` and `html_url` when it is given
  `return_run_details`, which `gh workflow run` asks for from gh 2.87.0, printing the run's address.
- **Annotated tags through the REST API.** `POST /repos/{owner}/{repo}/git/tags` makes only the tag object, from `tag`,
  `message`, `object`, `type` (`commit`) and an optional `tagger` with `name`, `email` and an optional `date` (ISO
  8601); the documentation doesn't say which date it takes without one. `POST /repos/{owner}/{repo}/git/refs` with
  `ref` `refs/tags/<tag>` and the object's `sha` then makes the tag. Both need Contents write and answer 201, or 409 or
  422. github-actions[bot] is the user 41898282, so its noreply address is
  `41898282+github-actions[bot]@users.noreply.github.com`.
- **The contents API's raw media type.** `GET /repos/{owner}/{repo}/contents/{path}?ref=<commit>` with the header
  `Accept: application/vnd.github.raw+json` answers with the file itself, as
  `application/vnd.github.raw+json; charset=utf-8`, for files of up to 100 MB, and needs Contents read. Through gh
  2.106.0, `README.md`, `release.yml` and the manifest at 60b33ca came byte for byte as git has them. A missing file is
  a 404 "Not Found", and a ref that names no commit a 404 "No commit found for the ref …"; as for every error, gh prints
  the answer's body on stdout and `gh: <message> (HTTP <status>)` on stderr. A missing tag's `commits/refs/tags/<tag>`
  is still the 422 that decision 29 found.

uv, PyPI and GitHub Actions, for the beta canary (decision 35), checked on 2026-11-26 with uv 0.13.7 (its documentation,
and resolutions for CPython 3.14.4 on Linux x86_64), PyPI's JSON API, `homeassistant/package_constraints.txt` at core's
tag 2026.12.0b0, setup-uv v10.4.0's source and GitHub's documentation:

- **PyPI.** `pytest-homeassistant` has 2026.11.0b4 to b6, 2026.11.0 to 2026.11.3 and 2026.12.0b0, uploaded on
  2026-11-25 at 18:38 UTC, and no dev releases. PyPI's latest version (`info.version`) leaves pre-releases out: it is
  2026.11.3. Its 0.1.0 to 0.1.3, of 2020, are another project's, boralyl/pytest-homeassistant, which had the name
  before. 2026.12.0b0 requires Python >= 3.14.2 and pins `homeassistant==2026.12.0b0`, pytest 9.2.0 and
  pytest-asyncio 1.5.0. `homeassistant` 2026.12.0b0 pins 46 requirements exactly and leaves 4 open: certifi, packaging,
  typing-extensions and urllib3.
- **How uv picks pre-releases.** By default (`if-necessary`), uv prefers stable versions and takes a pre-release only
  when no stable version meets a requirement. So an exact pin such as `homeassistant==2026.12.0b0` gets it, also as
  another package's dependency, while a bound such as `pytest-homeassistant>=2026.11.3a0` still gets 2026.11.3.
  `--prerelease allow` drops that preference for every package: each gets its highest version that fits, pre-releases
  included, and `--prerelease-package <name>=allow` does that for one package. `uv pip install --prerelease allow
  pytest-homeassistant` with the other pins of `requirements_test.txt` gets 2026.12.0b0, but also httpx 1.0.dev6 for
  respx, which asks for `httpx>=0.25.0` (Home Assistant itself requires `httpx2`), and grpcio 1.86.0rc1 for
  hass-nabucasa (`grpcio>=1.75.1,<2`), where CI's resolution has 0.28.1 and 1.85.4. Home Assistant's own
  `package_constraints.txt`, which it installs packages with, pins them at 0.28.1 and 1.83.1, httpx "so a dependency
  cannot pull in an untested httpx version alongside httpx2". With `requirements_test.txt` and
  `--overrides` holding `pytest-homeassistant==2026.12.0b0`, in the default mode, the resolution differs from CI's only
  in `pytest-homeassistant` and what it and `homeassistant` pin exactly: 129 packages instead of 130, 11 such pins at
  other versions, and greenlet gone, as SQLAlchemy 2.1.9 needs it only for its `asyncio` extra. `homeassistant` and
  `pytest-homeassistant` are the only pre-releases. An override replaces every requirement on its package, a direct
  one too.
- **The lookup.** `uv pip compile --no-deps` resolves only the packages it is given, and `--python-version` sets the
  exact Python to resolve for, even one that doesn't exist. With `--prerelease allow`, `pytest-homeassistant` gives
  2026.12.0b0 for 3.14.4 and for 3.99, but 0.1.3 for 3.14.1: uv passes over the versions whose requires-python that
  Python doesn't meet, without a word. `uv pip compile -o <file>` prefers the versions that the file already holds
  (`[preference]` in its verbose log) unless given `--upgrade`; its standard output has no such file.
- **setup-uv's cache** (v10.4.0, `src/cache/restore-cache.ts` and `src/save-cache.ts`). The key is
  `setup-uv-2-<arch>-<platform>-<os>-<python>`, then `-py` with `cache-python`, `-` and the hash of the files of
  `cache-dependency-glob`, and `-<cache-suffix>`. It is restored only on an exact match, without restore keys, and a
  job that restored its key doesn't save it ("Cache hit occurred on key …, not saving cache."). So of the jobs that
  share a key, the first to save it after a change of the hashed files fills it for all of them. GitHub restores a
  cache saved on the run's own ref or on the default branch, and for a pull request on its base branch too; a pull
  request's run saves to its merge ref, `refs/pull/<n>/merge`, which only that pull request's runs restore
  (*Dependency caching*). So *Beta*'s runs on `main`, scheduled or by hand, fill the cache that its other runs restore.
- **Scheduled workflows** (*Events that trigger workflows* and *Notifications for workflow runs*). They run on the
  latest commit of the default branch, in UTC unless a `timezone` is given. A scheduled run can be delayed when GitHub
  Actions is busy, as at the start of every hour, and some are dropped; the documentation advises another minute of the
  hour. Notifications of a scheduled workflow's runs go to the user who created it; once another user changes its
  `cron` syntax, to that user, and after it was disabled and enabled again, to whoever enabled it. A user gets them only
  with notifications for Actions turned on, for every run or for failed runs only. In a public repository, scheduled
  workflows are disabled after 60 days without activity in it; a commit that changes the `cron` enables one again.
- **Manual runs and path filters.** `workflow_dispatch` runs a workflow only if its file is on the default branch, and
  GitHub shows *Run workflow* only then; once a workflow has run, the API and gh can dispatch it against any branch or
  tag. *Beta* showed the latter: after its first run, on its pull request, `gh workflow run beta.yml --ref <branch>`
  ran it on that pull request's branch, while `main` didn't have the file. A `pull_request` trigger with `paths` runs
  the workflow when at least one file of the pull request's three-dot diff, between its head and the merge base,
  matches: so every push to a pull request that changes `beta.yml` runs it, as #43's did, and a pull request that
  doesn't change it doesn't. A workflow left out by a path filter leaves its checks pending, which blocks a pull
  request only if a ruleset requires them.

The Marketplace's catalog (decision 31), checked on 2026-11-25 in `hacs/default` (its `checks.yml`, `lint.yml` and
scripts), the guide *Get into the catalog* on contributors.home-assistant.io, and HACS's *Include default repositories*:

- **The catalog** is the repository `hacs/default`, which the Marketplace and HACS read. A repository gets in through a
  pull request that adds `owner/repository` to the list of its type, `integration` here. The pull request comes from
  the owner's account, or from a major contributor's (at least a third of the top contributor's commits), and not from
  an organization's. Each list is a JSON array kept sorted by `str.casefold` (`scripts/is_sorted.py`). The template
  has six checkboxes and three links: the latest Release, and passing runs of the HACS action, without `ignore`, and of
  hassfest. `hacs-bot` closes a pull request without the links. One with a false statement or a review requested is
  closed without notice.
- **Checks.**
  - *Lint* (`lint.yml`): the lists are valid JSON (`jq`), match the schema (every entry `^[\w.-]+/[\w.-]+$`), and are
    sorted.
  - *Check* (`checks.yml`) runs *Preflight* and *Editable PR* (maintainer edits allowed) on every pull request. The
    others run only once it has the label *New default repository*, which `hacs-bot` or a maintainer adds, and whose
    `labeled` event runs *Check* again:
    - *Owner*;
    - *Releases*: at least one GitHub Release;
    - *Removed repository* and *Existing repository*: the lists at `data-v2.hacs.xyz`;
    - *Core domain conflict*: the domains of `next.home-assistant.io/integrations.json`, the upcoming release's;
    - *Hassfest*: the image `ghcr.io/home-assistant/hassfest:latest` on a shallow clone of the default branch, which
      fails on any `[WARNING] [BRAND_IMAGES]`;
    - the *HACS action*.
  - A first-time contributor's runs started without a maintainer's approval.
- **hassfest 2026.12** (2026.12.0b0; home-assistant/core#187957, `packages/hassfest/hassfest/brand_images.py`) checks a
  custom integration's `brand/`:
  - only the eight images that Home Assistant uses, each a PNG;
  - the icons 256×256 and 512×512;
  - an `@2x` image only with its normal one;
  - no image identical to the one that it falls back to.

  For a custom integration it only warns, but the catalog's *Hassfest* fails on those warnings. `latest` was built on
  2026-11-25 at 18:31 UTC, the day 2026.12.0b0 came out.
- **Review.** The HACS documentation says that additions take months. The 200 latest merged ones took a median of 1.6
  days, and 6 at most, while 332 others were open, the oldest since 2026-06-07, so a submission may wait far longer
  than that median. Of the last 60 closed, 6 weren't merged. Of the four of those read:
  - one lacked the links;
  - a maintainer declined three: a second catalog, and two integrations that overlap core's, for devices and
    protocols that core integrations support.

  `hacs-bot`'s comment on a new submission asks not to comment, open another pull request or merge the default branch
  while it waits.

Translated errors and the config entry's functions (decision 36), checked on 2026-11-26 in the installed 2026.11.3, its
frontend 20261028.8 on GitHub, hassfest 2026.11.3 run from core's tag, and the contributors blog:

- **Their text.** A `HomeAssistantError` made with `translation_domain`, `translation_key` and
  `translation_placeholders`, and no message, makes its text when it is first turned into one (`__str__`):
  `async_get_exception_message` takes `component.<domain>.exceptions.<key>.message` from the cached English
  translations, strips the periods at its end, then fills in the placeholders with `str.format`; a text that isn't
  cached gives the key itself. So `{message}` keeps the period that ends the message. Home Assistant caches all of an
  integration's translations, `exceptions` among them, in its language and in English, when it sets the integration
  up.
- **What users see.** A service call through the WebSocket API that raises one logs "Error during service call to
  button.press: <text>", and answers with the text and the translation's domain, key and placeholders. The frontend
  (`callService` in `src/state/connection-mixin.ts`) shows a toast for 10 s: the translation in the user's language
  with the placeholders, or else "Failed to perform the action button/press." and the text.
- **hassfest**'s strings schema allows a top-level `exceptions`: slugs, each with an optional `message`, a translation
  value (no HTML, URLs, leading or trailing spaces or placeholders in single quotes, and placeholders that are
  identifiers). Run from core's tag (`script/hassfest`, every integration plugin but `requirements`, which needs
  tqdm), it passed the integration, and refused a copy whose exception had a key that isn't a slug, a `title`, or
  `'{message}'`.
- **The entry's functions.** `_async_process_entry_method_result` in `config_entries.py` takes None from
  `async_setup_entry`, `async_unload_entry` and `async_migrate_entry` as success. A bool goes to `report_usage`, which
  ignores a custom integration's: True succeeds, and False raises `ConfigEntryError` with `async_setup_entry_failed`
  ("Failed to set up the config entry."), `async_unload_entry_failed` ("Failed to unload the config entry.") or
  `async_migrate_entry_failed`, in the `homeassistant` domain; a core integration's bool is an error.
  `async_unload_platforms_or_raise(entry, platforms)` raises `ConfigEntryError` with `unload_platforms_failed` ("Failed
  to unload one or more platforms.") when `async_unload_platforms` gives False. A failed unload leaves the entry
  `FAILED_UNLOAD`, with the error's text and translation as its reason, and runs none of its unload callbacks. The
  contributors blog of 2026-10-23, *Config entry functions now return None*, makes None the rule for core integrations
  from 2026.11.0, and lets custom integrations keep returning a bool, without a deprecation.

## Decisions

| # | Decision | Why |
|---|----------|-----|
| 1 | A Python custom integration, domain `sonoff2hue`, for Home Assistant 2026.11 and later only (`hacs.json` `homeassistant` 2026.11.0, Python 3.14), with the product rules that Behavior states | The user asked for an integration like SonoffLAN, for 2026.11 and later. Integrations run in Home Assistant's own Python process |
| 2 | A public repository of its own, `abpostelnicu/sonoff2hue-homeassistant`, under the Apache-2.0 license | The Marketplace installs only from public repositories with one integration. Apache-2.0 is Home Assistant core's license; the user chose it |
| 3 | `engine/` holds the engine, with no Home Assistant imports, which `tests/test_own_mdns.py` checks. Its rule for edge cases was retired by decision 30 | Keeps the engine testable without Home Assistant, module by module |
| 4 | Hue directly over CLIP v2, with its own link-button pairing, the pinned certificate (`aiohttp.Fingerprint`), and an aiohttp connector of its own that keeps idle connections 5 minutes and resolves names with the system's resolver (`aiohttp.ThreadedResolver`, decision 30) | The user chose direct control. Home Assistant's shared connector would close the connection between two keep-alives (15 s against 45 s), and a warm connection keeps a toggle quick. Home Assistant's shared session answers `.local` names through its zeroconf, which the integration doesn't rely on |
| 5 | Home Assistant's own UI, as the user chose. A single config entry for the one bridge: its setup flow finds or takes the bridge's address, then pairs it, or imports a `sonoff2hue.yaml`; reconfigure pairs again with the switches kept, and reauth runs after the key is rejected. One config subentry of type `switch` per MINIR4M, with identify-by-press. Entities: Status, Bridge reachable (named Reachable, decision 25) and eWeLink devices seen on the bridge's device; Press (event), Last action, Online and Test toggle on each switch's device. Repairs for problems, the logbook for presses and their outcomes (decision 26), and redacted diagnostics. A switch's `lights` and `groups` hold a resource's name if it is unique, else its id, with `room:`/`zone:` prefixes for rooms and zones, as a `sonoff2hue.yaml` holds them | Everything the integration shows or does has a native place in Home Assistant, whose users and HTTPS protect it. One encoding makes imported and new switches resolve alike |
| 6 | Pinned tools: `pytest-homeassistant` at the Home Assistant version (2026.11.3), pytest-cov, ruff (lint and format, lines of up to 120 characters) and mypy (strict, with `types-PyYAML`), installed by uv. `scripts/check` runs them all, and CI and `test-each-commit.sh` run that | pytest-homeassistant-custom-component no longer works with 2026.11. One script keeps local checks, per-commit checks and CI the same |
| 7 | Retired by decision 30. The integration's behavior is tested with hand-written pytest cases, and the mDNS layer's recorded cases (`tests/engine/mdns_packets.json`, `mdns_text.json`) are its own test data | Decision 30 |
| 8 | The listener is `engine/mdns.py`, on asyncio. It owns its UDP 5353 socket (SO_REUSEADDR, SO_REUSEPORT, TTL 255, loopback), joins 224.0.0.251 on the interfaces that are up, multicast-capable and not loopback (`interface_states`: SIOCGIFFLAGS, SIOCGIFHWADDR and ETHTOOL_GLINK ioctls, with the IPv4 addresses from `ifaddr`), and watches IPv4 address changes on a netlink socket of its own, debounced 3 s, with polling outside Linux. One listener per config entry serves both the LAN monitor and the runner: each adds a packet handler and a started callback, which runs at once if the socket is open, as if each had a listener of its own. A started callback starts eagerly and runs until it first waits, so that its user resets its tracker before the socket's first packet. Removing a started callback is awaited: it also cancels the callback if it is running and waits for it, so the runner can stop and start again, as the supervisor restarts it, while the listener and the LAN monitor go on. Sockets are behind a small transport interface, so the tests drive joins, errors and datagrams without the network | No mDNS library: the engine needs the raw TXT updates and the probes, which python-zeroconf's service browsing hides. One socket serves both users, and logs a socket error once. Whether it binds UDP 5353 next to Home Assistant's own zeroconf on Home Assistant OS was the spike's question; it does (decision 19) |
| 9 | Retired by decision 30. Unexpected failures are ordinary Python exceptions: a malformed answer from the bridge is a transient `HueApiError` and a problem in a pasted file a `ConfigError`, and the flows catch `Exception` and log the traceback where they show their `unexpected` text | Decision 30 |
| 10 | Retired by decision 30. `hue_address.py` reads the bridge address with `ipaddress` and yarl, with a check of a host name's labels (Behavior, Bridge address) | Decision 30 |
| 11 | The engine's `pair` asks for a key every 2 s until 60 s have passed, reports each attempt that didn't pair (`on_attempt`), and ends with "The link button was not pressed within 60 seconds. Try again." | The setup flow shows how the pairing goes while it waits (decision 22), and the message says what to do in a dialog |
| 12 | `tools/fake_hue` is a fake Hue bridge with fixed ids, the endpoints, answers and messages of the Hue API calls that sonoff2hue makes, the 30 s link-button window, request and PUT logs, and its settings, and with `Fault`s (a status, body or delay for matching requests) for tests. `tests/hue.py` runs it on 127.0.0.1 over HTTPS with a certificate made at every start, and a manual clock | The client is tested over real HTTPS with a pinned certificate. Development and end-to-end CI need the same bridge without hardware |
| 13 | From 2026-11-24 the remaining work runs in parallel, one agent per task: each works in its own git worktree, `.worktrees/<task>` (in `.git/info/exclude`), on its own branch from `main`, and opens a pull request without merging it. The coordinating session reviews the pull requests, merges them by fast-forwarding `main` one at a time, and asks the others to rebase. Decision numbers are reserved per task: 14 the runner, 15 the YAML import, 16 the Home Assistant skeleton, 17 the LAN monitor and device registry, 18 development tooling, 19 the mDNS spike; then 20 the switch rules, 21 the bridge supervisor and the status tracker, 22 the setup flow, and the next free numbers for the tasks that follow, as the coordinator assigns them. In shared files each agent changes only its own lines (its table rows, its verification entry, its status item). The work of decision 30 ran the same way | The user asked to split the work across agents working in parallel. Separate worktrees let them build, test and commit at the same time without picking up each other's edits |
| 14 | `engine/runner.py` is the runner, on asyncio. Its observer is a base class, `RunnerObserver`, whose methods do nothing; one that raises is logged and ignored. The runner opens no socket: `run(listener)` adds a packet handler and a started callback to the config entry's `MdnsListener` (decision 8) and sends the keep-alive at once. When cancelled, it removes the handler and cancels the keep-alive and the toggles first, then awaits the started callback's remover and them, so that everything is told to stop before anything is awaited. Toggles are tasks behind a per-switch `asyncio.Lock`, which queues them in the order of the presses. `stop_toggles()` cancels them, and `when_toggles_complete()` waits with `asyncio.wait`, so cancelling the wait doesn't stop them. The age of a press is measured with an injected clock (`datetime.now(UTC)` by default), the time a toggle took with `time.perf_counter`. `resolve_bindings` takes a logger, the supervisor's. Toggles and the keep-alive start eagerly (`eager_start=True`), and a delay of 0 isn't awaited: `handle_packet` drops a stale press, or finishes a toggle that needs no I/O, before it handles the packet's next event, and `run` sends its first ping before it first waits. The listener starts its started callbacks the same way: a (re)started socket resets the trackers before its first packet is handled, and the runner, the LAN monitor and Hue discovery send their first query at once. The runner also reports a rejected key, for Home Assistant's reauthentication (decision 21): `RunnerObserver.on_key_rejected()`, which does nothing by default, comes after each keep-alive request or toggle that a 401 or 403 ends, right after the reachability report. Two switches with one device id are a `ValueError` (`bindings_by_device_id`), which `validate_for_run` prevents | The listener is shared, and the runner has to stop and start again, as the supervisor restarts it, while the LAN monitor goes on. `asyncio.gather` would cancel the toggles when its caller is cancelled. The injected clock tests the 5 s rule without waiting. A task started on the event loop's next turn put a toggle's lines after those of the rest of its packet, and let a broadcast that arrived right after a socket restart be judged against the old baseline and toggle the lights |
| 15 | Retired by decision 30. `yaml_import.parse(text)` reads a pasted `sonoff2hue.yaml` with PyYAML and a probatio schema, and raises `ConfigError` with the problem's line and column (Behavior, YAML import) | Decision 30 |
| 16 | The Home Assistant skeleton. `manifest.json`: domain and name `sonoff2hue`, version 1.0.0, `codeowners` @abpostelnicu, `config_flow`, `single_config_entry`, `integration_type` hub, `iot_class` local_push, `categories` `plugs_switches` (the MINIR4Ms, the primary one) and `lighting.lighting_systems` (Hue, as the core `hue` integration has it), `connectivity` `network`, and this repository's `documentation` and `issue_tracker`; no `requirements`, `dependencies` or `after_dependencies`. `__init__.py` sets up and unloads the entry and declares `type Sonoff2HueConfigEntry = ConfigEntry[Sonoff2HueData]`; at first `Sonoff2HueData` was an empty placeholder, until the runtime (decision 23), and `config_flow.py` one `user` step, until the setup flow (decision 22). `translations/en.json` holds every string; `icons.json` came with the entities (decision 25). `brand/icon.png` and `icon@2x.png` are the project's icon, an SVG of a full-bleed rounded square, rendered by resvg (resvg-py 0.5.0) at 256 and 512 pixels and compressed losslessly by oxipng (pyoxipng 9.1.1, level 6) as interlaced 8-bit RGBA, in a throwaway environment outside the repository. `.github/workflows/validate.yml` runs *hassfest* (`home-assistant/actions/hassfest@master`) and *HACS* (`hacs/action@main`, category `integration`, nothing ignored) on pushes to `main`, on pull requests and on demand. The package imports Home Assistant at module level, as integrations do, so importing an engine module imports Home Assistant first (0.3 s, 1.3 s cold); the tests and the tools already run in `.venv`, which has it | It installs and loads in Home Assistant 2026.11 and passes hassfest and the HACS action, which the Marketplace requires (Research findings). Each action runs its validator's current image, so the reference their documentation gives is used; a tag would pin only the wrapper. aiohttp, cryptography, PyYAML, ifaddr and yarl come with Home Assistant itself, and hassfest rejects such a requirement; the engine calls neither the `network` nor the `zeroconf` integration, which Home Assistant sets up before any config entry anyway. A hub: one bridge, and the switches as its subentries. local_push: presses arrive as broadcasts. The categories and connectivity place it in the Marketplace and the integration list. Since 2026.3 a custom integration ships its own brand images, which the brands repository no longer takes; these show the project's own icon, prepared as the image specification asks, with no Home Assistant branding. The engine itself still imports nothing from Home Assistant (decision 3) |
| 17 | The LAN view is three engine modules (Layout). `registry.py`'s `DeviceRegistry` keys and orders devices by their casefolded ids (decision 30), takes an injected clock that returns an aware UTC datetime, and reports changes to lists of callbacks, each added with a method that returns its remover; they run in order, and one that raises stops the rest and reaches the caller. `lan_monitor.py`'s `LanMonitor` runs the config entry's `MdnsListener` (decision 8), with its packet handler and started callback on it: the registry is `listening` while the listener runs, also while its socket retries, and an unexpected stop of the listener is logged and retried 10 s later. The runner attaches to the same listener and leaves it while the LAN monitor goes on. The LAN monitor reads the `EngineConfig` it takes only in its tracker, for the device keys that decrypt the payloads (relay and setting changes, the summary, detached mode, decode errors), and in its catch-up queries, for the switches (decision 36); `config_changed` makes a fresh tracker and takes the new switches. The listener logs each line once. `activity.py`'s `ActivityBuffer` is a `logging.Handler` that keeps the last 1,000 entries (a `collections.deque`) and answers `get(after, limit)`. An entry has its level's name, the logger's name after its last dot as its category, the record's time in UTC, and the exception as "Type: message", with its Python class name. A failure while it records an entry is reported as `logging` reports a failing handler, never to the code that logs. Which loggers and levels feed the buffer is the Home Assistant layer's choice (decision 23) | Home Assistant's entities, the status tracker and the switch flow's identify-by-press follow the registry. A clock and callbacks keep Home Assistant out of the engine and let its tests set the time. The LAN monitor is the listener's only user that runs as long as the integration, so it runs and restarts the listener, while the runner comes and goes with the supervisor. The integration logs no secrets, so every line can go to the buffer, which the diagnostics show |
| 18 | Local development and debugging. `scripts/develop` runs Home Assistant from `.dev/venv`, an environment of its own that uv makes from `requirements_dev.txt`: `requirements_test.txt`, for the same release, and the 12 packages that 2026.11.3 installs at its first start with the development configuration, pinned as its manifests require them and installed with its `package_constraints.txt`; `tests/tools/test_develop.py` checks the pins. Home Assistant runs with `--skip-pip-packages pymicro-vad,pyspeex-noise`, from the repository root as `python -m homeassistant`, so the root is first on `sys.path` and `custom_components.sonoff2hue` is the checkout's own, without a symlink or `PYTHONPATH`. `tools/develop` makes its configuration folder, `.dev/config`: `configuration.yaml` (no `default_config`; the logbook; `custom_components.sonoff2hue` at debug) and the HTTP store, written with the http integration's own schema, on 127.0.0.1 and port 8123 or `SONOFF2HUE_DEV_PORT`; a port in use stops it. `scripts/develop` runs the fake Hue bridge in the background (link button pressed, log in `.dev/fake-hue.log`) unless one of them is asked for alone, starts Home Assistant again after exit code 100, and has `press` and `lights`. The simulated wall switch is `tools/send-ewelink-announcements.py`, a script on the standard library that ruff and mypy check (decision 30); `press` sends the last seq again, then the next (`.dev/seq`), for the made-up device 1000abcdef, with TTL 0 on the default route's interface (`0.0.0.0`). `.vscode/launch.json` has debugpy configurations for Home Assistant (`.dev/venv`, `preLaunchTask` `scripts/develop setup`, Just My Code off, `onTerminate: KeyboardInterrupt`, Home Assistant's page opened at "Now listening on port") and the fake Hue bridge (`.venv`, 127.0.0.1:18443, link button pressed), and a compound of both; `tasks.json` runs `scripts/develop`; `extensions.json` recommends the Python, Python Debugger, Ruff and Mypy Type Checker extensions | The user asked for a real Home Assistant 2026.11 with the checkout's integration, the fake bridge and a simulated switch, debugged in VS Code with breakpoints, on Linux (WSL 2 included) and macOS, without hardware. An environment of its own keeps the test environment as CI's, and away from what Home Assistant installs while it runs. Its first-start packages, installed up front, make every start take seconds without installing anything, where Home Assistant would install them itself and retry the two that can't build at every start. Home Assistant 2026.11 keeps its HTTP settings only in that store (an `http:` section becomes a trial that it reverts, and a repair), and 127.0.0.1 keeps a development instance and its open onboarding off the LAN; checking the port first avoids Home Assistant's recovery mode. Running from the root keeps the files that run the checkout's own, so breakpoints, frames and tracebacks name them; through a symlink, breakpoints are hit but frames name the link. That the configuration folder's `custom_components` isn't searched then doesn't matter for developing this integration. The graceful Stop lets Home Assistant save its storage |
| 19 | The listener keeps its own UDP 5353 socket (decision 8), with its choice of interfaces: the mDNS spike confirmed it on the user's Home Assistant OS on 2026-11-25, so the fallback, a passive listener on Home Assistant's zeroconf, isn't needed. On Home Assistant OS the socket also joins Docker's bridges, `docker0` and `hassio`; on `hassio`, the Supervisor's multicast plugin repeats every packet from the LAN, and the tracker takes the copies as duplicates. On Linux the socket is bound to the group, 224.0.0.251, instead of 0.0.0.0, so that it receives multicast only; elsewhere it stays on 0.0.0.0, as macOS can't send from a socket bound to the group. A broadcast without SRV and A records gives its sender as the device's address only when the sender isn't one of this host's own IPv4 addresses, which the listener reads with the interfaces at each (re)start (`MdnsPacket.from_this_host`); otherwise the address is unknown, and the LAN registry keeps the one it knew. Hue bridge discovery follows the same rule: such an answer without an A record lists no bridge, so the `hassio` bridge's address is never offered as a Hue bridge. The spike was a throwaway custom integration, `sonoff2hue_spike`, on the branch `tmp/haos-spike`, which never reached `main` and was deleted afterwards; it ran the engine's own modules, copied unchanged. It had to be started from configuration.yaml: it was installed over SSH, in the Terminal & SSH app, which lacks `homeassistant_api`, so that app can't add a config entry through the Supervisor's proxy to Core's API | On Home Assistant OS 18.4 with Core 2026.11.3, the socket bound at the first attempt next to Home Assistant's 3 zeroconf sockets and 10 of other processes, and in 3 minutes received the broadcasts of all 21 eWeLink devices and each of the 5 presses of the user's switch as a new `seq`, with no malformed packet, socket error or network change. Home Assistant's zeroconf kept receiving, with the same 21 devices, unicast replies still reach it, and SonoffLAN, which listens through it, wrote its entities' state within 5 s after every press. A listener on Home Assistant's zeroconf would also lose the probes, which reset a device's baseline: python-zeroconf hands its record listeners only the records of responses. Bound to 0.0.0.0 with SO_REUSEPORT, the engine's socket would share the port's unicast with python-zeroconf's socket on 0.0.0.0:5353, which Home Assistant opens when only its default adapter is enabled and it has no IPv6: Linux spreads unicast over the sockets bound to the same address by a hash of the sender, so the engine would take replies meant for Home Assistant. Bound to the group, it gets every multicast datagram and no unicast, and its queries still go out from the address of their interface (Research findings). The multicast plugin's copy of a press carries no address records, so taking its sender made the `hassio` bridge's address the switch's address in the LAN registry after each press. A packet from one of this host's own addresses never tells where a device is: it is a relay's copy, or this host's own, such as the simulated switch's, whose A record still gives the address |
| 20 | The switch rules, without Home Assistant imports, which the switch subentry flow shows (decision 24). Their recorded cases and their own string rules were retired by decision 30, which gave the resolver, the picker and the rules one rule for names, `strip()` and `casefold()`. `picker.py` resolves a switch's `lights` and `groups` entries against the bridge's resources (`targets.refers_to`), keeps the picker's selection and saves it, comparing entries by identity. `switch_rules.py` holds the rules a switch meets before it is saved: its validation, adding and changing a switch, the check against the bridge, and the picker's resources, ordered by the casefolded name. The check against the bridge uses `targets.resolve`, a parameter that tests can replace, and takes the bridge as an async context manager (`connect`), so that the flow can pass a `HueClient`. The message about a malformed device id names the made-up `1000abcdef`. `switch_form.py` is the form's own logic: its checks before the rules, the input it gives them, the key's modes and warning, identify-by-press, and how the rules' errors map to its fields (`same_id`, `switch_label`, `field_errors`) | The switches are stored and checked by one set of rules, which the dialogs, the import and the runtime share, and which stays testable without Home Assistant. This repository is public, so its examples are made up |
| 21 | The bridge supervisor and the status tracker are `engine/supervisor.py` and `engine/status.py` (Layout). `BridgeSupervisor(config, listener, status, *, hue_clients, on_key_rejected, observer, clock)` runs one `EngineConfig` until it is cancelled; for each change of the configuration, the runtime cancels it and starts one for the new configuration (decision 23). Its `config_changed()` logs "Configuration changed; restarting the bridge." while it runs, but not during the 30 s wait after an unexpected stop; the runtime calls it from the entry's update listener. `hue_clients` is a `HueClientFactory`. The runner runs on the shared listener with the supervisor's clock: stopping the supervisor stops the runner and closes the client while the listener goes on, and a failing listener is the LAN monitor's to restart (decision 17). A rejected key is one callback for the runtime, `on_key_rejected`, which starts Home Assistant's reauthentication. It covers both cases: the supervisor stopping in the error state on a 401 or 403, and, while the runner runs, each keep-alive request or toggle that a 401 or 403 ends (decision 14), where the bridge goes on running and is shown as unreachable. It runs on the event loop (`call_soon`), so one that raises reaches the loop's exception handler instead of the supervisor or the runner. The runner's observer is a forwarder in the supervisor, `_Observer`, which passes every report to the tracker, then to `observer` (decision 23), and a rejected key to the hook as well; a test checks that it forwards each `RunnerObserver` method. The messages that say how to pair the bridge name the reconfigure flow, which pairs again (decision 5), with `PAIR_AGAIN`: "The Hue bridge no longer accepts sonoff2hue's application key. Reconfigure the sonoff2hue integration to pair the bridge again.", the warning "hue.certificateSha256 is not set, so the bridge certificate is not verified. Reconfigure the sonoff2hue integration to pair the bridge again and pin it.", and "Pair the Hue bridge to get started: reconfigure the sonoff2hue integration.", which Home Assistant's flows give no reason to show. "Add a switch: pick a MINIR4M and the Hue lights it should toggle." is the switch subentry's own action. The `StatusTracker(config, registry, clock)` is the runner's observer; its listeners get a `StatusSnapshot`: the start time, the service, the Hue bridge, the LAN and each switch. `ServiceState`'s values are the status sensor's options: `not_configured`, `invalid_config`, `connecting`, `running` and `error`. Listeners get one snapshot 500 ms after the first change, with every change until then (`loop.call_later`), and one that raises stops the rest and reaches the loop's exception handler. `close()` drops a pending snapshot and leaves the registry. `config_changed(config)` takes a new configuration, for a runtime that keeps the tracker and its registry; a tracker made at each setup starts connecting, with "Starting.", and without last actions and errors. It is used on the event loop only, so it has no lock, and an injected clock gives the time | Each run checks the whole configuration from the start, so its states and messages are always those of the configuration it runs; only the update listener knows that a change caused it. A callback is explicit and comes at once, and Home Assistant's `async_start_reauth` does nothing while a reauth or reconfigure flow of the entry runs, so the hook may come again, after every refused request and after a restart; a state to watch would be known only by its message, and would come 500 ms late. The messages say how to pair the bridge in Home Assistant. The supervisor and the runner report one event in several calls, such as the bridge's reachability and then the state, and the debounce hands the entities one consistent snapshot of them, while it keeps Home Assistant's state writes, which the recorder stores, to two a second. The sensor's options double as its translation keys |
| 22 | The setup flow, reconfigure and reauth (`config_flow.py`), which replaced the skeleton's placeholder. **Steps:** `user` is a menu, *Find bridges*, *Enter the bridge address* or *Import sonoff2hue.yaml* (`SETUP_MENU`), and `reconfigure` the first two (`PAIR_MENU`). `discover` is a progress step that runs the engine's `discover` for 5 s, with Home Assistant's shared aiohttp session for the cloud. It leads to `pick`, a list of `IP — bridge id — source` (leaving out a missing id) and *Another address*, which preselects the first bridge, or when reconfiguring the paired one if it was found again (by bridge id, without regard to case); to `no_bridges`, with *Search again* or the address; or to `discovery_failed`, with the error. `address` strips the input and checks it with `hue_address`, whose message goes on the field; when reconfiguring it suggests the stored address. Then the bridge is read (`get_bridge_info`): `cannot_connect` for a `HueApiError`, `invalid_address` for a `ConfigError`, `unexpected` for any other exception, which is logged with its traceback. The flow's unique id becomes the bridge id, so another flow with it is aborted (`already_in_progress`), and reauth aborts with `wrong_bridge` for another bridge id. `link` is a progress step with the bridge (name, model, bridge id, firmware, address) and the certificate to pin, and "Press the round link button on top of the bridge now. Waiting up to 60 seconds..."; it runs the engine's `pair` and logs how it goes. It ends in `paired`, `link_timeout` (`PairingTimeoutError`'s message) or `link_failed` (the message of a `HueApiError` or `ConfigError`, else "Pairing failed: …"); these two menus offer *Retry*, which reads the bridge at the same address again, and *Choose another bridge* (the first menu), and in reauth only *Retry*. `reauth` asks for a confirmation (`reauth_confirm`) before it reads the stored bridge and links. **Data:** the entry's `data` is `bridge_ip` (the address entered or chosen, stripped), `bridge_id`, `app_key` and `certificate_sha256`, the field names of the engine's `HueSettings` (`CONF_*` in `const.py`); its unique id is the bridge id and its title `sonoff2hue` (`ENTRY_TITLE`). A switch is a subentry of type `switch` (`SUBENTRY_SWITCH`): its unique id is `SwitchSettings.normalized_device_id`, its title its name, or that id without one, and its `data` holds `device_id`, `name`, `device_key`, `lights` and `groups` (`SwitchSettings`' names) with the values as given. Reconfigure and reauth save with `async_update_and_abort` (decision 23), reconfigure with the new unique id, so the switches stay. **Messages:** errors, aborts, menus and progress texts are translation keys, and a message from the engine is its exact text in the placeholder `{error}`. **Progress:** the text changes whenever an attempt's outcome does, by `progress_action` (`link` or `link_retrying`) and `async_notify_flow_changed()`: "The bridge did not answer (…); still trying." while the bridge doesn't answer, nothing more while it waits for the button. There is no countdown. Tasks start after their step returns (`eager_start=False`). **Resources:** `async_remove` cancels the search and the pairing, whose connections and mDNS socket close as they stop, and a flow closed while the bridge is read starts no pairing (`UnknownFlow`). **Choices:** closing the dialog stops the pairing; a second flow for the same bridge is aborted, and the flow that adds the entry aborts the others; deleting the entry deletes the switches too, and reconfigure replaces the bridge; the paired bridge is preselected; an unexpected failure shows its own message; the search's hint says "the same network as Home Assistant"; the timeout's log line ends "Start the pairing again in the sonoff2hue setup." **Import** (2026-11-25): the menu's third way, *Import sonoff2hue.yaml*, is `import_yaml`, a multi-line text field. `yaml_import.parse` reads the text, then the run's checks follow (`validate_for_run`), with their messages, lines and columns, under the field (`invalid_config`); any other exception says "The file can't be read: …" (`unreadable_config`) and is logged with its traceback. With the bridge's `bridgeIp` and `appKey` (`is_paired`), `import_check` connects as the supervisor does when it starts, once and without its retries: the supervisor's client (`create_hue_client`, with the file's key and pin) and its first keep-alive (`ping`). The bridge id always comes from the bridge (`get_bridge_info`), as it is the unique id, which reauth and reconfigure compare with the bridge's: the entry stores the bridge's spelling, and when the file's `bridgeId` is another id, other than in letter case, the new entry's text says so, next to the note about a missing pin. The entry gets the file's other `hue` values as written, and one `switch` subentry per switch with its values as written, the normalized device id as the unique id and the stripped name, or that id, as the title. Lights, rooms and zones aren't resolved then. A failure leads to `import_failed`: *Try again*, *Pair the bridge again and keep the switches* (`import_pair`), or *Paste the file again*; its message says "pair the bridge again" where the engine's says to reconfigure the integration, which doesn't exist yet. Without `bridgeIp` and `appKey`, the run's checks see stand-ins for them, so that only the switches' remain, and `import_pair` leads to the search or the address (which suggests the file's `bridgeIp`) and to `link`; `paired` then adds the switches, and a failed link's *Choose another bridge* goes back to `import_pair`. A file without a pin is imported: the entry keeps none, the runtime warns at every start (`NOT_PINNED`), and the new entry's text says so. Secrets: the pasted text is never shown back, not even after an error; the dialogs name the switches by name or device id; and `async_on_create_entry` drops `subentries` from the answer to the frontend, which would hold their device keys (Research findings) | The flow pairs the bridge in Home Assistant's own dialogs, with the engine's rules and messages. The title: the entry stands for the whole setup, one bridge and its switches, and there is only one; an imported `sonoff2hue.yaml` names no bridge (`HueSettings` has no name), and its import doesn't read the bridge's name; and reconfigure can move it to another bridge without renaming it or overwriting a title the user chose. The bridge's name, model and firmware are shown while linking, and belong to the bridge's device. Reauth asks first because Home Assistant runs its first step as soon as the runtime starts it, and the 60 seconds must run while the user is at the bridge. A progress step can't count down without a timer of its own, and `async_update_progress` shows a percentage, which reads as work done rather than time left. A step still reading the bridge when its flow is removed would start a pairing that Home Assistant never cancels, and an eager task would run, or end, inside the step that starts it. The import refuses what the runtime couldn't run, with messages that say where. The supervisor resolves the names at every start and reports what it doesn't find, so the import leaves them to it, and the switch dialogs can fix them, where a pasted text can't be edited. A pin can be added later by reconfiguring, while refusing the file would lose its switches |
| 23 | The config entry's runtime is `runtime.py` (Layout): `Sonoff2HueData`, a dataclass, is `entry.runtime_data`. **What lives as long as the entry:** the mDNS listener, which the LAN monitor runs; the device registry (`registry`); the status tracker (`status`); and the activity buffer (`activity`), a handler on `custom_components.sonoff2hue`, which records what the integration logs at Home Assistant's configured levels: warnings by default, info and debug lines with "Enable debug logging" or `logger:`. The runtime never changes a log level (the coordinator's decision of 2026-11-25). The bridge supervisor runs `config` (`config_from_entry`: `HueSettings` from the entry's data, and one `SwitchSettings` per `switch` subentry, in the subentries' order, with the values as stored) with the status tracker, and with `observer`, an optional parameter of `BridgeSupervisor`: its forwarder passes each of the runner's reports to the tracker, then to the observer, which fires the bus events. The engine names the two messages that the runtime uses, `CERTIFICATE_MISMATCH` and `PRESS_DROPPED`. **Changes:** `async_setup_entry` adds an update listener, which Home Assistant calls after any change of the entry and its subentries. When the configuration differs, the running supervisor logs "Configuration changed; restarting the bridge." and is cancelled, the LAN monitor and the status take the new configuration (`config_changed`), and a new supervisor starts once the cancelled ones have ended; a change of a title alone restarts nothing. Nothing reloads the entry: reconfigure and reauth save with `async_update_and_abort`, and subentries are added, changed and removed with Home Assistant's own API. So the listener's socket, the registry, the status with each switch's last action and error, and the activity stay; a reload by the user, or Home Assistant's restart, starts them afresh. **Signals**, each formatted with the entry's id: after a restart `SIGNAL_CONFIG_CHANGED` (the new `EngineConfig`), then per `switch` subentry `SIGNAL_SUBENTRY_REMOVED` (its id), `SIGNAL_SUBENTRY_UPDATED` (data, title or unique id) or `SIGNAL_SUBENTRY_ADDED`; and `SIGNAL_STATUS` with each snapshot that the tracker publishes. A platform adds the entities of the subentries that exist in its `async_setup_entry`, and those of an added one on its signal, with `config_subentry_id`; Home Assistant removes a removed one's devices and entities itself. The registry's own callbacks serve identify by press: a device whose `last_event` is a press, with a new `last_press_at`. **Tasks:** the LAN monitor and the supervisors are Home Assistant's background tasks, not the entry's, and each is cancelled once. `async_stop`, an `async_on_unload` callback, so also after a failed setup, closes the status, takes the activity buffer off the logger, stops the supervisors, then the LAN monitor, and deletes the issues; at Home Assistant's stop, which cancels the tasks itself, the runtime closes the status and takes the buffer off. **Reauth:** `on_key_rejected` calls `entry.async_start_reauth`. **Bus events** (`const.py`): `sonoff2hue_press` with `seq`, and one `sonoff2hue_toggle` per press, with `targets` and `result`: `toggled`, with `turned_on`, `changed`, `unchanged`, `failed` and `elapsed_ms`; `failed`, when the toggle couldn't run, or `dropped`, when the press waited more than 5 s, with `message`. Both name the switch: `device_id`, the Home Assistant device linked to its subentry, or None while it has none; `switch_id`, its normalized device id; and `name`, as configured. **Repairs**, linked to the entry and created and deleted from each published snapshot and at the start: per switch `not_detached_<id>` and `decode_error_<id>` (warnings, in the runner's words), `invalid_config` (an error: the supervisor's message and problems, titled "The configuration has problems"), `certificate_not_pinned` (a warning) and `certificate_changed` (an error, while the bridge's last reachability message is `CERTIFICATE_MISMATCH`). The two certificate issues are fixable: `repairs.py`'s flow confirms, then aborts with the integration's reconfigure flow as `next_flow`, which the frontend opens, and the issue stays until the runtime sees the new pin. **Switch test:** `async_test_toggle(device_id)` toggles the switch's targets once and returns a `SwitchTestResult`, or raises `SwitchTestError` with its message and the targets' `problems`; the button turns it into a `HomeAssistantError`. **Tests** replace `runtime.new_listener` and `runtime.create_hue_client` (the `lan` and `bridge` fixtures) | Only the supervisor needs the new configuration; a reload would close the mDNS socket, start the LAN view afresh and drop the switches' last actions and errors. Home Assistant calls the update listener for every change of the entry and its subentries, and with one, `async_update_reload_and_abort` logs a report that this breaks in 2026.12.0, and still reloads. The new supervisor waits for the old one, so that they never run side by side, and a task is cancelled only once: the second cancellation, which the entry's unload gives its own background tasks, cut short a supervisor that closed its Hue client, as a test showed. Signals keep the platforms apart from the runtime's objects, and a subentry added later needs its entities without a reload. An observer next to the tracker keeps the tracker as it was. The logbook matches events to a device by `device_id` in their data, so the switch's device has that key and the eWeLink id another, and one toggle event per press, with its result, is one event to follow in automations. Issues linked to the entry go with it and hide while it is disabled. A repair flow that finished would delete its issue before the bridge is paired again, so it aborts, and `next_flow` opens the reconfigure flow, which pairs again and pins the certificate; the other problems are fixed in the eWeLink app, in a switch's settings or on the bridge, which no repair flow can do yet |
| 24 | The switch subentry flow (`switch_flow.py`, `SwitchSubentryFlow`), which the setup flow declares for the type `switch` (`async_get_supported_subentry_types`), so the integration's page has *Add switch* and each switch *Edit switch*; Home Assistant's own UI deletes one. It shows the engine's switch rules (decision 20) in Home Assistant's dialogs. **Device:** `user` is a menu, *Press the wall switch* (`identify`), *Pick a device seen on the LAN* (`pick`) or *Enter the device ID* (`manual`); `reconfigure` puts *Keep device …* (`keep`) first. Both abort with `entry_not_loaded` unless the entry is loaded. `identify` is a progress step whose task follows the LAN registry's changed callbacks for up to 60 s (`IDENTIFY_TIMEOUT`), and the entry's state (`async_on_state_change`), so that a reload's registry is followed too. A device event is a press when its last event is a press, at a time the device didn't have. `switch_form.identify` then selects the device, whose message heads the next form, or names the switch that uses it in the progress text (`identify_used`) and goes on waiting. After 60 s, `identify_timeout` offers the ways again. `pick` lists the registry's devices (radio buttons), unused first, the latest press first, then by id as the registry sorts them, each labelled with its id, address, type, *Online* or *Offline*, its last press as a relative time ("just now", "12 s ago", "3 min ago", "2 h ago", "yesterday 21:04" or a date) in Home Assistant's time zone, and the switch that uses it; `no_devices` when there are none. `manual` is a text field. The three check the device by the form's rule (`form_problems`' message on the field), and a device that identify selects but the form refuses goes to `manual` with the message. **Settings:** `settings` lists the bridge's lights, rooms and zones from a fresh client of the runtime's factory on the entry's data (`load_resources`) in three multi-selects, `lights`, `rooms` and `zones`, in the mode Home Assistant chooses by their number (checkboxes, or chips with a searchable picker from 6), labelled with the name, else the id; the id too beside a repeated name; a light's on or off; a room's or zone's "no lights". Their values are positions in the bridge's lists. The switch's entries are chosen with `create_selection`, those that don't resolve are chips (`unresolved`) kept until removed, and the choices are saved with `selection_to_config`. The device key is a collapsed *Advanced* section: *Keep it*, *Replace it* or *Remove it* (`key_mode`) and *New device key*, shown only to replace the key (a `visible` condition), for a switch with a key, else an optional key; the key warning is the mode's description, and the section opens with it or with a key error. When the resources can't be listed, `pair_first` (409) or `lights_unavailable` (502, with the bridge's message) offers *Try again* or *Enter them by hand*: the form with the text fields `light_names` and `group_names`, filled with `join_list` and read with `split_list`. **Saving:** the form's checks (`form_input`, `form_problems`), then `create_switch` or `update_switch` with the entry's switches and a fresh client. Messages go to their fields (`field_errors`): the name, the key's section, the typed fields; the rest (the device, the picker's lights, rooms and zones, keys that no field has) to the form. The form's device check isn't run again, as the device can't change there; the rules' conflict or missing switch aborts (`conflict`, `not_found`) with its message. The rules' warnings lead to `unchecked`: *Try again*, or *Save without checking*, which runs the rules' write again without the bridge (unpaired settings), so its device checks and key rule see the switches as they are then. A new switch is a subentry with the rules' values (`device_id`, `name`, `device_key`, `lights`, `groups`), the normalized device id as unique id and the name, or that id, as title; an edited one is updated in place with `async_update_subentry`, as `async_update_and_abort` takes no placeholders. "Added …" and "Saved …" name it. Any other exception is logged with its traceback and shown: while listing, by `lights_unavailable`; while saving, as "The switch was not saved: …". **Runtime** (decision 23): the flow starts only while the entry is loaded, and takes the runtime's factory of Hue clients then (`hue_clients`, as the runtime's switch test makes its clients), so that it can still ask the bridge while the entry reloads. `lan_registry(entry)` is `entry.runtime_data.registry` while the entry is loaded, else `None`, which lists no devices and lets identify wait for the next runtime's registry. The switches and the bridge are the runtime's `config_from_entry`, with the values as stored. A saved or deleted switch reaches the runtime through the entry's update listener, which restarts only the bridge supervisor. Home Assistant cancels identify's task when the dialog closes, as the progress step's task. **Secrets:** the stored device key never reaches the frontend, only whether there is one; a key typed in the dialog is suggested again after an error, as Home Assistant's flows do; the flow API answers a new switch without its data. **Choices:** identify waits 60 s and starts only when chosen; a device of another switch can be chosen and is then refused with the form's message; a device the form refuses after identify goes to the device id; the device list's times don't refresh; the edited switch's device is kept with *Keep device …*; a menu comes before typing the lights; the rules' warnings are confirmed before saving; a conflict or a deleted switch ends the dialog; key errors show above the *Advanced* section; the chips' picker searches the lists | The switches are stored and checked by the engine's rules (decision 20), in Home Assistant's own dialogs, as the user chose. Home Assistant 2026.11 supports progress steps in subentry flows, in the backend and in its frontend (Research findings), so identify needs no fallback; a progress step can only be left by closing the dialog, so it can't wait without end. A form can't show the LAN's devices live, so a menu chooses the way to the device, and keeping the device first makes editing a switch one click. Home Assistant's options can't be disabled, so a used device is refused rather than hidden. Positions as values don't depend on the resources' ids, and removable chips show the entries that don't resolve. A section with a `visible` condition gives the key an advanced part and a field shown only when needed. Home Assistant can't show a note after its dialog closed, so the warning comes before saving; running the rules' write again keeps their checks of the moment in the engine's code. The settings form can't change the device, so a conflict there ends the dialog. The registry and the bridge's clients come from the runtime, which runs only while the entry is loaded, hence the abort while it isn't; core integrations abort the same way. One factory of Hue clients serves the supervisor, the switch test and the dialogs, and lets the tests give them all the same bridge |
| 25 | The devices and entities: `entity.py` and the platforms `sensor.py`, `binary_sensor.py`, `event.py` and `button.py`, which `PLATFORMS` lists (decision 23 left it empty), with `icons.json` and the `entity` section of `en.json`. **Devices:** the bridge's, identified by the bridge id (the entry's `bridge_id`, else its entry id), named "Hue bridge" and made by "Signify Netherlands B.V.", without a model or firmware; and one per `switch` subentry, linked to it (`config_subentry_id`), identified by the normalized device id, named by the subentry's title, made by "SONOFF", model "MINIR4M", with the device id as its serial number. **Entities:** each has `has_entity_name`, a `translation_key` with its name, states, attribute names and unit in `en.json`, its icons in `icons.json`, and the unique id `<its device's identifier>_<its translation key>`; none polls, and all are available while the entry is loaded. On the bridge's device: *Status* (`sensor.hue_bridge_status`), an enum of `ServiceState`'s values with `message`, `problems` and `since`; *Reachable* (`binary_sensor.hue_bridge_reachable`, connectivity, diagnostic), on or off, or unknown while the snapshot's `reachable` is None (not checked yet, or not paired), with `address`, `message` and `checked_at`, its states named "Reachable" and "Unreachable"; *eWeLink devices seen* (`sensor.hue_bridge_ewelink_devices_seen`, diagnostic, in devices), the LAN registry's count, with `listening` and `last_packet_at`. On each switch's device: *Press* (`event.<switch>_press`, device class button, event type `press`), with `seq`, fired by the runtime's `sonoff2hue_press` for its switch; *Last action* (`sensor.<switch>_last_action`), an enum of `turned_on`, `turned_off`, `failed` and `dropped`, with `at`, `changed`, `unchanged`, `failed`, `elapsed_ms`, `targets` and `message`; *Online* (`binary_sensor.<switch>_online`, connectivity, diagnostic), states "Online" and "Offline", so offline also when never heard, with `address`, `last_seen_at`, `last_press_at`, `seq`, `encrypted`, `has_device_key` (a key that isn't blank), `detached` and `decode_error`; *Test toggle* (`button.<switch>_test_toggle`, config), which runs `async_test_toggle` and turns a `SwitchTestError` into a `HomeAssistantError` with its message, translated as `test_toggle_failed` since decision 36. **Sources:** Status, Reachable and Last action show the status tracker's snapshot when they are added, then each one on `SIGNAL_STATUS`. Last action shows the newer of the switch's last action and last error: the tracker records the error of a toggle that some lights failed at the toggle's time, so an error at the time of a toggle without failures came after it, and a toggle with failures shows with its error's message; the error `PRESS_DROPPED` is `dropped`, any other `failed`; `targets` are the snapshot's, the toggles while the bridge runs. eWeLink devices seen and Online read the LAN registry: the count at each snapshot, at once for a new device (the registry's callback) and with the last packet every 30 seconds; Online at each snapshot, when 5 minutes have passed since the switch was last heard (a timer for that moment), and every 30 seconds while it is offline. `last_packet_at`, `last_seen_at` and `checked_at` aren't recorded. **Subentries:** each platform adds the entities of the `switch` subentries in its setup, and those of one added later on `SIGNAL_SUBENTRY_ADDED`, with `config_subentry_id`; they follow their subentry on `SIGNAL_SUBENTRY_UPDATED`, and Home Assistant removes a removed one's device and entities. **Reconfigured ids:** `async_track_devices`, which `__init__.py` starts before the platforms, keeps the devices in step with the entry, once at the start, for the changes made while it wasn't loaded, and after each change (`SIGNAL_CONFIG_CHANGED`, `SIGNAL_SUBENTRY_UPDATED`): a new bridge id, or a switch's new device id, becomes its device's identifier, and a switch's serial number, and the start of its entities' unique ids, and a switch's new title becomes its device's name. The device, its entities and their entity ids stay. A helper linked to the device, which belongs to another entry, keeps its unique id, and a device that the platforms didn't make is left alone. With the devices there, the runtime's bus events carry the switch's device id from its first press, for automations. **Logbook:** the logbook, and a switch's device page, also show the entities' changes (Research findings), so with a `logbook.py` that described the bus events, a press and its outcome each showed twice; the user chose the entities' entries (decision 26). **Choices:** Status's states have short names ("Not configured", "Invalid configuration", "Connecting", "Running", "Error"); Reachable is unknown both before the first check and while the bridge isn't paired; Last action shows the newer of the last action and the last error, with its counts as attributes; Press fires for the presses that the bridge acts on, so not while the bridge doesn't run, and keeps its last event across a restart, as Home Assistant's event entities do, while Online's `last_press_at` counts every press that the LAN heard, from the entry's start; there is no entity for the count of switches online, the version or the uptime. The plan's *Bridge reachable* is named *Reachable* | The entities show the state that the diagnostics show too, the status tracker's snapshot and the LAN registry (the coordinator's rule). Home Assistant names an entity on its device, so *Bridge reachable* on "Hue bridge" would read "Hue bridge Bridge reachable" (`binary_sensor.hue_bridge_bridge_reachable`). The entry doesn't store the bridge's model and firmware (decision 22's data), and reading them would be a request at every start; read once, the firmware would go stale after an update. The bus events are the runtime's interface for presses (decision 23), so Press and the toggle events agree, and an automation on Press sees the presses that toggle the lights. One state can't show two outcomes; the newer is the switch's last action, and the tracker's times order them, with the one tie that the tracker makes on purpose. The status tracker publishes a LAN device only when it is a configured switch's, and the registry reports a broadcast only when it isn't a repeat, so neither a new device that no switch maps, nor the last packet, nor a switch heard again only through repeats, after multicast was lost for a while, would show without the registry's callback and the 30-second looks; a timer at the end of the 5 minutes shows a switch going offline when it does, without polling. Attributes that change at every look say nothing in the history. Signals and the bus keep the platforms apart from the runtime's objects (decision 23). Reconfiguring can pair another bridge (decision 22), and the switch flow can give a switch another MINIR4M or another name: without the tracking, the old identifiers would stay, a switch added later with a device id that another switch had before would take that switch's device, which Home Assistant then moves to the new subentry, and the next start would make new entities next to the old ones. Keeping the device and its entities keeps their history, area, names and the automations on them |
| 26 | Diagnostics and the logbook show what the integration does, with Home Assistant's log (the user's choice of 2026-11-24). **Diagnostics** (`diagnostics.py`): `async_get_config_entry_diagnostics` gives four views in one file: `config`, the `EngineConfig` that the supervisor runs, with the values as the entry and its switch subentries store them; `status`, the status tracker's snapshot: `started_at`, `service`, `hue`, `lan` and each switch with its LAN device, its resolved `targets`, its last action and its last error; `lan_devices`, the LAN registry's snapshot, each device with `online`; and `activity`, the activity buffer as it is, its last 1,000 entries, oldest first, as Home Assistant's log levels let them in (decision 23). Dataclasses become objects of their fields, times ISO 8601 and enums their values, so the answer is plain JSON. Home Assistant's `async_redact_data` then replaces the values under `TO_REDACT`, the keys under which the entry and a switch subentry keep the secrets (`app_key`, `certificate_sha256`, `device_key`): the key shows as `**REDACTED**`, and the pin and each device key as `**REDACTED**` or null, which tells only whether it is set; `status.hue` has `paired` and `certificate_pinned` too. An entry that isn't loaded has no runtime, so only `config`. No text holds a secret: the engine sends the application key only in the `hue-application-key` header, gives the pin only to aiohttp's check of the certificate and the device key only to the payload's decryption, and turns aiohttp's errors into its own messages, which name the bridge's address and port, a method and a path, never a header; the decode errors are fixed texts, no log line names a key or the pin, and the activity keeps an exception as "Type: message", without the exceptions chained to it. The texts aren't scrubbed for the secrets' values, which would hide from the tests a message that also puts a secret into Home Assistant's log. A switch's device has no diagnostics of its own. hassfest needs no `dependencies` or `after_dependencies` for the platform (Research findings). **Logbook** (the user's choice of 2026-11-25): Home Assistant's own entries only, two for each press, the switch's Press ("Press") and the change of its Last action ("Turned on", "Turned off", "Failed" or "Dropped") (decision 25), also on the switch's device page, with the details in their attributes. The integration has no `logbook.py`, so the logbook has nothing for the bus events `sonoff2hue_press` and `sonoff2hue_toggle`, which stay for automations and the entities (decision 23). The first `logbook.py`, of the same day, described them as the runner logs them: an entry named "Hall (1000abcdef)" with "wall switch operated (seq 12).", and one with "turned ON light 'Ceiling' in 85 ms.", "could not toggle the lights: …" or "dropped a press that waited more than 5s for the bridge.". Last action adds an entry only when its state changes: a press whose outcome is the one before shows as Press alone, and of the outcomes in the 500 ms before the entities show the status, only the newest shows. A toggle that can't reach the bridge also shows as the bridge's Reachable going off, on the bridge's device. The runner's lines, such as "…: wall switch operated (seq 12)." and "…: turned ON light 'Ceiling' in 85 ms (1 already on).", and the others (online, ignored relay and setting changes, restarts, (re)joined, the bridge's reachability, configuration changes and more), are in Home Assistant's log at its levels and in the diagnostics' activity | Someone who diagnoses the integration needs the four views together, which explain any state: what is configured, what the bridge and each switch are doing, what the LAN hears, and what happened. The engine's dataclasses keep every field they have, and redacting by the stored keys covers the secrets wherever such a key appears. A switch's problem is read with the bridge's state, the configuration and the activity, which a file for its device would need as well; the entry's file is small, and Home Assistant offers it on the integration's page. Home Assistant's diagnostics find the platform in an integration set up before or after them, and hassfest allows the platform's import of its component. With the first `logbook.py` and the entities, each press made four entries, two of them for the bus events, and Home Assistant offers integrations no way to keep an entity's changes out of the logbook: it leaves out only some domains and sensors with a unit, a state class or a numeric device class (Research findings, Entities and devices). The user chose the entities' entries, which name the press and its outcome in Home Assistant's own words, with the details a click away |
| 27 | The end-to-end test. `scripts/e2e` runs `python -m tools.e2e`, and CI's job *End-to-end* (`ci.yml`, id `e2e`) runs `scripts/e2e` on ubuntu-latest, on pushes to `main` and on pull requests, within 15 minutes, with uv's downloads cached on `requirements_test.txt` and `requirements_dev.txt`; a failure uploads the logs as the artifact `end-to-end-logs`. **What runs:** `scripts/develop setup` (decision 18), with its setting `SONOFF2HUE_DEV_CONFIG`, makes Home Assistant 2026.11.3's environment `.dev/venv` and a configuration folder of the test's own, `.dev/e2e/config`. The test starts Home Assistant itself, as `scripts/develop hass` runs it (from the repository root, with the same `--skip-pip-packages`), on 127.0.0.1 and `SONOFF2HUE_DEV_PORT`, and the fake bridge, `python -m tools.fake_hue`, on 127.0.0.1 and `FAKE_HUE_PORT`, with its link button released; both ports are free ones, chosen at each run. The simulated switch is a made-up MINIR4M `1000e2xxxx`, new at each run, which `tools/send-ewelink-announcements.py` announces with TTL 0 on the default route's interface, as decision 18's `press` does. **The setup, through Home Assistant's API as the frontend uses it:** the test waits for the HTTP server, onboards a throwaway owner with onboarding's first step only, signs in for a token, and waits for the core state `RUNNING` before anything else, as a stop before that would abandon the start. The setup flow (`/api/config/config_entries/flow`) takes *Enter the bridge address* with the fake bridge's address, after its link button is pressed (`POST /_fake/link-button`), and the link step, whose end the flow's `data_entry_flow_progressed` event announces, as the frontend gets it. The entry has no switch, and its status asks for one. The switch comes through *Add switch*, the switch subentry flow (decision 24, `/api/config/config_entries/subentries/flow` with the handler `[entry_id, "switch"]`): the switch announces seq 1, which the LAN monitor takes as its state, then identify-by-press starts and the switch announces seq 2, a press, at once; `settings` then gets the name *End-to-end*, and the light `Hallway` and the room `Living room`, picked by their labels from the form's lists. The test checks the subentry, and that the configuration holds the switch as the picker saves it: `Hallway` and `room:Living room`. **The status:** the test reads the integration's status, `entry.runtime_data.status`, through the diagnostics download (decision 26), `/api/diagnostics/config_entry/<entry_id>`, whose `data.status` is the tracker's state at that moment: `not_configured` with the supervisor's "Add a switch: …" after the setup flow, and `running` before any press, after the switch is added and after the restart. After each round of presses it checks the status: running, and the switch online at its address, detached, with its targets, its last toggle on or off and no error; and what the entities show (decision 25), through the REST API: the bridge's *Status* `running`, and the switch's *Last action* `turned_off` or `turned_on` and *Press* with the event type `press` and the last press's seq, found in the entity registry (`config/entity_registry/list`) by their translation keys. **The presses:** the switch announces seq 2 again, then 3 and 4: for the runner, which the new switch restarted, 2 is the switch's state. The test expects exactly the fake bridge's PUTs of on, then off (`/_fake/puts`), all lights off, the bus events through the WebSocket API's `subscribe_events`: `sonoff2hue_press` with seq 3 and 4, each followed by its `sonoff2hue_toggle`, `toggled` with the targets, 2 changed and `turned_on` true, then false; the status; and the supervisor's and the runner's lines in the log: "Configuration changed; restarting the bridge.", "Listening for wall switch presses.", the switch's state, the presses and the toggles. **The restart:** the `homeassistant.restart` action, over the WebSocket API; Home Assistant exits with code 100, and the test starts it again and waits for `RUNNING` and the status. Then the switch announces seq 4 again and 5: only 5 may be a press, with one toggle, on, and its two PUTs, and the runner logs 4 as the switch's state, a baseline: no false press after a restart. **The end:** Ctrl+C (SIGINT) must stop Home Assistant with exit code 0, and each start's `home-assistant.log` must hold no warning or error of the integration, no error naming it and no report about it. **Timings:** on the development machine 14 to 16 s in all, with `.dev/venv` there: setup 1 to 1.5 s, Home Assistant's start 2.2 to 2.5 s, each flow 0.1 to 0.2 s, the two presses 3 s (the switch sends 1.5 s apart), the restart 3.2 to 3.9 s. On GitHub's runner 17 to 22 s, in a job of 31 to 42 s with uv's cache: setup 3 to 5 s, Home Assistant's start 3.5 to 4.8 s, the restart 2.6 to 4 s. The verification log has the runs | Only this job runs the integration as Home Assistant loads it, with its flows, the runtime, the engine, the mDNS socket and the Hue client together, over real sockets, and through a restart, all the way to the Hue API. **The switch through Add switch, identified by a press:** the plan's way to add a switch (decision 5), and the path with the most of the integration in it: the LAN monitor and its registry, identify-by-press, the picker, the check against the bridge, and the update listener, which restarts the supervisor for the new switch without a reload (decision 23). *Enter the device ID* would leave out the LAN. Until the subentry flow was merged, the test imported a `sonoff2hue.yaml` without a bridge, whose flow then paired one (decision 22); it no longer does: the import is walked through Home Assistant's HTTP flow API against the fake bridge in `test_config_flow.py`, an entry that starts with its switch is what the restart tests, and with one entry (`single_config_entry`) both would take two setups. **Ports and device id of each run's own:** announcements with TTL 0 reach every listener on the machine, so two runs at once, in two checkouts, can take each other's as presses, and `scripts/develop` may hold the usual ports. Identify-by-press selects the first device that reports a press, so the switch's state comes before it starts and its press right after: a press of another device on the machine's network has a window of about 0.1 s to be selected instead, which the check of the selected device would report. CI has no other device. **Determinism:** each wait is for a condition: a port that answers, the core state, a flow's progressed event, the status and the devices in the diagnostics, the bus events of the last announcement's press, the entities' states, which follow the status's snapshots up to 500 ms later, a line in the log; there is no fixed sleep. The supervisor sets the status to running in the same turn of the event loop in which the runner adds its packet handler to the listener, so from then on a broadcast reaches the runner. A flow's announcements are counted before the request that starts its progress step, as the announcement can come before that request's answer. The diagnostics' 404, until Home Assistant has registered the platform after the integration's first setup, counts as not yet. The test starts Home Assistant itself rather than through `scripts/develop hass`, so that it sees the restart's exit code and knows that the next answer comes from the new process. Log lines are waited for, as a thread writes the file. Temporary mutations of the integration each failed it: a first broadcast taken as a press (also after the restart), identify-by-press selecting nothing, a runner that gets no packet or never says it listens, a status never set to running, `turned_on` inverted in the events or in the status, and Press with another seq |
| 28 | `README.md` is the documentation for the integration's users: the Marketplace shows it on the integration's page, GitHub on the repository's, and the manifest's `documentation` leads to it (decision 16). **Structure:** what the integration does, with a diagram, and that it lives in Home Assistant's own UI and needs neither Home Assistant's Hue integration nor SonoffLAN; *How it works*, with its own mDNS socket; *Requirements*: Home Assistant 2026.11 or later (OS, Container or Core) on the switches' subnet, Container on the host's network and a virtual machine with a bridged adapter, **Detach relay**, a Hue bridge with CLIP v2, and nothing else that toggles the same lights; *Installation* from the Marketplace, by the My link `marketplace_repository` or *Add from link*, which need a GitHub connection, or by hand from a release's source; *Setup*: pairing; the import, with the format of a `sonoff2hue.yaml` (its keys, an example with made-up values), what the import checks and how it reports problems, and what it keeps; *Reconfigure* and reauthentication; *Switches*: the three ways to the MINIR4M, *Switch settings*, names matched without regard to case, the device key and where to get it (the eWeLink cloud API), changing and deleting; *Devices and entities*, each entity with its states and attributes as `en.json` names them (decision 25); *Presses in automations*: *Press* with the trigger *Event received*, and the bus events with their data (decision 23), in YAML for the tests' switch Hall, `1000abcdef`; *Repairs*, each with what it means and what to do; *Activity, log and diagnostics*: the logbook as the user decided on 2026-11-25, Home Assistant's own entries only, the entities' changes such as *Press* and *Last action* (decision 26), with the details in *Last action*'s attributes, Home Assistant's log and the diagnostics, and the coordinator's rule for the log levels: *Enable debug logging*, or `logger:` for the info lines; *Troubleshooting*, with the entities and messages that show each problem, such as the bridge's transport messages; *Limitations*; then *Development* (decision 18, the tests, the mDNS layer's recorded cases and the guard test of decision 30, and the end-to-end test of decision 27), *Releases* (decision 29) and the license. The UI's labels are in italics, word for word as `en.json` or Home Assistant's frontend shows them, and the eWeLink app's **Detach relay** in bold. Sections name each other in the text, without links. **Markers:** *Installation* is between `<!-- ha-marketplace:exclude-start -->` and `<!-- ha-marketplace:exclude-end -->` (the user's choice of 2026-11-25), which the Marketplace leaves out from Home Assistant 2026.12, as its page has an install button of its own; 2026.11 shows the whole README, so the installation is still written for both readers. **No status line:** the README describes the integration as released, without "in development" or "no release yet", as the Marketplace shows the README at the tag of the release on offer, and 1.0.0 is a tag only (decision 29). **Left out:** the design choices that users can't notice (one socket, the socket bound to the group on Linux, the order of the lists), which this file keeps; Home Assistant OS's multicast plugin, whose copies users never see (decision 19); the trace-level lines, which the `logger` integration can't select; the brand icon, which the Marketplace shows above the README, and whose `align` its sanitizer drops; the import's limits that a `sonoff2hue.yaml` never reaches, such as tags and nesting; a table of contents and in-page links | The Marketplace has no documentation page of its own, and Home Assistant's UI links a custom integration to its `documentation`, so the README is the users' only manual. A label written as the UI shows it can be found on the screen. Home Assistant 2026.11.3's `components/marketplace` downloads `README.md` raw at the version and only disables `<svg`; its frontend 20261028.8 renders it with marked 18.1.1 (GFM, no breaks) and js-xss 1.0.17, which removes HTML comments, so there a comment hides a text on both sites alike; from 2026.12 its frontend first leaves out the text between the markers, so they hide it in the Marketplace only (Research findings). There, headings get no ids, so an in-page link points at nothing, which GitHub's own outline of a README doesn't need; relative links lead to GitHub for `.md` files and to the raw file otherwise, at the version; a `#12` outside a link becomes a link to the repository's issue 12; and GitHub's alerts become `ha-alert`s. A status line would still be there in the Marketplace at the tag of 1.0.0. The logbook lists an entity's change only when its state changes, so the README says that two presses with the same outcome show *Last action* once. Details that users can't notice would only lengthen it |
| 29 | Each version is a GitHub Release, made by `.github/workflows/release.yml` (*Release*) when a tag `vX.Y.Z` is pushed. Its job *Check the tag* may only read the contents. It checks out `github.sha`, the tagged commit, with main's history, and `.github/scripts/release.sh` stops it with an `::error::` unless the tag is exactly `vX.Y.Z` (three numbers, without leading zeros and without a suffix), `X.Y.Z` is exactly the `version` in that commit's `custom_components/sonoff2hue/manifest.json`, and the commit is on `main`. Then it lists the published GitHub Releases, neither drafts nor pre-releases, with `gh api --paginate`; their tags `vX.Y.Z` are the released versions, and git tags alone don't count. It hands on X.Y.Z, whether that is the highest released version (Latest), and the highest released version below it, where the notes start. The job *GitHub Release* needs it, has the workflow's only `contents: write`, checks nothing out and runs no action. It checks that the outputs fit the tag and that the tag still names `github.sha`, leaves a Release that exists already as it is, and takes Latest away if a higher version has a Release by now: when only the failed jobs of a run are run again, GitHub reuses the check job's old outputs. Then it runs `gh release create vX.Y.Z --verify-tag --target <github.sha> --title "sonoff2hue X.Y.Z" --generate-notes --latest=<true or false> [--notes-start-tag v<previous>]`, shows the Release, and fails if the tag was moved meanwhile. The workflow's token may only read by default, and releases run one at a time (concurrency group `release`, which never cancels a running release). The Releases have no assets and `hacs.json` sets no `zip_release`: the Marketplace installs a Release from its tag's source archive (Research findings, Releases). `.github/scripts/release-test.sh` tests the checks, and runs both jobs' steps from `release.yml` as GitHub runs them, against a stand-in `gh`; CI's *Test* job and `scripts/check` run it. Immutable releases are on since 2026-11-24 at 22:58 UTC, turned on once the refusals were tested on GitHub with throwaway tags, which created no Release. Release 1.0.0 is a tag `v1.0.0` on `main` after the live test, nothing more | The plan ends with release 1.0.0, and the Marketplace installs integrations from GitHub Releases: a tag alone isn't a version, and without a Release users get the default branch's newest commit. Checking before anything is created means that a refused tag leaves nothing behind, which matters with immutable releases, where a Release locks its tag's name for good. The version check keeps the Marketplace's version, the tag, and the one Home Assistant shows, the manifest's, the same, as the Marketplace's guides ask. Only commits of `main` are released, since they have passed CI and the ruleset's checks. Write access in one job without a checkout means that none of the repository's code runs with it; the tag is checked again there because no ruleset protects tags. Latest and the notes come from the Releases, so a stray or refused tag can't hold them back: a lower patch released later doesn't take Latest, and its notes start at the version below it, for that version's users. The Marketplace itself offers the newest Release in GitHub's list, which is neither Latest nor the highest version (Research findings), hence the rule to release in rising versions. A Release can't be made for a test without locking its version, so the tests run the workflow offline, and the throwaway tags on GitHub test only the refusals. Immutability ties each version to one commit for good, as the user chose |
| 30 | **The integration stands alone** (the user's choice of 2026-11-25). Python's standard library and the libraries Home Assistant ships decide edge cases: `str` methods such as `strip()` and `casefold()`; `json` and `base64`; `ipaddress` and yarl for the bridge address; aiohttp's own handling of answers; PyYAML with a probatio schema for the import. **The product rules stay:** how presses are detected and lights toggled; the timings (5 s, 10 s, 30 s, 45 s, 60 s, 500 ms, 5 min); pairing and discovery; the switches' limits and their messages; the flows, entities, repairs, bus events and diagnostics. **Tests are pytest cases of the integration's own behavior.** The generators of recorded cases and their fixtures are gone. **The mDNS layer is the exception:** `dns.py`, `mdns.py`, the record part of `ewelink.py` and the mDNS part of Hue discovery (`_discover_mdns` in `hue_setup.py`). It stays the integration's own and never relies on Home Assistant's mDNS: no python-zeroconf, no `zeroconf` or `network` integration, no manifest matcher, and no shared HTTP session for `.local` names. It keeps exactly its behavior for every input, with its own string rules for names, white space, device ids and `seq`. `tests/engine/mdns_packets.json` and `tests/engine/mdns_text.json` pin it, and `tests/test_own_mdns.py` guards the rule. **The work** runs as Phase 1, which retires the generators and keeps the mDNS layer's recorded cases, then six slices in parallel (A `lan`, B `hue`, C `yaml`, D `switches`, E `engine-core`, F `ha-layer`), then a cleanup and a rewrite of the README, `en.json` and this file, each step in a worktree and pull request of its own (decision 13). **Supersedes** decision 3's parity clause, decisions 7, 9, 10 and 15, the parts of decision 20 about the recorded cases and the picker's own string rules (`jstext.py`), and the parity rule of "How to work in this repository", except for the mDNS layer. **Done** (Verification log): the mDNS layer's string rules moved into `dns.py` and `ewelink.py`, and payloads are read with `base64` and `json` (A); the bridge address with `ipaddress` and yarl, the bridge's answers with Python's codecs and `json`, a malformed answer as a transient `HueApiError`, on a connector with the system's resolver (B); the import with PyYAML and probatio (C); names and ids by one rule, `strip()` and `casefold()`, in the resolver, the picker and the switch rules, and a device id configured twice refused by `validate_for_run` (D); a device id mapped twice a `ValueError`, and the activity buffer a `deque` (E); the flows catch `Exception` where they showed their `unexpected` text (F); the cleanup (#34) deleted `text.py`, `jsonnode.py` and `errors.py` and what nothing used, made `create_base_url` raise `ConfigError` in place of `try_create_base_url`'s tuple, typed the bridge's ids and names as text, refused null entries of `lights` and `groups` in the import at their line and column, so that `SwitchSettings.lights` and `groups` hold text only, and logs a missing value as `None`; and the README, `en.json` and this file describe the integration on its own | The user asked to retire the other implementation and every mention of it, to use Python's libraries as much as possible and to make the code more Pythonic; the same day, the user also asked not to rely on the mDNS that Home Assistant OS provides, and to keep the mDNS behavior as it is. Matching another platform's edge cases took about 5,000 lines of emulation, plus tools that CI couldn't run. The mDNS layer is where the integration meets the network directly, and decision 19's measurements were made on it |
| 31 | **sonoff2hue in the Marketplace's catalog** (the user's choice of 2026-11-25), so that users find it by searching the Marketplace, without its link. **The pull request,** hacs/default#14951, comes from the owner's account, through the fork `abpostelnicu/default` and its branch `add-sonoff2hue-homeassistant`, made from `hacs/default`'s `master`. It adds one line to `integration`, at its place by `str.casefold`, between `ABovsh/ukraine-alarm-pro` and `abroeders/smart-shutter-control`. It allows maintainer edits and requests no review. Its body is the template, filled in: the user's statement that they read the publishing documentation; links to Release 1.0.0 and to the *HACS* and *hassfest* jobs of *Validate* run 45696084499 on the released commit, which passed before the Release was published; and an *About* section on what sonoff2hue does, which says that it overrides and replaces no core integration and adds no light entities. **Before it:** the notes of Release 1.0.0 became a summary of what it does and needs, in place of GitHub's list of the pull requests, some of whose titles named the other implementation (the user's choice). The README's *Installation* is marked for the Marketplace to leave out (decision 28). **While it waits,** nobody comments on it or pushes to it, as `hacs-bot` asks. **Once it is merged,** the user's Home Assistant moves from the copy installed by hand to the Marketplace's install, which tracks the updates, and the README's *Installation* names searching the Marketplace | The catalog is the only way into the Marketplace's search and list; without it, a user adds the repository from its link, which needs a GitHub connection. The catalog's checks are the HACS action and hassfest that *Validate* already runs (decision 16), and the Release that decision 29 publishes. *Validate* also passed on the final `main` with hassfest's 2026.12 image, whose brand-image warnings the catalog treats as failures. A reviewer follows the release link, and the Marketplace shows a Release's notes before an update. The review declines integrations that overlap core's, so the *About* says that sonoff2hue replaces none. The user installed sonoff2hue by hand for the live test, and a Marketplace install also tests the path that users take |
| 32 | **Switches keep running when a light, room or zone is renamed or deleted** (the user's choices of 2026-11-26). **Labels:** a target's label shows its id after its name when another resource of its kind has that name, by `targets.name_key`: another light, for a light, and another room, or zone, for a room or zone. `resolve` sets `HueTarget.label_id` to the light's id, or to the room's or zone's own id rather than its grouped light's, and `HueTarget.__str__` adds it: `light 'Ceiling' (<light id>)`, `group 'Kitchen' (<room id>)`. The label shows wherever targets do: *Last action*'s `targets`, the status's targets, the diagnostics, the bus events' `targets` and the log lines ("… -> …", "…: turned ON …", the switch test's). **Per switch:** each switch is resolved on its own (`resolve_bindings`), so an entry that resolves to nothing never stops another switch. Its binding keeps the resolver's message for it (`SwitchBinding.problems`), as the status does per switch (`SwitchStatus.problems`), and the service's problems are all of them. **Missing:** the switch toggles the targets that resolve; the service stays running, and its message counts what is missing ("2 wall switches toggle 5 Hue lights, rooms and zones; 1 is missing."); the supervisor warns with each problem; and the runtime raises a warning repair per switch, `missing_targets_<device id>`, from the status snapshots, titled "Some lights, rooms or zones of … are missing", with the problems and what to do. A switch with no target left still reports its presses (the *Press* event and `sonoff2hue_press`), and each toggle fails with `NO_TARGETS`, "None of the lights, rooms and zones of this switch is on the Hue bridge.", so *Last action* is `failed` with that message. `invalid_config` stays for the configuration's problems (`validate_for_run`) and settings that can't be used. The switch test resolves as the runner does: it toggles what resolves, names the rest in its result (`SwitchTestResult.problems`) and its log line ("…; 1 is missing: …"), and fails with the problems only when nothing resolves. The switch rules' check against the bridge, when a switch is saved, stays strict. **Remembered resources:** after each resolution, the light, room or zone that each entry found is remembered, keyed by the switch's normalized device id, the setting (`lights` or `groups`) and the entry by `name_key` (`targets.EntryKey`), as its kind, its id (a room's or zone's own) and its name (`RememberedResource`); an entry that finds nothing keeps what it found before, and only the configuration's entries are kept (`ResolvedSwitches.remembered`). The runtime keeps them in a Home Assistant `Store` of its own, `sonoff2hue.<entry id>`, version 1, written atomically and tagged with the bridge id (`remembered.py`): loaded in `async_setup_entry` before the runtime starts, saved 10 s after a change (`async_delay_save`) and at once when the entry unloads, forgotten for removed switches and entries and for another bridge, and deleted in `async_remove_entry`. The engine imports nothing from Home Assistant: the supervisor takes them (`remembered`) and reports each resolution (`on_resolved`, a `ResolvedSwitches` with its configuration). **Renames:** an entry that matches nothing, or several, resolves to its remembered resource while the bridge has it, of the same kind and id. When the entry no longer names it, it was renamed (`Renamed`), and the runtime saves the picker's spelling (`targets.encode_light` and `encode_group`, factored out of `picker.py`: the name if no other resource of its kind has it, else the id, and `room:` or `zone:` before a room's or zone's name) in the switch's subentry in place of the entry (`async_update_subentry`), only while the subentry still has the entry, and logs "…: light 'Ceiling' was renamed to 'Ceiling lamp' on the Hue bridge; saved the new name." at info level. The renames of a resolution are saved together, so the update listener restarts the supervisor once (decision 23), and the new one finds the new names by themselves; an entry that names its resource by its id never needs it. A report of a configuration that has changed since is dropped, as the next supervisor resolves again. The switch test resolves with the remembered resources too, and the subentries' data and the `sonoff2hue.yaml` format stay as they were. **Checking again:** while the runner runs, the supervisor resolves the switches again every 60 s while a switch has an entry that resolves to nothing (`MISSING_TARGETS_CHECK_INTERVAL`), else every 15 minutes (`TARGETS_CHECK_INTERVAL`), which notices a rename made meanwhile, so the saved name never stays stale for long. The new targets go to the running runner without a restart (`Runner.update_targets`): it keeps its trackers and baselines and each switch's lock, and a toggle that a press started before finishes with the targets that it had. The status takes the new targets and problems, and publishes only when they changed (`set_running`). A toggle in which the bridge answered 404 for a target, whose failure the toggler now reports with the answer's status (`TargetFailure`), has them resolved at once (`on_targets_gone`). A bridge error during such a resolution changes nothing, and the keep-alive reports the bridge's reachability as before. A rename found while running is saved as one found at the start, with one restart of the supervisor, which then runs the runner as a task of its own next to the checks. **End to end** (decision 27's scenario): the fake bridge's `POST /_fake/rename/{id}?name=…` and `POST /_fake/delete/{id}` change its resources as the Hue app does. Before the restart, the test renames the switch's light `Hallway` to `Hallway lamp`. After it, it waits for "…: light 'Hallway' was renamed to 'Hallway lamp' on the Hue bridge; saved the new name.", "Configuration changed; restarting the bridge." and a second "Listening for wall switch presses.", then checks the switch in the diagnostics' `config`, saved with `Hallway lamp` and `room:Living room`, the entry's one subentry (`config_entries/subentries/list`), and that the next press toggles `light 'Hallway lamp'` and the room. Then it deletes the light: the next press (seq 6) turns the room off, and the light's PUT gets 404 (`sonoff2hue_toggle` with 1 changed and 1 failed), which has the switch resolved again at once. The status stays running, "1 wall switch toggles 1 Hue light, room or zone; 1 is missing.", with the problem; Repairs lists the warning `missing_targets_<device id>` with it (`repairs/list_issues`, waited for, as the issues follow the published snapshots); and the press after (seq 7) turns the room alone on. The 404's error and the missing light's warning are then the only problems of the integration that the log may have | Two lights of one name looked the same in the log, the status and the events, so a toggle's outcome couldn't tell which of them it switched. The switch dialog already lists a repeated name with the id beside it (`switch_flow.resource_options`), so the same rule names a target the same way everywhere. One light, room or zone renamed or deleted in the Hue app stopped every switch at the next start, until the user edited the switch or reloaded; the user chose to skip what is missing, toggle the rest and raise a repair. A repair per switch names the switch to fix, as its other repairs do, and a warning fits a switch that still works. A press of a switch with nothing left is still a press for automations, and its failure says why on *Last action*. A saved switch is what the user chose, so it must resolve. The user chose to follow renames and to save the new name in the switch, so that the switch's settings keep naming what it toggles. A resource keeps its id when it is renamed, so the id that an entry found before tells which resource a renamed one is, and a store of the runtime's own keeps that without changing the subentries' format or the import's. The tag keeps a reconfigured entry from using another bridge's ids. Saving the new name restarts the supervisor once, after which the entry resolves by its name again, so nothing loops. The configuration's identity tells a report from an older supervisor, which a change cancelled while its report was on its way. A fix made in the Hue app, or a deletion, should show without a reload: a minute while something is missing tells the user within the time that the repair promises, and 15 minutes otherwise catches renames at little cost to the bridge. A toggle's 404 says at once that a resource is gone, so the next press needn't fail for it. Restarting the runner for new targets would make the next press a baseline and drop toggles in flight. The end-to-end test renames and deletes as a user does, in the Hue app while Home Assistant runs, so it runs the store across a restart, the subentry's update through Home Assistant and the restart that it causes, the resolution after a 404, and the repair through Home Assistant's registry |
| 33 | **sonoff2hue follows the Hue bridge to a new address** (the user's choices of 2026-11-26). **When:** once the bridge has been unreachable at its address for 60 s (`SEARCH_AFTER`), while the supervisor waits for it and while the runner reports it unreachable, the supervisor looks for it on the network with a `BridgeSearch`, until it is reachable again, also with a refusal, or the supervisor stops, which cancels a search under way. The search lists the bridges with its finder, the engine's discovery (`hue_setup.discover`, whose `mdns` and `cloud` choose the sources), mDNS at most every 5 minutes (`MDNS_SEARCH_INTERVAL`) and Signify's endpoint at most every 15 (`CLOUD_SEARCH_INTERVAL`): each search asks the sources that are due, on the event loop's clock, those within a millisecond of each other together (`DUE_TOGETHER`), and the search keeps their times, so the rates hold across the times that the bridge is unreachable and across the supervisors that share it. The log has one info line as the search begins ("The Hue bridge at 192.0.2.10 has been unreachable for 60 s; looking for it on the network.") and nothing per search; while connecting, the message ends "…; looking for it on the network.", and the states stay. **The bridge:** a bridge listed at another address than the stored one, by host and port, counts with the stored bridge id, compared with `casefold()`, or without an id; each is read with the search's reader, `get_bridge_info` (`GET /api/0/config`, without the application key), and its id compared again. **With a pin,** its certificate's SHA-256 has to be the pin, compared as the client compares them (`hue_client.matches_pin`): the supervisor then reports its address once (`on_bridge_moved`, a `BridgeMoved` with the configuration), on the event loop's next turn, and the search ends. Another certificate isn't used, and is warned about once. **Without a pin,** the first such bridge's address goes to the status (`StatusTracker.set_bridge_found`, `HueStatus.found_at`), warned about once, until the bridge is reachable at its address again or the address, key or pin changes; the search goes on. **Host names:** a stored host name is never replaced, so no search is made for it (`BridgeUrl.is_ip_address`), nor for a configuration without a bridge id; an IPv6 address is an address. **The runtime** makes the search for its config entry and gives every supervisor the same one (`runtime.new_bridge_search`): the engine's discovery for 5 s with Home Assistant's shared session for Signify's endpoint, a public host name, as the setup flow searches, and `get_bridge_info`. It saves a reported address as the entry's `bridge_ip` with `async_update_entry`, without a reload, only while the report's configuration is the current one, and logs "The Hue bridge 001788FFFE100491 moved from 192.0.2.10 to 192.0.2.20; saved its new address." at warning level, before the update listener restarts the supervisor once (decision 23); the remembered resources, tagged with the bridge id, stay (decision 32). From the status's `found_at` it raises the fixable warning repair `bridge_moved`, "The Hue bridge answers at another address", with the stored and the found address (`address`, `found`) and the entry's id as its data: its fix flow, `repairs.py`'s, which now gives its form the issue's placeholders, as Home Assistant's own confirm flow does, leads to *Reconfigure*, whose *Find bridges* preselects the paired bridge by its id. The repair goes when the bridge answers at its address again or its address, key or pin changes; the certificate repairs stay as they were. **The fake bridge** serves a certificate and key that it is given (`tools/fake_hue/server.py`'s `start` and `FakeHueHost.start` take `certificate`), so two fake bridges present one certificate and bridge id | The router's DHCP can give the bridge another address, after a power cut or with a new router, and every switch then stopped until the user reconfigured. With a pin, the certificate proves the bridge, which the client checks at every connection, so only the paired bridge gets the application key at its new address. Without one, the client checks no certificate, so following a bridge id that any device can claim would send the key to whatever answers: the user's choice is to confirm it by pairing the bridge there, which pins it too. A host name is the user's way to let a resolver follow the bridge. 60 s waits out a short outage, such as the bridge's restart after an update, before anything is asked. Signify allows one request to its discovery endpoint per 15 minutes per client, and asks for mDNS for more regular scans (Research findings); one search for the config entry keeps to that also when the configuration changes meanwhile. A host and port compare as the client connects, so `192.0.2.10:443` is the stored `192.0.2.10`. The repair names the address found where the user looks for problems, and pairing the bridge there through *Reconfigure* pins its certificate, after which sonoff2hue follows it by itself. Saving the address in the entry's data keeps the subentries and the remembered resources, and restarts only the supervisor, as every change does. Two fake bridges with one certificate follow the bridge over real HTTPS, as the client checks the pin, from the first, stopped as if the address had changed, to the second |
| 34 | **A room or zone for many lights** (the user's choice of 2026-11-26): when a switch would be saved with 2 or more single lights, the switch dialog offers the room or zone that has exactly those lights, or else to create a zone of them on the bridge. **Members:** `HueClient.get_lights_and_groups` reads the lights, then the rooms and zones, each with the ids of the lights it holds (`HueGroup.light_ids`), from the three answers that the switch rules, the runner and the switch test read before, in the same order, so no request is added; it takes the place of `get_lights` and `get_groups` there, and `get_groups` is gone. A room or zone holds the lights among its children, those of the devices among them, which each light's `owner` names, and those of the rooms and zones among them, once even when groups refer to each other, as the bridge's grouped services do; a child of another kind holds none. Only these reads take owners and children, so the toggle's and the keep-alive's reads stay as they were. The fake bridge's lights have devices as owners, its room's children are devices, a dimmer switch's among them, and its zone's children are lights. **Creating a zone:** `HueClient.create_zone(name, light_ids)` posts a zone of the lights with the archetype `other` (`ZONE_ARCHETYPE`), then reads the new zone up to 5 times, 0.5 s apart (`ZONE_READS`, `ZONE_READ_DELAY`), until it lists its grouped light, and returns it as a `HueGroup`. The bridge's refusal is its status and message, as for any request. The fake bridge creates such zones, with the ids that follow its room's and zone's, refuses the bodies that a bridge would, records every POST of a zone in `/_fake/posts`, as it records PUTs, and serves any resource by its id. **The offer's rules** are the engine's `group_offer.py` (Behavior, Room or zone offer): `find_offer(switch, lists)` gives a `UseGroup` (the room or zone, and the lights), a `CreateZone` (the name, and the lights) or `None`; `zone_name` names a new zone; `with_group` puts the room or zone in the lights' place, in the switch as the form gave it, so that the rules check and save it as any other. The rules' check hands back the lists it read (`TargetCheck.lists` and `SwitchWrite.lists`, `BridgeLists`), so the offer comes from the lists that the switch was just checked against, and there is none when the bridge wasn't asked. **The dialog** (`switch_flow.py`, Behavior, Switch dialogs): when the settings form passes the form's checks and the rules' check without warnings, adding or editing a switch, the offer comes before anything is saved: `group_offer` with *Use room "…"* or *Use zone "…"* (`use_room`, `use_zone`), or `zone_offer` with *Create zone "…" on the bridge* (`create_zone`), and *Keep the lights* (`keep_lights`). Each description gives the room's or zone's name and the lights, and says that a room or zone switches all its lights at once, with one command, and also switches any light added to it later in the Hue app. Each choice runs the rules' check and save again, as the settings form does, without another offer: their errors go back to the form, a conflict ends the dialog, and their warnings lead to *Save without checking?*. *Create zone* makes the zone with a fresh client of the runtime's factory first; a refusal or failure, with its message, leads to `zone_failed`, *Couldn't create the zone*, with *Try again* (`retry_zone`), which checks the switch and offers again from the bridge's new lists, and *Keep the lights*; an unexpected exception is logged with its traceback ("Creating the zone … on the Hue bridge failed."). The YAML import offers nothing, and the toggle is as it was: a room or zone is one target | A switch with many single lights switches them one after another, one PUT each: on a real installation, 10 lights took 524 ms and came on in a visible wave, where a room's grouped light switched all its lights with one PUT in 59 ms. To offer the room or zone that holds exactly a switch's lights, the dialog needs each one's lights. The light answer names each light's device, which maps a room's devices to their lights without another request or another answer to read; reading a zone's lights by the same hierarchy rule covers any kind of child the bridge lists (Research findings). "other" is the archetype for any use. A zone switches its lights through its grouped light, without which the resolver refuses it ("has no lights"), and the bridge may list it a moment after the zone; five readings 0.5 s apart wait for it for about 2 s, at two requests a second at most, well within the 5 s that a request may take. A bridge's limits differ by model, and a full bridge says so itself. Rooms come first: a device is in one room only, so a room is its lights' own place, where a light can be in several zones (Research findings). A zone's name that no room or zone has, by the resolver's rule, is saved as `zone:<name>` and resolves to it alone. *Try again* offers anew rather than post again: a zone whose answer was lost is then offered for use, not made twice. Checking again after a choice keeps the rules' checks of the moment, as *Save without checking* does, as time passes while a menu is open. The texts quote names with double quotes, as the frontend's ICU message format shows text after an apostrophe and a brace as it is (Research findings) |
| 35 | **A canary for the next Home Assistant release** (part 1 of 2). `.github/workflows/beta.yml`, the workflow *Beta*, runs its job *Test (Home Assistant beta)* on Mondays at 06:23 UTC, on demand (`workflow_dispatch`) and on a pull request that changes `beta.yml` (`paths`). It may only read the contents, stops after 15 minutes, and a newer run on the same ref cancels an older one. As CI's *Test* job does, it installs uv 0.13.7 with `astral-sh/setup-uv@v10.4.0` and makes `.venv` with Python 3.14; uv's cache is keyed on `requirements_test.txt` and the suffix `beta`. It looks up the newest `pytest-homeassistant`, pre-releases included, as an exact pin (`uv pip compile --prerelease allow --no-deps --python-version 3.99`), then installs `requirements_test.txt` with that pin in place of its own (`--overrides`), in uv's default pre-release mode. It shows the Home Assistant and Python versions in the log and the job summary, then runs mypy, for Linux, and pytest, after a failure of mypy too, without `tests/tools/test_develop.py::test_requirements_dev_pins_what_home_assistant_requires`. Ruff, which doesn't depend on Home Assistant, and the end-to-end test, whose environment is `requirements_dev.txt`, pinned to the release, stay with CI. No ruleset requires it. **Part 2, planned:** once 2026.12.0 is on PyPI, the pins of `requirements_test.txt` and `requirements_dev.txt` move to the latest release, and a new CI job, which the ruleset requires, tests the minimum, 2026.11.3 | Home Assistant ships a beta every month, a week before the release: 2026.12.0b0 came out on 2026-11-25, and 2026.12.0 is expected around 2026-12-02. A weekly run against the newest version warns before users update; between betas it tests the latest release, which the pins reach only with part 2. **An exact pin of the newest version** keeps everything else as CI resolves it. `--prerelease allow` would take any package's pre-release: on 2026-11-26 also httpx 1.0.dev6 and grpcio 1.86.0rc1, which neither CI nor Home Assistant's own installation takes. And without a pin, uv would pass over a newest version that conflicts with a pin or needs a newer Python, without a word, for an older one, even 0.1.3, another project's (Research findings). Looked up for any Python, a beta that needs a newer Python than the runner's fails at the install, as CI would once the pins reach it. **The test of `requirements_dev.txt`'s pins** is left out: they are the pinned release's requirements, so it fails on any other release. **The triggers:** a workflow can be run by hand only once it is on `main` or has run, so the `pull_request` trigger tests a change of the workflow on its own pull request, by itself, and its path filter keeps a beta's failures off the pull requests that don't change it. 06:23 rather than on the hour, when GitHub delays scheduled runs and may drop some. **Not required:** a beta's failure says nothing about the change at hand. The ruleset matches checks by their job names, so the job's name is none of the required checks' (*Lint*, *Test*, *All commits pass*, *hassfest*, *HACS*, *End-to-end*). GitHub notifies whoever last changed the `cron` line, today the owner's account, which pushes `main`. **A cache key of its own:** with CI's key, whichever of the two jobs saved it first after a change of `requirements_test.txt` would fill it for both |
| 36 | **Small fixes** (2026-11-26). **A steadier Online:** after its first three queries, the LAN monitor looks every 30 s (`CATCH_UP_INTERVAL`) and asks when 2 minutes (`QUERY_INTERVAL`) have passed since its last query, and at each look while a configured switch is late: a switch of its `EngineConfig` that it heard before, last heard more than 2 min 30 s ago (`CATCH_UP_AFTER`) but within the 5 minutes of Online, as the registry's clock tells (`DeviceRegistry.heard_within` and `is_online`). Each query starts the 2 minutes again, so a look sends one query at most, and the queries keep their 2-minute rhythm while every switch answers. `config_changed` takes the new configuration's switches from the next look on. The runner's queries for a switch that it hasn't seen since it started stay as they were. **Test toggle's error** is translated: the button raises `HomeAssistantError(translation_domain=DOMAIN, translation_key="test_toggle_failed", translation_placeholders={"message": …})` with the switch test's message, and `en.json`'s new `exceptions` section gives it the text `{message}`, the message as it was. **Home Assistant 2026.11's conventions:** `async_setup_entry` and `async_unload_entry` return None, and the unload is `async_unload_platforms_or_raise`, so a platform that fails to unload leaves the entry with Home Assistant's reason "Failed to unload one or more platforms."; the config flow tests' stand-ins for them return None too | Every MINIR4M answers each query, and Online is on while the switch was heard within 5 minutes, so two lost answers in a row turned it off for a minute, as one switch's Online did once overnight on the user's Home Assistant OS. The user chose catch-up queries: after a lost answer, a query every 30 s gives the switch four more chances to answer before Online would turn off, at no cost while the switches answer, and a switch that is gone for good is asked every 2 minutes again once it is offline. One loop that looks every 30 s, with one wait that every query restarts, never sends two queries for the same moment, and it judges a switch's silence on the registry's clock, as Online does. A translated error is Home Assistant's way to give an integration's errors their own text: the frontend shows its text, in the user's language where there is one, instead of its own generic one. The text is the switch test's message, which says what to fix, as it was; the toast now shows it alone, without "Failed to perform the action button/press." before it, and the log line, which writes the error's text, stays the same. The contributors blog of 2026-10-23 makes None the rule for core integrations' entry functions; a custom integration may keep returning a bool, so this follows the convention rather than fixing a fault. A False from the unload already failed it, but with the generic "Failed to unload the config entry."; the error that the new call raises says that a platform didn't unload |
| 37 | **Releases from a merged release pull request** (the user's request of 2026-11-26), so that a Release agent that works on its own on GitHub, a Copilot cloud agent, can prepare a release that the owner merges. **The release pull request** raises `version` in the manifest to X.Y.Z and adds the release's notes, written for users, as `.github/release-notes/vX.Y.Z.md`, the file that marks a release commit: a raised version without it releases nothing by itself. **Tag the release** (`.github/workflows/tag.yml`) runs when *CI* completes on `main` (`workflow_run`, `branches: [main]`), one run at a time (concurrency group `tag`, which never cancels a run in progress). Its job *Check the release commit* runs only after CI passed on a push (`conclusion == 'success'`, `event == 'push'`), may only read, and checks out the commit that CI passed on (`workflow_run.head_sha`), with main's history and without credentials. It reads that commit's version (`release.sh manifest-version`, exactly X.Y.Z, else an error). Without that version's notes file the commit isn't a release commit: a notice, and nothing is tagged. If `vX.Y.Z` exists and names the commit or a commit before it, a notice, and nothing is tagged; if it names another commit, an error. Otherwise the commit must be on `main` (`release.sh check-main`) and X.Y.Z higher than every released version (`release.sh released` and `check-higher`). Its outputs are `version`, `sha` and `tag`. Its job *Tag and release* runs only when `tag` is `true`, has the workflow's only write permissions, `contents: write` and `actions: write`, and checks nothing out. It checks the outputs, then makes the annotated tag through the REST API: the tag object (`POST git/tags`, with the message "sonoff2hue X.Y.Z" and the tagger `github-actions[bot]`, its noreply address and the time), then `refs/tags/vX.Y.Z` (`POST git/refs`). If that fails because the tag was pushed meanwhile, it stops with a notice when the tag names the commit, as its push ran *Release*, and fails when it names another; any other failure fails it. Then it runs *Release* on the tag (`gh workflow run release.yml --ref vX.Y.Z`), and its summary shows the tag and links *Release*'s runs. **Release** (`release.yml`) also runs by hand (`workflow_dispatch`, without inputs); on anything but a tag, such as `main`, its check job fails with "… isn't a tag" (`release.sh version`). Its GitHub Release job reads the tagged commit's notes file through the contents API's raw media type, still without a checkout: the Release's notes are exactly that file (`--notes-file`, without `--generate-notes` and `--notes-start-tag`); on a 404 GitHub generates them, as before; any other failure is an error, and no Release is created. **By hand,** a pushed tag still releases, as decision 29 has it. **CI must pass on the release commit itself.** CI's concurrency group cancels its older run on `main` when a newer push lands, and CI can fail there, from a flaky test for example. Either way the release commit isn't tagged, and the next commit of `main` whose CI passes, which still has the notes file, gets the tag `vX.Y.Z` instead: the release then includes the changes merged after the release commit. So if CI fails or is cancelled on the release commit, re-run it (`gh run rerun <id>`, or *Re-run jobs*) before merging anything else: the re-run's success tags the release commit itself. `tag.yml`, the README and this file say so, and nothing else guards against it | The cloud agent can only push its own `copilot/…` branch and open a pull request: it can't merge, push tags or create Releases. So the owner's merge decides a release, and GitHub Actions publishes it. A tag that a workflow pushes with its `GITHUB_TOKEN` starts no workflow, so `release.yml`'s push trigger wouldn't run, but a dispatch does, and a run on a tag has the tag's ref and commit, as its push would, so *Release* keeps all of decision 29's checks (Research findings). The notes file marks the release in the pull request that the owner reviews, and its text becomes the notes that the Marketplace shows before an update, where generated notes would list the pull requests' titles. **A tag on a commit before this one is a notice, not an error:** the notes file stays on `main` after its release, so until the next version every later commit is a release commit by its file, and an error would fail the workflow after every merge. CI's success on a push to `main` gates the tag, as decision 29 releases only what passed CI; a pull request's CI runs on its own branch, which a fork may call `main`. The check job runs the repository's scripts, so it may only read, and the job that writes runs none of them, as the GitHub Release job doesn't. One run at a time, so that each run sees the tag that the one before it made. The tag is annotated, with decision 29's message, as `git tag -a` makes it, and the tagger's time is given, as the documentation doesn't say which time the API takes without one. The checks run before the tag, so that a commit that *Release* would refuse isn't tagged, and *Release* checks the tag again. Notes that can't be read stop the Release, instead of GitHub's notes in their place, and the job can be run again |

## Record keeping

- This file and one descriptive commit per step.
- The plan that started this repository was approved on 2026-11-23, in session da483334. Its todos, in order: create
  this repository; CI and a ruleset; a spike of the mDNS socket on the user's Home Assistant OS; the engine (DNS,
  eWeLink and press detection, the mDNS listener, the fake Hue bridge, the Hue client and pairing, the runner, the
  YAML import); the Home Assistant layer (skeleton, runtime, setup flow, switch subentry, entities, diagnostics and
  logbook); development tooling; end-to-end CI; the README; the live test; release 1.0.0. The Status list below
  tracks them.
- The plan of decision 30 was approved on 2026-11-25, in session 1e9ba998: Phase 1, which kept the mDNS layer's recorded
  cases and added the guard, the six slices A to F side by side, then `cleanup-leftovers`, `docs-and-memory` and the
  coordinator's `final-verification`.
- The plan of decision 31 was approved on 2026-11-25, in session 88500144: rewrite the notes of Release 1.0.0, mark the
  README's installation, check again, submit to the catalog, watch the checks and record it all. Once it is merged:
  verify the listing, move the user's Home Assistant to the Marketplace's install, and record the inclusion.
- The plan of decisions 32 to 36 was approved on 2026-11-26, in session 9d5a36e3, after a review of 1.0.0 on the user's
  Home Assistant OS and against Home Assistant 2026.12.0b0. Its workstreams ran side by side, each with its own pull
  request (decision 13):
  - 32: renames and deletions (#47);
  - 33: a moved bridge (#48), after 32;
  - 34: rooms and zones (#45);
  - 35: the beta canary (#43), then, once 2026.12.0 is out, the pins at the latest release and a required job for the
    2026.11.3 minimum;
  - 36: the small fixes (#44);
  - then the live test.

  On the way, two flaky tests were fixed (#46, #49). The user left out:
  - device keys from SonoffLAN's cache;
  - the standard `single_press` event type;
  - curated release notes, issue forms and Dependabot;
  - a slimmer copy of this file;
  - README screenshots;
  - a release.

## Environment observations

- 2026-11-24: development machine: WSL 2 on aarch64, Debian 13, with Python 3.14.6 and 3.13.5 and uv 0.13.7. gh is
  Debian's 2.46.0, which has no `gh release verify`. There is no Docker. `scripts/setup` installed Home Assistant
  2026.11.3 and the tools in 15 s.
- 2026-11-25: a second development machine, macOS on arm64, with uv 0.13.9, gh 2.105.0 and Node 26.13.1.
  `scripts/setup` took 18 s. There `scripts/check` stops at mypy, with 2 errors in `engine/mdns.py`: mypy checks for
  the host's platform, and the netlink code is Linux-only (`socket.NETLINK_ROUTE`, and a statement it deems
  unreachable). `.venv/bin/python -m mypy --platform linux` checks as CI does, and passes. The rest of the checks pass
  there too; pytest skips the 2 tests of the socket bound to the group, which runs on Linux only.

## Verification log

- 2026-11-24: bootstrap. `.github/scripts/check-commits.sh` checks each commit's message and `git diff --check`. The
  first commit holds only documents, so `scripts/check` passes from the second one on, which adds the tools and the
  first code.
- 2026-11-24: the mDNS layer's first tests. The DNS parser (`engine/dns.py`): truncated packets, compression pointers,
  TXT keys, unknown record types, overruns, long names and unsupported labels. The listener (`engine/mdns.py`): packets
  to several handlers, malformed packets and failing handlers, queries on every joined interface, started callbacks for
  each socket and their cancellation, socket errors and their retry, debounced network changes, the interface filter,
  netlink messages, and on a real socket: sharing the port, TTL, loopback, and a join that fails. A full run showed the
  started callbacks cancelled in a set's order, once in a few dozen runs; they are cancelled in the order they started.
  On the development machine (WSL 2), `interface_states` gave `lo` as loopback without multicast, and `eth0` up with
  multicast.
- 2026-11-24: after the first green runs on `main` (71d226a, 77da7d5), ruleset 28003079 "main: require CI and per-commit
  checks" was created: *Lint*, *Test* and *All commits pass*, with an admin bypass. *hassfest* and *HACS* followed
  (below), and *End-to-end* later.
- 2026-11-24: the runner (decision 14). `test_runner.py` covers the eight on/off combinations of two lights and a room
  switched by encrypted broadcasts, queued presses with and without a rejected first read, a queued press 5.001 s old,
  which is dropped, and one exactly 5 s old, which isn't; a cancelled wait that leaves the toggles running, unexpected
  errors while toggling and in the keep-alive, a device id mapped twice, the log lines of each kind of broadcast, of
  payload problems and of toggles, `resolve_bindings`' retries, its list of unresolved names and a refusal, and
  `run(listener)` on `FakeNetwork`: the queries, the keep-alive, a press, a new baseline after the socket restarts, and
  stopping while the listener goes on. Breaking the runner on purpose (keeping the started callback, `gather` for the
  wait, dropping at 5 s, no reset on a restart, a keep-alive that waits first, reporting every ping, no guard around the
  observer) failed the matching tests. The runner, the LAN monitor and Hue discovery all wait 0, 1 and 2 s before their
  first three queries, which go out at 0, 1 and 3 s.
- 2026-11-24 and 2026-11-25: eager starts (decision 14).
  - The runner: a stale press is dropped, and reported, before `handle_packet` returns; a toggle that needs no I/O logs
    "turned …" before the next event of its packet; `run` sends its first ping before "Listening for wall switch
    presses." and before it first waits. Starting the toggles or the keep-alive on the next turn, or awaiting the
    keep-alive's zero delay, failed these tests.
  - The listener's started callbacks: before they started eagerly, a broadcast that arrived right after a socket restart
    reached the runner before its reset and toggled the lights. The tests (callbacks run before their socket's first
    packet, and at once when added to an open socket; a broadcast right after a restart is a new baseline; `run` sends
    its first query before it first waits) failed with the old start.
  - The LAN monitor and Hue discovery don't await a delay of 0 either, so their first query goes out as the socket
    opens; their tests failed with `sleep(0)`, and the LAN monitor's queries still go out at 0, 1, 3, 123 and 243 s.
- 2026-11-24: the Home Assistant skeleton (decision 16).
  - `tests/test_init.py` and `tests/test_config_flow.py` pass in Home Assistant 2026.11.3. The entry sets up, holds a
    `Sonoff2HueData` and unloads. The `user` step shows its form, whose description Home Assistant loads from
    `translations/en.json`, and adds the entry, which loads. A flow started while the entry exists aborts with
    `single_instance_allowed`. Without `single_config_entry`, or without `en.json`, the matching test fails.
  - hassfest 2026.11.3, run as its image runs it (from a sparse checkout of core at that tag, in a Python 3.14
    environment with core's `requirements.txt` and `packages/hassfest`, without pymicro-vad, pyspeex-noise and
    PyTurboJPEG, which have no wheel for this machine and which hassfest doesn't import), passes: no errors and no
    warnings. On a copy of the integration it reported a core dependency in `requirements`, unsorted manifest keys and
    a URL in a string as errors, and an unknown category as a warning.
  - The brand images are PNGs of 256×256 and 512×512 pixels, 8-bit RGBA, interlaced, 5,055 and 11,486 bytes. The
    bounding box of their alpha channel is the whole image (trimmed), and their corners are transparent; oxipng's
    output decodes to the same pixels as resvg's rendering. `tests/test_brand.py` checks them, and fails for a copy
    with a transparent border.
  - actionlint 1.7.12 finds nothing in `validate.yml`.
  - On pull request #5 (run 45458068984), *hassfest* (the 2026.11.3 image) found one integration, none invalid and no
    warnings, and *HACS* validated the branch `ha/skeleton`: all 10 checks passed, `brands` without falling back to
    the brands repository.
- 2026-11-24: the LAN view (decision 17).
  - The device registry: what a broadcast without a seq, address or decodable payload keeps, a fresh tracker after a
    configuration change, repeats, the 5-minute window, the time of the last packet, the devices' keys and order, and
    the callbacks. Temporary mutations (reporting repeats, not keeping detached mode, `<` for the window) each failed a
    test.
  - The LAN monitor: 8 tests on `VirtualTime` and the fake network. Queries go out at 0, 1, 3, 123 and 243 s. A network
    change gives a new socket 3 s later, where the next broadcast is a baseline and the queries come at once and after
    1 and 3 s again. Probes and other hosts' queries reach the tracker and the time of the last packet.
    `config_changed` decrypts with the new key. The registry is listening while the socket retries. A listener that
    fails before its socket opens is logged and restarts 10 s later. Stopping removes the handler and the started
    callback, and another user of the listener comes and goes while the LAN monitor goes on. Temporary mutations (a 60
    s interval, waits of 0, 1 and 3 s, no reset, a 5 s retry, no awaited removal, a `config_changed` that does nothing,
    listening left on after a failure) each failed a test.
  - The activity buffer: the newest entries kept, `get` (a limit of 0 gives nothing, where Python's `[-0:]` would give
    everything), the level names of Python's levels, categories without a dot or ending in one, exceptions and the
    record's time. Temporary mutations (`[-limit:]`, the info boundary) each failed a test. Its callbacks, tested then,
    went with the cleanup, as nothing followed them.
- 2026-11-24: hassfest and HACS passed on `main` (Validate run 45472628208 on a4318c3), so ruleset 28003079 requires
  them too.
- 2026-11-24: local development and debugging (decision 18), on the development machine, on the ports 18123 and 28443
  instead of 8123 and 18443, which the user may be using.
  - `scripts/develop setup` made `.dev/venv` in 7 s with uv's cache filled; a second run took 1.2 s. Home Assistant's
    constraints moved 10 packages that `requirements_test.txt` had resolved to older versions. The first start then
    installed nothing: from `scripts/develop hass` to "Home Assistant initialized in 2.16s" took 6.9 s. It logged only
    the known errors: `assist_pipeline` and `assist_satellite` (the skipped packages), ffmpeg and libturbojpeg.
  - Home Assistant listened on 127.0.0.1:18123 only. Onboarded through its API with a throwaway user, it answered
    `GET /api/`, its frontend's page and the logbook; sonoff2hue's flow showed its `user` step and added the entry,
    which loaded; `/api/brands/integration/sonoff2hue/icon.png` served the checkout's `brand/icon.png`. SIGINT, as
    Ctrl+C sends it, stopped it in 3 s with exit code 0, and a restart through its API (exit code 100) started it again.
  - `scripts/develop` also started the fake bridge, whose `/api/0/config` answered over HTTPS, and `lights` listed its
    lights. On Ctrl+C, Home Assistant stopped, and the script stopped the fake bridge, which ignores SIGINT as a
    background job; both ports were free afterwards.
  - The launch configurations, run as VS Code runs them: a throwaway client read `launch.json` and `tasks.json`, ran the
    `preLaunchTask` and spoke DAP to the adapter of the installed Python Debugger extension 2026.6.0 (debugpy 1.8.20),
    answering its `runInTerminal`. Home Assistant stopped at a breakpoint in `async_setup_entry` of
    `custom_components/sonoff2hue/__init__.py`, reported at the checkout's path, and its output matched the
    `serverReadyAction` pattern. Stop after its start ended it with exit code 0 in 2.3 s, with `core.restore_state`
    written; Stop during its start abandoned the start (exit code 254, nothing saved), as Ctrl+C does. The fake bridge
    stopped at a breakpoint in `tools/fake_hue/server.py` when `/api/0/config` was asked for. Through a symlink in a
    copy of the configuration folder, the breakpoint in `__init__.py` was hit too, but reported at the link's path.
  - `scripts/develop press`, which the press task runs, sent seq 1 and 2 of the device 1000abcdef with TTL 0. The
    engine's own listener (`engine/mdns.py`, joined on `eth0`) received both from the machine's own address, and the
    tracker decided Baseline, then Press. With Home Assistant and its python-zeroconf also on UDP 5353, the next press
    (seq 2 again, then 3) was received the same way.
  - `tests/tools/test_develop.py` fails for a store written without the schema's defaults, for a port check that always
    says free, and for a pin that differs from Home Assistant's. shellcheck 0.11.0 finds nothing in `scripts/develop`.
- 2026-11-25: the status tracker and the bridge supervisor (decision 21).
  - The status tracker, on `VirtualTime`: a new tracker's state; when "since" moves; the running message for one and
    several switches and targets; running again with the same message; a device id mapped twice; reachability while
    paired and unpaired; the last action and error per switch; each switch's LAN device (online for 5 minutes, a decode
    error); the 500 ms debounce; which registry changes publish; removing listeners and closing; a new configuration
    with the same or another bridge. Temporary mutations (a 400 ms debounce, no check for an unchanged service, keeping
    the targets, keeping the message while reachable, publishing every device, a `close` that keeps its timer,
    reachability while unpaired, rounding the milliseconds) each failed a test. pytest-homeassistant's cleanup check
    fails a test whose tracker still has a snapshot pending.
  - The bridge supervisor: a supervisor per configuration with the tracker kept, across three configurations, and 20
    tests (34 cases) on `VirtualTime`, with `InMemoryBridge` and the listener on `FakeNetwork`. They cover each state
    and message in the order of the checks: not paired, for five kinds of missing address or key, with and without a
    switch; no switch; the configuration's problems; an address and a key that can't be used, with the real client
    factory; names that aren't on the bridge. Then pings at 0, 10, 20 and 30 s with one warning while the error changes;
    resolving's retries, logged by the supervisor; a key rejected by the ping and by resolving, with one callback; a 404
    and a 207 with errors, without one; a callback that raises; the certificate warning; two switches running; a press
    toggling through the runner into the tracker; an unexpected stop, logged with its traceback and started again 30 s
    later with a new client; a runner that returns; stopping while waiting for the bridge, during a request and while
    running (the client closed, the keep-alive and the queries gone, the listener going on); and `config_changed`
    before, during and after a run and in the wait after a failure. Temporary mutations (retrying every 5 s,
    restarting after 10 s, not closing the client, warning at every attempt, logging in the wait after a failure, the
    callback for any refusal or called directly, problems keeping their "- ", no reachability while waiting, the
    runner's own logger for resolving, no certificate warning) each failed a test.
  - A key rejected while the runner runs (decisions 14 and 21): refused pings (401, 401 again, 403) each report the
    rejected key right after the reachability report, which still comes on changes only, and a 503 doesn't; refused
    toggles do the same before the failed toggle, with the same log lines; a refused write stays a failed target
    without the call; and the call goes through the observer guard. The supervisor's hook comes while running, for
    keep-alive requests refused at 45 and 90 s and a toggle refused at 141 s but not for a 503 at 135 s, with the state
    still running, no restart and the same reachability report; and the forwarder passes each `RunnerObserver` method
    to the tracker. Temporary mutations (no call from the ping or from the toggle, a call for any bridge error, the call
    before the reachability report, a forwarder without the hook or without one method, the tracker as the runner's
    observer, calling the hook directly) each failed a test.
- 2026-11-24: the setup flow, reconfigure and reauth (decision 22).
  - `tests/test_config_flow.py` runs them in Home Assistant 2026.11.3: 39 tests of every step, error, abort and retry,
    of reconfigure and reauth, of the single entry, and of flows closed while searching, while the bridge is read,
    while waiting and during a request. They pair over HTTPS with the fake bridge, its link button pressed at once or
    later, and use stand-ins for the search and for the pairing loop's attempts. Every result's texts are checked
    against `en.json`, with the placeholders they use, and one test walks the steps through Home Assistant's HTTP flow
    API, as the frontend gets them. They cover all 212 statements and 48 branches of `config_flow.py`, and passed 12
    runs in a row.
  - Breaking the flow on purpose failed the matching tests: no notification when an attempt's outcome changes, eager
    tasks (16 tests), the reconfigure flow's retry menu, no bridge check in reauth, and no check for a flow closed while
    the bridge is read, which left a pairing that nothing stopped. The tests also showed that `async_block_till_done`
    doesn't wait for a cancelled pairing to close its connection, so they wait for the close itself.
  - hassfest 2026.11.3, run as its image runs it (in a throwaway environment outside the repository), found no errors
    and no warnings; a copy with a placeholder in single quotes failed.
- 2026-11-25: the mDNS spike (decision 19), on the user's Home Assistant OS 18.4 (Core 2026.11.3, Python 3.14.10, an
  x86_64 VM) with SonoffLAN installed. `sonoff2hue_spike` 0.1.0, which `spike/pack.py` built from `tmp/haos-spike`
  7d2a682 (with the engine of `main` 005a4d4, copied unchanged), was installed over SSH in the Terminal & SSH app and
  started from configuration.yaml. Before that, on the branch, `scripts/check` passed with its 28 tests (fakes, no
  network), two builds gave the same zip byte for byte, and the unpacked zip imported without the integration.
  - Its verdict after 3 minutes: WORKS. The socket bound at the first attempt and stayed open: 1 start, 4 PTR queries,
    no socket error and no network change. It received 1,568 packets from 40 senders, 37 of them other hosts: 1,152
    responses and 416 queries, none malformed.
  - 21 eWeLink devices, all encrypted, each a baseline first, then duplicates: the multicast plugin's copies and the
    answers to later queries. No probes.
  - The user pressed the wall switch 5 times: 5 presses, each a `seq` one higher, 2.2–2.4 s apart, and each followed by
    3 duplicates, the switch's second announcement 1.1 s later and the plugin's copies of both.
  - Home Assistant's zeroconf kept receiving: 6,792 records, 6,771 of them from other hosts, with the same 21 devices;
    unicast replies safe. SonoffLAN, still starting when the spike started with Home Assistant, was loaded within the
    first minute, in local mode, browsing for eWeLink devices; its 147 entities wrote their state 211 times, within 5 s
    after each of the 5 presses.
  - In the first minute, Home Assistant warned that the spike's module was logging too frequently (Research findings).
  - When Home Assistant stopped for the removal, the spike logged its final result. Then configuration.yaml was
    restored byte for byte, the folder and the zip were deleted, and Home Assistant restarted. The log, which names the
    user's addresses and devices, stays out of this repository.
- 2026-11-25: the mDNS socket on Linux, and addresses from this host (decision 19).
  - Bound to the group: two new tests with real sockets, on Linux. 64 unicast datagrams from 64 ports to 127.0.0.1 all
    reached a socket on 0.0.0.0 with SO_REUSEPORT and none the engine's socket on the same port; and the engine's
    listener, joined on the loopback interface, sent its query from 127.0.0.1 and its own port, which another socket on
    the port and the listener itself received, and then received that socket's announcement. Bound to 0.0.0.0 again,
    the engine's socket got 32 of the 64 datagrams, and the bind's own check failed. By hand on the development machine
    (WSL 2, Linux 6.18), with TTL 0: joined on the LAN interface and the loopback one, the socket received the group on
    both and sent from each one's address. On GitHub's macOS 26.7.2 arm64 runner, in a throwaway workflow on the branch
    `tmp/macos-group-bind`, deleted afterwards (run 45502820479), the bind worked next to mDNSResponder, the socket
    received the group and no unicast, and sending from it failed with EADDRNOTAVAIL, as the runner's user and as root.
  - No address from a relay's copy: the listener marks a packet whose sender is one of this host's IPv4 addresses,
    which it reads with the interfaces at each (re)start, and an announcement without SRV and A records from such a
    sender has no address. Tests: the marking follows each new socket's addresses; `open_multicast_transport` takes
    them from the same look at the interfaces that chooses the ones it joins; through the LAN monitor, a press and the
    multicast plugin's copy of it from this host leave the switch's address as the press gave it; and the simulated
    switch's packet, built by `tools/send-ewelink-announcements.py` itself and sent from this host, keeps its A record's
    address. Taking the sender again, or never marking a packet, failed the matching tests, with the copy's sender as
    the switch's address in the registry. By hand, `tools/send-ewelink-announcements.py 0.0.0.0 1000fedcba 1 2`, as
    `scripts/develop press` runs it, reached the engine's real listener, bound to the group: the LAN monitor decided
    Baseline, then Press, and the registry had the A record's address, 192.0.2.10.
  - Hue discovery: a bridge's answer without an A record from the bridge and again from one of this host's addresses,
    and another bridge's answer with its A record from this host, list the first bridge at its own address and the
    second at its A record's. Taking the sender again listed this host's address as a third bridge.
- 2026-11-25: the Release workflow (decision 29).
  - **Offline:** `release-test.sh` passes 132 checks. They cover the tag rule, Latest and the notes' start,
    `check-version` and `check-main` in a scratch repository, and `released` against the stand-in `gh`: drafts,
    pre-releases and other tags left out, pages merged or one after the other, and failures. Then the workflow's
    structure, and 25 runs of its jobs. Mutations, each on a copy, failed the matching checks: `--latest=true` always,
    no exit for an existing Release, `check-main` always passing, the releases listed before the checks,
    `contents: write` for the check job, an expression in a script, pre-releases counted, the lowest version as the
    notes' start, a tag rule without its end anchor, `strip()` before the version is compared, no `--verify-tag`, no
    check of the tag after the Release, no `needs`, `cancel-in-progress: true`, no check of the previous version's
    form, and a `workflow_dispatch` trigger. shellcheck 0.11.0 and actionlint 1.7.12 find nothing.
  - **CI** on pull request #16: *Test* ran `release-test.sh` after pytest, 132/132 on the runner (CI run 45506697148),
    and `scripts/check` ran it for each commit (Commits run 45506697133).
  - **On GitHub,** with throwaway tags on the pull request's first commit, 2c3a5f5, each run's *Check the tag* failed at
    the expected step with its `::error::`, *GitHub Release* was skipped, and no Release was created: `v9.9.9` failed
    the version check (run 45505286707); `v09.9.9` the tag check (45505287396); `v9.9.9-rc.1`, cancelled while it
    waited behind them, as the concurrency group's comment says, failed the tag check when run again (45505286969); and
    `v9.9.8` on 307a291, a commit on no branch whose manifest has 9.9.8, passed the tag and version checks and failed
    "The tagged commit 307a291… isn't on main. …" (45505611040). `v9.9`, pushed with it, started no run. Each tag was
    deleted right after its run; the repository then had no tag and no Release.
  - **Dry run of 1.0.0:** `release.sh` passed `v1.0.0` on `main`'s a70e857 and, with no Release yet, chose Latest and no
    start for the notes. GitHub's generate-notes, which saves nothing, lists the merged pull requests and the new
    contributor for `v1.0.0`.
  - **Immutable releases:** at 22:58:41 UTC on 2026-11-24,
    `gh api repos/abpostelnicu/sonoff2hue-homeassistant/immutable-releases` gave
    `{"enabled":false,"enforced_by_owner":false}`, with no Release and no tag. `gh api -X PUT` on it answered 204 at
    22:58:44 UTC, and from 22:58:50 UTC on, six reads gave `{"enabled":true,"enforced_by_owner":false}`.
  - **A real Release:** 1.0.0's, checked on 2026-11-25 (below): its `immutable` field, "Immutable" on its page, its
    notes and Latest, and `gh release verify v1.0.0`, which needed a gh newer than this machine's 2.46.0.
- 2026-11-25: the import of a `sonoff2hue.yaml` in the setup flow (decision 22). 31 new tests in
  `tests/test_config_flow.py`, 70 in all, cover all 304 statements and 68 branches of `config_flow.py`. With the fake
  bridge and a key it issued: a file with its bridge adds the entry and two switches (unique ids, titles and data); the
  bridge id is the bridge's, without a note for a missing id or one in other letter case and with one for another id,
  also next to the warning about a missing pin; a file without a pin is imported with the warning; and another pin, a
  refused key, a key that can't be sent, a malformed address, a closed port, a bridge without an id and a malformed
  answer each lead to `import_failed`. Its three ways out and pairing the bridge for a file without one are followed to
  the end. Files that the import can't read, or that the run's checks refuse, show the engine's message, line and
  column, and an empty field. Through Home Assistant's HTTP flow API, the answer for an imported entry holds neither
  the application key nor the device keys; without `async_on_create_entry`, the device keys were in it. Breaking the
  import on purpose failed the matching tests: the subentries sent back, the bridge's checks without the bridge's
  settings, `PAIR_AGAIN` left in a message, the file's bridge id kept, a note for another letter case or for a missing
  id, a switch's unique id as written, and the text suggested again after an error.
- 2026-11-25: the runtime (decision 23).
  - The supervisor takes an `observer`, which its forwarder gives each of the runner's reports after the status
    tracker: the forwarder test checks both, in that order, with an observer and without, and a press through a running
    supervisor reaches the observer once the tracker has it.
  - `tests/test_runtime.py` runs it in Home Assistant 2026.11.3: 37 tests, which with `test_init.py` and
    `test_config_flow.py` cover all 257 statements and 54 branches of `runtime.py`, `repairs.py`, `__init__.py` and
    still all of `config_flow.py`. They cover the configuration as stored; setup and unload, a reload, Home Assistant's
    stop and a runtime that never started; a press with its events, toggled, failed and dropped, and the device of the
    switch's subentry; the status signal; the bridge across time, waited for every 10 s and watched every 45 s; a
    switch added, changed and removed, each restarting only the supervisor, with the listener's socket, the registry
    and the last action kept and the signals sent, and a title alone restarting nothing; a new device key reaching the
    LAN monitor; two changes while the first supervisor closes its client, and unloading meanwhile; a key rejected at
    the start and while running, with one reauthentication; reauth and reconfigure over HTTPS with the fake bridge,
    without a reload; every repair created and deleted, with its texts in `en.json`; the repair flow to reconfigure,
    also over HTTP, and for a removed entry; the switch test's results and errors; the activity at Home Assistant's
    levels. They passed 3 runs in a row.
  - Breaking the runtime on purpose, 23 ways, each failed a test: cancelling a task twice, not waiting for the old
    supervisor, the entry's background tasks, the status or the LAN monitor keeping the old configuration, no restart
    line, no reauthentication, no press event, no device id, a dropped press without its message, no issue for a
    switch not in detached mode, issues never deleted, the pin issue while unpaired, issues not linked to the entry or
    kept at unload, the activity buffer left on the logger, values trimmed, the test's client left open, the test's id
    not trimmed or its problems on lines, a restart for a title, a repair flow that finishes, and reconfigure reloading.
  - Live, in the development Home Assistant (decision 18) on 127.0.0.1, with the fake bridge and the simulated switch
    of the made-up device `1000feed23`: a pasted `sonoff2hue.yaml` without a pin started the runtime, whose socket
    bound to 224.0.0.251:5353 next to Home Assistant's zeroconf sockets; two presses toggled the light on, in 15 ms,
    and off. The repair "certificate not pinned" led through its flow to reconfigure, which paired again, restarted
    only the supervisor ("Configuration changed; restarting the bridge.", no second setup, no report about the reload)
    and cleared the issue. After the fake bridge started again with a new certificate, "certificate changed" was
    raised and fixed the same way, and the next press toggled the light. Ctrl+C stopped Home Assistant in 3 s, with no
    error about sonoff2hue in its log and no socket left on UDP 5353.
  - On pull request #19, *hassfest* found one integration, none invalid and no warnings, with `repairs.py`'s import of
    `homeassistant.components.repairs` and the issues' texts, and *HACS*, *Lint*, *Test* and the per-commit checks
    passed.
- 2026-11-25: the diagnostics (decision 26).
  - `tests/test_diagnostics.py`, 4 tests in Home Assistant 2026.11.3. The whole answer, exactly and in plain JSON, for
    a running entry with two switches, one with a device key, and three eWeLink devices, one of them unmapped and silent
    for 6 minutes, after a press that toggled and one whose toggle the bridge failed. The activity's last 1,000 of 1,006
    entries, oldest first, with an exception's type and message. An entry that isn't loaded, with only its
    configuration. Over HTTPS with the fake bridge and the integration's loggers at every level: three files downloaded
    through Home Assistant's HTTP API, while the pin wasn't the bridge's, while the bridge rejected the key, and after
    pairing again in the reauthentication, a press that toggled, a payload that the device key couldn't decrypt and the
    switch test, each with the 4 secrets as `**REDACTED**`; neither the files nor any line that the integration logged
    held the rejected or the new key, either pin in upper or lower case, with colons or as bytes, or a device key.
  - They cover all 28 statements and 10 branches of `diagnostics.py`. Breaking it on purpose, 8 ways, each failed a
    test: the pin, the device keys or the application key left out of `TO_REDACT`, no `online`, 999 activity entries, a
    status for an entry that isn't loaded, times as `str()`, and the bridge's settings alone for the configuration. So
    did a log line that named the new key while pairing, or the pin while waiting for the bridge.
  - A first `logbook.py` described the bus events, tested with 17 cases and the real logbook on the recorder; the user
    chose the entities' entries instead, and it was removed the same day (below). hassfest 2026.11.3 found one
    integration, none invalid and no warnings, with the diagnostics alone and with that logbook too.
  - Live, in the development Home Assistant, with the fake bridge and the simulated switch of the made-up device
    `1000d1a6e5`: the diagnostics file had the 4 parts, 9 activity entries at the configuration's debug level and the
    key and the pin as `**REDACTED**`, and neither of them anywhere in it.
  - On pull request #20 (CI run 45563283686, Validate run 45563283501, Commits run 45563283681): *hassfest* found one
    integration, none invalid and no warnings; *HACS* passed its 10 checks; *Lint*, *Test* with its 2,698 tests and
    each commit's checks passed.
- 2026-11-25: the switch subentry flow (decision 24), on the runtime (decision 23).
  - `tests/test_switch_flow.py` runs it in Home Assistant 2026.11.3 through the flow manager for subentries, with the
    integration set up and its runtime running on the fake network of `tests/fakes.py`: 79 tests of every step, error,
    warning and abort, which cover all 445 statements and 110 branches of `switch_flow.py`. The devices seen on the LAN
    are the runtime's registry, fed with synthetic tracker results: a press of an unused device, of another switch's
    device (one progress event per new message), of the edited switch's own one; baselines, a repeat, other events and
    a press at a time the device had; two presses at once; the timeout at 60 s by time travel; a real reload, whose
    runtime has a new registry; the entry unloaded; the dialog closed; and the device list's order and labels. Identify
    also selects a device from its broadcasts on the fake network. A whole add, from a broadcast to the switch that the
    runtime then runs, and a whole edit run against the fake Hue bridge over HTTPS; the other cases against the
    runtime's bridge in memory: repeated names, rooms without lights, entries that don't resolve, the key's three
    modes, a changed device, typed entries, a bridge that doesn't answer, one that isn't paired or can't be used,
    malformed answers, conflicts and a switch deleted meanwhile, *Save without checking* after the bridge answers again,
    and a dialog closed while the bridge is asked. Every text is checked against `en.json` with its placeholders, and
    two tests go through Home Assistant's HTTP flow API: the progress, the section and its `visible` condition, and no
    device key in any answer. 12 runs in a row of these tests and the setup flow's passed.
  - Breaking the flow on purpose, one change at a time, failed the matching tests: the registry read while the entry
    isn't loaded; a client of the flow's own instead of the runtime's; no check that the entry is loaded; a press at a
    time the device had counted; a 30 s or a 61 s wait; used devices not last; the oldest press first; rounding half to
    even in the times; *Replace it* as the key's default; *Save without checking* checking the names; a dialog closed
    while saving that still saved; a reload not followed; the switches of other subentries; the section never opened;
    no id beside a repeated name; no "no lights"; a press that never selects; removed entries kept; typed entries'
    errors on the form; the form's device check run at saving; the new key read from the wrong field; no "Selected …"
    line.
  - hassfest 2026.11.3's translations schema finds no errors in `en.json`; without `entry_type` it fails.
  - In a development Home Assistant, with the runtime, through its HTTP and websocket APIs, and the simulated wall
    switch for a made-up device: the entry reported `supported_subentry_types` `switch` with reconfigure; identify
    selected the device at its first press ("Selected …: it reported a press."); the switch was added with a light of
    the fake bridge, and the next press toggled that light; *Pick a device* listed the device with its address, type,
    *Online*, "Last press just now" and "used by …", and refused it with the form's message; identify while adding
    named the switch that uses it; the switch was edited with a new key, kept out of the answers, and deleted through
    Home Assistant's own API, each change restarting the bridge supervisor. The log had no error of the integration.
- 2026-11-25: the devices and entities (decision 25).
  - 30 test cases in Home Assistant 2026.11.3, with the LAN on `FakeNetwork` and the bridge in memory, cover all
    statements and branches of `entity.py`, `sensor.py`, `binary_sensor.py`, `event.py` and `button.py`, and still all
    of `__init__.py`, `runtime.py`, `config_flow.py` and `repairs.py`: Status in each state with its messages and
    `since`; Reachable on, off with the bridge's message, on again, and unknown while unpaired; eWeLink devices seen
    counting a device that no switch maps at once, listening, and the last packet of a repeat 30 s later; Last action
    through turned on, turned off, failed, dropped and a toggle that one of two targets failed; Online across the 5
    minutes with time travel, back within 30 s after a repeated broadcast, and with a device key, detached mode and a
    payload that can't be decoded; Press for each press of its switch only, and none while the bridge doesn't run; Test
    toggle's lights and log lines, and its three kinds of error as the action's message; the devices and their
    entities' unique ids, entity ids and categories, the same after a reload, a switch added, changed and removed while
    loaded, one added with *Add switch*, the switch's device id in its press events, and that the texts in `en.json` are
    exactly the entities' names, states, attribute names and unit.
  - Breaking the platforms on purpose, 15 ways, each failed a test: the toggle or the error always winning a tie, an
    older error's message on a toggle, a dropped press shown as failed, no count of a new device at once, no 30-second
    look at the last packet, snapshots not followed, no look at an offline switch or at the end of the 5 minutes, a
    never heard switch as unknown, a blank key counted as a key, every switch's presses, the test's error not
    converted, no entities for a switch added later, and a switch's new settings not followed.
  - Keeping the devices in step (`async_track_devices`): 5 tests in `test_entity.py`: a switch's new title renames its
    device, and a new device id moves its device and its entities' unique ids, while the entity ids stay and Press
    follows the new MINIR4M; a switch added afterwards with the old device id gets a device of its own; the same through
    *Edit switch*; a new bridge id moves the bridge's device and its entities' unique ids, and a reload then makes no
    new entities; changes saved while the entry wasn't loaded apply at its next start; and a helper of another entry
    linked to a switch's device, and a device of a subentry that isn't a switch, are left alone. Breaking the tracking
    on purpose, 8 ways, each failed one of them.
  - Live, in the development Home Assistant with the fake bridge and the simulated switch of the made-up device
    `1000c0ffee`: the devices "Hue bridge" by "Signify Netherlands B.V." and "Hall", "SONOFF" "MINIR4M" with the device
    id as its serial number, and the 7 entities with their unique ids, names, categories and icons
    (`frontend/get_icons`). Two presses toggled the fake bridge's light on and off: Status was running, Reachable on,
    eWeLink devices seen 1 device, Online on at the A record's address, Press at seq 3 and Last action turned off in 3
    ms. Test toggle turned the light on again, with its log line. Home Assistant logged no error about sonoff2hue and
    stopped on SIGINT in 2 s.
- 2026-11-25: the logbook without `logbook.py` (decision 26, the user's choice). `tests/test_logbook.py`: 2 tests with
  the real logbook on the recorder and the entities, asked as the frontend asks, for everything and for the switch's
  device page (the device's entities and the device). Two presses gave exactly Press's event and Last action's change
  each, `turned_on` and then `turned_off`, in both, and the switch's first broadcast only Online's change; the presses
  fired `sonoff2hue_press` and `sonoff2hue_toggle` with the device's id, and the logbook listed nothing for them. Two
  toggles that couldn't read the bridge gave Press, Reachable off on the bridge's device and Last action `failed`, then
  Press alone; a toggle that took 6 s and the press dropped behind it gave two Press entries, Reachable on again and
  Last action `dropped`, with no `turned_on`. With `logbook.py` back, both tests failed on the bus events' entries.
  `scripts/check` passed, with 2,797 tests.
- 2026-11-25: the end-to-end test (decision 27).
  - **Locally,** `scripts/e2e` passed all 45 checks in each of 7 runs, the last 6 in a row in 14.0 to 15.7 s: setup 0.9
    to 1.5 s with `.dev/venv` there, the fake bridge 0.4 s, Home Assistant's first start 2.2 to 2.5 s, each flow 0.1 to
    0.2 s, the two presses 3.0 s with toggles of 3 to 16 ms, the restart 3.2 to 3.9 s (1.1 s to the exit code 100),
    the press after it 1.5 s and the stop 1.1 to 1.3 s. A pass left no `.dev/e2e`, no process and no port behind.
  - **On GitHub's runner** (ubuntu-latest, the image `ubuntu-26.04` 20261116.202), in pull request #22, *End-to-end*
    passed all 45 checks in 6 runs with uv's cache restored (CI runs 45578042122 and 45579898752): 16.6 to 22.1 s, in
    jobs of 31 to 42 s. Setup took 3.0 to 4.9 s, Home Assistant's first start 3.5 to 4.8 s (its HTTP server answered
    after 2.1 to 3.0 s), the two presses 3.0 s with toggles of 2 to 4 ms, and the restart 2.6 to 4.0 s, 0.7 to 1.4 s of
    it to the exit code 100.
  - **Breaking it on purpose:** expecting the PUTs in the wrong order failed that check alone, with the PUTs expected
    and received, and exit code 1; the integration's lines of the log were printed and `.dev/e2e` kept the logs.
    Temporary mutations of the integration each failed the test: Press with the seq before the press's; a switch's
    first broadcast taken as a press; identify-by-press that never selects a device; a supervisor that never sets the
    status to running; the status recording each toggle inverted; a runner that never logs "Listening for wall switch
    presses."; a runner that gets no packets; and `turned_on` inverted in the toggle events.
  - **Faults of the test's own,** found and fixed on the way: the log scan read the empty `home-assistant.log.1` after a
    single start; a log line could be read before Home Assistant's log thread wrote it; right after the integration's
    first setup the diagnostics answered 404; and a flow's announcement could come before the answer that started its
    progress step, so the test counts the announcements before the request.
  - **Interrupted** with SIGINT during the restart, as Ctrl+C sends it: exit code 130, with Home Assistant and the fake
    bridge stopped and no process left. **Two runs at once,** from this checkout and a copy of its code, passed in 7
    pairs, 3 started together and 4 with the second run 2.0 to 3.5 s later; with one device id in both, temporarily,
    the later run took the first run's presses as its own and failed.
  - `scripts/develop setup` without `SONOFF2HUE_DEV_CONFIG` still made `.dev/config`, and with it another folder the
    same way. shellcheck 0.11.0 finds nothing in `scripts/e2e` and `scripts/develop`, actionlint 1.7.12 nothing in
    `ci.yml`.
- 2026-11-25: the README for the Marketplace (decision 28).
  - **Labels:** each italic span of `README.md` was found as a label: in `en.json`; in Home Assistant's frontend
    20261028.8 (*Add integration*, *Reconfigure*, *Reload*, *Enable debug logging*, *Download diagnostics*, *Delete*,
    *Submit*, and the Marketplace's *Add from link*, *GitHub link*, *Type*, *Integration*, *Install* and *Restart Home
    Assistant*); in core's strings (*Event received*; *Authentication expired for* the entry's title); or in
    `.vscode/tasks.json`. *End-to-end* names the CI job.
  - **Claims:** each checked against the code and this file. A switch whose names aren't on the bridge stops every
    switch: `resolve_bindings` raises for all of them together. Home Assistant 2026.11.3's logbook leaves out a change
    of attributes alone and sensors with a unit, so *eWeLink devices seen*, and its frontend shows an event entity's
    change as its event type, *Press*. The integration page's ⋮ menu at the top has *Enable debug logging*, the entry's
    menu has *Reconfigure*, *Reload* and, while it is loaded, *Download diagnostics*, and a switch's row has *Edit
    switch* and a menu with *Delete*.
  - **Automations:** a throwaway test set up both of the README's automations, read from its YAML blocks, next to the
    integration with the switch Hall (`1000abcdef`); one press ran both.
  - **Links:** the external ones answered 200, among them My Home Assistant's `marketplace_repository` and
    `config_flow_start` redirects.
  - **Rendering:** GitHub's markdown API (`gh api markdown`, GFM) and the Marketplace's own renderer (marked 18.1.1,
    js-xss 1.0.17 and the frontend's `markdown.ts`, bundled in a scratch folder) gave the same headings, code blocks
    and spans, the headings without ids, no HTML comment and no stray markup.
  - On pull request #25 (CI run 45578371321, Validate run 45578371560, Commits run 45578371548), *Lint*, *Test*, the
    commit's own checks, *hassfest* and *HACS* passed, the last with all 10 of its checks, `information` among them.
- 2026-11-25: `test_last_action_shows_only_when_its_state_changes` made deterministic (decision 26). Run alone on
  `main` 3aa1322 it failed 4 of 25 times: Reachable and Last action, which one status snapshot changes at the same
  moment, came in either order, as the logbook lists them as written. The test now compares the changes of each moment
  as counts, without regard to their order. Run alone 50 times and the whole file 10 times, it passed every time.
- 2026-11-25: the integration stands alone (decision 30).
  - **Phase 1, the recorded cases kept and the guard added:** five commits (pull request #27), from `main` at 190ec5b.
    - **Kept:** the mDNS layer's recorded cases, as the integration's own test data. `tests/engine/mdns_packets.json`
      holds 36 packet cases, 29 messages with their announcements and probes and 7 parse errors, each error as the
      message that `DnsFormatError` carries. `tests/engine/mdns_text.json` holds the string rules' tables: 25
      white-space code points, and 1,475 uppercase and 1,459 lowercase mappings. `test_mdns_packets.py` replays the 36
      packets, and the string rules' test reads the tables, 21 cases with their expectations unchanged. A parse error
      and a `seq` changed in the data failed exactly those two cases.
    - **Deleted:** the two generators of recorded cases (14 files), `tests/engine/fixtures` (28,671 lines), the 1,811
      cases that replayed them, and the fixture-driven parts of `test_picker.py` (150 cases), `test_switch_form.py` (27)
      and `test_switch_rules.py` (143), whose 27, 24 and 34 hand-written cases stayed: 33,500 lines in all.
    - **Added:** `tests/test_own_mdns.py`, 47 cases. It reads the integration's 39 modules, 27 of them in the engine,
      with `ast`, and its manifest. Its other 44 cases show its checks on synthetic sources and manifests: 22 forms of
      an mDNS import found and 7 other imports left alone, 6 forms of a Home Assistant import, relative imports, and 7
      manifests that rely on Home Assistant's mDNS and one that doesn't. It failed, and passed again once each was
      reverted, with `from homeassistant.components import zeroconf` added to `runtime.py`, with
      `from homeassistant.core import HomeAssistant` added to `engine/dns.py`, and with a `zeroconf` matcher and
      `network` in `after_dependencies` added to the manifest.
    - **types-PyYAML** 6.0.12.20261122, the newest release of the stubs for PyYAML 6.0, is pinned in
      `requirements_test.txt`; mypy (strict) reads `yaml`'s types from it, and `scripts/develop setup` installed it in
      `.dev/venv` with Home Assistant's constraints.
    - **Checks:** `scripts/check` passed with 2,797 tests on 190ec5b, 702 after the deletions and 749 at the end.
      `check-commits.sh` and `test-each-commit.sh` passed for all five commits, and `scripts/e2e` all 45 checks.
  - **A `lan`** (pull request #31): seven commits on `py/lan`.
    - **The mDNS layer keeps every result.** Its string rules moved into it: `dns.py` compares names and TXT keys by
      each character's simple uppercase form (`case_key`, with equals, starts-with and ends-with helpers) and counts a
      name's length in UTF-16 code units; the record part of `ewelink.py` keeps its 25-character white space, its
      one-to-one lowercase for device ids and its signed 64-bit `seq`; `_discover_mdns` uses `dns.py`'s helpers.
      `test_mdns_text.py` replays `mdns_text.json` for every code point against them, and checks that no non-ASCII
      character gets an ASCII key; `mdns_packets.json` is unchanged. A differential fuzz of the modules before and
      after, in the git-ignored `.dev`, gave the same results for 180,000 random strings, 180,000 random packets
      (153,588 parsed, with 15,032 announcements and 13,357 probes) and 600 runs of Hue discovery's mDNS part with the
      same random packets on the fake network (374 bridges). `mdns.py` and `lan_monitor.py` changed in words only:
      without docstrings, their syntax trees are as before.
    - **Python's rules outside it.** Payloads: `base64.b64decode(validate=True)` and `json.loads` into plain values
      (`ewelink.Json`), the key MD5 of the device key stripped with `str.strip()`, the same error strings, and nothing
      raised, also for a payload nested too deeply. Presses: settings compare with `==`, `relaySeparation` and outlets
      are read with `int()`, relay states compare with `casefold()`, the tracker keys devices by `casefold()`, and
      summaries and relay changes write values as `json.dumps` does. The LAN registry keys and orders devices by
      `casefold()`. Cloud discovery reads `response.json(content_type=None)`, logs any failure at debug level, and takes
      the `internalipaddress` values that `ipaddress` reads, written as it writes them, with an `id` that is a text.
    - **The simulated switch** has type hints and docstrings, and ruff and mypy (strict) check it, with only N999
      ignored for its name. Its packets are the same for ASCII ids; a TXT string counts its length in bytes, where a
      non-ASCII id made a record that overran itself.
    - **What users may notice, for unusual input only:** base64 with white space isn't valid; `NaN` and duplicate keys
      parse; `1`, `1.0` and `true` compare equal; other digits count in numbers; summaries escape as Python's `json`
      does (`-7.5` for `-7.50`); in the LAN registry 'ß' and 'ss', or the Kelvin sign and 'k', are one device, and '_'
      sorts before the letters; the cloud's keys are case-sensitive, and an answer with an entry that isn't an object
      gives no bridges.
    - **Checks:** `scripts/check` passed with 1,021 tests: the slice's files went from 182 tests to 223, with 23 more
      for presses, 14 for payloads and the simulated switch, 3 for cloud discovery and 1 for the string rules. Changing
      the simulated switch back failed its new test. `scripts/e2e` passed all 45 checks, in 19.9 s. `check-commits.sh`
      and `test-each-commit.sh` passed for all seven commits.
  - **B `hue`** (pull request #33): eight commits on `py/hue`.
    - `hue_address.py`, from 790 lines to 123: an address is an IPv4 address as `ipaddress` reads it; an IPv6 address,
      compressed and in brackets, with a zone of letters, digits and `._~-`, in brackets when a port follows; or a host
      name whose labels, in the ASCII form that yarl's IDNA gives an internationalized name, hold 1-63 letters, digits
      and hyphens, neither first nor last a hyphen, at most 253 characters in all, the last label not all digits. A port
      is 1-65535 in ASCII digits, else 443. `BridgeUrl` keeps the yarl URL that requests go to, with `host` and `port`,
      and the messages stay word for word.
    - `hue_client.py`, from 700 lines to 523: a body is decoded by its charset, quotes removed, through Python's codecs,
      as UTF-8 without one or for a charset Python can't decode text with, invalid bytes replaced and a byte order mark
      dropped; a reason phrase is the status line's, else `HTTPStatus`'s. A CLIP v2 answer is read with `json` into the
      client's dataclasses by the keys the bridge writes: a null counts as missing, and an answer that isn't one is the
      transient `HueApiError("Unexpected response from the Hue bridge for '<type>'.")`. URLs are built with yarl, and an
      id is one segment, `urllib.parse.quote` with its dots escaped too, so `..` stays an id. Requests carry aiohttp's
      default headers and are decompressed. The connector of its own keeps idle connections 300 s and uses
      `ThreadedResolver`, the system's resolver. A failure without an answer is told in the system's words
      (`os.strerror`), and a TLS failure or another certificate gives `CERTIFICATE_MISMATCH`.
    - `hue_setup.py`, outside discovery: the bridge's config and the pairing answers are read with `json`,
      `not_success_message` gives "HTTP 404 Not Found", and the device type takes what `str.isalnum` takes.
      `toggler.py`: a target without an id has no known state. The fake bridge reads its bodies with `json`, checks them
      with `str` methods and `len`, and its settings with `ipaddress` and `int`.
    - **What users may notice:** bridge addresses refused now: `192.168.1`, `0x7f.1` and octets with a leading zero, a
      name with `_`, a trailing dot or an `xn--` label that doesn't decode, `@bridge`, `bridge/a/..` and an empty port
      (`bridge:`); an internationalized name is mapped by IDNA (UTS 46), and the "(host:port)" of a failure shows an
      IPv6 address in brackets and a name in its Unicode form. Messages: "No Hue bridge answered at …: HTTP 404 Not
      Found", "…: The request timed out." and, for a device without TLS, "…: The TLS handshake failed (…)"; "Hue bridge
      not reachable: Connection reset by peer (…)" or "Server disconnected (…)", in Python's words; a JSON answer that
      isn't an object "does not look like a Hue bridge"; a status without a reason phrase no longer leaves two spaces. A
      malformed answer is a transient `HueApiError`: a toggle is tried once more and fails with its message, the
      supervisor waits for the bridge every 10 s instead of stopping and restarting 30 s later, and the flows show it as
      `cannot_connect`, `lights_unavailable` or `import_failed` with its message. Edge cases: keys only as the bridge
      writes them (`bridgeid`, not `BridgeID`), the last of a repeated key, any charset that Python decodes, byte order
      marks of UTF-16 and UTF-32 only with their charset, a pairing error with a blank description reads "no
      description", and names resolve with the system's resolver.
    - **Tests:** 257 cases in the slice's files, where there were 91: `tests/engine/test_hue_address.py`, new, 67 cases
      of addresses accepted and refused; `test_hue_client.py` 92 (was 18); the bridge info and pairing tests of
      `test_hue_setup.py` 48 (was 29); `test_toggler.py` 13 (was 8); `tests/tools/test_fake_hue.py` 37 (was 36). They
      cover all of `hue_address.py` and `toggler.py`; all of `hue_client.py` but the branches of a transport closed
      unused and of a connection without a certificate; and all of `hue_setup.py` outside discovery. 19 mutations, one
      at a time, each failed a test; two more survived, an explicit check for white space and a lowercasing that yarl
      already does, both then removed.
    - **Checks:** `scripts/check` passed on each commit, ending with 1,316 tests, and `check-commits.sh` passed.
      `scripts/e2e` passed all 45 checks in 13.6 s. Two TLS tests that depend on how a closed connection is reported
      each passed 10 runs in a row.
  - **C `yaml`** (pull request #30): three commits on `py/yaml`.
    - `yaml_import.py` reads the pasted text with PyYAML, through a subclass of
      `getattr(yaml, "CSafeLoader", yaml.SafeLoader)` whose only implicit resolver is null's. A pass over the parser's
      events refuses a second document, a tag other than `!!str` on a value and nesting deeper than 32 levels; a walk of
      the composed node tree, in the order of the text, refuses a key that isn't a single value and a repeated key; then
      a probatio schema checks the shape. Each error gets the line and column of its node, the key's for an unknown key,
      and the first in the text is reported. The YAML parser and URI modules are deleted, and `yaml_import.py` went from
      1,010 lines to 251: 4,041 lines removed and 668 added.
    - **What users may notice:** word for word as before: an unknown key, the three messages for a value of the wrong
      kind, `duplicate key '…'.`, the empty switch entry, and the form `Config, line L, column C: …`. New: a syntax
      error gives PyYAML's problem; a tag "the tag !!int isn't supported here. Write the value without the tag.", unsafe
      tags included; a second document "only one document is allowed, but another one starts here."; nesting "the text
      nests more than 32 levels deep here."; a character that YAML refuses "unacceptable character #x0007: control
      characters are not allowed". Columns count characters, a byte order mark at the start is skipped, and a document
      of a single value, such as `a`, is refused with "expected 'key: value' lines here.".
    - **Tests:** `tests/engine/test_yaml_import.py`, 133 cases, each run with the C loader and again with PyYAML's own,
      so 266, covering whole files, values kept as written (25 plain values that stay text), nulls, the empty switch
      entry, unknown and duplicate keys, values of the wrong kind, the first of several problems, documents that aren't
      "key: value" lines, anchors, aliases and a merge key, several documents, syntax errors, 19 refused tags, tabs,
      CRLF, a byte order mark, refused characters and the nesting limit, each with its line and column. They cover all
      144 statements and 52 branches of `yaml_import.py`. By hand, in scratch scripts in the git-ignored `.dev`: 400,000
      random texts, half for each loader, raised nothing but `ConfigError`; and 60,000 random settings written with
      `yaml.safe_dump`, in block and flow style, some with CRLF, read back exactly when they fit the schema and were
      otherwise refused at a line and column inside the text.
    - **Checks:** `scripts/check` passed on each commit, with 980 tests; `check-commits.sh` passed.
  - **D `switches`** (pull request #32): four commits on `py/switches`.
    - `config.py`, `targets.py`, `picker.py`, `switch_form.py`, `switch_rules.py` and `switch_flow.py` follow one rule
      for names: the resolver, the picker and the switch rules compare names and ids stripped and casefolded
      (`targets.name_key` and `targets.refers_to`), an empty entry names nothing, a `room:` or `zone:` prefix is taken
      in any case (`targets.split_kind`), and the known names in the resolver's messages are in `str.casefold` order. A
      device id is normalized with `strip().lower()` and compared with `casefold()` (`SwitchSettings.has_device_id`, for
      `find_switch`, `validate_for_run`'s new duplicate check and the switch rules); blank is `not s or s.isspace()`.
      The switch rules and the form count characters with `len()`, find control characters by their category Cc and
      white space in a key with `isspace()`, and remove duplicate entries by casefold; the picker's resources are
      ordered by the casefolded name, and its selections key resources by kind and id. The switch flow catches
      `Exception` and logs the traceback where it showed its `unexpected` texts. The limits, the messages, their fields
      and order, the rules for a conflict and a missing switch, the device key's rules, and the flow's steps, menus and
      translation keys stay.
    - **What users may notice:** edge cases only. Names and ids match under Unicode case folding in the picker and the
      resolver alike: `STRASSE` names `Straße`. White space is Python's everywhere: U+001C-U+001F are stripped too. A
      name of 63 letters and an emoji fits in 64 characters. The known names list `_Spare` before `Arc lamp`.
    - **Tests:** 293 in six files, where four had 164: `test_targets.py` (41, new), `test_config.py` (11, new),
      `test_picker.py` (37), `test_switch_form.py` (39), `test_switch_rules.py` (86), and `test_switch_flow.py`'s 79.
      Together they cover every statement and branch of the six modules, 884 and 250. 19 of 20 mutations each failed a
      test; the 20th was equivalent.
    - **Checks:** `scripts/check` passed with 1,150 tests; `check-commits.sh` and `test-each-commit.sh` passed for every
      commit, and `scripts/e2e` all 45 checks, in 16.2 s after the last rebase.
  - **E `engine-core`** (pull request #29): three commits on `py/engine-core`.
    - `runner.py`, `supervisor.py`, `status.py` and `activity.py`: device ids are keys in their casefolded form, and
      blank is `not s or s.isspace()`. A device id mapped twice, in the runner's bindings or in the status tracker's
      `set_running`, raises a `ValueError`, "More than one switch has the device id 1000abcdef." The status tracker
      tells bridges apart by a tuple of the address, the key and the pin, where a missing pin is an empty one. The
      activity buffer names an exception by `type(err).__name__`, keeps a deque of the newest 1,000 entries, and takes
      its ids from `itertools.count`. The states, messages, problems, timings, log lines and their levels, eager starts,
      cancellation, observer reports and `on_key_rejected` stay.
    - **What users may notice:** an activity entry names an exception by its Python class; U+001C-U+001F alone count as
      a missing bridge address, key, pin or device key.
    - **Tests:** 103 in the four files. Six mutations each failed a test.
    - **Checks:** `scripts/check` passed with 750 tests; `check-commits.sh` and `test-each-commit.sh` passed for all
      three commits, and `scripts/e2e` all 45 checks, in 14.6 s after the rebase.
  - **F `ha-layer`** (pull request #28): four commits on `py/ha-layer`.
    - The Home Assistant code (`__init__.py`, `runtime.py`, `config_flow.py`, `entity.py`, the four platforms,
      `diagnostics.py`, `repairs.py` and `const.py`) and its tests: input is stripped with `strip()`, blank is
      `not s or s.isspace()`, and device ids and bridge ids compare with `casefold()`. Where the setup flow showed its
      `unexpected` or `unreadable_config` text, it catches `Exception` and logs the traceback with `_LOGGER.exception`,
      with the same steps, translation keys and texts; ruff counts that call as handling the exception.
    - **What users may notice:** nothing in the flows, steps, menus, translation keys, data, entities, unique ids,
      devices, repairs, bus events, logbook or diagnostics; any other failure while reading the bridge, connecting to
      the file's bridge or reading the file shows the flow's text and logs its traceback, where Home Assistant's flow
      API answered 500.
    - **Checks:** `scripts/check` passed on each commit, with 749 tests. The 277 tests of the Home Assistant layer cover
      every statement and branch of its modules: 1,449 statements and 282 branches. `scripts/e2e` passed all 45 checks.
  - **The cleanup** (pull request #34): eight commits on `py/cleanup`, from `main` at 5a5f3eb, merged as b19800d.
    - **Deleted:** `engine/text.py`, `jsonnode.py` and `errors.py`, which nothing imported any more (487 lines), and
      what nothing in the integration used: `ActivityBuffer.add_added_callback`, with the `handle()` override that kept
      its callbacks outside any lock; `BridgeUrl.uri` and its `str()`; `switch_form.current_device_id`; the HTTP-like
      status codes of `SwitchError` and its subclasses, which the switch flow tells apart by their classes
      (`ResourcesError.status` stays, as the flow uses it); and the runner's helper that wrote a missing value as
      "(null)".
    - **Converted:** `hue_address.try_create_base_url`, which returned a URL or a message, is gone; `create_base_url`
      raises `ConfigError` with the same texts, which the setup flow's address step shows. The Hue ids and names are
      text: `HueLight.id` and `name`, `HueGroup.id` and `name`, `HueTarget.id`, and `HueBridgeInfo.bridge_id`, as the
      client refuses a resource without an id and names one without a name by its id; `HueGroup.grouped_light_id` and
      `HueBridgeInfo`'s `name`, `model_id` and `software_version` stay optional, as a well-formed answer can lack them.
      `SwitchSettings.lights` and `groups` are `Sequence[str]`, and the engine and the flows no longer handle `None`
      entries.
    - **What users may notice:** the import refuses a null entry of `lights` or `groups` (`-` alone, `~` or `null`) at
      the entry's value, or right after a lone `-`, with both PyYAML loaders: "Config, line 4, column 9: an entry of
      'lights' is empty. Write the light's name or id, or remove the '-'.", and for `groups` "… Write the room's or
      zone's name or id, or remove the '-'."; before, the run's checks refused such a file with "switches[0] ('Hall'):
      empty light/group name.", which a blank text entry such as `' '` still gets. Log lines write a missing value as
      `None`: "Hall (1000abcdef): online at None (seq None; payload not decoded)." and, at debug level, "Ignoring
      unconfigured eWeLink device 1000999999 at None.". The bridge address's messages and "Config: switches[1] is
      empty. Remove the stray '-' or fill in the entry." are unchanged.
    - **Reworded:** `engine/__init__.py`, `tools/e2e` and the `TRY003` comment of `pyproject.toml`; the docstrings of
      the picker and the switch rules that spoke of `None` ids; and those of `CONF_BRIDGE_IP` and
      `CONF_CERTIFICATE_SHA256`: an address entered in the setup flow is stored trimmed, one found by the search as the
      search wrote it, and an imported file's `bridgeIp`, key and pin as written; reauthentication keeps the stored
      address, and `create_base_url` strips it either way. Outside the README, `en.json` and this file, the sweep then
      found only legitimate words.
    - **Tests:** 1,307, where there were 1,316: 18 cases of removed behavior or null input went, and 9 came, for the
      empty entries, one more ordering case and the `None` log line, each with both loaders where it applies; each new
      test fails without its change. The mDNS layer's recorded cases, their tests and `tests/test_own_mdns.py` pass
      unchanged.
    - **Checks:** `scripts/check` passed on each commit (`test-each-commit.sh`), and `check-commits.sh` for all eight.
      `scripts/e2e` passed all 45 checks, in 13.0 s at the head. On the pull request, all 16 checks passed; one
      per-commit job failed on its first run, in the test that the entry below made deterministic, and passed when run
      again.
  - **The README, `en.json` and this file**: three commits on `py/docs`, prepared on 5a5f3eb and rebased onto the
    cleanup at b19800d.
    - `README.md` describes sonoff2hue on its own, and *How it works* says that it listens with an mDNS socket of its
      own and doesn't need Home Assistant's zeroconf. *Importing a sonoff2hue.yaml* documents the format: the keys under
      `hue` and `switches`, an example with made-up values, which `yaml_import.parse` reads and `validate_for_run`
      accepts, what the import checks, and how it reports a problem, quoted from the code
      (`Config, line 4, column 3: duplicate key 'appKey'.`, `switches[1]: deviceId is missing.`, and the cleanup's
      `Config, line 5, column 9: an entry of 'lights' is empty. Write the light's name or id, or remove the '-'.`,
      which the merged import gave for `- ~` in a switch's `lights`). *Troubleshooting*
      quotes the transport messages, which `get_bridge_info` and `HueClient.ping` gave for a closed port
      ("No Hue bridge answered at …: Connection refused (…)", "Hue bridge not reachable: Connection refused (…)").
      *Development* describes the tests, the mDNS layer's recorded cases, the guard test and the simulated switch. The
      section on differences from another program went, and its cautions became general ones. GitHub's markdown API
      rendered it with 26 headings, 8 code blocks, 169 italic and 19 bold spans, 11 links and no stray markup; its
      italic spans are the same set as before, each a label of `en.json` or Home Assistant's frontend (decision 28).
    - `en.json`: the import step's description and the hint for a switch's name, with their keys and placeholders
      unchanged.
    - This file: a project memory of its own, with a Behavior section for the product rules and the mDNS layer, the Home
      Assistant UI's places, the research findings without other platforms', each decision restated, the retired ones as
      one line, and a condensed verification log. Its layout matches `git ls-files` after the cleanup, and its Behavior,
      decision 30 and log follow the cleanup's changes: the import's empty entries, `None` in the log lines,
      `create_base_url`, and the activity buffer without callbacks.
      `gh api repos/abpostelnicu/sonoff2hue-homeassistant/rules/branches/main` lists the required checks *Lint*, *Test*,
      *All commits pass*, *hassfest*, *HACS* and *End-to-end*. GitHub's markdown API rendered this file with its four
      tables whole (the layout's 27 rows, the product rules' 26, the UI's 9 and decisions 1 to 30) and no stray markup.
    - **Checks:** `scripts/check` passed with 1,316 tests on 5a5f3eb and with 1,307 after the rebase, and
      `check-commits.sh` and `test-each-commit.sh` for each of the three commits. A sweep of the three files for the
      names of the other platform, its tools and its interface found only `radio_browser`, a Home Assistant integration,
      and the README's "your browser", where VS Code opens Home Assistant.
- 2026-11-25: `tests/test_diagnostics.py::test_no_secret_reaches_the_diagnostics_or_the_log` made deterministic
  (decision 26). It sends the switch Desk a payload encrypted under Hall's device key, and expects the decode error
  "decryption failed, check the deviceKey". The fake LAN (`Lan` in `tests/fakes.py`) encrypted it under a random IV,
  and under the wrong key the plaintext ended in valid PKCS7 padding for 755 of 200,000 random IVs, 1 in 265: then the
  error is "payload is not valid JSON, check the deviceKey", and the test failed, on `main` too.
  - **Reproduced:** with one of those IVs forced on every IV that `ewelink.encrypt` draws, by a throwaway pytest
    plugin, the test failed at that assertion every time.
  - **Fixed:** `Lan` encrypts its broadcasts under a fixed IV, `ENCRYPTION_IV`, under which Hall's key fails to decrypt
    Desk's payload. The other tests decrypt what they encrypt with the same key, which succeeds under any IV.
  - **Checked:** with the plugin forcing that IV as before, the test passed, as the fake no longer draws one. It passed
    25 runs in a row, and `scripts/check` passed with its 1,307 tests.
- 2026-11-25: the live test on the user's Home Assistant OS (Status), on Home Assistant OS 18.4 with Core 2026.11.3 on
  Python 3.14.10, in an x86_64 virtual machine.
  - **Before:** the integration had run there since that morning from `9dccc0e`, from before decision 30, with the
    paired bridge and 4 switches. Their broadcasts are encrypted and none has a device key, so the runner reads their
    presses by `seq` alone. Real presses on two of them had toggled their lights, a group in 74 ms and 10 lights in
    542 ms, and *Test toggle* on a third had turned its 3 lights on.
  - **Deployed:** `main` at 2bec130, by hand over SSH in the Terminal & SSH app. The installed folder was saved off the
    host, its 83 files compared by SHA-256; the 38 files of `custom_components/sonoff2hue` at 2bec130 were unpacked next
    to it and compared by SHA-256 with git; then the new folder took the old one's place, and the old one was deleted.
    A copy renamed inside `custom_components` would compete for the domain: Home Assistant 2026.11.3's loader
    (`_get_custom_components`) resolves every folder there that has a `manifest.json`, and keys the integrations by
    their manifests' domain. `ha core check` passed, and `ha core restart` took 22 s.
  - **After the restart:** the entry loaded, and Home Assistant compiled the 33 modules of the new build, none of the
    six that decision 30 removed. Status was running, "4 wall switches toggle 17 Hue lights, rooms and zones.", without
    problems; Reachable on; each switch Online at its own address, not the `hassio` bridge's (decision 19); eWeLink
    devices seen 21. No false press: each Press kept its last event and `seq`, each switch's `seq` was the one before
    the restart, and no light changed; Last action was unknown, as each setup starts the status tracker afresh. The log
    had no warning or error of the integration, only Home Assistant's notice about custom integrations.
  - **Switch test:** *Test toggle* turned a switch's 3 lights on, then off again, as they had been; Press and Last
    action stayed as they were, as the test reaches neither.
  - **Log levels:** the log had the integration's info lines before the restart and only its warnings afterwards:
    `configuration.yaml` has no `logger:` section, and the level set while it ran didn't outlast the restart.
  - **Real press:** the user pressed a switch with 10 lights. Its `seq` was one above the one before the restart; Press
    fired once with it, and the toggle turned all 10 lights off in 549 ms, none failed. Online's `last_press_at` took
    the press's time, and the log had no warning or error. Exactly one toggle ran: Press kept that one event, and the
    lights stayed off.
- 2026-11-25: release 1.0.0 (decision 29), after the live test. The manifest's `version` on `main` was already 1.0.0,
  and *CI* and *Validate* had passed on `main` at 7ba561f. `release.sh` passed `v1.0.0` on that commit locally, step by
  step as *Check the tag* runs it: the tag, the manifest's version and the commit on `main`; with no released version,
  Latest and no start for the notes. `git tag -a v1.0.0 -m "sonoff2hue 1.0.0"` on 7ba561f was pushed at 20:34:58 UTC.
  - **The workflow:** *Release* (run 45696334492) passed *Check the tag*, and *GitHub Release* published the GitHub
    Release "sonoff2hue 1.0.0" at 20:35:16 UTC.
  - **The Release:** the tag `v1.0.0` on 7ba561f; neither a draft nor a pre-release; no assets; `immutable` true, and
    Immutable on its page; Latest, as `releases/latest` gives `v1.0.0`. Its notes list the 39 merged pull requests by
    their titles, six of which, from before decision 30, name the other implementation; then the new contributor and a
    link to the commits up to the tag. `gh release verify v1.0.0`, with gh 2.105.0, verified its attestation.
  - **What the Marketplace installs:** the tag's source archive, `archive/refs/tags/v1.0.0.zip`, holds the 38 files of
    `custom_components/sonoff2hue` that the live test ran, the same by SHA-256.
- 2026-11-25: the README's *Installation* marked for the Marketplace (decision 28).
  - **GitHub:** its markdown API rendered the README as before, but for two empty lines where the markers are, and
    without their text.
  - **The Marketplace:** `markdownWithRepositoryContext` of frontend 20261125.0, run with Node 26 on the README, left
    out exactly the text between the markers: the headings *Installation*, *From the Marketplace* and *By hand*, with
    the install badge. *Requirements*, *Setup* and its badge stayed.
  - The Marketplace shows the README at the tag of the release on offer, so the markers reach it with the release
    after 1.0.0.
- 2026-11-25: the submission to the Marketplace's catalog (decision 31).
  - **The notes of Release 1.0.0:** GitHub's generated notes, kept outside the repository, were replaced through the
    REST API with a summary of what sonoff2hue does and needs; only `body` changed. The Release stayed immutable and
    Latest, with its time of publication and its tag on 7ba561f, and `gh release verify v1.0.0` still verified its
    attestation.
  - **Before submitting:** *Validate* on `main` at 4765618 (run 45700352910), with hassfest's image of that day: one
    integration, none invalid, no warnings; the HACS action passed its 10 checks. hassfest 2026.12.0b0's
    `brand_images.py`, run by itself on `brand/`, reported nothing. sonoff2hue was in none of `data-v2.hacs.xyz`'s
    lists, the removed one included, and `sonoff2hue` wasn't a domain of the upcoming core release. The repository has
    a description, 15 topics, issues, the Apache-2.0 license and a Release.
  - **The pull request:** hacs/default#14951, opened at 21:02:38 UTC from `abpostelnicu`, with maintainer edits
    allowed, no review requested, `integration` +1/-0, and the body as drafted. After the edit, the list was valid
    JSON, sorted by `str.casefold`, and every entry matched the schema.
  - **Its checks:** *Lint* (*JQ*, *JSON schema*, *Sorted*) and *Check*'s *Preflight* and *Editable PR* passed, without
    a maintainer's approval. The checks of a new repository skip until it has the label *New default repository*,
    which `hacs-bot` hadn't added after 8 minutes; it was then 334th of 334 in the open, non-draft queue.
- 2026-11-26: the beta canary (decision 35, part 1), `.github/workflows/beta.yml`.
  - **Lint:** actionlint 1.7.12 with shellcheck 0.11.0, their release binaries for macOS checked against their
    published SHA-256, found nothing in the five workflows, and shellcheck nothing in each of `beta.yml`'s five scripts
    on its own.
  - **The workflow's commands, locally,** with uv 0.13.7 in a throwaway environment under `/tmp`, deleted afterwards,
    and mypy for Linux. The lookup gave `pytest-homeassistant==2026.12.0b0`, and the install 132 packages for macOS on
    CPython 3.14.4, with `homeassistant` and `pytest-homeassistant` 2026.12.0b0 the only pre-releases. mypy found no
    issue in 47 source files. pytest passed 1,304 tests in 25 s, skipped the 2 Linux-only socket tests, and left out
    the test of `requirements_dev.txt`'s pins, which fails on the beta, as expected: `aiohasupervisor` 0.11.0 against
    0.8.1. Its 14 warnings are aiohttp's `NotAppKeyWarning`, from Home Assistant's own `components/http`; 2026.11.3
    shows none. With every log line at WARNING or above printed (`log_cli`), Home Assistant reported nothing about the
    integration: no "Detected that custom integration" and no "will stop working".
  - **A race in a test:** in the first run, `tests/test_switch_flow.py::test_adding_a_switch_with_the_fake_bridge`
    passed but failed at its teardown. asyncio's server accepted a connection to the fake Hue bridge after it had
    closed (`Server._attach`), and the `hass` fixture raised the error that was logged. It isn't the beta's: run alone
    30 times, it failed that way 2 times on the beta and 2 times on 2026.11.3, on macOS. None of the last 200 runs on
    GitHub failed.
  - **On GitHub,** in #43 at 79a7720, on ubuntu-latest (`ubuntu-26.04`, 20261116.88.1). Its push ran *Beta* through the
    path filter (run 45770919263), next to *CI*, *Commits* and *Validate*, which passed. The job missed its new key,
    `setup-uv-2-x86_64-unknown-linux-gnu-ubuntu-26.04-3.14.4-py-<hash of requirements_test.txt>-beta`, made `.venv`
    with `/usr/bin/python3.14` (3.14.4), and resolved and installed 129 packages, as the resolution for Linux had it:
    `pytest-homeassistant` and `homeassistant` 2026.12.0b0 the only pre-releases, httpx 0.28.1, grpcio 1.85.4. It
    printed "Home Assistant 2026.12.0b0 on Python 3.14.4" and appended it to the job summary. mypy found no issue in 47
    source files, and pytest passed 1,306 tests, the socket tests included, with 1 deselected and the same 14 warnings,
    in 23 s. The job took 48 s and saved its cache, 164 MB, under its own key, while CI's *Test* restored CI's key,
    without the suffix, and saved nothing. Its only annotation is setup-uv's warning that there is no managed Python to
    cache.
  - **By hand, on the branch:** `gh workflow run beta.yml --ref ha-beta-canary`, before `main` had the file, started a
    `workflow_dispatch` run on the branch (run 45771243119), which passed the same way in 51 s. It missed the key
    again, as a pull request's cache stays on its merge ref, and saved its own.
- 2026-11-26: decision 36, the small fixes, on the branch `small-fixes`.
  - **A steadier Online:** the LAN monitor's new tests in `test_lan_monitor.py` run it on `VirtualTime` with MINIR4Ms
    that answer each query as it goes out (`Answers`), so a switch heard at 243 s is late after 393 s and online up to
    543 s. While two switches answer, the queries go out at 0, 1, 3, 123, 243, 363, 483 and 603 s. With the answers to
    the queries at 363 and 423 s lost, the looks ask at 423 and 453 s, whose answer comes, and the next query is at
    573 s. A switch gone after 243 s is asked at 423, 453, 483, 513 and 543 s, then at 663, 783 and 903 s. An
    unconfigured device that stops answering brings no query. A switch that `config_changed` adds at 200 s and removes
    at 250 s, silent since 3 s, brings queries at 213 and 243 s. In `test_binary_sensor.py`, Online stays on when two
    answers in a row are lost, in Home Assistant with its clock ticked by the second: the queries went out at 123 s
    (lost), 153 s (lost), 183 s and 303 s. `test_registry.py` checks `heard_within` at its bound.
  - **Mutations**, applied by a throwaway pytest plugin outside the repository. With the LAN monitor of `main`, the
    tests of the lost answers, the silent switch and the configuration change failed, and Online went on, off and on
    again, as on the user's Home Assistant OS; the tests of the switches that answer and of the unconfigured device
    pass on it, as they pin what the change must not add. Each of these failed some of the new tests: a look that may
    send a regular and a catch-up query together (three, with two queries at 483 s for the silent switch), a switch
    late at any silence (four, those two included), late beyond the 5 minutes (the silent switch), late at exactly
    150 s (two), every device of the registry counting (the unconfigured device and the configuration change), and a
    `config_changed` that keeps the old switches (the configuration change).
  - **Test toggle's error:** `test_button.py` checks the error's translation domain, key and placeholders, and that its
    text is the switch test's message, for a bridge that isn't paired, a light that isn't on it and a bridge error.
    With the button of `main`, all three cases failed, and so they did with the `exceptions` section dropped from the
    loaded translations, where the error's text was `test_toggle_failed`. Through the WebSocket API, a throwaway test
    outside the repository got an answer with the message, and with the change only, the translation's domain, key and
    placeholders; the log line "Error during service call to button.press: …" was the same before and after. hassfest
    passed the integration (Research findings).
  - **The entry's functions:** in `test_init.py`, a platform that doesn't unload leaves the entry `FAILED_UNLOAD` with
    Home Assistant's `unload_platforms_failed` as its reason; with the unload of `main`, the reason was
    `async_unload_entry_failed`, and the test failed. The config flow tests' stand-ins for the setup and the unload
    return None.
  - **Checks:** `scripts/check` passed on each of the three commits (`test-each-commit.sh`, with mypy for Linux), with
    1,312, 1,312 and 1,313 tests, 7 and then 1 more than `main`'s 1,305, and `check-commits.sh` passed for all three.
    `scripts/e2e` passed all 45 checks, in 26.4 s. hassfest 2026.11.3 passed the integration.
  - **Seen on the way:** the teardown race of `tests/test_switch_flow.py::test_adding_a_switch_with_the_fake_bridge`,
    which the beta canary's entry above describes, errored in some local runs, on `main` too (3 of 20 runs of its
    module).
- 2026-11-26: decision 34, a room or zone for many lights, on the branch `rooms-zones`.
  - **Research:** the CLIP v2 shapes and limits of Research findings, read in aiohue 4.9.2, openhue-api and openHAB's
    Hue binding at the commits named there, with diyHue's emulation, Home Assistant 2026.11.3's Hue integration and
    node-hue-api's documentation; Hue's own API reference needs a developer account.
  - **The client, over HTTPS against the fake bridge:** `get_lights_and_groups` gave the living room the ceiling light
    and the floor lamp, through their devices, its dimmer switch adding none, and the zone downstairs its three lights,
    with the requests `GET` light, room and zone and no other. `create_zone` created the zone Hall of two lights, read
    it back with its grouped light, and the next listing had it; a name of 33 characters got the fake bridge's 400 and
    its message, and an answer that named no zone was unexpected. In memory: the hierarchy rule (devices, lights,
    nested rooms and zones, a cycle, other kinds), malformed owners and children, the POST's exact body, and the
    read-back 0.5 s apart on virtual time, 5 readings at most.
  - **The switch dialog,** in Home Assistant's flow manager with the bridge in memory: the room and the zone of
    exactly the lights chosen; a new zone, with its POST, and its name numbered or cut; keeping the lights; a refusal,
    with *Try again* and *Keep the lights*; a zone that a failed attempt made after all, offered for use on *Try
    again*; an unexpected failure; the switch's other rooms and zones; nothing for no or one single light, or for a
    switch that has the room already; editing a switch, its key kept; typed lights; a room gone before it is chosen;
    the dialog closed while the zone is created; and a zone created on the fake bridge over HTTPS. Every text was
    checked against `en.json`, and over the frontend's HTTP API the offer's steps never held the device key.
  - **The texts:** intl-messageformat formatted `Use room '{group}'` without the name, so the texts quote names with
    double quotes, which it formats with the name (Research findings).
  - **Checks,** on `main` at d5a7c2a: `scripts/check` passed with 1,415 tests, 2 skipped (the socket bound to the
    group, Linux only); `check-commits.sh` passed the four commits, and `test-each-commit.sh` ran `scripts/check` for
    each, with 1,324, 1,356, 1,391 and 1,415 tests; and `scripts/e2e` passed its 45 checks, in 31.0 s before the
    rebase and 12.8 s after it: the switch of one light and one room got no offer, and the fake bridge got the PUTs as
    before.
- 2026-11-26: `tests/test_switch_flow.py::test_adding_a_switch_with_the_fake_bridge` made deterministic (decision 12).
  It passed but failed at its teardown: run alone on macOS, 4 of 60 times on `main` e600cb4 and 11 of 60 on d98754c,
  each with an `AssertionError` in asyncio's `Server._attach`, which the `hass` fixture raised. None of the last 200
  runs on GitHub's Linux runners had failed.
  - **Cause:** the `fake_hue` fixture stops the fake bridge before the `hass` fixture stops the integration. Adding the
    switch starts a bridge supervisor for the new configuration, whose new Hue client connects to the bridge
    (`_wait_for_bridge`'s ping) as the test ends. asyncio accepts a connection in one turn of the event loop and makes
    its transport in the next; when the bridge's `runner.cleanup()` closed the server in between, `Server._attach`
    failed its assertion, which the loop, in debug mode, reported (Research findings, asyncio servers). In a traced
    failing run, the test ended at the loop's turn 122, the bridge accepted the supervisor's connection at 127, and at
    128 its server closed, before the transport was made.
  - **Reproduced:** a throwaway pytest plugin held the integration's next request to the bridge, from the start of the
    teardown, until the bridge's stop began, and had the stop go on in the turn of that connection's accept, ahead of
    its transport. The test then failed 20 of 20 times.
  - **Not chosen:** stopping the integration before the bridge. In a throwaway probe on a debug-mode loop, a client
    cancelled 1 to 7 turns after it began to connect, while its TLS handshake with a running server went on, was
    reported the same way, with `ConnectionResetError`: unloading the entries first would move the race, not end it.
  - **Fixed:** `RunningBridge.stop()` (`tools/fake_hue/server.py`) removes the listening sockets' readers, lets the
    connections accepted until then get their transports in one more turn, and closes the server. aiohttp then shuts
    down the connections it serves, after their requests in progress, as before; the rest, such as one whose TLS
    handshake hasn't finished, are aborted, and the stop waits until the server has closed. `start()` makes the server
    itself, as aiohttp's `TCPSite` does, and keeps it. Every test of `fake_hue` and `fake_hue_pressed` stops the bridge
    this way, and so does `python -m tools.fake_hue`. A test in `tests/tools/test_fake_hue.py` stops the bridge
    between a connection's accept and its transport, with a client that never starts its handshake: with the old
    stop, the loop reported the `AssertionError`, and the half-made transport raised `TypeError` when it was
    collected; without the abort, the connection stayed open.
  - **Checked:** with the plugin, the test passed 20 of 20 times, the interleaving forced each time, and run alone, 60
    of 60. `test_a_zone_is_created_on_the_fake_bridge`, which also ends as a supervisor starts, passed 60 of 60 before
    and after: in 20 forced runs, its supervisor had connected before the teardown. The new test passed 30 runs in a
    row. `scripts/check` passed with 1,416 tests, 2 skipped, and `scripts/e2e` its 45 checks. Stopped by Ctrl+C with
    an idle keep-alive connection and one that never started its TLS handshake, `python -m tools.fake_hue` exited
    with 0 in 0.03 s, and closed the second.

- 2026-11-26: decision 32, switches that keep running when a light, room or zone is renamed or deleted, on the branch
  `resilient-targets`, rebased onto `main` at 2a1569d (#46).
  - **Tests:** hand-written, with made-up names, on the in-memory bridge of `tests/fakes.py` or the fake Hue bridge on
    127.0.0.1. In the engine: the labels; each switch resolved on its own; a room left without lights; the remembered
    resources for no match and for several; a renamed light, room and zone; a rename to a name that another resource of
    its kind has, saved as the id; an entry that names its resource by its id; deletions; a switch with nothing left,
    whose press is reported and whose toggle fails with `NO_TARGETS`; the status's problems and message; the toggler's
    failures with their status; and the supervisor on `VirtualTime`: a re-check every minute while an entry is
    missing and every 15 minutes otherwise, the runner taking the new targets without a restart, the resolution at
    once after a 404, and a bridge error during a re-check, which changes nothing. In Home Assistant: the store's
    load, delayed save, bridge-id tag, pruning, flush at unload and removal with the entry; a rename saved in its
    subentry once, with one restart of the supervisor, also for two switches renamed at once; a user's edit made
    meanwhile, which stays; the repair `missing_targets_<device id>` raised and removed, with its texts in `en.json`;
    Status running with the problems; *Last action* `failed` for a switch with nothing left; the labels in *Last
    action*'s targets and the log; and the switch test with a missing entry. In `tests/tools/test_fake_hue.py`: the
    fake bridge's renames and deletions, with its devices, children, zone numbers and reset.
  - **Mutations**, applied by throwaway scripts outside the repository on the rebased branch, each failed the tests
    meant for it: labels without the id (6 tests), the running message without what is missing (7), no failure for a
    switch with nothing left (3), no repair for missing targets (2), no remembered light (13), another bridge's ids
    kept (1), removed entries kept (1), a rename saved over the user's edit (1), 15 minutes between re-checks while
    an entry is missing (2), no resolution after a 404 (3), and a bridge error during a re-check that stops the bridge
    (1). In the fake bridge: a deleted light's device left in its room, zones numbered by their count, a reset by
    position and a room's name of 33 characters (1 each). With the renames not saved, the repair not raised, or no
    resolution after a 404, `scripts/e2e` failed: on its 30 s wait for the saved name, on Repairs' issue, and on the
    status's problem, which only the minute's re-check would have brought.
  - **Checks:** `scripts/check` passed on each of the five commits (`test-each-commit.sh`, with mypy for Linux), with
    1,420, 1,428, 1,447, 1,455 and 1,465 tests, 4 to 49 more than `main`'s 1,416, and 2 skipped on macOS each time.
    Before #46 fixed the teardown race of `test_switch_flow.py` (above), one run of the last commit had an error at a
    test's setup or teardown, as that race shows, and its next three runs passed. `check-commits.sh` passed for all
    five. `scripts/e2e` passed all 64 checks in 13.3 to 14.6 s, five times: setup 0.9 to 1.5 s, the fake bridge's
    start 0.2 to 0.3 s, Home Assistant's first start 1.7 to 2.1 s, the flows 0.1 to 0.3 s, the two presses 3.0 s, the
    restart, the rename's save and the second start of the bridge included, 2.6 to 2.7 s, the press after it 1.5 s,
    the two presses after the deletion 0.6 s, and the stop 0.8 s.
  - **Rebased** onto e600cb4 (#43), d5a7c2a (#44), d98754c (#45) and 2a1569d (#46), keeping both sides. #46 changes how
    the fake bridge's server starts and stops, not its routes, so it merged without a conflict. With #45, the
    resolutions and the switch test read `HueClient.get_lights_and_groups()`, `group_offer.py` imports `encode_group`
    from `targets.py`, where this branch moved it, the in-memory bridge's deletions also leave the zones that POSTs
    create, and the fake bridge's deletions take its new devices and children along; its created zones are numbered by a
    counter and its reset finds them by the ids of its own room and zone, as a deletion would otherwise free a number or
    shift them.
  - **Seen on the way:** `git stash` keeps one list for all the worktrees of a repository (`refs/stash`), so a `pop` or
    `drop` in one worktree can take another's entry: one of this branch's was dropped that way, and restored by its
    commit's hash. Since then, the checks that revert a change replace a file's text and restore it, without the stash.

- 2026-11-26: decision 33, sonoff2hue follows the Hue bridge to a new address, on the branch `follow-bridge`.
  - **Research:** Signify's rate limit for its discovery endpoint, as node-hue-api's author quoted Hue's guide, which
    needs a developer account, and as makers of other Hue clients document it; Home Assistant 2026.11.3's Hue
    integration and aiohue, which follow a bridge's new address from zeroconf by its id alone (Research findings).
  - **Engine tests,** on `VirtualTime` with the bridge in memory, made-up addresses and ids: no search in the first
    60 s, while connecting and while the runner runs, where the keep-alive noticed at 45 s and the first search came at
    105 s; mDNS at 60, 360, 660, 960, 1260, 1560 and 1860 s, and Signify's endpoint at 60, 960 and 1860 s; the rates
    kept when the bridge is unreachable again, and across two supervisors that share a search; both sources asked in one
    search at two times of the clock where the first search's time plus 15 minutes, and plus three times 5, round apart,
    the one or the other way; the bridge found with its id and pin reported once, also when it was listed without an id,
    with a pin written in lower case with colons; a bridge listed with another id never read, those with the id read
    before those without, another id after reading ignored, and another certificate warned about once over three
    searches; the bridge's own address, also as `:443`, never read; the search cancelled when the bridge answers, also
    with a refusal, and when the supervisor stops, while connecting and while running; a host name, and a configuration
    without a bridge id, never looked for, where an IP address with a port and IPv6 addresses are; without a pin, the
    address in the status, warned about once, until the bridge answers again; a search that fails unexpectedly logged
    with its traceback, and the next one at its rate. The status's `found_at`: published once per address, kept by a
    check that finds the bridge unreachable and by editing the switches, forgotten when the bridge answers or the
    address, key or pin changes, and shown only while paired. `matches_pin` agrees with the client's own connections
    over HTTPS for eight spellings of the pin; `BridgeUrl.is_ip_address`; discovery without the cloud, and without mDNS.
  - **Home Assistant tests,** with `freezer` and the bridge in memory, which now answers nowhere at the addresses that
    it moved away from (`moved_from`): the address saved once, the supervisor restarted once and no reload, the move's
    warning before the restart's line, and the switch toggling at the new address, with no other search in the hour
    after; the remembered resources kept, so that a light renamed meanwhile is followed at the new address; another
    certificate not used; a stale report dropped; the rates kept across a restart for a switch's change;
    `new_bridge_search` asking the engine's discovery for 5 s on the shared session; the repair `bridge_moved` with both
    addresses, its texts in `en.json`, its fix flow to *Reconfigure* with the addresses in its form, and gone when the
    bridge answers again or the address changes. The diagnostics' `status.hue` has `found_at`.
  - **Over HTTPS:** `test_fake_hue.py` shows two fake bridges with one given certificate, and a third with its own. In
    `test_runtime.py`, two fake bridges with one certificate and the bridge id: the first stopped, as if the address had
    changed, and the search listed the second, which sonoff2hue read without the key, pinned, saved and switched the
    light on; the search's 60 s were shortened there. It passed 30 runs in a row, and failed without the save.
  - **Mutations**, applied by throwaway scripts outside the repository, each caught by the tests meant for it: 31 in the
    engine (no 60 s, sources due at once asked apart, the rates of 5 and 15 minutes changed, kept per outage or per
    supervisor, a report per search, the id not checked after reading or in the list, no pin check, the own address
    read, the search not ended by an answer, a refusal or the stop, host names and missing ids looked for, the unpinned
    address not in the status or checked as pinned, a failure ending the search, the message and its period, the info
    line, warnings per search, the status's `found_at` kept, forgotten, shown or published wrongly, host names counted
    as addresses, a pin not normalized and a malformed one matching) and 9 in the runtime and the repair flow (the move
    never saved, or saved when stale, the restart before the move's line, no repair, no placeholders in the fix flow, no
    search, a search per supervisor, the sources swapped, another duration).
  - **Checks:** `scripts/check` passed on each of the four commits (`test-each-commit.sh`, with mypy for Linux), with
    1,467, 1,517, 1,526 and 1,528 tests, 2 to 63 more than `main`'s 1,465, and 2 skipped on macOS each time;
    `check-commits.sh` passed for all four. `scripts/e2e` passed all 64 checks, in 27.3 s, which built `.dev/venv`, and
    13.2 s: the fake bridge always answers there, so the search never starts.
  - **Seen on the way:** CI's *Test* job, on the merge result, failed once in
    `test_the_rates_hold_across_the_supervisors_that_share_a_search`, which had passed on the four commits there and
    locally. On a runner whose clock had just started, the loop's clock is small, and there a first search's time plus
    15 minutes and plus three times 5 can round apart (for 0.3% of such times, as a throwaway script showed), so the
    search at 960 s asked the endpoint and mDNS one after the other. The search now asks together the sources due within
    a millisecond of each other (`DUE_TOGETHER`), the new test above reproduces it, and the tests' helper shows the
    searches when they differ, where it gave only `False`.
- 2026-11-26: `VirtualTime.elapsed` (tests/engine/fakes.py) rounded to the nanosecond.
  - **The failure:** CI's *Test* job on `main` at 2529398 failed in
    `test_the_switches_are_resolved_again_every_15_minutes_and_the_runner_takes_their_new_targets` (decision 32), which
    had passed on its pull request and locally. The resolution at 900 s was recorded at 899.9999999999999 s, so the
    exact comparison failed. The aborted test then left the runner's keep-alive running, an error at its teardown.
  - **The cause:** the virtual clock starts at the loop's real time, an arbitrary float that differs from machine to
    machine, and each jump adds a timeout to it, so `now - start` can miss a round value by a few units in the last
    place.
  - **Reproduced:** with a throwaway pytest plugin that starts the virtual clock at a chosen value, the test failed at
    1234.5678 every time and passed at seven other values. Run at 12 values, the 232 tests of the files that use
    `VirtualTime` failed at 2 of them, 1234.5678 and 42.4242, each with this test.
  - **Fixed:** `elapsed`, and with it `utc()`, is rounded to the nanosecond. No test depends on less than a
    millisecond. With the fix, the 232 tests passed at all 12 values, and `scripts/check` passed with 1,528 tests.
- 2026-11-26: the live test of decisions 32 to 36 on the user's Home Assistant OS 18.4 (Core 2026.11.3, Supervisor
  2026.11.0). It was deployed by hand over SSH in the Terminal & SSH app, as before:
  - the installed folder saved off the host and compared by SHA-256 with git;
  - `main`'s 40 files staged on the host and compared the same way;
  - the folders swapped, then `ha core check` and `ha core restart`, which took 22 and 27 s.
  - **`main` at 2eaddc1** (decisions 32, 34, 35 and 36), deployed over 1.0.0's files from 2bec130:
    - The entry loaded, and Status was running: "4 wall switches toggle 17 Hue lights, rooms and zones.", without
      problems.
    - No false press: each switch was *Online* with its `seq` as before the restart, and each *Press* kept its last
      event.
    - The log had no warning or error of the integration.
    - The store of the remembered resources, `.storage/sonoff2hue.<entry id>`, held 17 resources for the 4 switches,
      at version 1 and tagged with the bridge id.
    - Two lights with one name, on one switch, were labelled with their ids in the runner's line and in *Last action*'s
      targets. The log level was set to info for this with `logger.set_level`, the entry reloaded, and the level set
      back to warning.
  - **Without the multicast plugin** (decision 19; Supervisor 2027.02 disables it everywhere):
    - `ha multicast options --enabled=false` removed it for 6 minutes.
    - Meanwhile the integration still heard all 21 eWeLink devices, and each switch answered the LAN monitor's queries
      and stayed *Online*.
    - `ha multicast options --enabled` brought it back, and its mdns-repeater ran again on the `hassio` bridge.
  - **`main` at cb4e0fc** (decision 33 too):
    - The same results after the restart.
    - The bridge answered, so no search started.
  - **Online:** the recorder's history and a probe on the same subnet found one switch that stops receiving multicast
    about once an hour, and with it answering the queries, while it still sends (Research findings).
    - The catch-up queries of decision 36 don't bridge that. The README's Troubleshooting now describes it.
    - The other three switches stayed *Online* throughout.
    - The probe's direct queries, at 10:07 UTC, also made the switch answer the integration once.
  - **Left for the user,** who must be there:
    - real presses;
    - a light renamed in the Hue app, followed and saved;
    - the room or zone offer when editing a switch.

## Status

- [x] Repository started: the license, this memory, the tools (`scripts/setup`, `scripts/check`) and the commit checks
- [x] CI: lint, tests and per-commit checks, hassfest and HACS (`validate.yml`), and the end-to-end test, all required
  for `main` by ruleset 28003079
- [x] mDNS spike on the user's Home Assistant OS: own UDP 5353 socket next to Home Assistant's zeroconf (decision 19)
- [x] The engine with its tests: the mDNS layer (DNS, the listener), eWeLink payloads and press detection, the Hue
  client and pairing, the fake Hue bridge, the runner, the YAML import
- [x] The LAN view (decision 17): the device registry, the LAN monitor and the activity buffer
- [x] The switch rules (decision 20): the picker, the rules a switch meets before it is saved, and the form's own checks
- [x] The bridge supervisor and the status tracker (decision 21), with the reauthentication hook for the runtime
- [x] Home Assistant layer: skeleton (decision 16), runtime (decision 23), setup flow with reconfigure, reauth and the
  import of a `sonoff2hue.yaml` (decision 22), switch subentries (decision 24), entities (decision 25), diagnostics and
  the logbook (decision 26: the logbook shows the entities Press and Last action)
- [x] Development tooling (decision 18) and end-to-end CI with a real Home Assistant, the fake bridge and a simulated
  switch (decision 27: `scripts/e2e` and CI's *End-to-end*, which the ruleset requires)
- [x] README for the Marketplace (decision 28)
- [x] Releases (decision 29): the *Release* workflow with its checks and their tests, the refusals tested on GitHub,
  immutable releases. Release 1.0.0 is then a tag `v1.0.0` on `main`
- [x] The integration stands alone (decision 30): Phase 1, the recorded cases kept and the guard added (#27); the six
  slices, A `lan` (#31), B `hue` (#33), C `yaml` (#30), D `switches` (#32), E `engine-core` (#29) and F `ha-layer`
  (#28); the cleanup (#34); and the README, `en.json` and this file rewritten
- [x] Live test on the user's Home Assistant OS: `main` at 2bec130, with a real press (Verification log)
- [x] Release 1.0.0: the tag `v1.0.0` on 7ba561f and its immutable GitHub Release "sonoff2hue 1.0.0", Latest
  (Verification log)
- [ ] In the Marketplace's catalog: submitted on 2026-11-25 as hacs/default#14951, waiting for its review (decision 31)
- [x] A canary for the next Home Assistant release (decision 35, part 1): *Beta* (`beta.yml`) tests the newest
  `pytest-homeassistant` weekly, and passed with 2026.12.0b0 on #43. Part 2, the pins at the latest release and a
  required job for the 2026.11.3 minimum, follows 2026.12.0
- [x] Small fixes (decision 36): catch-up queries for a steadier *Online*, a translated error for *Test toggle*, and
  Home Assistant 2026.11's conventions for the entry's setup and unload
- [x] A room or zone for many lights (decision 34): the client reads which lights each room and zone holds and
  creates zones, as the fake bridge does, and the switch dialog offers the room or zone of exactly a switch's single
  lights, or a new zone of them, in their place
- [x] Switches keep running when a light, room or zone is renamed or deleted (decision 32): ids in the labels of
  repeated names, each switch resolved on its own with a repair for what is missing, renames followed with the
  remembered resources and saved in the switch, re-checks while the bridge runs, and the end-to-end test's rename and
  deletion
- [x] sonoff2hue follows the Hue bridge to a new address (decision 33): it looks for a bridge that stopped answering,
  with mDNS every 5 minutes and Signify's endpoint every 15; with the pinned certificate it saves the address that it
  finds for its bridge id, and without one a repair offers it; a host name is never replaced
- [x] Two flaky tests made deterministic: the fake Hue bridge's shutdown (#46) and the virtual clock's elapsed time
  (#49)
- [x] Releases from a merged release pull request (decision 37): *Tag the release* tags a release commit `vX.Y.Z` once
  CI passes on it on `main` and runs *Release* on the tag, which takes the commit's `.github/release-notes/vX.Y.Z.md`
  as the notes; a tag pushed by hand still releases. Left: its first run after CI on `main`, which finds no notes file
  there and tags nothing, is to be seen on GitHub after the merge
- [ ] Live test of decisions 32 to 36 on the user's Home Assistant OS: `main` at cb4e0fc runs there, checked without
  the multicast plugin too (Verification log); real presses, a rename in the Hue app and the room or zone offer wait for
  the user
- [ ] Home Assistant 2026.12 (decision 35, part 2): once 2026.12.0 is on PyPI, the pins at it and a required job for
  the 2026.11.3 minimum
- [ ] Proposed, for the user's decision: a direct unicast query to a late switch's last address, which a MINIR4M that
  stops receiving multicast answers by multicast (Research findings); it adds a kind of query to the mDNS layer, which
  decision 30 freezes
