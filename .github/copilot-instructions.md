# sonoff2hue for Home Assistant: Copilot instructions and project memory

This file is the project's GitHub Copilot memory. Copilot loads `.github/copilot-instructions.md` automatically
for chat, agent and code-review requests in this repository. It records what was asked, what was learned, what was
decided and why, and where the work stands. Every step is also recorded as a git commit.

Keep it current. When you make a decision, learn something about Home Assistant, the devices or the APIs, verify
something or finish a step, update the matching section below in the same commit as the change. Append; don't
renumber decisions.

**This repository is public.** Never put secrets here (Hue application keys, eWeLink device keys, certificate pins),
and nothing about the user's own network or devices: no host names, addresses, device ids, serial numbers or SSH host
keys. Those belong in the memory of the private repository `abpostelnicu/sonoff2hue`. Tests use made-up device ids
and keys.

## Project

`sonoff2hue-homeassistant` is a Home Assistant custom integration, domain `sonoff2hue`, for Home Assistant 2026.11 and
later. When a wall switch wired to a Sonoff MINIR4M in detached relay mode is operated, it toggles the Philips Hue
lights mapped to that MINIR4M. It listens passively for the MINIR4M's eWeLink LAN mDNS broadcasts and switches the
lights through the Hue bridge's local CLIP v2 API.

It ports the sonoff2hue app (.NET 10, in the private repository `abpostelnicu/sonoff2hue`, where decision 36 records
the move) module by module, with the same behavior. Home Assistant's own UI replaces that app's web interface. The
.NET app stays and is maintained next to this integration.

## Goal (from the user, 2026-11-23)

> I want to migrate this app to Home Assistant and to be an integration like SonoffLAN; the behavior and
> functionality should be exactly like it is for the moment.

> My app only addresses HA 2026.11+.

The user's choices, made on 2026-11-23 when the plan was approved:

- A Python custom integration that ports the engine. The alternative, the .NET backend as a Home Assistant app
  (add-on) with a thin integration, was not chosen.
- Its own public repository, `abpostelnicu/sonoff2hue-homeassistant`, under the Apache-2.0 license.
- The .NET app is kept and maintained, with a parity rule between the two.
- Hue is controlled directly: own link-button pairing, the pinned certificate and the warm connection, as now. Home
  Assistant's light entities aren't used.
- Home Assistant's own UI:
  - a setup flow and one config subentry per switch;
  - entities, repairs, logbook entries and diagnostics;
  - no custom panel.
- A one-time import of an existing `sonoff2hue.yaml`.
- The live test runs on the user's Home Assistant OS.

> The interface must use Home Assistant components. (2026-11-24)

Asked what that covers, the user chose Home Assistant's own UI only, as planned: dialogs built from Home Assistant's
form controls, devices and entities, repairs, the logbook and diagnostics, and no custom panel or web page. The user
didn't choose Home Assistant's entity and area pickers for the lights, its Hue integration for switching them, or
SonoffLAN for reading the switches, so those stay as above. The switch editor's rules are ported for these dialogs; its
HTML and browser code are not.

## How to work in this repository

- Run `scripts/setup` once. It uses uv to create `.venv` with Python 3.14 and the pinned tools of
  `requirements_test.txt`, Home Assistant included. Then `scripts/check` runs what CI runs: `ruff check`,
  `ruff format --check`, `mypy` (strict, on `custom_components` and `tools`) and `pytest`. Arguments of
  `scripts/check` go to pytest.
- `custom_components/sonoff2hue/engine/` is the integration's engine. Its modules import nothing from Home Assistant,
  which `tests/test_own_mdns.py` checks; the rest of `custom_components/sonoff2hue` adapts them to Home Assistant. Keep
  that split.
- Keep the product rules: how presses are detected and lights toggled, the timings, pairing and discovery, the
  switches' limits and their messages, the log lines and their levels, and the flows, entities, repairs, bus events and
  diagnostics. Outside the mDNS layer, use Python's standard library and the libraries Home Assistant ships rather than
  re-implementing another platform's rules, and test the integration's own behavior with hand-written pytest cases
  (decision 30).
- The mDNS layer, `engine/dns.py`, `engine/mdns.py`, the record part of `engine/ewelink.py` and the mDNS part of Hue
  discovery in `engine/hue_setup.py`, keeps exactly its behavior for every input. A change to it must keep
  `tests/engine/mdns_packets.json`, `mdns_text.json` and its tests passing unchanged: `test_mdns_packets.py`,
  `test_text.py`, `test_dns.py`, `test_mdns.py`, `test_lan_monitor.py`, the record tests of `test_ewelink.py` and the
  discovery tests of `test_hue_setup.py`. It must not rely on Home Assistant's mDNS: no python-zeroconf, no `zeroconf`
  or `network` integration and no zeroconf matcher in the manifest, which `tests/test_own_mdns.py` checks (decision 30).
- Never run two listeners that toggle the lights for the same switch: every press would toggle twice and leave the
  lights as they were.
- Stay listen-only towards the switches: the only packets sent are standard mDNS queries.
- Tests must not need the network, a Hue bridge or a MINIR4M. Use the packet builders in `tests/engine/packets.py`,
  the fake Hue bridge in `tools/fake_hue` (on 127.0.0.1) and fake sockets.
- Log the way Home Assistant does: a module-level `_LOGGER` and lazy `%s` arguments (ruff's G rules), with the
  existing lines' wording. Trace-level lines (repeated broadcasts, malformed packets) use level 5, below DEBUG.
- Diagnostics and logs never show the Hue application key, device keys or the certificate pin. Never commit a
  `sonoff2hue.yaml`; `.gitignore` excludes it.
- Make one descriptive commit per step, and every commit must pass `scripts/check` on its own. Check the whole branch
  with `.github/scripts/check-commits.sh origin/main..HEAD` (the message rules below and `git diff --check`) and
  `.github/scripts/test-each-commit.sh origin/main..HEAD` (`scripts/check` per commit, in a temporary worktree).
- Commit messages: a subject of at most 72 characters, a capital first letter, an imperative verb ("Add", not "Added"
  or "Adds"), no trailing period, and a blank second line. No fixup!/squash!/WIP commits and no whitespace errors.
  Commit with `--no-gpg-sign` (the user's preference) and end the message with
  `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`.
- Stage your own files by explicit path; never use `git add -A` or `git commit -a`, which would pick up another
  agent's unfinished edits. Agents working in parallel each use their own git worktree under `.worktrees/` (add it to
  `.git/info/exclude`) and their own branch.
- Push new commits to a branch, open a pull request and wait for the checks. Then merge by fast-forwarding `main`
  (`git push origin <sha>:main`), which keeps the commit hashes; GitHub then marks the pull request as merged. A
  ruleset on `main` (id 28003079) requires *Lint*, *Test*, *All commits pass*, *hassfest* and *HACS*; repository admins
  can bypass it. It matches the checks by their job names, so rename such a job only together with the ruleset.
- CI (`.github/workflows/ci.yml`) runs *Lint* (ruff, the formatter, mypy), *Test* (pytest with coverage) and
  *End-to-end* (decision 27: `scripts/e2e`, a real Home Assistant with the integration, the fake Hue bridge and the
  simulated wall switch) on `main` and on the merge result of each pull request. *Commits* (`commits.yml`) checks every
  commit of a pull request on its own: the message rules and `git diff --check`, then `scripts/check` per commit, summed
  up by *All commits pass*. They install uv 0.13.7 with `astral-sh/setup-uv@v10.4.0` (it publishes only full-version
  tags) and run `scripts/setup`, with uv's cache keyed on `requirements_test.txt`, and for *End-to-end* on
  `requirements_dev.txt` too. A failed *End-to-end* keeps Home Assistant's and the fake bridge's logs as the artifact
  `end-to-end-logs`. Run `scripts/e2e` before pushing a change to how the integration sets up, runs or toggles.
- Releases (decision 29): raise `version` in `custom_components/sonoff2hue/manifest.json` through a pull request, then
  tag the merged commit on `main` with `vX.Y.Z` (`git tag -a vX.Y.Z -m "sonoff2hue X.Y.Z"`) and push the tag. The
  *Release* workflow checks it and creates the GitHub Release "sonoff2hue X.Y.Z", from which the Marketplace installs.
  Release in rising versions: the Marketplace offers the newest Release, not the highest version. Releases are
  immutable: a published Release's tag can't be moved or deleted, and a deleted Release's tag name can never be used
  again, so never delete a Release; fix a bad release with the next patch. Any tag `vX.Y.Z` on `main` whose `version`
  is `X.Y.Z` releases for real. A tag pushed for a test must fail one of the checks before the Release (another
  version, a commit that isn't on `main`, a suffix) and is deleted right afterwards. `.github/scripts/release-test.sh`
  tests the checks and the workflow; `scripts/check` and CI's *Test* job run it.

| Folder | Contents |
|--------|----------|
| `custom_components/sonoff2hue` | The integration (decision 16): `manifest.json`; `const.py` (`DOMAIN`); `__init__.py`, which sets up and unloads the config entry and declares `Sonoff2HueConfigEntry`, whose `runtime_data` is `Sonoff2HueData`; `config_flow.py`, the setup flow; `translations/en.json`, its strings (custom integrations ship no `strings.json`); `brand/`, its icons (`icon.png`, `icon@2x.png`) |
| `custom_components/sonoff2hue/config_flow.py` | The setup flow with reconfigure and reauth (decision 22): it finds or takes the bridge's address and pairs it with the engine's `hue_setup`, or imports a `sonoff2hue.yaml` with `yaml_import`. `const.py` holds the data contract: the config entry's keys (`CONF_BRIDGE_IP`, `CONF_BRIDGE_ID`, `CONF_APP_KEY`, `CONF_CERTIFICATE_SHA256`), its title (`ENTRY_TITLE`), and a switch subentry's type and keys (`SUBENTRY_SWITCH`, `CONF_DEVICE_ID`, `CONF_NAME`, `CONF_DEVICE_KEY`, `CONF_LIGHTS`, `CONF_GROUPS`) |
| `custom_components/sonoff2hue/switch_flow.py` | The switch subentry flow (decision 24), which `config_flow.py` declares for the type `switch`: it adds and edits the switches with the engine's `switch_form`, `switch_rules` and `picker`. From the loaded entry's runtime (decision 23) it takes the devices seen on the LAN (`lan_registry`) and the factory of Hue clients, and the switches and the bridge from `config_from_entry`. `tests/test_switch_flow.py` runs it in Home Assistant with the runtime |
| `custom_components/sonoff2hue/engine` | Ports of the .NET engine, without Home Assistant imports. `text.py` holds the .NET string and number rules (trim, ordinal case-insensitive comparison and ordering, `ToLowerInvariant`, `TryParse`). `dns.py` is `Mdns/DnsMessage.cs`; `mdns.py` is `Mdns/MdnsListener.cs` with the parts of .NET's `NetworkInterface` and `NetworkChange` it uses; `ewelink.py` is `Ewelink/EwelinkAnnouncement.cs` and `EwelinkCrypto.cs`; `press.py` is `Ewelink/PressDetector.cs` and `SwitchTracker.cs`; `config.py` is the model of `Configuration/AppConfig.cs`; `jsonnode.py` reads JSON as System.Text.Json's `JsonNode` does. `hue_address.py` is `Hue/HueBridgeAddress.cs` with the parts of .NET's `Uri`, `IPAddress` and `IdnMapping` it relies on; `hue_client.py` is `Hue/HueClient.cs`, `HueTls.cs` and `HueModels.cs`; `hue_setup.py` is `Hue/HueBridgeSetup.cs` with the pairing loop of `Web/Management/HuePairing.cs`; `errors.py` holds `UnexpectedError` (decision 9). `targets.py` is `Bridge/TargetResolver.cs`; `toggler.py` is `Bridge/HueToggler.cs`; `runner.py` is `Bridge/Sonoff2HueRunner.cs` and `IRunnerObserver.cs` (decision 14). `yaml_import.py` is `AppConfig.Parse` with the parts of YamlDotNet's deserializer it reaches, on `yaml_parser.py`, YamlDotNet 18.1.0's scanner and parser, and `yaml_uri.py`, .NET's `Uri.IsWellFormedUriString` for tags (decision 15) |
| `custom_components/sonoff2hue/engine`, the switch editor | The web UI's switch editor, without its UI, for the switch subentry flow (decision 20). `jstext.py` holds JavaScript's string rules (`trim()`, `toLowerCase()`, UTF-16 lengths); `picker.py` is the UI's `targets.js`; `switch_rules.py` is the server's checks in `Web/Management/ConfigEndpoints.cs` (`SwitchValidation`, adding and changing a switch, the check against the bridge) and the picker's resources of `HueEndpoints.cs`; `switch_form.py` is the pure logic of the UI's `views/switcheditor.js`, with `sameId` and `switchLabel` of `format.js` and `applyFieldErrors` of `ui.js`. `tools/dotnet_oracle/Switches.cs` and `switch_cases.py` check `switch_rules.py` against the app's own code |
| `tests/engine` | Tests of the engine, ported from the .NET tests. `packets.py` builds raw DNS packets and MINIR4M broadcasts (the .NET tests' `TestPackets.cs`) and `MdnsPacket`s; `fakes.py` has fake multicast transports for `MdnsListener` |
| `custom_components/sonoff2hue/engine` (the LAN view) | `registry.py` is `Web/Monitoring/DeviceRegistry.cs`; `lan_monitor.py` is `Web/Monitoring/LanMonitor.cs`, which runs the config entry's mDNS listener; `activity.py` is `Web/Monitoring/LogBuffer.cs`, as a `logging.Handler` (decision 17). Their tests use `VirtualTime` from `tests/engine/fakes.py`, which makes the event loop's clock jump to the next timer whenever the loop would wait |
| `custom_components/sonoff2hue/engine` (the bridge's state) | `status.py` is `Web/Monitoring/StatusTracker.cs`, the state that the entities and diagnostics show; `supervisor.py` is `Web/Management/BridgeSupervisor.cs`, which runs the runner for one configuration (decision 21). Their tests run on `VirtualTime`, with `InMemoryBridge` for the bridge and `FakeNetwork` for the listener |
| `tests` | `hue.py` runs the fake Hue bridge on 127.0.0.1 with a manual clock (the .NET tests' `FakeHueTestHost`), and has `InMemoryBridge`, which answers and records requests without sockets (the .NET tests' `FakeHttpHandler`); `conftest.py` has its fixtures, `fake_hue` and `fake_hue_pressed`, which enable sockets |
| `tests` (the integration) | `test_init.py` and `test_config_flow.py` run the integration in Home Assistant, with pytest-homeassistant's `hass` fixture and `MockConfigEntry`; `test_brand.py` checks the brand images |
| `custom_components/sonoff2hue/runtime.py` | The config entry's runtime (decision 23): `Sonoff2HueData`, which is `entry.runtime_data`, with the engine's parts that live as long as the entry (the mDNS listener that the LAN monitor runs, the LAN registry, the status tracker, the activity buffer) and the bridge supervisor of the current configuration (`config_from_entry`); the dispatcher signals; the bus events, whose names and data keys are in `const.py`; the repair issues; the switch test (`async_test_toggle`). `__init__.py` starts it, adds the update listener that applies each change of the entry, and forwards `PLATFORMS`, the entity platforms (decision 25). `repairs.py` has the repair flow that leads to the reconfigure flow |
| `tests` (the runtime) | `fakes.py`: `Lan`, the entry's listener on `FakeNetwork` with the broadcasts of MINIR4Ms in plain text or encrypted; `Bridge`, an `InMemoryBridge` with lights, a room, faults, held answers and counts of the clients made and closed; `switch()` and `paired()`, a switch subentry and an entry. The `lan` and `bridge` fixtures of `conftest.py` put them in place of `runtime.new_listener` and `runtime.create_hue_client`. `test_runtime.py` runs the runtime in Home Assistant with them, or with the fake bridge over HTTPS, and moves time with `freezer` |
| `custom_components/sonoff2hue/diagnostics.py` | The config entry's diagnostics (decision 26): `async_get_config_entry_diagnostics` gives the configuration that the supervisor runs, the status snapshot, the LAN registry's devices with `online` and the activity buffer's last 1,000 entries, in plain JSON, through `async_redact_data` with `TO_REDACT`, the keys of the secrets. The integration has no `logbook.py`: the logbook shows the entities' changes |
| `tests` (diagnostics and the logbook) | `test_diagnostics.py`: the whole file for a running entry, the activity's last 1,000 entries, an entry that isn't loaded, and, over HTTPS with the fake bridge, no secret in the files downloaded through Home Assistant's HTTP API or in any line that the integration logs. `test_logbook.py`: the real logbook, on the recorder (`recorder_mock`), with the entities: Press and Last action for each press, also on the switch's device page, nothing for the bus events, and the presses for which Last action adds nothing |
| `custom_components/sonoff2hue/entity.py` and the platforms | The devices and entities (decision 25). `entity.py` has what they share: the bridge's device and each switch's (`bridge_identifier`, `switch_of`); `Sonoff2HueEntity`, named by its translation key, which can follow the status tracker's snapshots (`_follow_status`); `BridgeEntity`, on the bridge's device; `WallSwitchEntity`, on a switch's device, which follows its subentry; `async_add_switch_entities`, which adds a platform's entities for each `switch` subentry, now and when one is added; and `async_track_devices`, which keeps the devices' identifiers and names and the entities' unique ids in step with a new bridge id, device id or title. The platforms: `sensor.py` (Status, eWeLink devices seen, Last action), `binary_sensor.py` (Reachable, Online), `event.py` (Press) and `button.py` (Test toggle). `icons.json` has their icons, and `translations/en.json` their texts under `entity` |
| `tests` (the entities) | `platforms.py`: the entity ids on the bridge's device and on the switch "Hall" of `fakes.switch()`, and `start`, `settle` and `tick`, for the `lan` and `bridge` fixtures. `test_entity.py` checks the devices, the unique ids across a reload, switches added, changed and removed while the entry is loaded, also with *Add switch*, the device ids in the bus events, a new bridge id, device id or title, also while the entry wasn't loaded, and that every entity's texts are in `en.json` and nothing else; `test_sensor.py`, `test_binary_sensor.py`, `test_event.py` and `test_button.py` check each platform's entities through the status's states, presses, toggles and time |
| `tests/tools` | Tests of the fake Hue bridge (the .NET app's `Sonoff2Hue.FakeHue.Tests`) |
| `tests/engine/fixtures` | `dotnet_text.json` and `dotnet_engine.json`, made by `tools/dotnet_oracle` from .NET and from the .NET app's own code (decision 7) |
| `tools/fake_hue` | The fake Hue bridge, a port of the .NET app's `tests/local/FakeHue` (decision 12): `bridge.py` (state, `Fault`s), `server.py` (endpoints, HTTPS, certificate), `__main__.py` (`python -m tools.fake_hue`, settings `FAKE_HUE_BIND`, `FAKE_HUE_PORT`, `FAKE_HUE_LINK_BUTTON`) |
| `tools/dotnet_oracle` | The generator of those fixtures: a .NET 10 console app (`Program.cs`, `Engine.cs`, and `Yaml.cs`, which runs the YAML cases in child processes, since some end .NET's process) that references the app's `Sonoff2Hue.csproj`, the cases it evaluates (`engine_cases.py`, with `yaml_cases.py`) and `generate.sh` |
| `tools/js_oracle` | The generator of `tests/engine/fixtures/js_picker.json` (decision 20): `oracle.mjs` runs the .NET app's web UI modules `targets.js` and `format.js` in Node on the cases of `picker_cases.py`, and records JavaScript's `trim()` and `toLowerCase()` for every code point; `generate.sh` |
| `tools/develop` | The development Home Assistant's configuration folder (decision 18): `python -m tools.develop <folder>` copies `configuration.yaml` once and writes Home Assistant's HTTP store, `.storage/http`, on 127.0.0.1 (port 8123 or `SONOFF2HUE_DEV_PORT`), and refuses a port in use. `tests/tools/test_develop.py` loads that store with Home Assistant's own code, and checks the pins of `requirements_dev.txt` against its manifests |
| `tools/e2e` | The end-to-end test (decision 27), `python -m tools.e2e`, which `scripts/e2e` runs: `__init__.py` (the switch and the PUTs to expect), `hass.py` (Home Assistant's process, started and restarted as `scripts/develop` does, its onboarding, and its REST and WebSocket APIs), `devices.py` (the fake bridge's process and the simulated switch), `checks.py` (the report, as the .NET app's `local.sh` gives it, and the bus events received), `scenario.py` (the steps and their checks) and `__main__.py` (free ports, and the work folder `.dev/e2e`) |
| `tools/send-ewelink-announcements.py` | The simulated wall switch: the .NET repository's `.github/scripts/send-ewelink-announcements.py`, copied unchanged, so both repositories simulate presses the same way. ruff (with `force-exclude`) and mypy leave it out, as they would ask for changes to it |
| `scripts` | `setup` (the virtual environment) and `check` (lint, formatting, types, tests); `develop` (decision 18) runs a local Home Assistant with this checkout's integration from `.dev/venv` (`requirements_dev.txt`), and the fake Hue bridge and the simulated wall switch from `.venv`, with its commands `run`, `hass`, `fake-hue`, `setup`, `press` and `lights`; its state is in `.dev/`, which git ignores, and `SONOFF2HUE_DEV_CONFIG` chooses another configuration folder than `.dev/config`; `e2e` (decision 27) runs the end-to-end test, `tools/e2e` |
| `.github/scripts` | `check-commits.sh` (the .NET repository's, unchanged) and `test-each-commit.sh` |
| `.github/workflows` | `ci.yml` (*Lint*, *Test*, *End-to-end*) and `commits.yml` (per-commit checks, *All commits pass*) |
| `.github/workflows/validate.yml` | *hassfest* and *HACS*, the validation the Marketplace requires (decision 16) |
| `.vscode` | The VS Code debug setup (decision 18): `launch.json` runs Home Assistant and the fake Hue bridge under debugpy, alone or together; `tasks.json` runs `scripts/develop setup`, `press` and `lights`; `extensions.json` recommends the extensions. `.gitignore` keeps everything else in `.vscode/` out |
| `.github/workflows/release.yml` | *Release* (decision 29), for a pushed tag `vX.Y.Z` only: *Check the tag* checks it and chooses Latest and the notes' start; *GitHub Release* creates the GitHub Release "sonoff2hue X.Y.Z" |
| `.github/scripts`, releases | `release.sh`: the release checks, the released versions, Latest and where the notes start. `release-test.sh`: its tests, and both jobs of `release.yml` run as GitHub runs them, against a stand-in `gh`; CI's *Test* job and `scripts/check` run it |
| `README.md` | The users' documentation, which the Marketplace and GitHub show (decision 28): what the integration does and how, its requirements, the installation, the setup with the import of a `sonoff2hue.yaml`, the switches and their device keys, the devices and entities, presses in automations, the repairs, the activity, log and diagnostics, troubleshooting, limitations and the differences from the .NET app; then *Development* (decisions 18 and 27) and *Releases* (decision 29) for contributors |

## Parity with the .NET app

**Superseded by decision 30**, except its rows *mDNS socket*, *Interfaces*, *Network change*, *Queries*,
*Announcements* and *Rejoin*, which stay the mDNS layer's specification.

The C# tests are ported as the executable form of this table, with recognizable names.

| Area | What stays exactly the same | .NET source |
|---|---|---|
| mDNS socket | IPv4 224.0.0.251:5353, bound to 0.0.0.0, except on Linux (a deliberate difference, below). Options: SO_REUSEADDR+SO_REUSEPORT, multicast TTL 255, loopback on. 64 KiB receive buffer. A malformed packet is dropped at trace level, and a failing handler is logged without stopping the loop | `Mdns/MdnsListener.cs` |
| Interfaces | Joins every interface that is up, multicast-capable and not loopback, on its IPv4 addresses. A failed join is skipped. With none joined, it fails, warns "mDNS socket error (…); retrying in 5s." and retries every 5 s | same |
| Network change | Address changes, debounced 3 s, close the socket and rejoin. Each (re)start resets the trackers and queries again | same |
| Queries | PTR `_ewelink._tcp.local` 0, 1 and 3 s after each (re)start (waits of 0, 1 and 2 s). The LAN monitor repeats it every 2 min after the third (123 s, 243 s, …). The runner repeats it every 30 s while a mapped switch is unseen, warning "Not seen on the LAN yet: …" | `Bridge/Sonoff2HueRunner.cs`, `Web/Monitoring/LanMonitor.cs` |
| Announcements | Responses only. TXT records with TTL > 0 named `*._ewelink._tcp.local` whose instance starts with `ewelink`. The id is TXT `id`, or the instance from character 8, lowercased. `seq` is a long. `data1`..`data4` are concatenated. Encrypted when `encrypt=true`. The address comes from SRV→A, or else the sender, unless that is one of this host's own addresses (a deliberate difference, below) | `Ewelink/EwelinkAnnouncement.cs` |
| Rejoin | A query with authority records for `eWeLink_<id>…` (a probe) resets that device's baseline | same, `Ewelink/SwitchTracker.cs` |
| Press rule | Baseline / Duplicate / SequenceReset / RelayChanged / SettingsChanged / Press, decided as `PressDetector` does: its setting keys, relay parsing (`switches[]` and `switch`), the payload compare when `seq` is missing, and `relaySeparation` for detached mode | `Ewelink/PressDetector.cs` |
| Decryption | AES-128-CBC/PKCS7, key MD5(trimmed device key), 16-byte IV. The same error strings, e.g. "iv has N bytes instead of 16". Never raises | `Ewelink/EwelinkCrypto.cs` |
| Targets | Names or ids, case-insensitive, with an optional `room:`/`zone:` prefix. Exactly one match is required, and a group without `grouped_light` is an error. Duplicates are removed by (kind, id). The same messages, which list the known names in `StringComparer.OrdinalIgnoreCase` order (by code point) | `Bridge/TargetResolver.cs` |
| Toggle | If any target is on, all go off; otherwise all go on. It reads the light and/or grouped_light states, with one retry after 300 ms on a transient error. PUTs are sequential, targets already in the wanted state are skipped, and failures are counted per target | `Bridge/HueToggler.cs` |
| Runner | The names are resolved again every 10 s while the bridge's errors are transient; every name that doesn't resolve is listed. Toggles of one switch are serialized. A press that waited more than 5 s since it was received is dropped. Keep-alive `GET /clip/v2/resource/bridge` at once, then every 45 s. Reachability is reported on changes only. Stopping stops the queries, the keep-alive and every toggle, and waits for them (decision 14). The same log lines | `Bridge/Sonoff2HueRunner.cs`, `IRunnerObserver.cs` |
| HTTP | 5 s timeout, 4 s connect. Idle connections are kept 5 min by its own connector. Header `hue-application-key`, and no Accept, Accept-Encoding or User-Agent. Requests go to `Uri.IdnHost`, ids escaped as `Uri.EscapeDataString` does and dot segments removed. The pin is the SHA-256 of the leaf certificate's DER, normalized as `HueTls.Normalize` does. A 2xx with `errors[]` is a failure, and 401/403 means the key was rejected. Transient means no status, or 429/500/502/503/504. Answers are decoded as `ReadAsStringAsync` does (charset, byte order marks) and read as `JsonSerializer` with Web defaults reads `HueEnvelope`; a reason phrase the status line lacks is .NET's. Where .NET throws something other than `HueApiException` for a malformed answer, the port raises `UnexpectedError` with .NET's exception and message. The same messages | `Hue/HueClient.cs`, `Hue/HueTls.cs`, `Hue/HueModels.cs` |
| Supervisor | Its checks in .NET's order, each with its state, message and problems: not paired, no switch, `ValidateForRun`'s problems, a client that can't be made, then connecting. It pings the bridge every 10 s while its errors are transient, showing "Waiting for the Hue bridge at …: …" and warning once, then resolves the names, which retries every 10 s on the supervisor's logger. Names that aren't on the bridge, a rejected key and any other refusal wait for the next change; the unpinned-certificate warning comes before running. An unexpected stop is logged with its trace and shown as "The bridge stopped unexpectedly: …", and the bridge starts again 30 s later, as after a runner that returned. Any config change restarts it, once the run before it has ended (decision 23). Stopping stops the runner and closes the client. The same messages, except where they said to pair the bridge (decision 21) | `Web/Management/BridgeSupervisor.cs` |
| Status | The service's state, message and problems, and "since", which moves only when one of them changes. Any state but running forgets the resolved targets, and running again with the same message changes them without a publish. The bridge's reachability, its message while unreachable and the time of each report, shown only while paired; a new configuration resets them only when the address, key or pin changes. Per switch, in the configuration's order: its LAN device (online for 5 min), the targets, and the last action and error, kept across configuration changes. Changes reach the listeners together, 500 ms after the first; a LAN device's only when it is a configured switch's | `Web/Monitoring/StatusTracker.cs` |
| Pairing | `GET /api/0/config` gives the bridge's id and the pin, read from the same connection, as `GetFromJsonAsync` reads it (keys without regard to case). `POST /api` with devicetype `sonoff2hue#<host up to its first dot, at most 19 letters and digits>` and `generateclientkey`, every 2 s for 60 s. Error 101 means keep waiting, and transient failures are retried; each attempt is reported, as the web UI showed it. After 60 s: "The link button was not pressed within 60 seconds. Try again." A changed certificate, or a refusal other than 101, aborts | `Hue/HueBridgeSetup.cs`, `Web/Management/HuePairing.cs` |
| Discovery | For 5 s, in parallel: mDNS `_hue._tcp.local` (queries at 0, 1 and 3 s) and `https://discovery.meethue.com/`. An mDNS answer's address comes from SRV→A, or else its sender, unless that is one of this host's own addresses (a deliberate difference, below). Deduplicated by IP and sorted ordinally, each with its source. The cloud's answer is read as `GetFromJsonAsync<JsonArray>` reads it; its failures are only logged at debug level, as .NET catches them, except an `ArgumentException`, which fails the discovery in both | `Hue/HueBridgeSetup.cs` |
| Bridge address | Accepted exactly when .NET's `Uri` reads `https://<address>/` as a host and a port, and normalized as it does (Research findings): IPv6 gets brackets, IPv4 forms such as `192.168.1` are written out, host names are lowercased only when they have an ASCII capital letter. A scheme, path, user info, query, fragment or spaces are rejected, with the same message | `Hue/HueBridgeAddress.cs` |
| Setup flow | The web UI's Hue bridge page and `hue-pair`: the 5 s search, the address check, reading the bridge with the same messages, `hue-pair`'s text about the bridge and the certificate to pin, the pairing's attempts as the web UI showed them ("The bridge did not answer (…); still trying."), its outcomes, messages and log lines, and Retry reading the bridge again. The bridge is saved as `HuePairing` saved it, the switches untouched. An imported `sonoff2hue.yaml` gets the run's checks (`ValidateForRun`) and its first connection to the bridge (the supervisor's client and keep-alive), with their messages | `Web/Management/HueEndpoints.cs`, `HuePairing.cs`, `BridgeSupervisor.cs`, `Cli/Commands.cs` (`hue-pair`, `run`), the UI's `views/hue.js` |
| Fake Hue bridge | The same resources, ids, endpoints, answers and messages, the 30 s link-button window, the request and PUT logs, and the settings. Python adds `Fault`s for tests | `tests/local/FakeHue` |
| LAN registry | Per device: address, type, encrypted, seq, first and last seen, last event and its time, last press, detached, summary, decode error. A broadcast without an address, type or seq keeps the earlier one, and one that can't be decoded keeps the summary and detached mode; the decode error is always the last broadcast's. A repeat only moves the time last seen. Every broadcast but a repeat is reported, and a device's first. Ids match and sort as `OrdinalIgnoreCase` does. Online means heard within the last 5 min. The time of the last packet counts any packet | `Web/Monitoring/DeviceRegistry.cs` |
| LAN monitor | Runs as long as the integration, also before a switch is mapped, with a tracker of its own; a config change gives it a new one. The registry is listening while the listener runs, also while its socket retries. An unexpected stop of the listener is logged at error level as "The LAN monitor stopped; restarting it in 10 s.", and it starts again 10 s later | `Web/Monitoring/LanMonitor.cs` |
| Activity | The last 1,000 log entries: id, time, level, category (the logger's name after its last dot), message, and the exception as "Type: message", without the stack trace, which stays in the log. `get(after, limit)` gives the newest `limit` entries after `after`, oldest first | `Web/Monitoring/LogBuffer.cs` |
| Runtime | What lived as long as the process lives as long as the config entry: the LAN monitor with its listener, the device registry, the status tracker and the log buffer. A change of the configuration restarts only the bridge supervisor, as `ConfigStore`'s change token did: the running supervisor logs "Configuration changed; restarting the bridge." and stops, the LAN monitor takes a new tracker and the status the new configuration (`OnConfigChanged`), and the next run starts once the last one has ended | `Web/WebServer.cs`, `Web/Management/ConfigStore.cs`, `BridgeSupervisor.cs` |
| Logging | The same lines at the same levels, each on the logger of its module (`custom_components.sonoff2hue.<module>`), whose name after the last dot is the category. The log buffer takes every record of the integration's loggers that their levels let through, as .NET's took every category from its minimum level; the levels themselves are Home Assistant's (Deliberate differences) | `Web/WebServer.cs` (`ConfigureLogging`), `Web/Monitoring/LogBuffer.cs` |
| Switch test | The switch is found by its id, trimmed, without regard to case, else "No switch with device id … is configured."; the bridge must be paired, else "Pair the Hue bridge first.". A client of its own for the settings as they are, whose `ConfigError` is the answer, closed afterwards; the targets resolved from the bridge's lights and groups at that moment, their problems joined as the web UI showed them; the toggle with `HueToggler` on the `sonoff2hue` logger, the line "…: test from … turned ON …", and the result: turned on, changed, unchanged, failed, the milliseconds and the targets; a bridge error's message. The test reaches neither the status nor the runner's reports | `Web/Management/ConfigEndpoints.cs` (`TestAsync`), the UI's `views/switches.js` |
| Switch picker | A light is saved by its name if no other light has that name, else by its id; a room or zone as `room:<name>` or `zone:<name>` if its name is unique among its kind, else by its id. Entries that still resolve to a selected resource keep their spelling and are saved once; entries that don't resolve stay unless removed. Names match after JavaScript's `trim()` and `toLowerCase()`, not .NET's rules | the UI's `targets.js` |
| Switch form | Before the server sees it, with JavaScript's string rules: a device chosen or typed (and trimmed), of 4-40 ASCII letters and digits, not used by another switch; a name of at most 64 UTF-16 code units once trimmed; at least one target; a new key when replacing the current one. The dialog's messages, all at once. It sends the name or null, the key null to keep it, `""` to remove it, else trimmed, and typed entries split at commas. The server's errors are joined per field as the dialog showed them, the picker's field taking those of `targets`, `lights` and `groups` | the UI's `views/switcheditor.js`, `format.js`, `ui.js` |
| Switch validation | After trimming with .NET's `Trim()` and counting UTF-16 code units: a name of at most 64 characters, without control characters; an id of 4-40 ASCII letters and digits, lowercased with `ToLowerInvariant` first; a key of at most 128 characters, without white space or control characters. Each list holds at most 50 entries, counted before duplicates are removed without regard to case, each of 1-200 characters without control characters. At least one target. The same messages, in the same order, under the same keys (`name`, `deviceId`, `deviceKey`, `lights`, `groups`, `targets`). A new switch's device id must be free (409), and a changed switch must exist (404) and may not take another's id (409); both are checked before and again after the bridge is asked. The entries are checked against the bridge when it is paired and reachable, and only warned about otherwise. A key of null keeps the current one, `""` or white space removes it, and a changed device id drops a kept key | `Web/Management/ConfigEndpoints.cs`, the UI's `switcheditor.js` |
| Switch dialogs | The web UI's switch editor, a step at a time: identify-by-press with the dialog's rule for a press (a device event whose last event is a press, at a time the device didn't have) and `switch_form.identify`'s messages, a device of another switch named and skipped; the device list in its order (unused first, the latest press first, then by id), each device with its id, address, type, online state, last press (`relativeTime`) and the switch that uses it; the device id checked as the dialog checked it; the picker's lights with their state, rooms and zones with "no lights", the id beside a repeated name, entries that don't match the bridge kept until removed, and the choices saved with `selection_to_config`; typed entries split at commas; the key kept, replaced or removed, with the warning; the dialog's checks, then the server's, their messages on the fields the web UI used (`field_errors`); "Added …" and "Saved …" | the UI's `views/switcheditor.js`, `targets.js`, `format.js` and `dom.js`; `Web/Management/ConfigEndpoints.cs`, `HueEndpoints.cs` |
| YAML import | What `AppConfig.Parse` and YamlDotNet 18.1.0 accept and refuse, the values they read, and their messages, with line and column in UTF-16 code units: camelCase keys; an unknown key names its section and the valid keys; duplicate keys; `AppConfig`'s three messages for a value of the wrong kind; null sections, lists and values normalized, and an empty `switches` entry refused; scalars such as `1000e12345`, `yes` and `0x10` kept as written; a document of a single scalar read again; comments, styles, anchors, tags, directives, documents, a byte order mark, CRLF and tabs as YamlDotNet takes them (decision 15). `ValidateForRun` is `config.py`'s `validate_for_run` | `Configuration/AppConfig.cs` |

Deliberate differences, documented in the README:

- One UDP 5353 socket serves both the LAN monitor and the runner, where the .NET backend opens two. Each keeps its
  own tracker, reset as in .NET, so baselines and press decisions are the same.
- Home Assistant's default log level, warning, hides the info lines. The activity buffer, which diagnostics show,
  records the integration's lines at Home Assistant's levels too: warnings by default, info and debug lines once debug
  logging is on for the integration or `logger:` asks for them, where .NET's buffer had every line from Information
  up. The runtime never changes a log level (decision 23). The logbook shows each press as the switch's Press and
  Last action (decision 26).
- There is still exactly one bridge (`single_config_entry`).
- The supervisor's messages that said to pair the bridge say how in Home Assistant: reconfigure the sonoff2hue
  integration (decision 21).
- Where .NET's message comes from its JSON parser, such as for a bridge whose config isn't JSON, the text after "No Hue
  bridge answered at …:" is Python's. `JsonException` names a path, line and byte position that Python's parser
  doesn't.
- The message about a malformed device id gives the made-up example `1000abcdef`, where the .NET app's names one of the
  user's own devices, which this public repository doesn't.
- The switch editor lists lights, rooms and zones in the server's order: by name, with `OrdinalIgnoreCase`. The web UI
  sorted them again with the browser's collation (`localeCompare`), which Python can't reproduce, so only the order in
  which new selections are saved can differ.
- The YAML import refuses values tagged `!!float` or `!!timestamp`, reports a document that overflows .NET's stack
  as an error, and checks tags with an authority, a DOS path after a scheme or `mailto:` without parsing the host
  (decision 15).
- On Linux, the mDNS socket is bound to 224.0.0.251 instead of 0.0.0.0 (decision 19). It gets every multicast packet
  and sends its queries as before, but no unicast, so it never takes a unicast reply meant for Home Assistant's
  zeroconf, which shares the port. macOS can't send from such a socket, so there it stays on 0.0.0.0.
- A broadcast without SRV and A records gives its sender as the device's address only when the sender isn't one of
  this host's own IPv4 addresses; otherwise the LAN registry keeps the address it knew (decision 19). On Home
  Assistant OS, the Supervisor's multicast plugin repeats every broadcast from its `hassio` bridge, and the update
  after a press has no address records, so .NET's rule would show the bridge's address for the switch after each
  press. For the same reason, Hue bridge discovery lists no bridge for an answer without an A record from one of
  this host's own addresses.
- The switch test logs "test from Home Assistant" where .NET's said "test from the web interface", and "Pair the Hue
  bridge first." says how: "Pair the Hue bridge first: reconfigure the sonoff2hue integration." The button gets the
  server's messages, where the web UI showed its own "Pair the Hue bridge first, then test again." for both of the
  server's 409 answers, an unpaired bridge and settings that can't be used.
- What Home Assistant adds, without a counterpart in .NET (decision 23): the bus events of presses and their outcomes,
  reauthentication when the bridge rejects the key, and repair issues for what the dashboard showed as warnings and
  problems.
- The logbook has Home Assistant's own entries for each press, the switch's Press and the change of its Last action,
  with the details in their attributes, where the dashboard's Recent activity listed the runner's lines, such as "…:
  wall switch operated (seq 12)." and "…: turned ON light 'Ceiling' in 85 ms (1 already on).", among the last 30 log
  lines from information up. Those lines are in Home Assistant's log at its levels and in the diagnostics' activity.
  A press whose outcome is the one before shows as the press alone (decision 26).
- The switch dialogs (decision 24): identify-by-press waits 60 seconds and isn't turned on by itself; a device of
  another switch can be chosen from the device list and is then refused with the dialog's message, as Home
  Assistant's options can't be disabled; the list's times don't refresh; an edited switch keeps its device with
  *Keep device …*; when the bridge can't list its lights, a menu comes before typing them; the server's warnings are
  confirmed before saving instead of shown after it; a conflict or a deleted switch ends the dialog; and an
  unexpected failure shows its own message.
- The entities show what the dashboard showed, with these differences (decision 25). Status's states have short names
  where the banner had titles, and no checklist. Reachable is unknown both before the bridge is first checked and
  while it isn't paired, where the Hue card said "Not checked yet" or "Not paired". Last action shows the newer of the
  switch's last action and its last error, where the card showed the last action and, apart from it, the last error
  until a restart. Press fires for the presses that the bridge acts on, and keeps its last one across a restart;
  Online's `last_press_at` is the card's "Last press", which counted every press. The count of switches online, the
  version and the uptime have no entity. The dashboard also read the status every 30 seconds; the entities follow
  the status, the LAN registry and timers of their own.

## The web interface in Home Assistant

How each part of the .NET app's web interface maps to Home Assistant's own UI (the plan of 2026-11-23 and the user's
choice of 2026-11-24), filled in as each part is done and verified.

| The .NET app | In Home Assistant |
|---|---|
| Dashboard banner: the service's state, message, problems and since | `sensor` **Status** on the bridge's device (`sensor.hue_bridge_status`): an enum of `not_configured`, `invalid_config`, `connecting`, `running` and `error`, with `message`, `problems` and `since` (decision 25); the problems are also repairs (decision 23) |
| Hue card: paired, reachable, address, checked, message | The bridge's device, "Hue bridge", identified by the bridge id; its `binary_sensor` **Reachable** (`binary_sensor.hue_bridge_reachable`, connectivity, diagnostic), unknown until it is checked and while the bridge isn't paired, with `address`, `message` and `checked_at` (decision 25) |
| LAN card: listening, devices seen, last packet | `sensor` **eWeLink devices seen** (`sensor.hue_bridge_ewelink_devices_seen`, diagnostic, in devices), with `listening` and `last_packet_at` (decision 25) |
| Switches card: configured, online | No entity of its own: each switch's Online (decision 25) |
| A switch's card: name, device id, online, address, last press, last action, its toggles, warnings and error; the Switches page's Test button and "Device key set" | A device per `switch` subentry, named by its title, "SONOFF" "MINIR4M", with the device id as its serial number: `event` **Press** (button, event type `press`, `seq`), `sensor` **Last action** (`turned_on`, `turned_off`, `failed` or `dropped`; `at`, `changed`, `unchanged`, `failed`, `elapsed_ms`, `targets`, `message`), `binary_sensor` **Online** (connectivity, diagnostic: heard within 5 minutes; `address`, `last_seen_at`, `last_press_at`, `seq`, `encrypted`, `has_device_key`, `detached`, `decode_error`) and `button` **Test toggle** (config) (decision 25); the warnings are also repairs (decision 23) |
| Recent activity, Logs page | The logbook, also on the page of the switch's device, with Home Assistant's own entries for each press: the switch's **Press** ("Press") and the change of its **Last action** ("Turned on", "Turned off", "Failed" or "Dropped"), with the details in their attributes (`seq`; `changed`, `unchanged`, `failed`, `elapsed_ms`, `targets` and `message`) (decision 25). Home Assistant's log, with the runner's lines, with warnings by default, and info and debug lines once debug logging is on for the integration. The diagnostics (`diagnostics.py`), with the activity's last 1,000 entries at those levels next to the configuration, the status and the LAN's devices (decision 26) |
| Switches page, switch editor | *Add switch* on the integration's page and *Edit switch* on each switch: the switch subentry flow (`switch_flow.py`), which finds the MINIR4M by a press of its wall switch, among the devices seen on the LAN or by its id, then takes the name, the bridge's lights, rooms and zones and the device key, checked and saved as the web UI and the server did. The integration's page lists the switches, and Home Assistant's own UI deletes one (decision 24) |

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
- **Config subentries.**
  - `ConfigFlow.async_get_supported_subentry_types` maps each type to a `ConfigSubentryFlow`. A subentry flow starts
    only with `user` or `reconfigure`, with no discovery and no reauthentication.
  - `self._get_entry()` and `self._get_reconfigure_subentry()` give the entry and the subentry.
  - Adding, updating or removing a subentry fires the entry's update listeners (`_async_save_and_notify`).
    `async_update_reload_and_abort` raises when the entry has an update listener, so with the reload listener use
    `async_update_and_abort`.
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
  value the .NET app pins (`GetRawCertData`). It turns off certificate validation and checks the digest instead.
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
    should use it for scheduling a reload", which breaks in 2026.12.0, and still reloads; it doesn't raise, although
    the note on config subentries above says so. `async_update_and_abort` saves without a reload, and the update listener does the
    rest. An entry that isn't loaded has no listener and takes the new data when it is set up.
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
- **Interfaces and the multicast plugin.** .NET's choice of interfaces (decision 8) joined 224.0.0.251 on three: the
  LAN interface, and Docker's bridges `docker0` and `hassio`, which are up, multicast-capable and have IPv4 addresses.
  It left out `lo` and 8 interfaces without an IPv4 address, the containers' veth ends among them. Home Assistant's own
  network settings had only the LAN interface enabled; the engine doesn't read them, as .NET didn't.
  - The Supervisor's multicast plugin repeats mDNS between the LAN and the `hassio` network, so every packet from the
    LAN arrives twice: the copy, a few milliseconds later, comes from the `hassio` bridge's address, one of this host's
    own. The tracker takes the copy as a duplicate.
  - The user's MINIR4M announces each new `seq` twice, about 1.1 s apart, as mDNS announces a changed record, so each
    press arrived 4 times: once as the press, then as 3 duplicates.
  - The answers to the PTR queries carry SRV and A records, so their copies still name the device's own address. A
    press's update carries only the TXT record, so the engine takes its sender as the address, and for the plugin's
    copy that is the `hassio` bridge's. The LAN registry, like .NET's, keeps the address of every broadcast,
    duplicates included, so it would show the bridge's address for the switch after each press. Now the engine
    takes no address from a sender that is one of this host's own addresses (decision 19).
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
  service browser is one of the record listeners of Home Assistant's instance, so it gets every record that instance
  receives. While the engine's socket was open, the browser stayed registered, and SonoffLAN's entities wrote their
  state 211 times in 3 minutes, within 5 s after each of the 5 presses. The two listen to the same switches side by
  side.
- **The Supervisor's proxy** to Core's API, `http://supervisor/core/api/…` with the app's `SUPERVISOR_TOKEN`, forwards
  GET and POST, and DELETE since Supervisor 2026.02, but only for apps with `homeassistant_api: true`; any other app
  gets 401, and the Supervisor logs "Not permitted API access" (`api/proxy.py`, `_check_access`, in 2026.11.0). The
  official Terminal & SSH app has `hassio_api` and `hassio_role: manager` but not `homeassistant_api` (`ssh/config.yaml`
  in home-assistant/app-ssh), so it can't add or delete config entries. Over its SSH, a custom integration can start
  from a line in configuration.yaml (with `async_setup` and a `CONFIG_SCHEMA`), or a long-lived access token of an
  administrator calls Core's REST API directly, at `http://homeassistant:<port>/api` from the app. `ha core logs`
  prints the last 100 records unless `-n` asks for more.
- **Log volume.** Home Assistant's log listener (`HomeAssistantQueueListener`) counts each logger's records at INFO and
  above, and when one reaches 200 within 5 minutes, it warns once: "Module … is logging too frequently. 200 messages
  since last count". The spike's warning per packet reached it in its first minute; the engine logs per packet at debug
  and trace level, as .NET did.

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

.NET's own rules, which the port follows (measured on 2026-11-24 with .NET 10.0.15 and invariant globalization, as the
app runs; `tools/dotnet_oracle` keeps them as fixtures):

- `char.IsWhiteSpace`, which `Trim()` and `IsNullOrWhiteSpace` use, is true for 25 characters: U+0009-U+000D, U+0020,
  U+0085, U+00A0, U+1680, U+2000-U+200A, U+2028, U+2029, U+202F, U+205F and U+3000. Python's `isspace()` also counts
  U+001C-U+001F.
- `ToUpperInvariant` maps one character to one character. It leaves U+0131 (dotless i) and U+017F (long s), which
  Python maps to ASCII, and maps 27 Greek letters with ypogegrammeni that Python only maps to several characters.
  `ToLowerInvariant` agrees with Python's one-to-one mappings, and leaves 'İ'. `OrdinalIgnoreCase` compares the
  `ToUpperInvariant` of both sides, also for surrogate pairs, so 'ı' doesn't match 'i' and "eWeLınk" isn't "ewelink".
- `long.TryParse` takes white space U+0009-U+000D and U+0020 around an optional sign and ASCII digits, and ignores
  trailing NUL characters; `1_0`, Arabic-Indic digits and a no-break space fail.
- `Convert.FromBase64String` skips spaces, tabs, CR and LF, and accepts non-zero bits after the last character.
- `JsonNode.Parse` rejects `NaN` and `Infinity`, leading zeros, comments, trailing commas, a byte order mark, a NUL after
  the value, and more than 64 levels. An object with a duplicate key, or a string with a lone surrogate escape, parses,
  but reading it throws: `ArgumentException` "An item with the same key has already been added. Key: a (Parameter
  'key')", or `InvalidOperationException` "Cannot read incomplete UTF-16 JSON text as string with missing low
  surrogate." for a lone high surrogate, and "Cannot read invalid UTF-16 JSON text as string. Invalid surrogate value:
  '0xDC00'." for a lone low one. `ToJsonString` writes numbers as written and escapes strings as `JavaScriptEncoder.Default`
  does: everything outside printable ASCII and the characters `"&'+<>`` ` as `\uXXXX`.
- `JsonNode.DeepEquals` compares numbers by exact value (`1`, `1.0` and `1e0` are equal; `1` and `1.0000000000000001`
  aren't), objects without regard to order, and never `true` with `1`. Before it unescapes a string, it compares UTF-8
  lengths and the text before the first escape, so a string with a lone surrogate escape can compare as unequal without
  throwing.
- `StringComparer.OrdinalIgnoreCase.Compare`, which orders the names that the target resolver's messages list, compares
  code points, each as `ToUpperInvariant` maps it, and then the UTF-16 lengths; a lone surrogate counts as itself
  (`InvariantModeCasing.CompareStringIgnoreCase` in dotnet/runtime's release/10.0). So `'𐐨'` (U+10428) sorts after
  `'Ａ'` (U+FF21), where UTF-16 code units would put it first. LINQ's `Order` sorts stably.

JavaScript's string rules, which the .NET app's web UI applied and the port of its switch editor follows (measured on
2026-11-24 with Node 20.19.2, ICU 76.1 and Unicode 16.0, Python 3.14's version; `tools/js_oracle` keeps them as
fixtures):

- `String.prototype.trim` removes 25 characters: U+0009-U+000D, U+0020, U+00A0, U+1680, U+2000-U+200A, U+2028, U+2029,
  U+202F, U+205F, U+3000 and U+FEFF. .NET's `Trim()` has U+0085 instead of U+FEFF. Python's `strip()` also removes
  U+001C-U+001F and U+0085, but not U+FEFF.
- `toLowerCase` uses Unicode's full lowercase mappings and the final-sigma rule, as Python's `str.lower()` does; the two
  agree for every code point and in context. 'İ' becomes 'i' and U+0307, and a capital sigma after a cased letter and
  not before one, skipping case-ignorable characters, becomes the final sigma. .NET's `ToLowerInvariant` maps one
  character to one, keeps 'İ' and has no final-sigma rule, and `OrdinalIgnoreCase` compares uppercase forms. So the
  browser matches U+1E9E with 'ß', the Kelvin sign with 'k' and 'İ' with 'i' and U+0307, which .NET doesn't, and .NET
  matches the two small sigmas, or the micro sign and the Greek small mu, which the browser doesn't.
- `length` and `slice` count UTF-16 code units, as .NET's strings do; Python counts code points.
- In `targets.js`, the template literal `${kind}:${id}` writes a null id as "null", so a light with a null id and a light
  with the id "null" are saved once between them.

The .NET rules of the switch API, measured with `tools/dotnet_oracle` on 2026-11-24 (.NET 10.0.15, invariant
globalization):

- `char.IsControl` is true for U+0000-U+001F and U+007F-U+009F only, and .NET tests each UTF-16 code unit, so a
  surrogate pair is never a control character.
- `SwitchValidation` lowercases a device id before it checks it, so the Kelvin sign becomes an ASCII 'k' and the id
  passes, while 'İ' stays and is refused. A trailing U+0085 is trimmed and a leading U+FEFF isn't. It refuses 51 equal
  entries, because it counts them before it removes duplicates.
- `UpdateAsync` looks the switch up with `FindSwitch` (`OrdinalIgnoreCase`), but replaces it where `NormalizedDeviceId ==
  currentId` (ordinal). A stored id that differs from the requested one only by a final sigma passes the first check and
  is then not found (404).
- Only a null device key keeps the current key: a key of white space is trimmed to `""` and removes it.

.NET on Linux, from dotnet/runtime's release/10.0 sources (2026-11-24), for the listener:

- `NetworkChange.NetworkAddressChanged` comes from a netlink route socket (`pal_networkchange.c`) bound to
  `RTMGRP_LINK | RTMGRP_IPV4_IFADDR | RTMGRP_IPV4_ROUTE | RTMGRP_IPV6_ROUTE`. Only `RTM_NEWADDR` and `RTM_DELADDR`
  raise it, once per message, so only IPv4 address changes count; a datagram's messages are read until `NLMSG_DONE` or
  `NLMSG_ERROR`. Links and routes raise `NetworkAvailabilityChanged`, which the app doesn't use.
- `NetworkInterface.GetAllNetworkInterfaces` (`pal_interfaceaddresses.c`) reads `getifaddrs`. An interface is
  `OperationalStatus.Up` when it has both IFF_UP and IFF_RUNNING, unless `ETHTOOL_GLINK` reports no link; that check
  runs only for hardware types other than Unknown, Tunnel and Loopback. It supports multicast with IFF_MULTICAST or
  IFF_ALLMULTI. Its type comes from the ARPHRD type (`pal_maphardwaretype.c`: 772 is Loopback, 768 and 769 Tunnel,
  everything unlisted Unknown). An address on an alias such as `eth0:1` belongs to `eth0`, as `if_nametoindex` says.

.NET's URI and HTTP rules that the Hue client depends on, from dotnet/runtime's release/10.0 sources and measured with
`tools/dotnet_oracle` (2026-11-24):

- **Bridge address.** `HueBridgeAddress` puts brackets around the address when `IPAddress.TryParse` reads it as IPv6
  (`IsValidStrict`: a zone runs to the end; brackets may be followed by a decimal port, or a hexadecimal one after "0x").
  `Uri` then tries, in order: IPv6 (its own, lenient validator: a zone of anything but `]` and `/`, a `/nn` prefix, a
  leading zero in the last octet of an embedded IPv4 address), IPv4 when the host starts with a digit (one to four
  parts, decimal, octal after a 0 or hex after 0x, the last filling the rest: `192.168.1` is 192.168.0.1, `0x` alone is
  a host name), a DNS name under IRI rules, then a UNC name.
  - IRI DNS names: anything non-ASCII except U+0080-U+009F; each label starts with an ASCII letter or digit, or a
    character from U+00A0; a non-ASCII label counts 4 more, and 1 more for each character above U+00FF, against the
    63 limit; U+3002, U+FF0E and U+FF61 separate labels too.
  - UNC names take labels of letters, digits, `-` and `_`, up to 256 characters, so `-bridge.local`, `_bridge` and a
    64-character label pass that way.
  - Empty user info is allowed (`@bridge`); dot segments in the path are removed, with `%2e` as a dot, so `bridge/a/..`
    passes and `bridge/./` doesn't.
  - The host is lowercased only if it has an ASCII capital letter (`Σας.local` stays, `AΣ.local` becomes `aσ.local`),
    `localhost` and `loopback` become `localhost`, bidi controls are dropped from DNS names, and nothing is normalized
    to NFC.
  - `IdnHost`, which requests connect to: with invariant globalization `IdnMapping` is plain Punycode per label, without
    IDNA mapping (`straße` stays, soft hyphens and fullwidth letters are encoded). It throws, so requests fail with a
    `UriFormatException`, for an `xn--` label with non-ASCII characters, a label that mixes directions, or a label or
    name that gets too long. UNC names stay as they are.
- **Requests.** A relative URI loses its dot segments, so an id of `..` would PUT to `/clip/v2/resource/`.
  `HttpClient` sends no Accept, Accept-Encoding or User-Agent and doesn't decompress.
- **Answers.** `ReadAsStringAsync` takes the Content-Type's charset, one pair of quotes removed, through
  `Encoding.GetEncoding`'s built-in names only: utf-8, latin1, us-ascii, utf-16, utf-32 and their aliases. Other names
  (`utf8`, `windows-1252`) throw an `InvalidOperationException`, and UTF-7 a `NotSupportedException`. A byte order mark
  is dropped; without a charset it chooses the encoding (UTF-8, UTF-32 LE, UTF-16 LE or BE), else UTF-8. US-ASCII
  decodes bytes above 0x7F as `?`. `ReadFromJsonAsync` uses the charset without looking for a byte order mark.
  - A status line without a reason gets .NET's phrase for 60 codes (none for 418). `EnsureSuccessStatusCode` says
    "Response status code does not indicate success: 404 (Not Found).", without the parentheses for a blank reason.
- **JSON with Web defaults.** Property names match without regard to case (`OrdinalIgnoreCase`, so `İd` isn't `id`),
  and the last of duplicate properties wins in a typed object. A `JsonObject` made so finds keys without regard to case
  and throws an `ArgumentException` for keys that differ only in case. A lone surrogate in a string the type reads, or
  in any property name of a type it fills, is a `JsonException`; unknown properties are skipped unread.
  - The app's own code then throws for malformed CLIP v2 answers: `NullReferenceException` for a null in `errors`,
    `data` or before the match in `services`; `ArgumentNullException` for a null id in a state list, whose light
    otherwise has a null id and name; `ArgumentException` for ids that differ only in case.
- **aiohttp.** A small response's connection is released before the caller sees it (`response.connection` is None),
  so the certificate is read in `aiohttp.Fingerprint.check`, which the connector calls after every TLS handshake.
  asyncio reports a connection closed during the handshake as `ConnectionResetError`, which .NET calls "The SSL
  connection could not be established, see inner exception." yarl takes an IPv6 zone in brackets unencoded
  (`https://[fe80::1%eth0]/`), and aiohttp connects to `raw_host`.

YamlDotNet 18.1.0, which reads the app's `sonoff2hue.yaml`, and PyYAML, compared on 2026-11-24 and 2026-11-25 in
YamlDotNet's sources (the 18.1.0 tag, 748334a) and with a scratch .NET probe:

- **PyYAML differs.** Home Assistant 2026.11.3 ships PyYAML 6.0.3 with libyaml. Its composer and YamlDotNet's parser
  disagreed on 23 of 207 texts: tabs after a colon, `[@a]`, `[a]#c` (an error in YamlDotNet), a byte order mark,
  anchored flow values, control characters and more. Its `safe_load` turns `yes`, `on`, `~`, `0x10` and `010` into
  booleans, null and numbers.
- **The parser** reads the text as UTF-16 code units, so columns count them. A byte order mark isn't skipped:
  `\ufeffhue:` is an unknown key. A key of more than 1024 characters can't be a simple key, so YamlDotNet reads it as a
  document of a single scalar. Some mistakes become error tokens, which fail only when the parser reaches them: a `#`
  straight after `]`, `,` or a quoted scalar, a plain key over several lines, a tab where indentation counts.
- **The deserializer** that `AppConfig` builds reads the first document, then wants the stream's end ("Expected
  'StreamEnd', got 'DocumentStart' (at Line: …, Col: …, Idx: …)."). It maps only the tags `!!map`, `!!str`, `!!bool`,
  `!!int`, `!!float` and `!!timestamp`; any other, `!!null`, `!!seq` and a bare `!` included, is "Encountered an
  unresolved tag". Keys are read as text, with their tags and anchors ignored, and an alias as a key is an error. A
  plain empty value, `~`, `null`, `Null` or `NULL` is null, except as a key. `!!bool` takes `true` and `false` in any
  ASCII case only (.NET's `RegexOptions.IgnoreCase` doesn't match `ſ` with `s` there) and gives `True` or `False`. More
  than 130 levels of nesting is an error. An exception from the parser that isn't a `YamlException`, such as a tag's
  `ArgumentException`, becomes "Exception during deserialization" at the node being read, and escapes before the first
  node.
- **A document of a single scalar.** YamlDotNet takes `AppConfig`'s static `Parse(string)` as a conversion from text, so
  the scalar's value is parsed again as a config. One that reads as itself, such as `a`, `1` or `!!str a`, recurses
  until the stack overflows, and the process dies ("Stack overflow.", exit code 134).
- **Tags.** In a tag YamlDotNet decodes `%XX` as UTF-8 (U+FFFD for invalid bytes) and reads `+` as a space. A tag that
  doesn't start with `!` must pass `Uri.IsWellFormedUriString(…, UriKind.RelativeOrAbsolute)` ("Global tags must be
  valid URIs."). Its rules, from System.Private.Uri (release/10.0) and measured:
  - A relative reference has no colon before the first `/`, `?` or `#`, no backslash and nothing to escape.
  - A letter followed by `:` or `|` is a drive: `c:/x` is an absolute file URI, never well-formed, and `a:b` fails.
    Scheme names are compared with `OrdinalIgnoreCase`, so `maılto:` isn't `mailto:`.
  - A non-ASCII character, or an escape of one, of an unreserved character or with a non-hex digit, starts IRI
    processing: it unescapes safe escapes and UTF-8 sequences in the IRI ranges and escapes the rest. Then a character
    outside the Basic Multilingual Plane is never canonical, private use characters only in a query, and space, `"`,
    `<`, `>`, `[`, `\`, `]`, `^` and `` ` `` make a text ill-formed while `{`, `|` and `}` don't. Trailing white space
    is ignored unless that processing changed the text.
  - .NET 10 throws an `IndexOutOfRangeException` for a UNC or `file://` host of only bidirectional control characters,
    such as `\\` followed by U+200E.

The .NET app's status tracker and bridge supervisor, read in its `main` (2026-11-24), whose details the port keeps:

- **Status tracker.** "Since" moves only when the state, the message or the problems change, so a repeated
  `SetService` publishes nothing. `SetRunning` stores the targets before it calls `SetService`, so running again with
  the same message changes the targets without a publish; any other state forgets them. While the runner runs, the
  bridge's "checked at" moves only when its reachability changes, as the runner reports changes only. The bridge is
  compared as `$"{BridgeIp}|{AppKey}|{CertificateSha256}"`, so a null and an empty pin are the same. Last actions and
  errors are never cleared, also not for a switch that was removed. A LAN device is published only when it is a
  configured switch's, so `DevicesSeen` can lag behind the registry until the next change.
- **Bridge supervisor.** Its run is cancelled by the config store's change token or by the host's stop. "Configuration
  changed; restarting the bridge." is logged when a change cancels a run, or the 30 s wait after a runner that
  returned, but not the 30 s wait after an unexpected stop, whose cancellation is swallowed. While it resolves the
  names, the state keeps the last message of connecting, which can still be "Waiting for the Hue bridge at …" after
  the bridge answered. The runner gets the supervisor's logger for its "not usable yet" warnings. A key rejected
  while the runner runs only makes the bridge unreachable: the state stays running until the next change.

The eWeLink LAN protocol and the Hue API, from the .NET app's research (public sources):

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
- A concurrency group keeps at most one run waiting by default, so a third run cancels the waiting one. GitHub Actions
  now has `queue: max` for up to 100 waiting runs, but actionlint 1.7.12, its newest release, rejects the key, so the
  workflow keeps the default.
- A tag push runs the workflow files of the tagged commit. A `push` trigger that names only branches doesn't run for
  tags, so *CI* and *Validate* don't run for a release tag.

## Decisions

| # | Decision | Why |
|---|----------|-----|
| 1 | A Python custom integration, domain `sonoff2hue`, for Home Assistant 2026.11 and later only (`hacs.json` `homeassistant` 2026.11.0, Python 3.14). It ports the .NET engine module by module, keeping its behavior (Parity with the .NET app) | The user asked for an integration like SonoffLAN with the behavior exactly as now, for 2026.11+. Integrations run in Home Assistant's own Python process |
| 2 | A public repository of its own, `abpostelnicu/sonoff2hue-homeassistant`, under the Apache-2.0 license. The .NET app stays in its private repository and is maintained, under a parity rule recorded in both repositories | The Marketplace installs only from public repositories with one integration. The private repository's history names the user's hosts and addresses. Apache-2.0 is Home Assistant core's license; the user chose it |
| 3 | `engine/` holds the ports, with no Home Assistant imports. The C# tests are ported as the specification. Where Python's libraries accept input that .NET rejects, the port reproduces .NET's result | Keeps the port checkable against the .NET source, module by module, and the engine testable without Home Assistant |
| 4 | Hue directly over CLIP v2, with its own link-button pairing, the pinned certificate (`aiohttp.Fingerprint`), and an aiohttp connector of its own that keeps idle connections 5 minutes | The user chose direct control. Home Assistant's shared connector would close the connection between two keep-alives (15 s against 45 s), and the .NET app's decision 9 keeps it warm for a quick toggle |
| 5 | Home Assistant's own UI, as the user chose. A single config entry for the one bridge: its setup flow finds or takes the bridge's address, then pairs it, or imports a `sonoff2hue.yaml`; reconfigure pairs again with the switches kept, and reauth runs after the key is rejected. One config subentry of type `switch` per MINIR4M, with identify-by-press. Entities: Status, Bridge reachable and eWeLink devices seen on the bridge's device; Press (event), Last action, Online and Test toggle on each switch's device. Repairs for problems, logbook entries for presses and toggles, and redacted diagnostics. Stored `lights`/`groups` keep the web UI's encoding (the name if unique, else the id; `room:`/`zone:` prefixes) | Everything the web interface showed or did has a native place, and Home Assistant's users and HTTPS replace its own sign-in. The same encoding makes imported and new switches resolve as in .NET |
| 6 | Pinned tools: `pytest-homeassistant` at the Home Assistant version (2026.11.3), pytest-cov, ruff (lint and format, lines of up to 120 characters, as in the .NET repository) and mypy (strict), installed by uv. `scripts/check` runs them all, and CI and `test-each-commit.sh` run that | pytest-homeassistant-custom-component no longer works with 2026.11. One script keeps local checks, per-commit checks and CI the same |
| 7 | Pin the port to the .NET app's own results. `tools/dotnet_oracle` runs the app's classes (a `ProjectReference` to a checkout of `abpostelnicu/sonoff2hue`) on edge cases that `engine_cases.py` builds, and .NET's string rules for every code point; the fixtures it writes are committed, and `tests/engine/test_dotnet_parity.py` and `test_text.py` replay them through the port. Known differences are listed in the parity test with their reasons. After a change to the app's engine, run `tools/dotnet_oracle/generate.sh <checkout>` | "Exactly as now" is checkable only against the app itself: the first runs found four rules where Python's functions differ from .NET's (white space, case mappings, JSON escaping, lone surrogates). CI needs no .NET and no access to the private repository |
| 8 | The listener is `engine/mdns.py`, on asyncio. It owns its UDP 5353 socket (SO_REUSEADDR, SO_REUSEPORT, TTL 255, loopback), joins 224.0.0.251 on the interfaces that .NET would choose (`interface_states`: SIOCGIFFLAGS, SIOCGIFHWADDR and ETHTOOL_GLINK ioctls, with the IPv4 addresses from `ifaddr`), and watches IPv4 address changes on a netlink socket of its own, debounced 3 s, with polling outside Linux. One listener per config entry serves both the LAN monitor and the runner: each adds a packet handler and a started callback, which runs at once if the socket is open, as each had a listener of its own in .NET. A started callback starts eagerly and runs until it first waits, as .NET raises `Started` right after it opens the socket, so its user resets its tracker before the socket's first packet. Removing a started callback is awaited: it also cancels the callback if it is running and waits for it, so the runner can stop and start again, as the supervisor restarts it, while the listener and the LAN monitor go on. Sockets are behind a small transport interface, so the tests drive joins, errors and datagrams without the network | It keeps decision 3 of the .NET app (no mDNS library): the engine needs the raw TXT updates and the probes, which python-zeroconf's service browser hides. One socket sends and receives the same packets as the .NET backend's two did, and logs a socket error once instead of twice. Whether it binds UDP 5353 next to Home Assistant's own zeroconf on Home Assistant OS was the spike's question; it does (decision 19) |
| 9 | Where the .NET app's code throws an exception it doesn't catch as an expected failure (`NullReferenceException`, `ArgumentException`, `InvalidOperationException` and the like), the port raises `errors.UnexpectedError`, which names .NET's exception and has its message; `jsonnode.JsonAccessError` is one. Lights and groups keep a null id or name where .NET's do | Callers then fail where the app failed: a toggle that `HueToggler` doesn't count as failed, pairing that ends with "Pairing failed: …", discovery that fails as a whole. Python's own exceptions in their place would differ in type and message |
| 10 | `hue_address.py` ports `HueBridgeAddress` with the parts of `Uri`, `IPAddress.TryParse` and invariant `IdnMapping` it relies on, function by function and on UTF-16 code units, instead of approximating them with `ipaddress` and Python's IDNA codec | The address is typed by the user, stored, and used for every request. The oracle's first 285 addresses showed that Python's libraries differ in a dozen places (`0x`, `+1.1.1.1`, `@bridge`, zones, `::ffff:` forms, case, IDNA 2003); the literal port matched all of them on its first run |
| 11 | The engine's `pair` asks for a key every 2 s until 60 s have passed, as `HuePairing` does, reports each attempt that didn't pair (`on_attempt`), and ends with the web UI's message, not the command line's | The setup flow replaces the web UI's pairing page, whose message it shows while waiting |
| 12 | `tools/fake_hue` ports the .NET app's fake bridge, with its ids, endpoints, answers, messages and settings, and adds `Fault`s (a status, body or delay for matching requests) for tests. `tests/hue.py` runs it on 127.0.0.1 over HTTPS with a certificate made at every start, and a manual clock | The client is tested over real HTTPS with a pinned certificate, which the .NET tests did in memory. Development and end-to-end CI need the same bridge without hardware |
| 13 | From 2026-11-24 the remaining work runs in parallel, one agent per task: each works in its own git worktree, `.worktrees/<task>` (in `.git/info/exclude`), on its own branch from `main`, and opens a pull request without merging it. The coordinating session reviews the pull requests, merges them by fast-forwarding `main` one at a time, and asks the others to rebase. Decision numbers are reserved per task: 14 the runner, 15 the YAML import, 16 the Home Assistant skeleton, 17 the LAN monitor and device registry, 18 development tooling, 19 the mDNS spike; then 20 the switch editor's rules, 21 the bridge supervisor and the status tracker, 22 the setup flow, and the next free numbers for the tasks that follow, as the coordinator assigns them. In shared files each agent changes only its own lines (its table rows, its verification entry, its status item); the oracle's fixture is regenerated after a rebase, never merged by hand | The user asked to split the work across agents working in parallel. Separate worktrees let them build, test and commit at the same time without picking up each other's edits, as in the .NET repository's decision 30 |
| 14 | `engine/runner.py` ports `Sonoff2HueRunner` and `IRunnerObserver` on asyncio. The observer is a base class, `RunnerObserver`, whose methods do nothing, like the C# interface's default methods; one that raises is logged and ignored. The runner opens no socket: `run(listener)` adds a packet handler and a started callback to the config entry's `MdnsListener` (decision 8) and sends the keep-alive at once. When cancelled, it removes the handler and cancels the keep-alive and the toggles first, then awaits the started callback's remover and them, as cancelling the .NET runner's token stops everything before anything is awaited. Toggles are tasks behind a per-switch `asyncio.Lock`, which queues in order like `SemaphoreSlim`. `stop_toggles()` stands for the token that the .NET tests pass to `HandlePacket`, and `when_toggles_complete()` waits with `asyncio.wait`, so cancelling the wait doesn't stop them. The age of a press is measured with an injected clock (`datetime.now(UTC)` by default), the time a toggle took with `time.perf_counter`, as with .NET's `Stopwatch`. `resolve_bindings` takes a logger, as the .NET method does. Toggles and the keep-alive start eagerly (`eager_start=True`), and a delay of 0 isn't awaited: `handle_packet` drops a stale press, or finishes a toggle that needs no I/O, before it handles the packet's next event, and `run` sends its first ping before it first waits. The listener starts its started callbacks the same way, as .NET raises `Started` right after it opens the socket: a (re)started socket resets the trackers before its first packet is handled, and the runner, the LAN monitor and Hue discovery send their first query at once, as none of them awaits a delay of 0. The deliberate difference: "Listening for wall switch presses." drops "Press Ctrl+C to stop." The runner also reports a rejected key, an addition for Home Assistant's reauthentication (decision 21): `RunnerObserver.on_key_rejected()`, which does nothing by default, comes through `_notify` after each keep-alive request or toggle that a 401 or 403 ends, right after the reachability report; the log lines, the reachability reports and everything else stay .NET's | The listener is shared, and the runner has to stop and start again, as the supervisor restarts it, while the LAN monitor goes on. Python has no cancellation tokens, and `asyncio.gather` would cancel the toggles when its caller is cancelled, where .NET's `WhenAll` doesn't. The injected clock tests the 5 s rule without waiting. Calling a .NET async method runs it until it first waits, and `Task.Delay(0)` doesn't wait; a task started on the event loop's next turn put a toggle's lines after those of the rest of its packet, and let a broadcast that arrived right after a socket restart be judged against the old baseline and toggle the lights |
| 15 | The YAML import is `engine/yaml_import.py`. `parse(text)` takes the text of a `sonoff2hue.yaml`, as the user pastes it into the setup flow (decision 5), and returns an `EngineConfig`, or raises `ConfigError` with `AppConfig.Parse`'s messages. They name the text "Config", as `AppConfig.Parse(string)` does; the app's `Load` names "Config file '<path>'", which a pasted text doesn't have. The setup flow then runs `validate_for_run`, as the app's `run` does. The text is read by literal ports of YamlDotNet 18.1.0, on UTF-16 code units: `yaml_parser.py`, its scanner and parser, with the same events, errors, messages and marks; in `yaml_import.py`, the parts of its deserializer that `AppConfig`'s model reaches (type resolution with the tag mappings, aliases, the recursion limit of 130, the null, scalar, list and object deserializers, `TypeConverter`); and `yaml_uri.py`, the `Uri.IsWellFormedUriString` that YamlDotNet asks of every tag that doesn't start with `!`. PyYAML isn't used, so `types-PyYAML` isn't needed. Where .NET throws something that `AppConfig` doesn't turn into a `ConfigException`, the port raises `UnexpectedError` (decision 9). `SwitchSettings.lights` and `groups` may hold `None`, as the app's lists hold nulls, and the target resolver fails on one with .NET's `NullReferenceException`. Deliberate differences: a scalar tagged `!!float` or `!!timestamp` is refused ("Values tagged !!float are not supported. Write the value without the tag."), where .NET reads it with `double.Parse` or `DateTime.Parse` and writes it back with `ToString()`; a document of a scalar that reads as itself raises `UnexpectedError` `StackOverflowException`, where the app's process dies; and the URI check doesn't parse hosts: for an authority (`scheme://host`), a DOS path after a scheme (`xy:c:/`) and `mailto:`, it checks what follows the scheme as a path, query and fragment, and it doesn't throw where .NET 10 throws an `IndexOutOfRangeException` (Research findings) | PyYAML 6.0.3, which Home Assistant ships, reads 23 of 207 probe texts differently from YamlDotNet (Research findings): a reader on it would take files that the app refuses, and the other way round, with other messages and positions. YamlDotNet's messages come from its scanner and parser, so the literal port also keeps them word for word, unlike the JSON parser's (Parity with the .NET app). `double.Parse`, `DateTime.Parse` and `Uri`'s host parsing (IDN, IPv4 and IPv6, ports) are large ports for tags that a `sonoff2hue.yaml` never needs, and a crash has no place in Home Assistant |
| 16 | The Home Assistant skeleton. `manifest.json`: domain and name `sonoff2hue`, version 1.0.0, `codeowners` @abpostelnicu, `config_flow`, `single_config_entry`, `integration_type` hub, `iot_class` local_push, `categories` `plugs_switches` (the MINIR4Ms, the primary one) and `lighting.lighting_systems` (Hue, as the core `hue` integration has it), `connectivity` `network`, and this repository's `documentation` and `issue_tracker`; no `requirements`, `dependencies` or `after_dependencies`. `__init__.py` sets up and unloads the entry and declares `type Sonoff2HueConfigEntry = ConfigEntry[Sonoff2HueData]`; `Sonoff2HueData` is an empty placeholder until the runtime. `config_flow.py` has one `user` step that adds the entry, a placeholder for the setup flow. `translations/en.json` holds every string; no `icons.json` while nothing has an icon. `brand/icon.png` and `icon@2x.png` are the .NET app's favicon (its web interface's `favicon.svg`, a full-bleed rounded square), rendered by resvg (resvg-py 0.5.0) at 256 and 512 pixels and compressed losslessly by oxipng (pyoxipng 9.1.1, level 6) as interlaced 8-bit RGBA, in a throwaway environment outside the repository. `.github/workflows/validate.yml` runs *hassfest* (`home-assistant/actions/hassfest@master`) and *HACS* (`hacs/action@main`, category `integration`, nothing ignored) on pushes to `main`, on pull requests and on demand. The package imports Home Assistant at module level, as integrations do, so importing an engine module imports Home Assistant first (0.3 s, 1.3 s cold); the tests and the tools already run in `.venv`, which has it | It installs and loads in Home Assistant 2026.11 and passes hassfest and the HACS action, which the Marketplace requires (Research findings). Each action runs its validator's current image, so the reference their documentation gives is used; a tag would pin only the wrapper. aiohttp, cryptography, PyYAML, ifaddr and yarl come with Home Assistant itself, and hassfest rejects such a requirement; the engine calls neither the `network` nor the `zeroconf` integration, which Home Assistant sets up before any config entry anyway. A hub: one bridge, and the switches as its subentries. local_push: presses arrive as broadcasts. The categories and connectivity place it in the Marketplace and the integration list. Since 2026.3 a custom integration ships its own brand images, which the brands repository no longer takes; these show the app's own icon, prepared as the image specification asks, with no Home Assistant branding. The engine itself still imports nothing from Home Assistant (decision 3) |
| 17 | The LAN view is ported as engine modules (layout). `registry.py` keeps .NET's `DeviceRegistry`: devices are keyed by id with `text.fold` and sorted with `text.compare_ignore_case`, as `OrdinalIgnoreCase` keys and sorts them; an injected clock that returns an aware UTC datetime, in the runner's style, replaces `TimeProvider`; and the `Changed` and `ListeningChanged` events are lists of callbacks, each added with a method that returns its remover. They run in order, and one that raises stops the rest and reaches the caller, as with a .NET event. `lan_monitor.py`'s `LanMonitor` runs the config entry's `MdnsListener` (decision 8), as the .NET LAN monitor ran a listener of its own, with its packet handler and started callback on it: the registry is `listening` while the listener runs, also while its socket retries, and an unexpected stop of the listener is logged and retried 10 s later. The runner attaches to the same listener and leaves it while the LAN monitor goes on. Only the LAN monitor's tracker reads the `EngineConfig` it takes, for the device keys that decrypt the payloads (relay and setting changes, the summary, detached mode, decode errors); `config_changed` makes a fresh tracker, as `OnConfigChanged` did, although Home Assistant reloads the entry instead. The listener logs each line once: .NET's `LanListener` category, which logged the LAN monitor's own listener from warnings up because the runner's listener logged the same lines, has no counterpart. `activity.py`'s `ActivityBuffer` is a `logging.Handler` with .NET's ring buffer of the last 1,000 entries and `get(after, limit)`. An entry has .NET's level names, the logger's name after its last dot as its category, the record's time in UTC, and the exception as "Type: message", where an `UnexpectedError` gives .NET's type. Its callbacks run outside its lock; one that raises is reported as `logging` reports a failing handler, never to the code that logs. .NET's private category maps to nothing: it kept the first launch's setup code out of the web UI, Home Assistant has no setup code, and the integration logs no secrets. Which loggers and levels feed the buffer is the Home Assistant layer's choice (Research findings, Logging) | Home Assistant's entities, the status tracker and the switch flow's identify-by-press follow the registry, as the web UI's event stream and the status tracker subscribed to the events in .NET. A clock and callbacks keep Home Assistant out of the engine and let its tests set the time. The LAN monitor is the listener's only user that runs as long as the integration, so running the listener there keeps .NET's restart and `Listening` as they were, while the runner comes and goes with the supervisor. Diagnostics, which replace the activity view, and Home Assistant's log are seen and shared by the same people, so there is no channel left for lines meant for the host's console only |
| 18 | Local development and debugging, as the .NET app's decision 32 has it, for Home Assistant. `scripts/develop` runs Home Assistant from `.dev/venv`, an environment of its own that uv makes from `requirements_dev.txt`: `requirements_test.txt`, for the same release, and the 12 packages that 2026.11.3 installs at its first start with the development configuration, pinned as its manifests require them and installed with its `package_constraints.txt`; `tests/tools/test_develop.py` checks the pins. Home Assistant runs with `--skip-pip-packages pymicro-vad,pyspeex-noise`, from the repository root as `python -m homeassistant`, so the root is first on `sys.path` and `custom_components.sonoff2hue` is the checkout's own, without a symlink or `PYTHONPATH`. `tools/develop` makes its configuration folder, `.dev/config`: `configuration.yaml` (no `default_config`; the logbook; `custom_components.sonoff2hue` at debug) and the HTTP store, written with the http integration's own schema, on 127.0.0.1 and port 8123 or `SONOFF2HUE_DEV_PORT`; a port in use stops it. `scripts/develop` runs the fake Hue bridge in the background (link button pressed, log in `.dev/fake-hue.log`) unless one of them is asked for alone, starts Home Assistant again after exit code 100, and has `press` and `lights`. The simulated wall switch is the .NET repository's `send-ewelink-announcements.py`, copied unchanged to `tools/` and excluded from ruff (with `force-exclude`) and mypy; `press` sends the last seq again, then the next (`.dev/seq`), for the made-up device 1000abcdef, with TTL 0 on the default route's interface (`0.0.0.0`). `.vscode/launch.json` has debugpy configurations for Home Assistant (`.dev/venv`, `preLaunchTask` `scripts/develop setup`, Just My Code off, `onTerminate: KeyboardInterrupt`, the browser opened at "Now listening on port") and the fake Hue bridge (`.venv`, 127.0.0.1:18443, link button pressed), and a compound of both; `tasks.json` runs `scripts/develop`; `extensions.json` recommends the Python, Python Debugger, Ruff and Mypy Type Checker extensions | The user asked for a real Home Assistant 2026.11 with the checkout's integration, the fake bridge and a simulated switch, debugged in VS Code with breakpoints, on Linux (WSL 2 included) and macOS, without hardware. An environment of its own keeps the test environment as CI's, and away from what Home Assistant installs while it runs. Its first-start packages, installed up front, make every start take seconds without installing anything, where Home Assistant would install them itself and retry the two that can't build at every start. Home Assistant 2026.11 keeps its HTTP settings only in that store (an `http:` section becomes a trial that it reverts, and a repair), and 127.0.0.1 keeps a development instance and its open onboarding off the LAN; checking the port first avoids Home Assistant's recovery mode. Running from the root keeps the files that run the checkout's own, so breakpoints, frames and tracebacks name them; through a symlink, breakpoints are hit but frames name the link. That the configuration folder's `custom_components` isn't searched then doesn't matter for developing this integration. The graceful Stop lets Home Assistant save its storage. The same simulated switch keeps both repositories' checks alike; ruff and mypy would ask for changes to it |
| 19 | The listener keeps its own UDP 5353 socket (decision 8), with .NET's choice of interfaces: the mDNS spike confirmed it on the user's Home Assistant OS on 2026-11-25, so the fallback, a passive listener on Home Assistant's zeroconf, isn't needed. On Home Assistant OS the socket also joins Docker's bridges, `docker0` and `hassio`; on `hassio`, the Supervisor's multicast plugin repeats every packet from the LAN, and the tracker takes the copies as duplicates. On Linux the socket is bound to the group, 224.0.0.251, instead of 0.0.0.0 as in .NET, so that it receives multicast only; elsewhere it stays on 0.0.0.0, as macOS can't send from a socket bound to the group. A broadcast without SRV and A records gives its sender as the device's address only when the sender isn't one of this host's own IPv4 addresses, which the listener reads with the interfaces at each (re)start (`MdnsPacket.from_this_host`); otherwise the address is unknown, and the LAN registry keeps the one it knew. Hue bridge discovery follows the same rule: such an answer without an A record lists no bridge, so the `hassio` bridge's address is never offered as a Hue bridge. The spike was a throwaway custom integration, `sonoff2hue_spike`, on the branch `tmp/haos-spike`, which never reached `main`; it ran the engine's own modules, copied unchanged. It had to be started from configuration.yaml: it was installed over SSH, in the Terminal & SSH app, which lacks `homeassistant_api`, so that app can't add a config entry through the Supervisor's proxy to Core's API | On Home Assistant OS 18.4 with Core 2026.11.3, the socket bound at the first attempt next to Home Assistant's 3 zeroconf sockets and 10 of other processes, and in 3 minutes received the broadcasts of all 21 eWeLink devices and each of the 5 presses of the user's switch as a new `seq`, with no malformed packet, socket error or network change. Home Assistant's zeroconf kept receiving, with the same 21 devices, unicast replies still reach it, and SonoffLAN, which listens through it, wrote its entities' state within 5 s after every press. A listener on Home Assistant's zeroconf would also lose the probes, which reset a device's baseline: python-zeroconf hands its record listeners only the records of responses. Bound to 0.0.0.0 with SO_REUSEPORT, the engine's socket would share the port's unicast with python-zeroconf's socket on 0.0.0.0:5353, which Home Assistant opens when only its default adapter is enabled and it has no IPv6: Linux spreads unicast over the sockets bound to the same address by a hash of the sender, so the engine would take replies meant for Home Assistant. Bound to the group, it gets every multicast datagram and no unicast, and its queries still go out from the address of their interface (Research findings). The multicast plugin's copy of a press carries no address records, so .NET's rule made its sender, the `hassio` bridge's address, the switch's address in the LAN registry after each press. A packet from one of this host's own addresses never tells where a device is: it is a relay's copy, or this host's own, such as the simulated switch's, whose A record still gives the address |
| 20 | The switch subentry flow keeps the .NET web UI's switch editor, whose rules are ported without Home Assistant imports. `picker.py` is the UI's `targets.js`: resolving `lights` and `groups` entries, the picker's selection and how it saves them, with JavaScript's string rules (`jstext.py`), as the browser ran them, and objects compared by identity. `tools/js_oracle` pins it to the real module: `picker_cases.py` builds the cases, `oracle.mjs` runs them with `targets.js` in Node and writes `tests/engine/fixtures/js_picker.json`, and `test_picker.py` and `test_jstext.py` replay it. After a change to the web UI, run `tools/js_oracle/generate.sh <checkout>` with Node 20 or later. `switch_rules.py` is the server's part, `ConfigEndpoints.cs`, with .NET's rules: `tools/dotnet_oracle` runs `SwitchValidation.Validate` and the endpoints that add and change a switch through reflection, the endpoints one step after the other on a config file of their own (sections `switchValidation` and `switchWrites`), rather than only porting `WebConfigTests`. The check against the bridge uses `targets.resolve`, a parameter that tests can replace, and takes the bridge as an async context manager (`connect`), so that the flow can pass a `HueClient`. The picker's resources keep the server's order, `OrderBy` with `OrdinalIgnoreCase` (`text.compare_ignore_case`); the browser's second sort by `localeCompare` isn't ported, which only changes the order in which new selections are saved. The message about a malformed device id names the made-up `1000abcdef`, where the app's names an id that could be one of the user's devices; the oracle replaces it the same way. `switch_form.py` is the dialog's own logic, read from `views/switcheditor.js`, which imports the DOM helpers and can't run outside a browser: its checks before sending, the body it sent, the key's modes and warning, identify-by-press, and how the server's errors map to its fields; `sameId` and `switchLabel` run in the JavaScript oracle. UI only, not ported: the dialog's elements, texts and focus, the inputs' maximum lengths, the order of the device list and of the picker's lists (the browser's collation), listing a device the LAN hasn't reported, and the answers to a stale ETag (412) or a deleted switch (404), which Home Assistant handles | The flow must store and check switches as the web UI did. The browser matched names with `trim()` and `toLowerCase()`, which differ from .NET's rules and from Python's (Research findings), so the picker resolves some entries that the server's check doesn't, and the reverse. CI has neither Node nor .NET, so the fixture is committed, as the .NET oracle's are. `str.lower()` stands for `toLowerCase()`: with Unicode 16.0 on both sides they agree for every code point, and the test fails if the fixture's Unicode version isn't Python's. Through reflection the oracle checks the order of the checks, their messages and the device key's rules against the app itself; its bridges are never reachable (not paired, or settings that can't be used), so it needs no network, and the errors for entries that don't resolve are `TargetResolver`'s, which the runner's port checks. This repository is public |
| 21 | The bridge supervisor and the status tracker are ported as `engine/supervisor.py` and `engine/status.py` (layout). `BridgeSupervisor(config, listener, status, *, hue_clients, on_key_rejected, clock)` runs one `EngineConfig` until it is cancelled: .NET's `ConfigStore` restarted the run after every change, and Home Assistant reloads the config entry instead, which cancels the supervisor and starts one for the new configuration. Its `config_changed()` logs "Configuration changed; restarting the bridge." where .NET did, so not during the 30 s wait after an unexpected stop; the runtime calls it from the entry's update listener, before the reload. .NET's load error ("The config file can't be read.") has no counterpart, and `hue_clients` is `HueClientFactory`. The runner runs on the shared listener with the supervisor's clock: stopping the supervisor stops the runner and closes the client while the listener goes on, and a failing listener is the LAN monitor's to restart (decision 17), where .NET's supervisor restarted the runner, which had a listener of its own, 30 s later. A rejected key is one callback for the runtime, `on_key_rejected`, which starts Home Assistant's reauthentication. It covers both cases: the supervisor stopping in the error state on a 401 or 403, and, while the runner runs, each keep-alive request or toggle that a 401 or 403 ends (decision 14), where the bridge goes on running and is shown as unreachable, as in .NET. It runs on the event loop (`call_soon`), so one that raises reaches the loop's exception handler instead of the supervisor or the runner. The runner's observer is then a forwarder in the supervisor, which passes every report to the tracker and a rejected key to the hook as well; a test checks that it forwards each `RunnerObserver` method, and a tracker that raised would be logged as observer `_Observer`, where .NET named `StatusTracker`. Deliberate message differences, with `PAIR_AGAIN`, as reconfiguring pairs again (decision 5): "The Hue bridge no longer accepts sonoff2hue's application key. Reconfigure the sonoff2hue integration to pair the bridge again."; the warning "hue.certificateSha256 is not set, so the bridge certificate is not verified. Reconfigure the sonoff2hue integration to pair the bridge again and pin it."; and "Pair the Hue bridge to get started: reconfigure the sonoff2hue integration.", which Home Assistant's flows give no reason to show. "Add a switch: pick a MINIR4M and the Hue lights it should toggle." stays: adding a switch is the switch subentry's own action. The `StatusTracker(config, registry, clock)` is the runner's observer. Its `StatusSnapshot` is .NET's `StatusDto` without the version, which is the manifest's, and the config and password files' paths, which were the web interface's; listeners replace the `EventHub`. `ServiceState`'s values are the status sensor's options: `not_configured`, `invalid_config`, `connecting`, `running` and `error`. It keeps .NET's debounce: listeners get one snapshot 500 ms after the first change, with every change until then (`loop.call_later`), and one that raises stops the rest and reaches the loop's exception handler. `close()` drops a pending snapshot and leaves the registry. `config_changed(config)` ports `OnConfigChanged`, for a runtime that keeps the tracker and its registry across a reload; a tracker made at each setup starts as .NET's did, connecting with "Starting.", and without the last actions and errors that .NET kept across changes. It is used on the event loop only, so it has no lock, and an injected clock replaces `TimeProvider` | Every change applies through a reload (decision 5), so the supervisor needs no change token, and each run checks the whole configuration from the start, as .NET's did, so the states and messages come out the same; only the update listener knows that a reload is for a change. A callback is explicit and comes at once, and Home Assistant's `async_start_reauth` does nothing while a reauth or reconfigure flow of the entry runs, so the hook may come again, after every refused request and after a restart; a state to watch would be known only by its message, and would come 500 ms late. The messages name the Home Assistant equivalent of the .NET app's command line and pages (How to work). The supervisor and the runner report one event in several calls, such as the bridge's reachability and then the state, and the debounce hands the entities one consistent snapshot of them, as the dashboard got it, while it keeps Home Assistant's state writes, which the recorder stores, to two a second. The sensor's options double as its translation keys |
| 22 | The setup flow, reconfigure and reauth (`config_flow.py`), in place of the skeleton's placeholder. **Steps:** `user` is a menu, *Find bridges* or *Enter the bridge address* (`SETUP_MENU`, which the import of a `sonoff2hue.yaml` will join), and `reconfigure` the same two. `discover` is a progress step that runs the engine's `discover` for 5 s, with Home Assistant's shared aiohttp session for the cloud. It leads to `pick`, a list of `IP — bridge id — source` (leaving out a missing id) and *Another address*, which preselects the first bridge, or when reconfiguring the paired one if it was found again (by bridge id, without regard to case); to `no_bridges`, the web UI's message with *Search again* or the address; or to `discovery_failed`, with the error. `address` trims the input as .NET's `Trim` does and checks it with `hue_address`, whose message goes on the field; when reconfiguring it suggests the stored address. Then the bridge is read (`get_bridge_info`): `cannot_connect` for a `HueApiError`, `invalid_address` for a `ConfigError`, `unexpected` for an `UnexpectedError`, which is logged with its traceback. The flow's unique id becomes the bridge id, so another flow with it is aborted (`already_in_progress`), and reauth aborts with `wrong_bridge` for another bridge id. `link` is a progress step with `hue-pair`'s text about the bridge (name, model, bridge id, firmware, address) and the certificate to pin, and "Press the round link button on top of the bridge now. Waiting up to 60 seconds..."; it runs the engine's `pair`, with `HuePairing`'s log lines. It ends in `paired`, `link_timeout` (`PairingTimeoutError`'s message) or `link_failed` (the message of a `HueApiError` or `ConfigError`, else "Pairing failed: …"); these two menus offer *Retry*, which reads the bridge at the same address again as the web UI's Retry did, and *Choose another bridge* (the first menu), and in reauth only *Retry*. `reauth` asks for a confirmation (`reauth_confirm`) before it reads the stored bridge and links. **Data:** the entry's `data` is `bridge_ip` (the address entered or chosen, trimmed), `bridge_id`, `app_key` and `certificate_sha256`, the field names of the engine's `HueSettings` (`CONF_*` in `const.py`); its unique id is the bridge id and its title `sonoff2hue` (`ENTRY_TITLE`). A switch is a subentry of type `switch` (`SUBENTRY_SWITCH`): its unique id is `SwitchSettings.normalized_device_id`, its title its name, or that id without one, and its `data` holds `device_id`, `name`, `device_key`, `lights` and `groups` (`SwitchSettings`' names) with the values as given. The flow doesn't declare the type yet (Research findings). Reconfigure saves with `async_update_reload_and_abort` (the unique id and `data_updates`), so the switches stay; reauth the same, without the unique id. **Messages:** errors, aborts, menus and progress texts are translation keys, and where .NET shows a dynamic message, the engine's exact text is the placeholder `{error}`. **Progress:** the text changes whenever an attempt's outcome does, by `progress_action` (`link` or `link_retrying`) and `async_notify_flow_changed()`: "The bridge did not answer (…); still trying." while the bridge doesn't answer, nothing more while it waits for the button. There is no countdown. Tasks start after their step returns (`eager_start=False`). **Resources:** `async_remove` cancels the search and the pairing, whose connections and mDNS socket close as they stop, and a flow closed while the bridge is read starts no pairing (`UnknownFlow`). **Deliberate differences from the web UI:** closing the dialog stops the pairing, which ran on for 60 s on the server and survived a reload of the page; instead of one pairing at a time, a second flow for the same bridge is aborted, and the flow that adds the entry aborts the others; there is no countdown; there is no *Forget bridge* (deleting the entry deletes the switches too, and reconfigure replaces the bridge); the paired bridge is preselected instead of marked *Current*; an unexpected failure shows its own message, where the web UI showed a 500's; the search's hint says "the same network as Home Assistant"; the timeout's log line ends "Start the pairing again in the sonoff2hue setup." **Import** (2026-11-25): the menu's third way, *Import sonoff2hue.yaml*, is `import_yaml`, a multi-line text field. `yaml_import.parse` reads the text as `AppConfig.Parse` reads the file, then the run's checks follow (`validate_for_run`), both with their messages, lines and columns, under the field; where .NET crashes (`UnexpectedError`), it says "The file can't be read: …". With the bridge's `bridgeIp` and `appKey` (`IsPaired`), `import_check` connects as the run did at startup, once and without its retries: the supervisor's client (`create_hue_client`, with the file's key and pin) and its first keep-alive (`ping`). The bridge id always comes from the bridge (`get_bridge_info`), as it is the unique id, which reauth and reconfigure compare with the bridge's: the entry stores the bridge's spelling, and when the file's `bridgeId` is another id, other than in letter case, the new entry's text says so, next to the note about a missing pin. The entry gets the file's other `hue` values as written, and one `switch` subentry per switch with its values as written, the normalized device id as the unique id and the trimmed name, or that id, as the title. Lights, rooms and zones aren't resolved then. A failure leads to `import_failed`: *Try again*, *Pair the bridge again and keep the switches* (`import_pair`), or *Paste the file again*; its message says "pair the bridge again" where the engine's says to reconfigure the integration, which doesn't exist yet. Without `bridgeIp` and `appKey`, the run's checks see stand-ins for them, so that only the switches' remain, and `import_pair` leads to the search or the address (which suggests the file's `bridgeIp`) and to `link`; `paired` then adds the switches, and a failed link's *Choose another bridge* goes back to `import_pair`. A file without a pin is imported: the entry keeps none, the runtime warns at every start (`NOT_PINNED`), as .NET's run did, and the new entry's text says so. Secrets: the pasted text is never shown back, not even after an error; the dialogs name the switches by name or device id; and `async_on_create_entry` drops `subentries` from the answer to the browser, which would hold their device keys (Research findings) | The flow does what the web UI's Hue bridge page and `hue-pair` did, with their rules and words, in Home Assistant's own dialogs. The title: the entry stands for the whole setup, one bridge and its switches, as the .NET app's config file did, and there is only one; an imported `sonoff2hue.yaml` names no bridge (`HueSettings` has no name), and its import doesn't read the bridge's name; and reconfigure can move it to another bridge without renaming it or overwriting a title the user chose. The bridge's name, model and firmware are shown while linking, and belong to the bridge's device. Reauth asks first because Home Assistant runs its first step as soon as the runtime starts it, and the 60 seconds must run while the user is at the bridge. A progress step can't count down without a timer of its own, and `async_update_progress` shows a percentage, which reads as work done rather than time left. A step still reading the bridge when its flow is removed would start a pairing that Home Assistant never cancels, and an eager task would run, or end, inside the step that starts it. The import accepts what the .NET app ran and refuses what it refused, in its words. The supervisor resolves the names at every start and reports what it doesn't find, so the import leaves them to it, and the switch dialogs can fix them, where a pasted text can't be edited. A pin can be added later by reconfiguring, while refusing the file would lose its switches |
| 23 | The config entry's runtime is `runtime.py` (layout): `Sonoff2HueData`, a dataclass, is `entry.runtime_data`. **What lives as long as the entry**, as the .NET app's background services lived as long as its process: the mDNS listener, which the LAN monitor runs; the device registry (`registry`); the status tracker (`status`); and the activity buffer (`activity`), a handler on `custom_components.sonoff2hue`, which records what the integration logs at Home Assistant's configured levels: warnings by default, info and debug lines with "Enable debug logging" or `logger:`. The runtime never changes a log level (the coordinator's decision of 2026-11-25). The bridge supervisor runs `config` (`config_from_entry`: `HueSettings` from the entry's data, and one `SwitchSettings` per `switch` subentry, in the subentries' order, with the values as stored) with the status tracker, and with `observer`, a new optional parameter of `BridgeSupervisor`: its forwarder passes each of the runner's reports to the tracker, then to the observer, which fires the bus events. The engine names the two messages that the runtime uses, `CERTIFICATE_MISMATCH` and `PRESS_DROPPED`. **Changes:** `async_setup_entry` adds an update listener, which Home Assistant calls after any change of the entry and its subentries. When the configuration differs, the running supervisor logs "Configuration changed; restarting the bridge." and is cancelled, the LAN monitor and the status take the new configuration (`config_changed`), and a new supervisor starts once the cancelled ones have ended; a change of a title alone restarts nothing. Nothing reloads the entry: reconfigure and reauth save with `async_update_and_abort`, and subentries are added, changed and removed with Home Assistant's own API. So the listener's socket, the registry, the status with each switch's last action and error, and the activity stay; a reload by the user, or Home Assistant's restart, starts them afresh, as a restart of the .NET app did. **Signals**, each formatted with the entry's id: after a restart `SIGNAL_CONFIG_CHANGED` (the new `EngineConfig`), then per `switch` subentry `SIGNAL_SUBENTRY_REMOVED` (its id), `SIGNAL_SUBENTRY_UPDATED` (data, title or unique id) or `SIGNAL_SUBENTRY_ADDED`; and `SIGNAL_STATUS` with each snapshot that the tracker publishes. A platform adds the entities of the subentries that exist in its `async_setup_entry`, and those of an added one on its signal, with `config_subentry_id`; Home Assistant removes a removed one's devices and entities itself. `PLATFORMS` is empty, and forwarded. The registry's own callbacks serve identify by press: a device whose `last_event` is a press, with a new `last_press_at`. **Tasks:** the LAN monitor and the supervisors are Home Assistant's background tasks, not the entry's, and each is cancelled once. `async_stop`, an `async_on_unload` callback, so also after a failed setup, closes the status, takes the activity buffer off the logger, stops the supervisors, then the LAN monitor, and deletes the issues; at Home Assistant's stop, which cancels the tasks itself, the runtime closes the status and takes the buffer off. **Reauth:** `on_key_rejected` calls `entry.async_start_reauth`. **Bus events** (`const.py`): `sonoff2hue_press` with `seq`, and one `sonoff2hue_toggle` per press, with `targets` and `result`: `toggled`, with `turned_on`, `changed`, `unchanged`, `failed` and `elapsed_ms`; `failed`, when the toggle couldn't run, or `dropped`, when the press waited more than 5 s, with `message`. Both name the switch: `device_id`, the Home Assistant device linked to its subentry, or None while it has none; `switch_id`, its normalized device id; and `name`, as configured. **Repairs**, linked to the entry and created and deleted from each published snapshot and at the start: per switch `not_detached_<id>` and `decode_error_<id>` (warnings, in the runner's words), `invalid_config` (an error: the supervisor's message and problems, under the web UI's title "The configuration has problems"), `certificate_not_pinned` (a warning) and `certificate_changed` (an error, while the bridge's last reachability message is `CERTIFICATE_MISMATCH`). The two certificate issues are fixable: `repairs.py`'s flow confirms, then aborts with the integration's reconfigure flow as `next_flow`, which the frontend opens, and the issue stays until the runtime sees the new pin. **Switch test:** `async_test_toggle(device_id)` ports `TestAsync` and returns a `SwitchTestResult`, or raises `SwitchTestError` with .NET's message, and with the targets' `problems`; the button turns it into a `HomeAssistantError`. **Tests** replace `runtime.new_listener` and `runtime.create_hue_client` (the `lan` and `bridge` fixtures) | The .NET app's services lived as long as its process, and only its supervisor restarted for a change of the config file; a reload would start the LAN view afresh and drop the last actions and errors that .NET kept. Home Assistant calls the update listener for every change of the entry and its subentries, and with one, `async_update_reload_and_abort` logs a report that this breaks in 2026.12.0, and still reloads. The new supervisor waits for the old one, as .NET's loop did, so that it never runs next to it, and a task is cancelled only once: the second cancellation, which the entry's unload gives its own background tasks, cut short a supervisor that closed its Hue client, as a test showed. Signals keep the platforms apart from the runtime's objects, and a subentry added later needs its entities without a reload. An observer next to the tracker keeps the tracker as it was. The logbook matches events to a device by `device_id` in their data, so the switch's device has that key and the eWeLink id another, and one toggle event per press, with its result, is one event to follow in automations. Issues linked to the entry go with it and hide while it is disabled. A repair flow that finished would delete its issue before the bridge is paired again, so it aborts, and `next_flow` opens the reconfigure flow, which pairs again and pins the certificate; the other problems are fixed in the eWeLink app, in a switch's settings or on the bridge, which no repair flow can do yet |
| 24 | The switch subentry flow (`switch_flow.py`, `SwitchSubentryFlow`), which the setup flow declares for the type `switch` (`async_get_supported_subentry_types`), so the integration's page has *Add switch* and each switch *Edit switch*; Home Assistant's own UI deletes one. It shows the engine's switch editor (decision 20) in Home Assistant's dialogs. **Device:** `user` is a menu, *Press the wall switch* (`identify`), *Pick a device seen on the LAN* (`pick`) or *Enter the device ID* (`manual`); `reconfigure` puts *Keep device …* (`keep`) first. Both abort with `entry_not_loaded` unless the entry is loaded. `identify` is a progress step whose task follows the LAN registry's changed callbacks for up to 60 s (`IDENTIFY_TIMEOUT`), and the entry's state (`async_on_state_change`), so that a reload's registry is followed too. A device event is a press as in the web UI: its last event a press, at a time the device didn't have. `switch_form.identify` then selects the device, whose message heads the next form, or names the switch that uses it in the progress text (`identify_used`) and goes on waiting. After 60 s, `identify_timeout` offers the ways again. `pick` lists the registry's devices (radio buttons) in the web UI's order, unused first, the latest press first, then by id as the registry sorts them, each labelled with its id, address, type, *Online* or *Offline*, its last press as the web UI's `relativeTime` said it, in Home Assistant's time zone, and the switch that uses it; `no_devices` when there are none. `manual` is a text field. The three check the device as the dialog did (`form_problems`' message on the field), and a device that identify selects but the dialog refuses goes to `manual` with the message. **Settings:** `settings` lists the bridge's lights, rooms and zones from a fresh client of the runtime's factory on the entry's data (`load_resources`) in three multi-selects, `lights`, `rooms` and `zones`, in the mode Home Assistant chooses by their number (checkboxes, or chips with a searchable picker from 6), labelled as the web UI listed them: the name, else the id; the id too beside a repeated name; a light's on or off; a room's or zone's "no lights". Their values are positions, as an id may be missing. The switch's entries are chosen with `create_selection`, those that don't resolve are chips (`unresolved`) kept until removed, and the choices are saved with `selection_to_config`. The device key is a collapsed *Advanced* section: *Keep it*, *Replace it* or *Remove it* (`key_mode`) and *New device key*, shown only to replace the key (a `visible` condition), for a switch with a key, else an optional key; the key warning is the mode's description, and the section opens with it or with a key error. When the resources can't be listed, `pair_first` (409) or `lights_unavailable` (502, with the bridge's message) offers *Try again* or *Enter them by hand*: the form with the text fields `light_names` and `group_names`, filled with `join_list` and read with `split_list`. **Saving:** the dialog's checks (`form_input`, `form_problems`), then `create_switch` or `update_switch` with the entry's switches and a fresh client. Messages go to the fields the web UI put them on (`field_errors`): the name, the key's section, the typed fields; the rest (the device, the picker's lights, rooms and zones, keys that no field has) to the form. The dialog's device check isn't run again, as the device can't change there; the server's conflict or missing switch aborts (`conflict`, `not_found`) with its message. The server's warnings lead to `unchecked`: *Try again*, or *Save without checking*, which runs the server's write again without the bridge (unpaired settings), so its device checks and key rule see the switches as they are then. A new switch is a subentry with the server's values (`device_id`, `name`, `device_key`, `lights`, `groups`), the normalized device id as unique id and the name, or that id, as title; an edited one is updated in place with `async_update_subentry`, as `async_update_and_abort` takes no placeholders. "Added …" and "Saved …" name it as the web UI's notes did. An `UnexpectedError`, where .NET answered 500, is logged with its traceback and shown: while listing, by `lights_unavailable`; while saving, as "The switch was not saved: …". **Runtime** (decision 23): the flow starts only while the entry is loaded, and takes the runtime's factory of Hue clients then (`hue_clients`, as the runtime's switch test makes its clients), so that it can still ask the bridge while the entry reloads. `lan_registry(entry)` is `entry.runtime_data.registry` while the entry is loaded, else `None`, which lists no devices and lets identify wait for the next runtime's registry. The switches and the bridge are the runtime's `config_from_entry`, with the values as stored. A saved or deleted switch reaches the runtime through the entry's update listener, which restarts only the bridge supervisor. Home Assistant cancels identify's task when the dialog closes, as the progress step's task. **Secrets:** the stored device key never reaches the browser, only whether there is one; a key typed in the dialog is suggested again after an error, as Home Assistant's flows and the web UI's input did; the flow API answers a new switch without its data. **Deliberate differences from the web UI:** identify waits 60 s and isn't turned on by itself; a device of another switch can be chosen and is then refused with the dialog's message; a device the dialog refuses after identify goes to the device id; the device list's ids are in the registry's order (`OrdinalIgnoreCase`, not `localeCompare`) and its times don't refresh; the edited switch's device is kept with *Keep device …*, not listed when the LAN hasn't reported it; a menu comes before typing the lights; the server's warnings are confirmed before saving instead of shown after it; a conflict or a deleted switch ends the dialog; key errors show above the *Advanced* section; there's no filter box (the chips' picker searches); a null entry of an imported file is an empty entry that doesn't match the bridge, where the web UI failed to save it | The switches must be stored and checked exactly as the web UI and the server did (decision 20), in Home Assistant's own dialogs, as the user chose. Home Assistant 2026.11 supports progress steps in subentry flows, in the backend and in its frontend (Research findings), so identify needs no fallback; a progress step can only be left by closing the dialog, so it can't wait without end. A form can't show the LAN's devices live, so a menu chooses the way to the device, and keeping the device first makes editing a switch one click. Positions as values keep the picker's handling of a bridge's null ids, and removable chips are the web UI's own look for entries that don't resolve. A section with a `visible` condition gives the key the web UI's Advanced part and its hidden field. Home Assistant can't show a note after its dialog closed, so the warning comes before saving; running the server's write again keeps its checks of the moment in the engine's code. The settings form can't change the device, so a conflict there ends the dialog. The registry and the bridge's clients come from the runtime, which runs only while the entry is loaded, hence the abort while it isn't; core integrations abort the same way. The runtime's factory keeps the .NET server's one `HueClientFactory` for the supervisor and the dialogs, and lets the tests give both the same bridge |
| 25 | The devices and entities: `entity.py` and the platforms `sensor.py`, `binary_sensor.py`, `event.py` and `button.py`, which `PLATFORMS` lists now (decision 23 left it empty), with `icons.json` (decision 16 had none) and the `entity` section of `en.json`. **Devices:** the bridge's, identified by the bridge id (the entry's `bridge_id`, else its entry id), named "Hue bridge" and made by "Signify Netherlands B.V.", without a model or firmware; and one per `switch` subentry, linked to it (`config_subentry_id`), identified by the normalized device id, named by the subentry's title, made by "SONOFF", model "MINIR4M", with the device id as its serial number, as the dashboard's card showed the id. **Entities:** each has `has_entity_name`, a `translation_key` with its name, states, attribute names and unit in `en.json`, its icons in `icons.json`, and the unique id `<its device's identifier>_<its translation key>`; none polls, and all are available while the entry is loaded. On the bridge's device, the dashboard's banner, Hue card and LAN card: *Status* (`sensor.hue_bridge_status`), an enum of `ServiceState`'s values with `message`, `problems` and `since`; *Reachable* (`binary_sensor.hue_bridge_reachable`, connectivity, diagnostic), on or off, or unknown while the snapshot's `reachable` is None (not checked yet, or not paired), with `address`, `message` and `checked_at`, its states named "Reachable" and "Unreachable" as the card's badges; *eWeLink devices seen* (`sensor.hue_bridge_ewelink_devices_seen`, diagnostic, in devices), the LAN registry's count, with `listening` and `last_packet_at`. On each switch's device, its card: *Press* (`event.<switch>_press`, device class button, event type `press`), with `seq`, fired by the runtime's `sonoff2hue_press` for its switch; *Last action* (`sensor.<switch>_last_action`), an enum of `turned_on`, `turned_off`, `failed` and `dropped`, with `at`, `changed`, `unchanged`, `failed`, `elapsed_ms`, `targets` and `message`; *Online* (`binary_sensor.<switch>_online`, connectivity, diagnostic), states "Online" and "Offline" as the card's badge, so offline also when never heard, with `address`, `last_seen_at`, `last_press_at`, `seq`, `encrypted`, `has_device_key` (a key that isn't blank, as .NET's `hasDeviceKey`), `detached` and `decode_error`; *Test toggle* (`button.<switch>_test_toggle`, config), which runs `async_test_toggle` and turns a `SwitchTestError` into a `HomeAssistantError` with its message. **Sources:** Status, Reachable and Last action show the status tracker's snapshot when they are added, then each one on `SIGNAL_STATUS`. Last action shows the newer of the switch's last action and last error: the tracker records the error of a toggle that some lights failed at the toggle's time, so an error at the time of a toggle without failures came after it, and a toggle with failures shows with its error's message; the error `PRESS_DROPPED` is `dropped`, any other `failed`; `targets` are the snapshot's, the toggles while the bridge runs. eWeLink devices seen and Online read the LAN registry: the count at each snapshot, at once for a new device (the registry's callback) and with the last packet every 30 seconds; Online at each snapshot, when 5 minutes have passed since the switch was last heard (a timer for that moment), and every 30 seconds while it is offline. `last_packet_at`, `last_seen_at` and `checked_at` aren't recorded. **Subentries:** each platform adds the entities of the `switch` subentries in its setup, and those of one added later on `SIGNAL_SUBENTRY_ADDED`, with `config_subentry_id`; they follow their subentry on `SIGNAL_SUBENTRY_UPDATED`, and Home Assistant removes a removed one's device and entities. **Reconfigured ids:** `async_track_devices`, which `__init__.py` starts before the platforms, keeps the devices in step with the entry, once at the start, for the changes made while it wasn't loaded, and after each change (`SIGNAL_CONFIG_CHANGED`, `SIGNAL_SUBENTRY_UPDATED`): a new bridge id, or a switch's new device id, becomes its device's identifier, and a switch's serial number, and the start of its entities' unique ids, and a switch's new title becomes its device's name. The device, its entities and their entity ids stay. A helper linked to the device, which belongs to another entry, keeps its unique id, and a device that the platforms didn't make is left alone. With the devices there, the runtime's bus events carry the switch's device id from its first press, for automations. **Logbook:** the logbook, and a switch's device page, also show the entities' changes (Research findings), so with the first `logbook.py` a press showed twice, as Press's event ("Press") and as "wall switch operated (seq N).", and so did its outcome, as Last action's change and as the toggle's entry; the user chose the entities' entries (decision 26). **Deliberate differences from the dashboard:** Status's states have short names ("Not configured", "Invalid configuration", "Connecting", "Running", "Error") where the banner had titles such as "Running – listening for wall switch presses" and "Get started", and no checklist; Reachable is unknown both before the first check and while the bridge isn't paired, where the card said "Not checked yet" or "Not paired"; the card showed the last action, as "Turned ON 2 lights in 142 ms (1 already on, 1 failed)", and apart from it the last error until a restart, where Last action shows the newer of the two, its counts as attributes; Press fires for the presses that the bridge acts on, so not while the bridge doesn't run, and keeps its last event across a restart, as Home Assistant's event entities do, while Online's `last_press_at` is the card's "Last press", which counted every press and started empty; the Switches card's count of switches online, and the page's version and uptime, have no entity; the dashboard read the status every 30 seconds besides its events. The plan's *Bridge reachable* is named *Reachable* | The entities show what the dashboard showed, from the same state (the coordinator's rule): the snapshot that the dashboard's status events carried, and the LAN registry, which its 30-second reads of the status also reflected; where Home Assistant has no word of its own, the states take the badges' words. Home Assistant names an entity on its device, so *Bridge reachable* on "Hue bridge" would read "Hue bridge Bridge reachable" (`binary_sensor.hue_bridge_bridge_reachable`). The entry doesn't store the bridge's model and firmware (decision 22's data), and reading them would be a request at every start; read once, the firmware would go stale after an update. The bus events are the runtime's interface for presses (decision 23), so Press and the toggle events agree, and an automation on Press sees the presses that toggle the lights. One state can't show two outcomes; the newer is the switch's last action, and the tracker's times order them, with the one tie that the tracker makes on purpose. The status tracker publishes a LAN device only when it is a configured switch's, and the registry reports a broadcast only when it isn't a repeat, so neither a new device that no switch maps, nor the last packet, nor a switch heard again only through repeats, after multicast was lost for a while, would show without the registry's callback and the 30-second looks, which the dashboard's reads every 30 seconds made; a timer at the end of the 5 minutes shows a switch going offline when it does, without polling. Attributes that change at every look say nothing in the history. Signals and the bus keep the platforms apart from the runtime's objects (decision 23). Reconfiguring can pair another bridge (decision 22), and the switch flow can give a switch another MINIR4M or another name: without the tracking, the old identifiers would stay, a switch added later with a device id that another switch had before would take that switch's device, which Home Assistant then moves to the new subentry, and the next start would make new entities next to the old ones. Keeping the device and its entities keeps their history, area, names and the automations on them |
| 26 | Diagnostics and the logbook replace the web UI's Recent activity and Logs page, with Home Assistant's log (the user's choice of 2026-11-24). **Diagnostics** (`diagnostics.py`): `async_get_config_entry_diagnostics` gives, in one file, the four views that the web interface read from its API: `config` (`/api/config`), the `EngineConfig` that the supervisor runs, with the values as the entry and its switch subentries store them; `status` (`/api/status`), the status tracker's snapshot: `started_at`, `service`, `hue`, `lan` and each switch with its LAN device, its resolved `targets`, its last action and its last error; `lan_devices` (`/api/devices`), the LAN registry's snapshot, each device with `online`; and `activity` (`/api/logs`), the activity buffer as it is, its last 1,000 entries, oldest first, as Home Assistant's log levels let them in (decision 23). Dataclasses become objects of their fields, times ISO 8601 and enums their values, so the answer is plain JSON. Home Assistant's `async_redact_data` then replaces the values under `TO_REDACT`, the keys under which the entry and a switch subentry keep the secrets (`app_key`, `certificate_sha256`, `device_key`): the key shows as `**REDACTED**`, and the pin and each device key as `**REDACTED**` or null, which tells only whether it is set; `status.hue` has `paired` and `certificate_pinned` too. An entry that isn't loaded has no runtime, so only `config`. No text holds a secret: the engine sends the application key only in the `hue-application-key` header, gives the pin only to aiohttp's check of the certificate and the device key only to the payload's decryption, and turns aiohttp's errors into its own messages, which name the bridge's address and port, a method and a path, never a header; the decode errors are fixed texts, no log line names a key or the pin, and the activity keeps an exception as "Type: message", without the exceptions chained to it. The texts aren't scrubbed for the secrets' values, which would hide from the tests a message that also puts a secret into Home Assistant's log. A switch's device has no diagnostics of its own. hassfest needs no `dependencies` or `after_dependencies` for the platform (Research findings). **Logbook** (the user's choice of 2026-11-25): Home Assistant's own entries only, two for each press, the switch's Press ("Press") and the change of its Last action ("Turned on", "Turned off", "Failed" or "Dropped") (decision 25), also on the switch's device page, with the details in their attributes. The integration has no `logbook.py`, so the logbook has nothing for the bus events `sonoff2hue_press` and `sonoff2hue_toggle`, which stay for automations and the entities (decision 23). The first `logbook.py`, of the same day, described them as the runner logs them: an entry named "Hall (1000abcdef)" with "wall switch operated (seq 12).", and one with "turned ON light 'Ceiling' in 85 ms.", "could not toggle the lights: …" or "dropped a press that waited more than 5s for the bridge.". Last action adds an entry only when its state changes: a press whose outcome is the one before shows as Press alone, and of the outcomes in the 500 ms before the entities show the status, only the newest shows. A toggle that can't reach the bridge also shows as the bridge's Reachable going off, on the bridge's device. **Deliberate differences:** the logbook has Home Assistant's entries where the dashboard's Recent activity listed the runner's lines among the last 30 log lines from information up, such as "…: wall switch operated (seq 12)." and "…: turned ON light 'Ceiling' in 85 ms (1 already on)."; those lines, and the others (online, ignored relay and setting changes, restarts, (re)joined, the bridge's reachability, configuration changes and more), are in Home Assistant's log at its levels and in the diagnostics' activity | Someone who diagnoses the integration needs what the web interface showed, and the four views together explain any state: what is configured, what the bridge and each switch are doing, what the LAN hears, and what happened. The engine's dataclasses keep every field they have, and redacting by the stored keys covers the secrets wherever such a key appears. A switch's problem is read with the bridge's state, the configuration and the activity, which a file for its device would need as well; the entry's file is small, and Home Assistant offers it on the integration's page. Home Assistant's diagnostics find the platform in an integration set up before or after them, and hassfest allows the platform's import of its component. With the first `logbook.py` and the entities, each press made four entries, two of them for the bus events, and Home Assistant offers integrations no way to keep an entity's changes out of the logbook: it leaves out only some domains and sensors with a unit, a state class or a numeric device class (Research findings, Entities and devices). The user chose the entities' entries, which name the press and its outcome in Home Assistant's own words, with the details a click away |
| 27 | The end-to-end test, the counterpart of the .NET app's *Local stack test* (its decision 31). `scripts/e2e` runs `python -m tools.e2e`, and CI's job *End-to-end* (`ci.yml`, id `e2e`) runs `scripts/e2e` on ubuntu-latest, on pushes to `main` and on pull requests, within 15 minutes, with uv's downloads cached on `requirements_test.txt` and `requirements_dev.txt`; a failure uploads the logs as the artifact `end-to-end-logs`. **What runs:** `scripts/develop setup` (decision 18), with its new setting `SONOFF2HUE_DEV_CONFIG`, makes Home Assistant 2026.11.3's environment `.dev/venv` and a configuration folder of the test's own, `.dev/e2e/config`. The test starts Home Assistant itself, as `scripts/develop hass` runs it (from the repository root, with the same `--skip-pip-packages`), on 127.0.0.1 and `SONOFF2HUE_DEV_PORT`, and the fake bridge, `python -m tools.fake_hue`, on 127.0.0.1 and `FAKE_HUE_PORT`, with its link button released; both ports are free ones, chosen at each run. The simulated switch is a made-up MINIR4M `1000e2xxxx`, new at each run, which `tools/send-ewelink-announcements.py` announces with TTL 0 on the default route's interface, as decision 18's `press` does. **The setup, through Home Assistant's API as the frontend uses it:** the test waits for the HTTP server, onboards a throwaway owner with onboarding's first step only, signs in for a token, and waits for the core state `RUNNING` before anything else, as a stop before that would abandon the start. The setup flow (`/api/config/config_entries/flow`) takes *Enter the bridge address* with the fake bridge's address, after its link button is pressed (`POST /_fake/link-button`), and the link step, whose end the flow's `data_entry_flow_progressed` event announces, as the frontend gets it. The entry has no switch, and its status asks for one. The switch comes through *Add switch*, the switch subentry flow (decision 24, `/api/config/config_entries/subentries/flow` with the handler `[entry_id, "switch"]`): the switch announces seq 1, which the LAN monitor takes as its state, then identify-by-press starts and the switch announces seq 2, a press, at once; `settings` then gets the name *End-to-end*, and the light `Hallway` and the room `Living room`, picked by their labels from the form's lists. The test checks the subentry, and that the configuration holds the switch as the web UI saved it: `Hallway` and `room:Living room`. **The status:** the test reads the integration's status, `entry.runtime_data.status`, through the diagnostics download (decision 26), `/api/diagnostics/config_entry/<entry_id>`, whose `data.status` is the tracker's state at that moment: `not_configured` with the supervisor's "Add a switch: …" after the setup flow, and `running` before any press, after the switch is added and after the restart. After each round of presses it checks the status as the .NET app's test read the dashboard's: running, and the switch online at its address, detached, with its targets, its last toggle on or off and no error; and what the UI shows (decision 25), through the REST API: the bridge's *Status* `running`, and the switch's *Last action* `turned_off` or `turned_on` and *Press* with the event type `press` and the last press's seq, found in the entity registry (`config/entity_registry/list`) by their translation keys. **The presses:** the switch announces seq 2 again, then 3 and 4: for the runner, which the new switch restarted, 2 is the switch's state. The test expects exactly the fake bridge's PUTs of on, then off (`/_fake/puts`), all lights off, the bus events through the WebSocket API's `subscribe_events`: `sonoff2hue_press` with seq 3 and 4, each followed by its `sonoff2hue_toggle`, `toggled` with the targets, 2 changed and `turned_on` true, then false; the status; and the supervisor's and the runner's lines in the log: "Configuration changed; restarting the bridge.", "Listening for wall switch presses.", the switch's state, the presses and the toggles. **The restart:** the `homeassistant.restart` action, over the WebSocket API; Home Assistant exits with code 100, and the test starts it again and waits for `RUNNING` and the status. Then the switch announces seq 4 again and 5: only 5 may be a press, with one toggle, on, and its two PUTs, and the runner logs 4 as the switch's state, a baseline (the .NET app's decision 6: no false press after a restart). **The end:** Ctrl+C (SIGINT) must stop Home Assistant with exit code 0, and each start's `home-assistant.log` must hold no warning or error of the integration, no error naming it and no report about it. **Timings:** on the development machine 14 to 16 s in all, with `.dev/venv` there: setup 1 to 1.5 s, Home Assistant's start 2.2 to 2.5 s, each flow 0.1 to 0.2 s, the two presses 3 s (the switch sends 1.5 s apart), the restart 3.2 to 3.9 s. On GitHub's runner 17 to 22 s, in a job of 31 to 42 s with uv's cache: setup 3 to 5 s, Home Assistant's start 3.5 to 4.8 s, the restart 2.6 to 4 s. The verification log has the runs | The .NET app's CI pressed a switch through its deployed services all the way to the Hue API; here only this job runs the integration as Home Assistant loads it, with its flows, the runtime, the engine, the mDNS socket and the Hue client together, over real sockets, and through a restart. **The switch through Add switch, identified by a press:** the plan's way to add a switch (decision 5), and the path with the most of the integration in it: the LAN monitor and its registry, identify-by-press, the picker, the check against the bridge, and the update listener, which restarts the supervisor for the new switch without a reload (decision 23). *Enter the device ID* would leave out the LAN. Until the subentry flow was merged, the test imported a `sonoff2hue.yaml` without a bridge, whose flow then paired one (decision 22); it no longer does: the import is walked through Home Assistant's HTTP flow API against the fake bridge in `test_config_flow.py`, an entry that starts with its switch is what the restart tests, and with one entry (`single_config_entry`) both would take two setups. **Ports and device id of each run's own:** announcements with TTL 0 reach every listener on the machine, so two runs at once, in two checkouts, can take each other's as presses, and `scripts/develop` may hold the usual ports. Identify-by-press selects the first device that reports a press, so the switch's state comes before it starts and its press right after: a press of another device on the machine's network has a window of about 0.1 s to be selected instead, which the check of the selected device would report. CI has no other device. **Determinism:** each wait is for a condition: a port that answers, the core state, a flow's progressed event, the status and the devices in the diagnostics, the bus events of the last announcement's press, the entities' states, which follow the status's snapshots up to 500 ms later, a line in the log; there is no fixed sleep. The supervisor sets the status to running in the same turn of the event loop in which the runner adds its packet handler to the listener, so from then on a broadcast reaches the runner. A flow's announcements are counted before the request that starts its progress step, as the announcement can come before that request's answer. The diagnostics' 404, until Home Assistant has registered the platform after the integration's first setup, counts as not yet. The test starts Home Assistant itself rather than through `scripts/develop hass`, so that it sees the restart's exit code and knows that the next answer comes from the new process. Log lines are waited for, as a thread writes the file. Temporary mutations of the integration each failed it: a first broadcast taken as a press (also after the restart), identify-by-press selecting nothing, a runner that gets no packet or never says it listens, a status never set to running, `turned_on` inverted in the events or in the status, and Press with another seq |
| 28 | `README.md` is the documentation for the integration's users: the Marketplace shows it on the integration's page, GitHub on the repository's, and the manifest's `documentation` leads to it (decision 16). **Structure:** what the integration does, with the .NET README's diagram, and that it works as the sonoff2hue app does; *How it works*; *Requirements*: Home Assistant 2026.11 or later (OS, Container or Core) on the switches' subnet, Container on the host's network and a virtual machine with a bridged adapter, **Detach relay**, a Hue bridge with CLIP v2, and not the .NET app against the same switches; *Installation* from the Marketplace, by the My link `marketplace_repository` or *Add from link*, which need a GitHub connection, or by hand from a release's source; *Setup*: pairing, the import with what it keeps and checks, *Reconfigure* and reauthentication; *Switches*: the three ways to the MINIR4M, *Switch settings*, the device key and where to get it (the .NET README's eWeLink API), changing and deleting; *Devices and entities*, each entity with its states and attributes as `en.json` names them (decision 25); *Presses in automations*: *Press* with the trigger *Event received*, and the bus events with their data (decision 23), in YAML for the tests' switch Hall, `1000abcdef`; *Repairs*, each with what it means and what to do; *Activity, log and diagnostics*: the logbook as the user decided on 2026-11-25, Home Assistant's own entries only, the entities' changes such as *Press* and *Last action* (decision 26), with the details in *Last action*'s attributes, Home Assistant's log and the diagnostics, and the coordinator's rule for the log levels: *Enable debug logging*, or `logger:` for the info lines; *Troubleshooting*, the .NET README's where it applies, with the entities that show each problem, and Home Assistant's own; *Limitations*; *How it differs from the sonoff2hue app*, only what users notice; then *Development* (decision 18, with the end-to-end test of decision 27), *Releases* (decision 29) and the license. The UI's labels are in italics, word for word as `en.json` or Home Assistant's frontend shows them, and the eWeLink app's **Detach relay** in bold. Sections name each other in the text, without links. **No markers:** the Marketplace of 2026.11 shows the whole README and has no markers that hide a part of it, so the installation is written for both readers. **No status line:** the README describes the integration as released, without "in development" or "no release yet", as the Marketplace shows the README at the tag of the release on offer, and 1.0.0 is a tag only (decision 29). **Left out:** the deliberate differences that users can't notice (one socket, the socket bound to the group on Linux, the JSON parser's messages, the order of the lists, the refused YAML tags), which this file keeps; Home Assistant OS's multicast plugin, whose copies users never see (decision 19); the trace-level lines, which the `logger` integration can't select; the brand icon, which the Marketplace shows above the README, and whose `align` its sanitizer drops; a link to the .NET app's private repository, which users can't open; the YAML keys of a `sonoff2hue.yaml`, which is only imported; a table of contents and in-page links | The Marketplace has no documentation page of its own, and Home Assistant's UI links a custom integration to its `documentation`, so the README is the users' only manual. The .NET app's users know its README, so its words and troubleshooting are reused where they apply. A label written as the UI shows it can be found on the screen. Home Assistant 2026.11.3's `components/marketplace` downloads `README.md` raw at the version and only disables `<svg`; its frontend 20261028.8 renders it with marked 18.1.1 (GFM, no breaks) and js-xss 1.0.17, which removes HTML comments, so a comment hides a text on both sites alike, and none hides it in one of them only. There, headings get no ids, so an in-page link points at nothing, which GitHub's own outline of a README doesn't need; relative links lead to GitHub for `.md` files and to the raw file otherwise, at the version; a `#12` outside a link becomes a link to the repository's issue 12; and GitHub's alerts become `ha-alert`s. A status line would still be there in the Marketplace at the tag of 1.0.0. The logbook lists an entity's change only when its state changes, so the README says that two presses with the same outcome show *Last action* once. The differences that users can't notice would only lengthen it |
| 29 | Each version is a GitHub Release, made by `.github/workflows/release.yml` (*Release*) when a tag `vX.Y.Z` is pushed; it follows the .NET app's decision 33 without the images. Its job *Check the tag* may only read the contents. It checks out `github.sha`, the tagged commit, with main's history, and `.github/scripts/release.sh` stops it with an `::error::` unless the tag is exactly `vX.Y.Z` (three numbers, without leading zeros and without a suffix), `X.Y.Z` is exactly the `version` in that commit's `custom_components/sonoff2hue/manifest.json`, and the commit is on `main`. Then it lists the published GitHub Releases, neither drafts nor pre-releases, with `gh api --paginate`; their tags `vX.Y.Z` are the released versions, and git tags alone don't count. It hands on X.Y.Z, whether that is the highest released version (Latest), and the highest released version below it, where the notes start. The job *GitHub Release* needs it, has the workflow's only `contents: write`, checks nothing out and runs no action. It checks that the outputs fit the tag and that the tag still names `github.sha`, leaves a Release that exists already as it is, and takes Latest away if a higher version has a Release by now: when only the failed jobs of a run are run again, GitHub reuses the check job's old outputs. Then it runs `gh release create vX.Y.Z --verify-tag --target <github.sha> --title "sonoff2hue X.Y.Z" --generate-notes --latest=<true or false> [--notes-start-tag v<previous>]`, shows the Release, and fails if the tag was moved meanwhile. The workflow's token may only read by default, and releases run one at a time (concurrency group `release`, which never cancels a running release). The Releases have no assets and `hacs.json` sets no `zip_release`: the Marketplace installs a Release from its tag's source archive (Research findings, Releases). `.github/scripts/release-test.sh` tests the checks, and runs both jobs' steps from `release.yml` as GitHub runs them, against a stand-in `gh`; CI's *Test* job and `scripts/check` run it. Immutable releases are on since 2026-11-24 at 22:58 UTC, turned on once the refusals were tested on GitHub with throwaway tags, which created no Release. Release 1.0.0 is a tag `v1.0.0` on `main` after the live test, nothing more | The plan ends with release 1.0.0, and the Marketplace installs integrations from GitHub Releases: a tag alone isn't a version, and without a Release users get the default branch's newest commit. Checking before anything is created means that a refused tag leaves nothing behind, which matters with immutable releases, where a Release locks its tag's name for good. The version check keeps the Marketplace's version, the tag, and the one Home Assistant shows, the manifest's, the same, as the Marketplace's guides ask. Only commits of `main` are released, since they have passed CI and the ruleset's checks. Write access in one job without a checkout means that none of the repository's code runs with it; the tag is checked again there because no ruleset protects tags. Latest and the notes come from the Releases, so a stray or refused tag can't hold them back, and they follow the .NET app's rules: a lower patch released later doesn't take Latest, and its notes start at the version below it, for that version's users. The Marketplace itself offers the newest Release in GitHub's list, which is neither Latest nor the highest version (Research findings), hence the rule to release in rising versions. A Release can't be made for a test without locking its version, so the tests run the workflow offline, and the throwaway tags on GitHub test only the refusals. Immutability ties each version to one commit for good, as the user chose for the .NET app (its #42) |
| 30 | **The integration stands alone** (the user's choice of 2026-11-25). Python's standard library and the libraries Home Assistant ships decide edge cases: `str` methods such as `strip()` and `casefold()`; `json` and `base64`; `ipaddress` and yarl for the bridge address; aiohttp's own handling of answers; PyYAML with a probatio schema for the import. **The product rules stay:** how presses are detected and lights toggled; the timings (5 s, 10 s, 30 s, 45 s, 60 s, 500 ms, 5 min); pairing and discovery; the switches' limits and their messages; the flows, entities, repairs, bus events and diagnostics. **Tests are pytest cases of the integration's own behavior.** The generators of recorded cases and their fixtures are gone. **The mDNS layer is the exception:** `dns.py`, `mdns.py`, the record part of `ewelink.py` and the mDNS part of Hue discovery (`_discover_mdns` in `hue_setup.py`). It stays the integration's own and never relies on Home Assistant's mDNS: no python-zeroconf, no `zeroconf` or `network` integration, no manifest matcher, and no shared HTTP session for `.local` names. It keeps exactly its behavior for every input, with its own string rules for names, white space, device ids and `seq`. `tests/engine/mdns_packets.json` and `tests/engine/mdns_text.json` pin it, and `tests/test_own_mdns.py` guards the rule. **The work** runs as Phase 1, which retires the generators and keeps the mDNS layer's recorded cases, then six slices in parallel (A `lan`, B `hue`, C `yaml`, D `switches`, E `engine-core`, F `ha-layer`), then a cleanup and a rewrite of the README, `en.json` and this file, each step in a worktree and pull request of its own (decision 13). **Supersedes** decision 3's parity clause, decisions 7, 9, 10 and 15, the parts of decision 20 about the recorded cases and the picker's own string rules (`jstext.py`), and the parity rule of "How to work in this repository", except for the mDNS layer | The user asked to retire the other implementation and every mention of it, to use Python's libraries as much as possible and to make the code more Pythonic; the same day, the user also asked not to rely on the mDNS that Home Assistant OS provides, and to keep the mDNS behavior as it is. Matching another platform's edge cases took about 5,000 lines of emulation, plus tools that CI couldn't run. The mDNS layer is where the integration meets the network directly, and decision 19's measurements were made on it |

## Record keeping

- This file and one descriptive commit per step, as in the .NET repository.
- The plan that started this repository was approved on 2026-11-23, in session da483334. Its todos, in order: create
  this repository; CI and a ruleset; a spike of the mDNS socket on the user's Home Assistant OS; the engine port (DNS,
  eWeLink and press detection, the mDNS listener, the fake Hue bridge, the Hue client and pairing, the runner, the
  YAML import); the Home Assistant layer (skeleton, runtime, setup flow, switch subentry, entities, diagnostics and
  logbook); development tooling; end-to-end CI; the README; the live test; release 1.0.0. The Status list below
  tracks them.

## Environment observations

- 2026-11-24: development machine: WSL 2 on aarch64, Debian 13, with Python 3.14.6 and 3.13.5 and uv 0.13.7. gh is
  Debian's 2.46.0, which has no `gh release verify`. There is no Docker. `scripts/setup` installed Home Assistant
  2026.11.3 and the tools in 15 s.

## Verification log

- 2026-11-24: bootstrap. `.github/scripts/check-commits.sh` is the .NET repository's script, unchanged. The first
  commit holds only documents, so `scripts/check` passes from the second one on, which adds the tools and the first
  code.
- 2026-11-24: the DNS port (`engine/dns.py`, `engine/text.py`). The six cases of `DnsMessageTests` and three new ones
  pass (TXT keys, unknown record types, overruns, long names, unsupported labels), and ruff, the formatter and mypy
  (strict) find nothing.
- 2026-11-24: the eWeLink port: announcements and probes, payload decryption, `PressDetector` and `SwitchTracker`,
  with `jsonnode.py` for the payload's JSON. The cases of `EwelinkTests` and `PressDetectorTests` and the engine cases of
  `QaRegressionTests` pass, with the openssl reference vector, plus new ones for the fallbacks (no `id`, no address
  records), the missing IV and payloads that aren't JSON objects. Tests use made-up device ids.
- 2026-11-24: the .NET oracle, against `sonoff2hue v1.0.1-5-g4c08a3a` (`main`) built with .NET 10.0.15. All 197 cases
  of `dotnet_engine.json` pass: 36 packets, 83 payloads, 15 press-detection sequences, 38 summaries and 25 JSON
  comparisons, two of them the known differences of `DeepEquals`. `test_text.py` checks every code point against
  `dotnet_text.json`. Its first runs showed that `text.py` mapped 'ı', 'ſ' and 'İ' as Python does, which .NET doesn't, and
  missed the 27 Greek mappings; the fix is in the same commit. Breaking the dotless-i rule or the escaping of `+`, `` ` ``
  and `'` on purpose failed the matching cases. Regenerating gave the same fixtures, byte for byte.
- 2026-11-24: the mDNS listener. Its tests cover packets to several handlers, malformed packets and failing handlers,
  queries on every joined interface, started callbacks for each socket and their cancellation, socket errors and their
  retry, debounced network changes, the interface filter, netlink messages, and on a real socket: sharing the port,
  TTL, loopback, and a join that fails. On the development machine (WSL 2), `interface_states` gave the same view as
  .NET's `GetAllNetworkInterfaces`: `lo` loopback without multicast, `eth0` up with multicast.
- 2026-11-24: the Hue client, the bridge address and pairing, against the .NET oracle (the same build of the app). 543
  new cases pass: 285 bridge addresses (the C# `HueBridgeAddressTests` among them), 32 inputs of `IPAddress.TryParse`,
  51 pairing answers (the C# `ParsePairingResponse` cases among them), 108 `HueClient` calls on canned answers with the
  requests they send (the C# `HueTests` client cases among them), 8 pins, 7 status lines, 52 encoding names and .NET's
  60 reason phrases. A first, approximate address module differed from .NET in a dozen places; the literal port
  (decision 10) matched all 285 on its first run. The earlier parity tests now compare .NET's exception types too.
- 2026-11-24: the fake Hue bridge and the client over HTTPS on 127.0.0.1. 36 tests of the fake bridge (the C#
  `FakeHueTests`, `FakeHueOptionsTests` and `FakeHueCertificateTests`, a device type of 41 UTF-16 code units, and
  `Fault`s), 18 of the client (the pin in any notation, another or a malformed pin, no pin, a refused connection, a
  timeout, a 207 write, a connection closed during the handshake, a host without an IDN form, app keys) and 38 of
  bridge info, pairing and discovery (the bridge's identity and certificate and its failures, keys without regard to
  case, each outcome of a pairing attempt, the pairing loop, device types, mDNS and the cloud with .NET's failure
  handling). A full run showed the mDNS listener's started callbacks cancelled in a set's order, once in a few dozen
  runs; they are now cancelled in the order they started, a fix squashed into the listener's commit.
- 2026-11-24: after the first green runs on `main` (71d226a, 77da7d5), ruleset 28003079 "main: require CI and per-commit
  checks" was created like the .NET repository's: *Lint*, *Test* and *All commits pass*, an admin bypass.
  `gh api repos/abpostelnicu/sonoff2hue-homeassistant/rules/branches/main` lists the three checks.
- 2026-11-24: the target resolver and the toggler, against the .NET oracle (the same build of the app), in three new
  sections: 18 `targets` cases (the C# `ConfigurationTests` resolver tests among them), 24 `toggle` cases (`HueToggler`
  on canned answers, with the requests it sends and the lines it logs) and 30 `compareIgnoreCase` cases. The first
  `targets` run showed that .NET orders the known names by code point: the port sorted them by UTF-16 code units, so
  names outside the Basic Multilingual Plane came first; `text.compare_ignore_case` ports .NET's comparison now. The
  toggler's tests port `HueTests`' five toggler cases and `FakeHueTests`' toggle of a light and a room on the fake bridge
  over HTTPS, and cancel a toggle during its retry delay. Breaking the toggler on purpose (retrying every error, not
  retrying, a case-sensitive state lookup, reading the light states for a group alone) failed the matching oracle cases.
- 2026-11-24: the runner (decision 14). `test_runner.py` ports the seven `RunnerTests` and the runner cases of
  `QaRegressionTests`: the eight on/off combinations of two lights and a room switched by encrypted broadcasts, queued
  presses with and without a rejected first read, and a queued press 5.001 s old. It adds a press exactly 5 s old (not
  dropped), a cancelled wait that leaves the toggles running, unexpected errors while toggling and in the keep-alive, a
  device id mapped twice, the log lines of each kind of broadcast, of payload problems and of toggles,
  `resolve_bindings`' retries, its list of unresolved names and a refusal, and `run(listener)` on `FakeNetwork`: the
  queries, the keep-alive, a press, a new baseline after the socket restarts, and stopping while the listener goes on.
  Breaking the runner on purpose (keeping the started callback, `gather` for the wait, dropping at 5 s, no reset on a
  restart, a keep-alive that waits first, reporting every ping, no guard around the observer) failed the matching
  tests. The parity row "Queries" was right: the runner, the LAN monitor and Hue discovery all wait 0, 1 and 2 s before
  their first three queries (`new[] { 0, 1000, 2000 }`), which go out at 0, 1 and 3 s.
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
- 2026-11-24: the LAN view (decision 17). The device registry: the C# `DeviceRegistry_TracksPressesFromLanBroadcasts`
  passes, with 9 new tests: what a broadcast without a seq, address or decodable payload keeps, a fresh tracker after a
  config change, repeats, the 5-minute window, the time of the last packet, `OrdinalIgnoreCase` keys and order (the
  final and the plain sigma are one device, the dotless i and the i aren't), and the callbacks. A throwaway program on
  .NET 10.0.15 with invariant globalization sorted and keyed 639 strings as `snapshot` and `find` do; the port agrees on
  every order and key. Temporary mutations (sorting by Python's lowercase, finding by its uppercase, reporting repeats,
  not keeping detached mode, `<` for the window) each failed a test. The LAN monitor: 8 tests on `VirtualTime` and the
  fake network. Queries go out at 0, 1, 3, 123 and 243 s. A network change gives a new socket 3 s later, where the next
  broadcast is a baseline and the queries come at once and after 1 and 3 s again. Probes and other hosts' queries reach
  the tracker and the time of the last packet. `config_changed` decrypts with the new key. The registry is listening
  while the socket retries. A listener that fails before its socket opens is logged and restarts 10 s later. Stopping
  removes the handler and the started callback, and another user of the listener comes and goes while the LAN monitor
  goes on. Temporary mutations (a 60 s interval, waits of 0, 1 and 3 s, no reset, a 5 s retry, no awaited removal, a
  `config_changed` that does nothing, listening left on after a failure) each failed a test. The activity buffer: the C#
  `LogBuffer_KeepsTheNewestEntries_AndNeverTheSetupCode` passes without its setup-code line, with 6 new tests: `get` (a
  limit of 0 gives nothing, where Python's `[-0:]` would give everything), the level names of Python's levels,
  categories without a dot or ending in one, exceptions (an `UnexpectedError` as .NET's type, `exc_info=True` without
  one), the record's time, callbacks outside the lock (another thread reads the buffer while one runs) and a failing
  callback. Temporary mutations (`[-limit:]`, the info boundary, Python's type for an `UnexpectedError`, callbacks
  inside the lock) each failed a test.
- 2026-11-24: the switch editor's picker (`picker.py`, `jstext.py`) against the real `targets.js` of
  `sonoff2hue v1.0.1-5-g4c08a3a`, run by Node 20.19.2. All cases of `js_picker.json` pass on the first run: 28
  `splitKind`, 46 `resolveLight` and 44 `resolveGroup` entries, 35 items encoded as lights, rooms and zones, 6 selection
  scenarios with 701 edits (every subset of six resources, removed unresolved entries, names that are ids, duplicate
  names, room and zone clashes, null ids and names), 13 `splitList` texts, 49 `trim` and 67 `toLowerCase` texts, and
  JavaScript's white space and lowercase mapping for every code point. Using .NET's trim or lowercase in the picker, or
  a key without JavaScript's "null", on purpose failed the matching cases.
- 2026-11-24: the server's switch rules (`switch_rules.py`) against the same build of the app, through reflection. All
  pass: 138 inputs of `SwitchValidation.Validate` and 5 scenarios of 28 writes to the endpoints that add and change a
  switch (new and changed ids, conflicts, missing switches, kept, replaced, removed and dropped keys, ids stored
  untrimmed or with a final sigma or the Kelvin sign, a bridge that isn't paired, an address or a key that can't be
  used), with `WebConfigTests`' switch cases against the real target resolver and a stand-in bridge. Counting code
  points instead of UTF-16 units, Python's `strip()`, an ordinal-ignore-case lookup in the update, keeping the key
  across a changed id, or `casefold()` for duplicates, each on purpose, failed the matching cases.
- 2026-11-24: the switch dialog's own logic (`switch_form.py`). 15 `sameId` pairs and 12 `switchLabel` switches of the
  real `format.js` pass, regenerated into `js_picker.json` with its earlier cases unchanged byte for byte. The form's
  tests cover each message and their order, a typed or a chosen device, the edited switch keeping its id, a final
  newline that Python's `$` would accept, the Kelvin sign that the dialog refuses before the server would accept it,
  names counted in UTF-16 code units, the key's modes and warning, identify-by-press, the server's errors on the
  picker's and the typed entries' fields, and a switch picked, checked and saved through `switch_rules` with the real
  resolver. .NET's lowercase in `sameId`, Python's `$`, Python's `strip()` in the label, or the picker's field for
  typed entries, each on purpose, failed the matching tests.
- 2026-11-24: hassfest and HACS passed on `main` (Validate run 45472628208 on a4318c3), so ruleset 28003079 requires
  them too: `gh api repos/abpostelnicu/sonoff2hue-homeassistant/rules/branches/main` lists *Lint*, *Test*, *All commits
  pass*, *hassfest* and *HACS*.
- 2026-11-24: the runner's eager start (decision 14). A stale press is dropped, and reported, before `handle_packet`
  returns; a toggle that needs no I/O logs "turned …" before the next event of its packet, as .NET does with a handler
  that answers at once; `run` sends its first ping before "Listening for wall switch presses." and before it first
  waits. Starting the toggles or the keep-alive on the next turn, or awaiting the keep-alive's zero delay, failed these
  tests; the existing ones passed unchanged.
- 2026-11-24: the listener's started callbacks start eagerly too (decision 14). Before, a broadcast that arrived right
  after a socket restart reached the runner before its reset and toggled the lights. The new tests (callbacks run
  before their socket's first packet, and at once when added to an open socket; a broadcast right after a restart is a
  new baseline; `run` sends its first query before it first waits) failed with the old start; the listener's and Hue
  discovery's tests passed unchanged.
- 2026-11-25: the LAN monitor and Hue discovery don't await a delay of 0 either (decision 14), so their first query
  goes out before their started callback first waits, as .NET's `Task.Delay(0)` lets it. Their tests check that
  query as the socket opens, and failed with the old `sleep(0)`; the LAN monitor's `query_times` counts the queries
  sent as the socket opened, so its timings stay 0, 1, 3, 123 and 243 s.
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
  - Not yet possible: a press that toggles the fake bridge's lights through Home Assistant, as the integration's setup
    flow is still a placeholder.
- 2026-11-25: the YAML import (decision 15), against the .NET oracle (the same build of the app) in two new sections.
  The 374 `yaml` cases give YamlDotNet's parsing events for each text (their count and SHA-256 for 17 long ones) and
  what `AppConfig.Parse` makes of it: 132 configs and 232 errors, among them every message `AppConfig` writes, and 10
  texts that end .NET's process with a stack overflow. They cover comments, quoting, flow and block styles, anchors and
  aliases, tags and directives, several documents, byte order marks, CRLF, tabs, an empty text, documents that aren't
  mappings, keys that aren't text, lone surrogates, and long and deeply nested texts. The 127 `wellFormedUri` cases
  check the URI rules of tags directly. All pass; for `!!float` and `!!timestamp` the test checks the documented
  difference instead. A scratch probe (not committed) compared the port with YamlDotNet on more: the parser on 212
  hand-written texts, 120,000 random ones and 6,782 long ones (the scanner's look-ahead buffer and the 1024-character
  limit of simple keys), with the same events, errors and marks; `AppConfig.Parse` on 3,021 random configs; and the URI
  check on about 236,000 random texts in the forms it ports, without a difference, and 35,000 in the forms it
  approximates, 90% of them the same, where .NET threw for 11. `test_yaml_import.py` ports the reading tests of
  `ConfigurationTests` and the YAML of `CliTests` and `QaRegressionTests`, with made-up ids, and tests the port's own
  decisions: 37 tests. Breaking the port on purpose (the surrogate rule of the URI check, escaped ASCII kept in IRI
  processing, the recursion limit, `NULL` as null, Python's `upper()` for scheme names) failed matching cases.
- 2026-11-25: the status tracker (decision 21). The C# `StatusTracker_ReportsRunnerOutcomesPerSwitch` passes, with
  13 new tests (16 cases) on `VirtualTime`: a new tracker's state; when "since" moves; the running message for one and
  several switches and targets; running again with the same message; a device id mapped twice; reachability while
  paired and unpaired, with .NET's white space; the last action and error per switch; each switch's LAN device (online
  for 5 minutes, a decode error); the 500 ms debounce; which registry changes publish; removing listeners and closing;
  a new configuration with the same or another bridge. Temporary mutations (a 400 ms debounce, no check for an
  unchanged service, keeping the targets, keeping the message while reachable, publishing every device, comparing the
  bridge as a tuple, a `close` that keeps its timer, reachability while unpaired, rounding the milliseconds) each failed
  a test. pytest-homeassistant's cleanup check fails a test whose tracker still has a snapshot pending.
- 2026-11-25: the bridge supervisor (decision 21). The C# `BridgeSupervisor_FollowsTheConfiguration` passes as three
  reloads, a supervisor per configuration with the tracker kept, with 20 new tests (34 cases) on `VirtualTime`, with
  `InMemoryBridge` and the listener on `FakeNetwork`. They cover each state and message in the order of the checks:
  not paired, for five kinds of missing address or key, with and without a switch; no switch; the configuration's
  problems; an address and a key that can't be used, with the real client factory; names that aren't on the bridge.
  Then pings at 0, 10, 20 and 30 s with one warning while the error changes; resolving's retries, logged by the
  supervisor; a key rejected by the ping and by resolving, with one callback; a 404 and a 207 with errors, without
  one; a callback that raises; the certificate warning; two switches running; a press toggling through the runner
  into the tracker; an unexpected stop (a ping answered with `{"errors":[null]}`), logged with its trace and started
  again 30 s later with a new client; a runner that returns; stopping while waiting for the bridge, during a request
  and while running (the client closed, the keep-alive and the queries gone, the listener going on); and
  `config_changed` before, during and after a run and in the wait after a failure. Temporary mutations (retrying every
  5 s, restarting after 10 s, not closing the client, warning at every attempt, logging in the wait after a failure,
  the callback for any refusal or called directly, problems keeping their "- ", no reachability while waiting, the
  runner's own logger for resolving, no certificate warning) each failed a test.
- 2026-11-25: a key rejected while the runner runs (decisions 14 and 21). In the runner's 4 new tests, refused
  pings (401, 401 again, 403) each report the rejected key right after the reachability report, which still comes on
  changes only, and a 503 doesn't; refused toggles do the same before the failed toggle, with the same log lines; a
  refused write stays a failed target without the call; and the call goes through the observer guard. The runner's
  other 36 tests pass unchanged. The supervisor's 2 new tests: the hook while running, for keep-alive requests refused
  at 45 and 90 s and a toggle refused at 141 s but not for a 503 at 135 s, with the state still running, no restart
  and the same reachability report; and the forwarder passing each `RunnerObserver` method to the tracker. Temporary
  mutations (no call from the ping or from the toggle, a call for any bridge error, the call before the reachability
  report, a forwarder without the hook or without one method, the tracker as the runner's observer, calling the hook
  directly) each failed a test.
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
  - hassfest 2026.11.3, run as its image runs it (as for the skeleton, in a throwaway environment outside the
    repository), found no errors and no warnings; a copy with a placeholder in single quotes failed.
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
    first minute, in local mode, with its browser; its 147 entities wrote their state 211 times, within 5 s after each
    of the 5 presses.
  - In the first minute, Home Assistant warned that the spike's module was logging too frequently (Research findings).
  - When Home Assistant stopped for the removal, the spike logged its final result. Then configuration.yaml was
    restored byte for byte, the folder and the zip were deleted, and Home Assistant restarted. The log, which names the
    user's addresses and devices, stays out of this repository.
- 2026-11-25: the mDNS socket bound to the group on Linux (decision 19). Two new tests with real sockets, on Linux:
  64 unicast datagrams from 64 ports to 127.0.0.1 all reached a socket on 0.0.0.0 with SO_REUSEPORT and none the
  engine's socket on the same port; and the engine's listener, joined on the loopback interface, sent its query
  from 127.0.0.1 and its own port, which another socket on the port and the listener itself received, and then
  received that socket's announcement. Bound to 0.0.0.0 again, the engine's socket got 32 of the 64 datagrams, and
  the bind's own check failed. By hand on the development machine (WSL 2, Linux 6.18), with TTL 0: joined on the LAN
  interface and the loopback one, the socket received the group on both and sent from each one's address. On
  GitHub's macOS 26.7.2 arm64 runner, in a throwaway workflow on the branch `tmp/macos-group-bind`, deleted
  afterwards (run 45502820479), the bind worked next to mDNSResponder, the socket received the group and no
  unicast, and sending from it failed with EADDRNOTAVAIL, as the runner's user and as root.
- 2026-11-25: a relay's copy no longer gives a device's address (decision 19). The listener marks a packet whose
  sender is one of this host's IPv4 addresses, which it reads with the interfaces at each (re)start, and an
  announcement without SRV and A records from such a sender has no address. New tests: the marking follows each new
  socket's addresses; `open_multicast_transport` takes them from the same look at the interfaces that chooses the
  ones it joins; through the LAN monitor, a press and the multicast plugin's copy of it from this host leave the
  switch's address as the press gave it; and the simulated switch's packet, built by
  `tools/send-ewelink-announcements.py` itself and sent from this host, keeps its A record's address. Taking the
  sender again, or never marking a packet, failed the matching tests, with the copy's sender as the switch's address
  in the registry. By hand on the development machine, `tools/send-ewelink-announcements.py 0.0.0.0 1000fedcba 1 2`,
  as `scripts/develop press` runs it, reached the engine's real listener, bound to the group: the LAN monitor decided
  Baseline, then Press, and the registry had the A record's address, 192.0.2.10.
- 2026-11-25: Hue discovery takes no bridge address from a copy that this host relays (decision 19). A new test
  delivers a bridge's answer without an A record from the bridge and again from one of this host's addresses, and
  another bridge's answer with its A record from this host: discovery lists the first bridge at its own address and
  the second at its A record's. Taking the sender again listed this host's address as a third bridge.
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
  - **CI** on pull request #16 at 18f4bf5, before a rebase and this line: in CI run 45506697148, *Test* ran
    `release-test.sh` after pytest, 132/132 on the runner, and in Commits run 45506697133, `scripts/check` ran it for
    each commit; *Lint*, *hassfest* and *HACS* (Validate run 45506696945) passed too.
  - **On GitHub,** with throwaway tags on this pull request's first commit as it was then, 2c3a5f5; later rebases left
    the workflow and the scripts as they were. Each run's *Check the tag* failed at the expected step with its
    `::error::`, *GitHub Release* was skipped, and no Release was created:
    - `v9.9.9`, `v9.9.9-rc.1` and `v09.9.9`, pushed together at 22:54:47 UTC on 2026-11-24. The run of `v9.9.9`
      (45505286707) went first and failed the version check: "The tag v9.9.9 doesn't match the version in the tagged
      commit's custom_components/sonoff2hue/manifest.json, "1.0.0". …". The run of `v9.9.9-rc.1` (45505286969) waited
      and was cancelled when the run of `v09.9.9` (45505287396) came, as the concurrency group's comment says; that one
      failed the tag check: "v09.9.9 isn't a release tag. …". Run again, 45505286969 failed the tag check too.
    - `v9.9.8` on 307a291, a commit on no branch whose manifest has 9.9.8, made with `git commit-tree` on 2c3a5f5: run
      45505611040 passed the tag and the version checks and failed "The tagged commit 307a291… isn't on main. …".
      `v9.9`, pushed with it, started no run.
    - `release.yml` was on no branch on GitHub but this pull request's, so a tag push runs the workflow of the tagged
      commit.
    - Each tag was deleted right after its run, on GitHub and locally. The repository then had no tag and no Release.
    - The failed *Check the tag* check runs belong to 2c3a5f5; no required check has that name.
  - **Dry run of 1.0.0:** `release.sh` passed `v1.0.0` on `main`'s a70e857 (the tag, the manifest's `version`, on
    `main`) and, with no Release yet, chose Latest and no start for the notes. GitHub's generate-notes, which saves
    nothing, lists the merged pull requests and the new contributor for `v1.0.0`.
  - **Immutable releases:** at 22:58:41 UTC, `gh api repos/abpostelnicu/sonoff2hue-homeassistant/immutable-releases`
    gave `{"enabled":false,"enforced_by_owner":false}`, with no Release and no tag. `gh api -X PUT` on it answered 204
    at 22:58:44 UTC, and from 22:58:50 UTC on, six reads gave `{"enabled":true,"enforced_by_owner":false}`. Nothing
    else was changed, and no ruleset protects tags.
  - **Not checked yet:** a real Release. The first is 1.0.0's: its `immutable` field, "Immutable" on its page, its
    notes and Latest, and `gh release verify v1.0.0`, which needs a gh newer than this machine's 2.46.0.
- 2026-11-25: the import of a `sonoff2hue.yaml` in the setup flow (decision 22).
  - 31 new tests in `tests/test_config_flow.py`, 70 in all, cover all 304 statements and 68 branches of
    `config_flow.py`. With the fake bridge and a key it issued: a file with its bridge adds the entry and two switches
    (unique ids, titles and data); the bridge id is the bridge's, without a note for a missing id or one in other letter
    case and with one for another id, also next to the warning about a missing pin; a file without a pin is imported
    with the warning; and another pin, a refused key, a key that can't be sent, a malformed address, a closed port, a
    bridge without an id and an answer .NET fails on each lead to `import_failed`. Its three ways out and pairing the
    bridge for a file without one are followed to the end. Files that .NET can't read, or that its run refuses, show
    the engine's message, line and column, and an empty field.
  - Through Home Assistant's HTTP flow API, the answer for an imported entry holds neither the application key nor the
    device keys; without `async_on_create_entry`, the device keys were in it. Breaking the import on purpose failed the
    matching tests: the subentries sent back, the bridge's checks without the bridge's settings, `PAIR_AGAIN` left in a
    message, the file's bridge id kept, a note for another letter case or for a missing id, a switch's unique id as
    written, and the text suggested again after an error.
- 2026-11-25: the runtime (decision 23).
  - The supervisor takes an `observer`, which its forwarder gives each of the runner's reports after the status
    tracker: the forwarder test checks both, in that order, with an observer and without, and a press through a running
    supervisor reaches the observer once the tracker has it. The engine names the two messages that the runtime uses,
    `CERTIFICATE_MISMATCH` and `PRESS_DROPPED`, and its other tests pass unchanged.
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
- 2026-11-25: diagnostics and the logbook (decision 26).
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
  - `tests/test_logbook.py`, 17 cases. Each description: a press with and without a seq, of a switch with a name,
    without one and with a blank one; turned on and off, with no note, with things already on or off, with failures and
    with both; failed, dropped and another result; events without data and with values of other types. The events of a
    runtime, described, against the runner's own lines for them, which they equal as "<name>: <message>": a press that
    turned a room off, with the light already off and the room not switched by the bridge (a 207 with errors), one with
    the light already off, one whose read of the lights failed, one that took 6 s, and the press dropped behind it. And
    the real logbook, on the recorder, set up before and after the integration: all of the integration's entries, those
    of the switch with a device on that device's page, none on the bridge's device, and the switch without a device in
    the logbook only.
  - They cover all 48 statements and 8 branches of `logbook.py`. Breaking it on purpose, 7 ways, each failed a test:
    None as "None", "5 s" for "5s", a note for 0 targets, the notes in the other order, the events described under
    another domain (no entries on the device's page), the name repeated in the message, and the name read from another
    key.
  - hassfest 2026.11.3, run as its image runs it (from a sparse checkout of core at that tag in the worktree's
    git-ignored `.dev`, with core's `requirements.txt` without pymicro-vad, pyspeex-noise and PyTurboJPEG, and
    `packages/hassfest`), found one integration, none invalid and no warnings, with the diagnostics alone and with the
    logbook too.
  - Live, in the development Home Assistant (decision 18) on 127.0.0.1, with the fake bridge and the simulated switch
    of the made-up device `1000d1a6e5`: a pasted `sonoff2hue.yaml` with the bridge's key and pin added the entry, and
    two presses toggled the light and the room on, in 6 ms, and off. Home Assistant's REST API gave the logbook's 4
    entries, named "Landing (1000d1a6e5)": "wall switch operated (seq 2).", "turned ON light 'Hallway', group 'Living
    room' in 6 ms.", and the same for seq 3, turning them OFF. It gave the diagnostics file too, with the 4 parts, 9
    activity entries at the configuration's debug level and the key and the pin as `**REDACTED**`, and neither of them
    anywhere in it. Its log had no error about sonoff2hue.
  - On pull request #20, before this line (CI run 45563283686, Validate run 45563283501, Commits run 45563283681):
    *hassfest* found one integration, none invalid and no warnings; *HACS* passed its 10 checks; *Lint*, *Test* with
    its 2,698 tests and each commit's checks passed.
- 2026-11-25: the switch subentry flow (decision 24), rebased onto the runtime (decision 23).
  - `tests/test_switch_flow.py` runs it in Home Assistant 2026.11.3 through the flow manager for subentries, with the
    integration set up and its runtime running on the fake network of `tests/fakes.py`: 79 tests of every step, error,
    warning and abort, which cover all 445 statements and 110 branches of `switch_flow.py`; `config_flow.py` stays at
    100% (309 statements, 68 branches). The devices seen on the LAN are the runtime's registry, fed with synthetic
    tracker results: a press of an unused device, of another switch's device (one progress event per new message), of
    the edited switch's own one; baselines, a repeat, other events and a press at a time the device had; two presses at
    once; the timeout at 60 s by time travel; a real reload, whose runtime has a new registry; the entry unloaded; the
    dialog closed; and the device list's order and labels. Identify also selects a device from its broadcasts on the
    fake network. A whole add, from a broadcast to the switch that the runtime then runs, and a whole edit run against
    the fake Hue bridge over HTTPS; the other cases against the runtime's bridge in memory (`tests/fakes.py`'s
    `Bridge`, with the fake bridge's lights, rooms and zones): repeated names, rooms without lights, entries that don't
    resolve, the key's three modes, a changed device, typed entries, a bridge that doesn't answer, one that isn't paired
    or can't be used, answers .NET fails on, conflicts and a switch deleted meanwhile, *Save without checking* after
    the bridge answers again, and a dialog closed while the bridge is asked. Every text is checked against `en.json`
    with its placeholders, and two tests go through Home Assistant's HTTP flow API: the progress, the section and its
    `visible` condition, and no device key in any answer. 12 runs in a row of these tests and the setup flow's passed.
  - Breaking the flow on purpose, one change at a time, failed the matching tests: the registry read while the entry
    isn't loaded; a client of the flow's own instead of the runtime's; no check that the entry is loaded; a press at a
    time the device had counted; a 30 s or a 61 s wait; used devices not last; the oldest press first; rounding half to
    even in the times; *Replace it* as the key's default; *Save without checking* checking the names; a dialog closed
    while saving that still saved; a reload not followed; the switches of other subentries; the section never opened;
    no id beside a repeated name; no "no lights"; a press that never selects. Earlier, before the runtime: a progress
    event for every press of a used device; removed entries kept; typed entries' errors on the form; the dialog's
    device check run at saving; the new key read from the wrong field; no "Selected …" line. The unique id and the
    title from the saved device id rather than the normalized one changed nothing, as the server's write already
    normalizes it. The flow no longer cancels identify's task when it is removed, which Home Assistant does itself.
  - hassfest 2026.11.3's translations schema (`script/hassfest/translations.py`, run on `en.json` with stand-ins for its
    model, outside the repository) finds no errors; without `entry_type` it fails.
  - In a development Home Assistant (`scripts/develop setup`, on 127.0.0.1 with the fake bridge, both on other ports),
    with the runtime, through its HTTP and websocket APIs, and the simulated wall switch
    (`tools/send-ewelink-announcements.py`, TTL 0) for a made-up device: the entry reported `supported_subentry_types`
    `switch` with reconfigure; identify selected the device at its first press ("Selected …: it reported a press."); the
    switch was added with a light of the fake bridge, and the next press toggled that light; *Pick a device* listed the
    device with its address, type, *Online*, "Last press just now" and "used by …", and refused it with the dialog's
    message; identify while adding named the switch that uses it; the switch was edited with a new key, kept out of
    the answers, and deleted through Home Assistant's own API, each change restarting the bridge supervisor. The log had
    no error of the integration.
- 2026-11-25: the devices and entities (decision 25).
  - 30 new test cases in Home Assistant 2026.11.3, with the LAN on `FakeNetwork` and the bridge in memory, cover all
    statements and branches of `entity.py`, `sensor.py`, `binary_sensor.py`, `event.py` and `button.py`, and still all
    of `__init__.py`, `runtime.py`, `config_flow.py` and `repairs.py`. They check:
    - Status in each state: running, connecting, error (a rejected key), invalid configuration (a light not on the
      bridge), no switch and not paired, with the supervisor's messages and problems, and `since` moving only with a
      change;
    - Reachable on, off with the bridge's message after a keep-alive at 45 s, on again, and unknown while unpaired;
    - eWeLink devices seen counting a device that no switch maps at once, listening, and the last packet of a repeat
      30 s later;
    - Last action through turned on, turned off, failed, dropped and a toggle that one of two targets failed;
    - Online across the 5 minutes with time travel, back within 30 s after a repeated broadcast, and with a device key,
      detached mode and a payload that can't be decoded;
    - Press for each press of its switch only, and none while the bridge doesn't run;
    - Test toggle's lights and log lines, and its three kinds of error as the action's message;
    - the devices and their entities' unique ids, entity ids and categories, the same after a reload, a switch added,
      changed and removed while loaded, one added with the switch flow's *Add switch* (decision 24), the switch's
      device id in its press events, and that the texts in `en.json` are exactly the entities' names, states,
      attribute names and unit.
  - `test_runtime.py`'s press test now takes the switch's device from the platforms, and checks that the bus events
    name it from the first press, as the logbook needs. `test_logbook.py` takes the devices from the platforms too, and
    leaves the entities' changes out of its comparison.
  - With the real logbook on the recorder, as `test_logbook.py` runs it, a throwaway test (not committed) asked for a
    switch's device page as the frontend asks for it, its entities and the device: one press gave
    `sonoff2hue_press`'s entry, Press's change with `event_type` `press`, `sonoff2hue_toggle`'s entry, Last action's
    change to `turned_on` and Online's to `on`, and the whole logbook the same.
  - Breaking the platforms on purpose, 15 ways, each failed a test: the toggle or the error always winning a tie, an
    older error's message on a toggle, a dropped press shown as failed, no count of a new device at once, no 30-second
    look at the last packet, snapshots not followed, no look at an offline switch or at the end of the 5 minutes, a
    never heard switch as unknown, a blank key counted as a key, every switch's presses, the test's error not
    converted, no entities for a switch added later, and a switch's new settings not followed.
  - hassfest 2026.11.3's translations check, run as before in a throwaway environment outside the repository, finds no
    errors and no warnings in `en.json`.
- 2026-11-25: keeping the devices in step with reconfigured ids (decision 25).
  - 5 new tests in `test_entity.py`: a switch's new title renames its device, and a new device id moves its device and
    its entities' unique ids, while the entity ids stay and Press follows the new MINIR4M; a switch added afterwards
    with the old device id gets a device of its own; the same through the switch flow's *Edit switch*; a new bridge
    id moves the bridge's device and its entities' unique ids, and a reload then makes no new entities; a new bridge
    id, device id and title saved while the entry wasn't loaded apply at its next start, with the same two devices and
    entity ids; and a helper of another entry linked to a switch's device, and a device of a subentry that isn't a
    switch, are left alone. With them, the 35 tests of the entities cover all of `entity.py` again.
  - Breaking the tracking on purpose, 8 ways, each failed one of them: not starting it, not following titles, keeping
    the unique ids or the serial number, renaming the helper's unique id too, following only the switches or only the
    bridge, and not looking at the start.
  - Live, in the development Home Assistant (decision 18) on 127.0.0.1, with the fake bridge and the simulated switch
    of the made-up device `1000c0ffee`, both commits: the bridge paired with the setup flow and the switch added with
    *Add switch* (*Enter the device ID*) got their devices, "Hue bridge" by "Signify Netherlands B.V." and "Hall",
    "SONOFF" "MINIR4M" with the device id as its serial number, and the 7 entities their unique ids, names,
    categories and icons (`frontend/get_icons`). Two presses toggled the fake bridge's light on and off: Status was
    running, Reachable on, eWeLink devices seen 1 device, Online on at the A record's address, Press at seq 3 and Last
    action turned off in 3 ms. Test toggle turned the light on again, with its log line. The switch's device page's
    logbook had the press twice, as `sonoff2hue_press`'s entry and as Press's change, and the toggle twice, as
    `sonoff2hue_toggle`'s entry and as Last action's change. Home Assistant logged no error about sonoff2hue and
    stopped on SIGINT in 2 s.
- 2026-11-25: the logbook without `logbook.py` (decision 26, the user's choice).
  - `tests/test_logbook.py`, rewritten: 2 tests with the real logbook on the recorder and the entities (decision 25),
    asked as the frontend asks, for everything and for the switch's device page (the device's entities and the device).
    Two presses gave exactly Press's event and Last action's change each, `turned_on` and then `turned_off`, in both,
    and the switch's first broadcast only Online's change; the presses fired `sonoff2hue_press` and `sonoff2hue_toggle`
    with the device's id, and the logbook listed nothing for them. Two toggles that couldn't read the bridge gave
    Press, Reachable off on the bridge's device and Last action `failed`, then Press alone; a toggle that took 6 s and
    the press dropped behind it gave two Press entries, Reachable on again and Last action `dropped`, with no
    `turned_on`.
  - With `logbook.py` back, both tests failed on the bus events' entries.
  - The comments in `const.py`, and in the tests of the runtime and the entities, no longer say that the bus events
    are for the logbook. `scripts/check` passes, with 2,797 tests.
- 2026-11-25: the end-to-end test (decision 27), on `main` at 186da65, with the switch subentry flow (decision 24) and
  the entities (decision 25), then rebased onto 9dccc0e.
  - **Locally,** on the development machine, `scripts/e2e` passed all 45 checks in each of the final code's 7 runs, the
    last 6 in a row in 14.0 to 15.7 s. Setup took 0.9 to 1.5 s with `.dev/venv` there, the fake bridge 0.4 s, Home
    Assistant's first start 2.2 to 2.5 s, the setup flow 0.1 s, the switch subentry flow 0.1 to 0.2 s and the runner
    0.1 s after it, the two presses 3.0 s with toggles of 3 to 16 ms, the restart 3.2 to 3.9 s (1.1 s to the exit code
    100), the press after it 1.5 s and the stop 1.1 to 1.3 s. A pass left no `.dev/e2e`, no process and no port behind.
    The code before the entities' checks (43 checks) passed all of its 21 runs: 7 alone, 6 of them in a row in 12.8 to
    13.4 s, and 14 in the pairs below. Before that, the test had passed the same way while it mapped the switch through
    the YAML import (35 checks, 13.0 to 15.2 s) and then read the status from the diagnostics (39 checks).
  - **On 9dccc0e,** where the logbook lost `logbook.py` (decision 26), which the test never used: it reads the bus
    events and the entities. 5 runs in a row passed all 45 checks in 13.8 to 14.5 s, with the restart 3.3 to 3.6 s.
  - **On GitHub's runner** (ubuntu-latest, the image `ubuntu-26.04` 20261116.202), in pull request #22, *End-to-end*
    passed all 45 checks in 6 runs, the first attempt and two re-runs of the job at each of two heads, each with uv's
    cache restored. At 4a173a9, the same test on 186da65 (CI run 45578042122): 21.7, 21.3 and 22.1 s, in jobs of 39, 38
    and 42 s. At cf3c16b, the same code as this commit (CI run 45579898752): 16.8, 19.8 and 16.6 s, in jobs of 35, 35
    and 31 s. Setup took 3.0 to 4.9 s, the fake bridge 0.4 to 0.6 s, Home Assistant's first start 3.5 to 4.8 s (its
    HTTP server answered after 2.1 to 3.0 s), each flow 0.1 to 0.3 s, the two presses 3.0 s with toggles of 2 to 4 ms,
    and the restart 2.6 to 4.0 s, 0.7 to 1.4 s of it to the exit code 100. *Lint*, *Test*, *hassfest*, *HACS*, both
    commits on their own and *All commits pass* passed at both heads. The first version, which mapped the switch through
    the YAML import, passed its 35 checks there in 21.7 s (run 45564543062 at d7c13ba), with setup taking 6.6 s: it
    found no cache for its key yet, and saved one.
  - **Breaking it on purpose:** expecting the PUTs in the wrong order failed that check alone, with the PUTs expected
    and received, and exit code 1; the integration's lines of the log were printed and `.dev/e2e` kept the logs.
    Temporary mutations of the integration each failed the test. On the final code: Press with the seq before the
    press's (the entities' check of both rounds). On the code before the entities' checks, whose checks the final code
    keeps: a switch's first broadcast taken as a press (12 checks, among them presses 2, 3 and 4 instead of 3 and 4) and
    identify-by-press that never selects a device ("Identify-by-press didn't select the switch within 15 s"). On the
    code that read the status from the diagnostics, whose checks the final code keeps: a supervisor that never sets the
    status to running (after 30 s, with the status `connecting` that the diagnostics showed), the status recording each
    toggle inverted (both status checks), and a runner that never logs "Listening for wall switch presses." (both log
    checks). On the first code, with the same checks of the presses: a runner that gets no packets (no event and no PUT)
    and `turned_on` inverted in the toggle events (both event checks).
  - **Faults of the test's own,** found and fixed on the way: the log scan read the empty `home-assistant.log.1` after a
    single start; a log line could be read before Home Assistant's log thread wrote it; right after the integration's
    first setup the diagnostics answered 404 (a mutation run met it); and a flow's announcement could come before the
    answer that started its progress step. For the last, a pause of 0.5 s in the driver after the link step's answer, on
    purpose: counted after the answer, the test waited 75 s for an announcement that had come and failed; counted before
    the request, as now, it passed.
  - **Interrupted** with SIGINT during the restart, as Ctrl+C sends it: exit code 130, with Home Assistant and the fake
    bridge stopped and no process left.
  - **Two runs at once,** from this checkout and a copy of its code (`git archive` and the driver, with environments of
    their own). With the code before the entities' checks, 7 pairs passed: 3 started together, and 4 with the second
    run started 2.0, 2.05, 3.45 or 3.5 s later, so that its identify-by-press met the first run's presses. With the
    YAML import, both passed together and 2 s apart; with one device id in both, temporarily, the run started 2 s later
    failed 9 checks: it took the first run's seq 3 as its press, and its own seq 2 as a restart of the device.
  - `scripts/develop setup` without `SONOFF2HUE_DEV_CONFIG` still made `.dev/config`, and with it another folder the
    same way. shellcheck 0.11.0 finds nothing in `scripts/e2e` and `scripts/develop`, actionlint 1.7.12 nothing in
    `ci.yml`, and `scripts/check` passes, with 2,812 tests on 186da65 and 2,797 on 9dccc0e.
- 2026-11-25: the README for the Marketplace (decision 28), on the entities (decision 25).
  - **Labels:** a throwaway script, in the worktree's git-ignored `.dev`, took the 91 different italic spans of
    `README.md` and found each label: in `en.json`, the flows' and the entities' names, states and attribute names; in
    Home Assistant's frontend 20261028.8 (its `src/translations/en.json`: *Add integration*, *Reconfigure*, *Reload*,
    *Enable debug logging*, *Download diagnostics*, *Delete*, *Submit*, and the Marketplace's *Add from link*, *GitHub
    link*, *Type*, *Integration*, *Install* and *Restart Home Assistant*); in core's strings (*Event received*;
    *Authentication expired for* the entry's title, the reauthentication's repair); or in `.vscode/tasks.json`. The one
    span left, *End-to-end*, names the CI job in the section on `scripts/e2e`.
  - **Claims:** each checked against the code and this file: the steps, menus and messages of `config_flow.py` and
    `switch_flow.py`; the repairs, bus events and switch test of `runtime.py` and `repairs.py`; the devices and the
    entities' states, attributes and categories in `entity.py` and the four platforms; `diagnostics.py`'s parts and
    redaction; and the log lines and timings of the runner, the supervisor and the listener. A switch whose names
    aren't on the bridge stops every switch: `resolve_bindings` raises for all of them together. Home Assistant
    2026.11.3's logbook leaves out a change of attributes alone (`_is_state_filtered`, and the query's
    `last_updated_ts == last_changed_ts`) and sensors with a unit, so *eWeLink devices seen*. Its frontend shows an
    event entity's change as its event type, *Press*. The integration page's ⋮ menu at the top has *Enable debug
    logging*, the entry's menu has *Reconfigure*, *Reload* and, while it is loaded, *Download diagnostics*, a switch's
    row has *Edit switch* and a menu with *Delete*, and Settings has the Marketplace, and Repairs and Logs under System.
  - **Automations:** a throwaway test, not committed, set up both of the README's automations, read from its YAML
    blocks, next to the integration with the switch Hall (`1000abcdef`), the LAN on `FakeNetwork` and the bridge in
    memory. After a baseline, one press ran both: `event.received` on `event.hall_press` with the event type `press`,
    and `sonoff2hue_press` with that `switch_id`, whose data had `device_id`, `switch_id`, `name` and `seq`. The first
    one as written, with `switch.toggle`, was valid and triggered; its action failed only for the missing `switch`
    integration.
  - **Links:** the 10 external ones answered 200 to curl: the two SonoffLAN issues, the eWeLink developer portal, uv's
    documentation, the Releases page, and My Home Assistant's badges and redirects `marketplace_repository` and
    `config_flow_start`, which my.home-assistant.io's `redirect.json` lists, `marketplace_repository` since 2026.11 with
    `owner`, `repository` and an optional `category`, as the frontend's `ha-marketplace-my-redirect.ts` reads them.
    `LICENSE` and `.vscode/launch.json` exist, and there is no in-page link.
  - **GitHub** rendered it with its markdown API (`gh api markdown`, GFM, in the repository's context), with the
    section on `scripts/e2e` (decision 27): 27 headings, 7 code blocks, 181 italic and 19 bold spans, and no stray
    markup.
  - **The Marketplace:** read in Home Assistant 2026.11.3's `components/marketplace` (`async_get_readme_contents`,
    `get_documentation`, the `marketplace/repository/info` command) and its frontend 20261028.8 (the repository page,
    `tools/markdown.ts`, `ha-markdown-element.ts`, `markdown-worker.ts` and `package.json`), as decision 28 says.
    Rendered as the repository page renders it, with marked 18.1.1, js-xss 1.0.17 and the page's own `markdown.ts`
    bundled by esbuild 0.25.0, in a scratch folder: the same headings, code blocks and spans, the headings without ids,
    no HTML comment, no issue link added, and `LICENSE` and `.vscode/launch.json` linked to `raw.githubusercontent.com`
    at the version.
  - **Checks:** the HACS action's `information` check needs only a README in the repository's tree, and hassfest doesn't
    read it. `scripts/check` passes. On pull request #25, before three rebases and this line (CI run 45578371321,
    Validate run 45578371560, Commits run 45578371548), *Lint*, *Test*, the commit's own checks, *hassfest* and *HACS*
    passed, the last with all 10 of its checks, `information` among them.
- 2026-11-25: `test_last_action_shows_only_when_its_state_changes` made deterministic (decision 26).
  - Run alone on `main` 3aa1322 it failed 4 of 25 times, and 10 of 25 for the docs agent: Reachable and Last action,
    which one status snapshot changes at the same moment, came in the other order. A scratch test that recorded the
    state writes found them in both orders from run to run, the logbook listing them as written.
  - The test now compares the changes of each moment as counts, without regard to their order; counts, as two
    presses at once give two Press changes at the same moment. Run alone 50 times and the whole file 10 times, it
    passed every time.
- 2026-11-25: retiring .NET (decision 30).
  - **Phase 1, the oracles retired:** five commits on `py/retire-oracles`, from `main` at 190ec5b.
    - **Kept:** the mDNS layer's recorded cases, as the integration's own test data, without the generator's metadata.
      `tests/engine/mdns_packets.json` holds the engine fixture's 36 packet cases, 29 messages with their announcements
      and probes and 7 parse errors, each error as the message that `DnsFormatError` carries: a check asserted the
      exception type's prefix on all 7 before it removed it. `tests/engine/mdns_text.json` holds the text fixture's
      tables unchanged: 25 white-space code points, and 1,475 uppercase and 1,459 lowercase mappings. Both read back as
      the same data. `test_mdns_packets.py` replays the packets as the parity test did, 36 cases, and `test_text.py`
      reads the tables from their new place, with its 21 cases and their expectations unchanged. A parse error and a
      `seq` changed in the data failed exactly those two cases.
    - **Deleted:** `tools/dotnet_oracle` (10 files) and `tools/js_oracle` (4), `tests/engine/fixtures` (28,671 lines),
      `test_dotnet_parity.py` (1,689 cases), `test_jstext.py` (122), and the fixture-driven parts of `test_picker.py`
      (150 cases), `test_switch_form.py` (27) and `test_switch_rules.py` (143), whose 27, 24 and 34 hand-written cases
      stay: 33,500 lines in all. Docstrings no longer point at them, and `.gitignore` no longer lists the generator's
      build output. Outside this file, only `jsonnode.py`'s alias `dotnet_text`, for the module `text`, still matches
      their names.
    - **Added:** `tests/test_own_mdns.py`, 47 cases. It reads the integration's 39 modules, 27 of them in the engine,
      with `ast`, and its manifest, and passes on today's code. Its other 44 cases show its checks on synthetic sources
      and manifests: 22 forms of an mDNS import found and 7 other imports left alone, 6 forms of a Home Assistant
      import, relative imports, and 7 manifests that rely on Home Assistant's mDNS and one that doesn't. It failed, and
      passed again once each was reverted, with `from homeassistant.components import zeroconf` added to `runtime.py`,
      with `from homeassistant.core import HomeAssistant` added to `engine/dns.py`, and with a `zeroconf` matcher and
      `network` in `after_dependencies` added to the manifest.
    - **types-PyYAML** 6.0.12.20261122, of 2026-11-22, the newest release of the stubs for PyYAML 6.0, is pinned in
      `requirements_test.txt`. `scripts/setup` installed it, mypy (strict) reads `yaml`'s types from it, and
      `tests/tools/test_develop.py` passes. `requirements_dev.txt` takes it through `requirements_test.txt` and needs
      no change; `scripts/develop setup` installed it in `.dev/venv` with Home Assistant's constraints.
    - **Checks:** `scripts/check` passed with 2,797 tests on 190ec5b, 2,833 after the first commit, 702 after the
      deletions and 749 at the end. `check-commits.sh` and `test-each-commit.sh` passed for all five commits.
      `scripts/e2e` passed all 45 checks in 28.7 s, its setup taking 8.2 s for a new `.dev/venv`.
  - **A `lan`:**
    - **The mDNS layer keeps every result.** Its string rules left `text.py`: `dns.py` compares names and TXT keys by
      each character's simple uppercase form (`case_key`, with equals, starts-with and ends-with helpers) and counts a
      name's length in UTF-16 code units; the record part of `ewelink.py` keeps its 25-character white space, its
      one-to-one lowercase for device ids and its signed 64-bit `seq`; `_discover_mdns` uses `dns.py`'s helpers.
      `test_text.py` became `test_mdns_text.py`: it replays `mdns_text.json` for every code point against them, and
      checks that no non-ASCII character gets an ASCII key. `mdns_packets.json` is unchanged. A differential fuzz of
      `main`'s modules against these, in the git-ignored `.dev`, gave the same results for 180,000 random strings,
      180,000 random packets (153,588 parsed, with 15,032 announcements and 13,357 probes) and 600 runs of Hue
      discovery's mDNS part with the same random packets on the fake network (374 bridges). `mdns.py` and
      `lan_monitor.py` changed in words only: without docstrings, their syntax trees are `main`'s.
    - **Python's rules outside it.** Payloads: `base64.b64decode(validate=True)` and `json.loads` into plain values
      (`ewelink.Json`), the key MD5 of the device key stripped with `str.strip()`, the same error strings, and
      nothing raised, also for a payload nested too deeply. Presses: settings compare with `==`, `relaySeparation` and
      outlets are read with `int()`, relay states compare with `casefold()`, the tracker keys devices by `casefold()`,
      and summaries and relay changes write values as `json.dumps` does. The LAN registry keys and orders devices by
      `casefold()`. Cloud discovery reads `response.json(content_type=None)`, logs any failure at debug level, and
      takes the `internalipaddress` values that `ipaddress` reads, written as it writes them, with an `id` that is a
      text. None of the six modules imports `text`, `jsonnode` or `errors`, and none names another platform.
    - **The simulated switch** has type hints and docstrings, and ruff and mypy (strict) check it, with only N999
      ignored for its name. Its packets are the same for ASCII ids; a TXT string now counts its length in bytes,
      where a non-ASCII id made a record that overran itself.
    - **What users may notice, for unusual input only:** base64 with white space isn't valid; `NaN` and duplicate
      keys parse; `1`, `1.0` and `true` compare equal; other digits count in numbers; summaries escape as Python's
      `json` does (`\u00e4` and `<` where they had `\u00E4` and `\u003C`, and `-7.5` for `-7.50`); in the LAN registry
      'ß' and 'ss', or the Kelvin sign and 'k', are one device, and '_' sorts before the letters; the cloud's keys are
      case-sensitive, and an answer with an entry that isn't an object gives no bridges.
    - **Checks:** seven commits on `py/lan`, rebased onto `main` at cead38d. `scripts/check` passed with 1,021 tests,
      `main`'s 980 and 41 more: the slice's files went from 182 tests to 223, with 23 more for presses, 14 for
      payloads and the simulated switch, 3 for cloud discovery and 1 for the string rules. Changing the simulated
      switch back failed its new test. `scripts/e2e` passed all 45 checks, in 19.9 s. `check-commits.sh` and
      `test-each-commit.sh` passed for all seven commits.
  - **B `hue`:**
    - Pending.
  - **C `yaml`:**
    - **Changed:** three commits on `py/yaml`, from `main` at 2a958f9, the last of them this entry. `yaml_import.py`
      reads the pasted text with PyYAML, through a subclass of `getattr(yaml, "CSafeLoader", yaml.SafeLoader)` whose
      only implicit resolver is null's, so any other plain value stays the text written. A pass over the parser's
      events refuses a second document, a tag other than `!!str` on a value and nesting deeper than 32 levels; a walk
      of the composed node tree, in the order of the text, refuses a key that isn't a single value and a repeated
      key; then a probatio schema checks the shape. Each of its errors gets the line and column of its node, the
      key's for an unknown key, and the first in the text is reported. probatio carries the messages without an
      explicit validator: `Maybe(X)` reports X's own error class, `DictInvalid`, `SequenceTypeInvalid` or
      `TypeInvalid`, which names the kind expected, where `Any(X, None)` reports `AnyInvalid`, and it collects every
      error with its path. `yaml_parser.py` and `yaml_uri.py` are deleted, and `yaml_import.py` went from 1,010 lines
      to 251 and imports neither `errors` nor `text`. Code and tests: 4,041 lines removed and 668 added.
    - **User-visible:** word for word as before: an unknown key, which names its section and the valid keys; the
      three messages for a value of the wrong kind; `duplicate key '…'.`; the empty switch entry; and the form
      `Config, line L, column C: …`. New: a syntax error gives PyYAML's problem, such as "did not find expected key";
      a tag "the tag !!int isn't supported here. Write the value without the tag.", unsafe tags such as
      `!!python/object/apply:…` included; a second document "only one document is allowed, but another one starts
      here."; nesting "the text nests more than 32 levels deep here."; a character that YAML refuses "unacceptable
      character #x0007: control characters are not allowed", with PyYAML's reason. Edge cases: columns count
      characters, not UTF-16 code units; a byte order mark at the start is skipped; and a document of a single value,
      such as `a`, is refused with "expected 'key: value' lines here.", where it was read again as a whole file and
      the setup flow showed `unreadable_config` for `a`.
    - **Tests:** `tests/engine/test_yaml_import.py`, rewritten by hand: 133 cases, each run with the C loader and
      again with PyYAML's own, for which a fixture reloads the module with `yaml.CSafeLoader` hidden, so 266 where
      there were 37. They cover whole files, values kept as written, 25 plain values that stay text (`yes`, `on`,
      `0x10`, `010`, `1e3`, dates and times among them), nulls, null sections and switch values, the empty switch
      entry, unknown and duplicate keys, values of the wrong kind, the first of several problems, documents that
      aren't "key: value" lines, anchors, aliases and a merge key, several documents, syntax errors, 19 refused tags,
      tabs, CRLF, a byte order mark, refused characters and the nesting limit, each with its line and column. The
      test that a null light fails the target resolver left with the old reader; the resolver is slice D's. In
      `tests/test_config_flow.py`, `test_a_file_that_the_import_refuses` gets a fourth case, `a\n`; F's other
      assertions there pass unchanged.
    - **Checks:** `scripts/check` passed on each commit (`test-each-commit.sh`), with 980 tests, where `main` at
      2a958f9 has 750. The tests cover all 144 statements and 52 branches of `yaml_import.py`. By hand, in scratch
      scripts in the git-ignored `.dev`: 400,000 random texts, half for each loader, from YAML fragments, mutated
      files and random code points, raised nothing but `ConfigError`; and 60,000 random settings written with
      `yaml.safe_dump`, in block and flow style, some with CRLF, read back exactly when they fit the schema and were
      otherwise refused at a line and column inside the text. Both loaders refuse the same nodes at the same places;
      they differ in PyYAML's problem texts, and libyaml takes a tab inside a plain value, which the pure-Python
      reader refuses. A `ReaderError`'s position counts bytes of the UTF-8 text in the C loader and characters in
      the other, so the character is looked up; the C loader raises `UnicodeEncodeError` for a lone surrogate. With
      Python 3.14, 1,000 nested lists stop PyYAML's pure-Python composer and 100,000 the C one with a
      `RecursionError`; the event pass refuses 33 levels before either composes. `check-commits.sh` passed for the
      three commits.
  - **D `switches`:**
    - **What changed:** four commits on `py/switches`, from `main` at cc3b702 and rebased onto 0a20e10 after slices
      F, E, C and A, and this entry's. `config.py`, `targets.py`, `picker.py`, `switch_form.py`, `switch_rules.py` and
      `switch_flow.py` import neither `text`, `jsonnode`, `errors` nor `jstext`, and `jstext.py` is gone. One rule for
      names: the target resolver, the picker and the switch rules compare names and ids stripped and casefolded
      (`targets.name_key` and `targets.refers_to`, which the picker uses too), an empty entry names nothing, a `room:`
      or `zone:` prefix is taken in any case (`targets.split_kind`), and the known names in the resolver's messages are
      in `str.casefold` order. A null entry is an empty name, which matches nothing and is reported, and a resource
      without a name matches by its id. A device id is normalized with `strip().lower()` and compared with
      `casefold()`, by a new `SwitchSettings.has_device_id`, which `find_switch`, the duplicate check of
      `validate_for_run` and the switch rules use; blank is `not s or s.isspace()`. The switch rules and the form count
      characters with `len()`, find control characters by their category Cc and white space in a key with
      `isspace()`, and remove duplicate entries by casefold; the picker's resources are ordered by the casefolded name,
      and its selections key resources by kind and id, so a null id and the id "null" stay apart. The switch flow
      catches `Exception` and logs the traceback with `_LOGGER.exception` where it caught `UnexpectedError`, on the
      same steps and texts, and turns the rules' conflict and missing switch into its aborts before that catch-all. As
      in F, ruff counts the logging call as handling the exception, so these handlers have no `# noqa: BLE001`.
      Docstrings, comments and test names describe the form's checks, the switch rules and the picker on their own
      terms. The limits, the messages, their fields and order, the 409 and 404 rules, the device key's rules, and the
      flow's steps, menus and translation keys stay.
    - **User-visible:** edge cases only, none for valid device ids and ordinary names. Names and ids match under
      Unicode case folding in the picker and the resolver alike, which can no longer disagree about an entry:
      `STRASSE` names `Straße`, and `Straße` and `STRASSE`, or the Kelvin sign and `k`, are one entry when duplicates
      are removed. White space is Python's everywhere: U+001C-U+001F are stripped too, and alone count as a missing
      value; the picker also strips U+0085, and no longer U+FEFF. Lengths count characters, so a name of 63 letters and
      an emoji fits in 64. The known names list `_Spare` before `Arc lamp`. A null entry is reported as "no Hue light
      named ''" where the resolver raised, and any other failure while the flow lists the lights or checks a switch
      shows the flow's existing texts.
    - **Tests:** 293 in six files, where four had 164. `test_targets.py` (41, new): resolution by name, id and
      prefix, case, ambiguity, a room without lights, duplicates, empty and null entries, and the order of the known
      names. `test_config.py` (11, new): the device id, blank and duplicate rules. `test_picker.py` (37): resolving,
      encoding, selections and their edits, and `split_list`. `test_switch_form.py` (39) adds `same_id` and the
      labels. `test_switch_rules.py` (86): every validation limit and its neighbours, control characters, white space,
      empty values, the order of the fields, and the write scenarios: 409, 404, and a key kept, replaced, removed and
      dropped. `test_switch_flow.py` keeps its 79; its two unexpected failures come from a client whose `get_lights`
      raises `RuntimeError`, so they don't depend on how the Hue client reads a malformed answer. Together they cover
      every statement and branch of the six modules, 884 and 250. 19 of 20 mutations each failed a test; the 20th,
      identify-by-press keyed by the raw device id, is equivalent, as the registry's ids are lowercase.
    - **Outside the slice:** nothing in the end. Until slice C's rewrite of `tests/engine/test_yaml_import.py` was
      merged, a first commit removed its test that expected the resolver to raise for a null entry; the rewrite has
      no such test, so the rebase dropped that commit. `test_targets.py` covers the null entry as a reported empty
      name.
    - **Checks:** `scripts/check` passed with 1,150 tests, where 0a20e10 has 1,021. `check-commits.sh` and
      `test-each-commit.sh` passed for every commit, before and after each rebase. `scripts/e2e` passed all 45
      checks in 36.0 s on 2a958f9, its setup taking 11.6 s for a new `.dev/venv`, and in 16.2 s after the last rebase.
  - **E `engine-core`:**
    - **What changed:** on `py/engine-core`, from `main` at cc3b702 and rebased onto 72b6372 after slice F, in two
      commits and this entry's. `runner.py`, `supervisor.py`, `status.py` and `activity.py` import neither `text` nor
      `errors`. Device ids are keys in their casefolded form, from each switch's normalized id, and blank is
      `not s or s.isspace()`. A device id mapped twice, in the runner's bindings or in the status tracker's
      `set_running`, raises a `ValueError`, "More than one switch has the device id 1000abcdef.", from one helper,
      `runner.bindings_by_device_id`; `validate_for_run` refuses such a configuration before anything runs. The status
      tracker tells bridges apart by a tuple of the address, the key and the pin, where a missing pin is an empty one.
      The activity buffer names an exception by `type(err).__name__`, keeps its deque of the newest 1,000 entries, takes
      its ids from `itertools.count`, and answers `get` with a deque bounded by the limit, so a limit of 0 still gives
      none. The docstrings and comments describe decisions 14, 21 and 23 in asyncio's terms. The states, messages,
      problems, timings, log lines and their levels, eager starts, cancellation, observer reports and `on_key_rejected`
      stay as they were, and so do the public names that the other modules use.
    - **User-visible:** an activity entry, in the diagnostics, names an exception by its Python class, where an
      `UnexpectedError` showed the type it stood for; U+001C-U+001F alone now count as a missing bridge address,
      application key, pin or device key; and settings whose address and key differ only where the old joined text ran
      together (`a|b` and `c`, against `a` and `b|c`) are another bridge. Valid settings see no difference.
    - **Tests:** 103 in the four files, one more than before. The duplicate ids expect the `ValueError`. The unexpected
      failures are injected, a toggler or a keep-alive that raises and a first resolving that fails, so the tests don't
      depend on how the Hue client reads a malformed answer. The activity's exception cases name a class of the test's
      own, `PermissionError` and `KeyError`, and the bridge's identity and U+001F are new cases. Coverage of the four
      modules is as before: every statement but the default clocks. Six mutations each failed a test: duplicates not
      refused, a type name with its module, the joined identity, a limit of 0 giving everything, and blank as only None
      or empty, in the tracker and in the supervisor.
    - **Checks:** `scripts/check` passed with 750 tests, where cc3b702 and 72b6372 have 749. `check-commits.sh` and
      `test-each-commit.sh` passed for all three commits, before and after the rebase, and `scripts/e2e` passed all 45
      checks, in 21.5 s before the rebase and in 14.6 s after it. No other slice's files changed.
  - **F `ha-layer`:**
    - **Changed:** four commits on `py/ha-layer`, from `main` at cc3b702, the last of them this entry, in the Home
      Assistant code (`__init__.py`, `runtime.py`, `config_flow.py`, `entity.py`, the four platforms,
      `diagnostics.py`, `repairs.py` and `const.py`) and its tests. It imports neither `engine/text.py` nor
      `engine/errors.py` any more. Input is trimmed with `strip()`, blank is `not s or s.isspace()`, and device ids and
      bridge ids compare with `casefold()`: the switch test's lookup, the device that the bus events name, Last action,
      Press, the paired bridge chosen first when reconfiguring, and the import's note about another bridge id. Where
      the setup flow caught `UnexpectedError`, reading the bridge, connecting to an imported file's bridge and reading
      the pasted file, it catches `Exception` and logs the traceback with `_LOGGER.exception`, with the same steps,
      translation keys and texts. ruff counts that call as handling the exception, so these handlers have no
      `# noqa: BLE001`, which it would flag as unused (RUF100). Docstrings, comments and test names describe the code
      on its own terms, and say Home Assistant's frontend where they meant it.
    - **User-visible:** nothing in the flows, steps, menus, translation keys, data, entities, unique ids, devices,
      repairs, bus events, logbook or diagnostics. Edge cases only: the bridge address and the imported switches'
      names are trimmed of Python's white space, which adds U+001C-U+001F; ids match under Unicode case folding, so
      `ß` matches `ss`, which valid device ids and bridge ids never hold; and any other failure while reading the
      bridge, connecting to the file's bridge or reading the file shows the flow's `unexpected` or `unreadable_config`
      text and logs its traceback, where it used to escape the step and Home Assistant's flow API answered 500.
    - **Tests:** `test_config_flow.py` keeps its 70 tests. The five that reached those paths through `UnexpectedError`,
      or through an answer or a file that only the engine's old parsers failed on, raise a `RuntimeError` instead: in
      the search's and the pairing's stand-ins, and from `get_bridge_info` or `yaml_import.parse`, replaced for the
      test. How the engine reads malformed answers and files is for its own tests. Four tests were renamed, and
      docstrings and comments in six test files reworded, with their expectations unchanged.
    - **Checks:** `scripts/check` passed on each commit (`test-each-commit.sh`), with 749 tests, as on cc3b702. The
      277 tests of the Home Assistant layer (`tests/` without `engine/` and `tools/`) cover every statement and branch
      of its modules, `switch_flow.py` included: 1,449 statements and 282 branches. `tests/test_own_mdns.py` passes
      its 47 cases. `check-commits.sh` passed for every commit, and `scripts/e2e` passed all 45 checks in 24.9 s, its
      setup taking 8.6 s for a new `.dev/venv`. An AST scan of the 25 owned files finds no import of `text`, `errors`,
      `jsonnode` or `jstext`, and no line with a term that the wording rule bars.

## Status

- [x] Repository started: the license, this memory, the tools (`scripts/setup`, `scripts/check`) and the commit checks
- [x] CI: lint, tests and per-commit checks, hassfest and HACS (`validate.yml`), all required for `main` by ruleset
  28003079
- [x] mDNS spike on the user's Home Assistant OS: own UDP 5353 socket next to Home Assistant's zeroconf (decision 19)
- [x] Engine port with its tests: DNS, eWeLink with press detection, the mDNS listener, the Hue client and pairing,
  the fake Hue bridge, the runner (done), YAML import (done)
- [x] The LAN view's engine ports (decision 17): the device registry, the LAN monitor and the activity buffer
- [x] The web UI's switch editor in the engine (decision 20): the picker, the server's validation and the form's own
  checks
- [x] The bridge supervisor and the status tracker (decision 21), with the reauthentication hook for the runtime
- [ ] Home Assistant layer: skeleton (done), runtime (done, decision 23), setup flow with reconfigure, reauth and the
  import of a `sonoff2hue.yaml` (done), switch subentries (done, decision 24), entities (done, decision 25),
  diagnostics and the logbook (done, decision 26: the logbook shows the entities Press and Last action)
- [x] Development tooling (done) and end-to-end CI with a real Home Assistant, the fake bridge and a simulated switch
  (done, decision 27: `scripts/e2e` and CI's *End-to-end*, which the ruleset is to require once it has passed on `main`)
- [x] README for the Marketplace (decision 28)
- [ ] Live test on the user's Home Assistant OS
- [x] Releases (decision 29): the *Release* workflow with its checks and their tests, the refusals tested on GitHub,
  immutable releases. Release 1.0.0 is then a tag `v1.0.0` on `main`
- [ ] Release 1.0.0
- [ ] Retiring .NET (decision 30): Phase 1, the oracles retired (done); the six slices, A `lan`, B `hue`, C `yaml`,
  D `switches`, E `engine-core` and F `ha-layer`; the cleanup; and the README, `en.json` and this file rewritten
