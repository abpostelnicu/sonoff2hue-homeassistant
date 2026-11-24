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
- `custom_components/sonoff2hue/engine/` is the port of the .NET engine. Its modules import nothing from Home
  Assistant; the rest of `custom_components/sonoff2hue` adapts them to Home Assistant. Keep that split.
- Keep the behavior the same as the .NET app: the same rules, timings, limits and log messages ("Parity with the .NET
  app" below). A change to how presses are detected, lights are toggled or the bridge is paired goes into both
  repositories, each with its tests. Where Python's libraries are more lenient than .NET's, such as base64 with
  whitespace, JSON with `NaN` or duplicate keys, or `True == 1`, the port reproduces .NET's result, and a test pins
  it. Messages that name the .NET app's command line or web pages name the Home Assistant equivalent instead.
- Never run this integration and the .NET backend against the same switch: every press would toggle twice and leave
  the lights as they were.
- Stay listen-only towards the switches: the only packets sent are standard mDNS queries, as the .NET app sends.
- Tests must not need the network, a Hue bridge or a MINIR4M. Use the packet builders in `tests/engine/packets.py`,
  the fake Hue bridge in `tools/fake_hue` (on 127.0.0.1) and fake sockets.
- Log the way Home Assistant does: a module-level `_LOGGER`, lazy `%s` arguments (ruff's G rules), and the .NET app's
  wording. Its trace-level lines (repeated broadcasts, malformed packets) use level 5, below DEBUG.
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
- CI (`.github/workflows/ci.yml`) runs *Lint* (ruff, the formatter, mypy) and *Test* (pytest with coverage) on `main`
  and on the merge result of each pull request. *Commits* (`commits.yml`) checks every commit of a pull request on its
  own: the message rules and `git diff --check`, then `scripts/check` per commit, summed up by *All commits pass*. Both
  install uv 0.13.7 with `astral-sh/setup-uv@v10.4.0` (it publishes only full-version tags) and run `scripts/setup`,
  with uv's cache keyed on `requirements_test.txt`.

| Folder | Contents |
|--------|----------|
| `custom_components/sonoff2hue` | The integration (decision 16): `manifest.json`; `const.py` (`DOMAIN`); `__init__.py`, which sets up and unloads the config entry and declares `Sonoff2HueConfigEntry`, whose `runtime_data` is `Sonoff2HueData`; `config_flow.py`, the setup flow; `translations/en.json`, its strings (custom integrations ship no `strings.json`); `brand/`, its icons (`icon.png`, `icon@2x.png`) |
| `custom_components/sonoff2hue/config_flow.py` | The setup flow with reconfigure and reauth (decision 22): it finds or takes the bridge's address and pairs it with the engine's `hue_setup`. `const.py` holds the data contract: the config entry's keys (`CONF_BRIDGE_IP`, `CONF_BRIDGE_ID`, `CONF_APP_KEY`, `CONF_CERTIFICATE_SHA256`), its title (`ENTRY_TITLE`), and a switch subentry's type and keys (`SUBENTRY_SWITCH`, `CONF_DEVICE_ID`, `CONF_NAME`, `CONF_DEVICE_KEY`, `CONF_LIGHTS`, `CONF_GROUPS`) |
| `custom_components/sonoff2hue/engine` | Ports of the .NET engine, without Home Assistant imports. `text.py` holds the .NET string and number rules (trim, ordinal case-insensitive comparison and ordering, `ToLowerInvariant`, `TryParse`). `dns.py` is `Mdns/DnsMessage.cs`; `mdns.py` is `Mdns/MdnsListener.cs` with the parts of .NET's `NetworkInterface` and `NetworkChange` it uses; `ewelink.py` is `Ewelink/EwelinkAnnouncement.cs` and `EwelinkCrypto.cs`; `press.py` is `Ewelink/PressDetector.cs` and `SwitchTracker.cs`; `config.py` is the model of `Configuration/AppConfig.cs`; `jsonnode.py` reads JSON as System.Text.Json's `JsonNode` does. `hue_address.py` is `Hue/HueBridgeAddress.cs` with the parts of .NET's `Uri`, `IPAddress` and `IdnMapping` it relies on; `hue_client.py` is `Hue/HueClient.cs`, `HueTls.cs` and `HueModels.cs`; `hue_setup.py` is `Hue/HueBridgeSetup.cs` with the pairing loop of `Web/Management/HuePairing.cs`; `errors.py` holds `UnexpectedError` (decision 9). `targets.py` is `Bridge/TargetResolver.cs`; `toggler.py` is `Bridge/HueToggler.cs`; `runner.py` is `Bridge/Sonoff2HueRunner.cs` and `IRunnerObserver.cs` (decision 14). `yaml_import.py` is `AppConfig.Parse` with the parts of YamlDotNet's deserializer it reaches, on `yaml_parser.py`, YamlDotNet 18.1.0's scanner and parser, and `yaml_uri.py`, .NET's `Uri.IsWellFormedUriString` for tags (decision 15) |
| `custom_components/sonoff2hue/engine`, the switch editor | The web UI's switch editor, without its UI, for the switch subentry flow (decision 20). `jstext.py` holds JavaScript's string rules (`trim()`, `toLowerCase()`, UTF-16 lengths); `picker.py` is the UI's `targets.js`; `switch_rules.py` is the server's checks in `Web/Management/ConfigEndpoints.cs` (`SwitchValidation`, adding and changing a switch, the check against the bridge) and the picker's resources of `HueEndpoints.cs`; `switch_form.py` is the pure logic of the UI's `views/switcheditor.js`, with `sameId` and `switchLabel` of `format.js` and `applyFieldErrors` of `ui.js`. `tools/dotnet_oracle/Switches.cs` and `switch_cases.py` check `switch_rules.py` against the app's own code |
| `tests/engine` | Tests of the engine, ported from the .NET tests. `packets.py` builds raw DNS packets and MINIR4M broadcasts (the .NET tests' `TestPackets.cs`) and `MdnsPacket`s; `fakes.py` has fake multicast transports for `MdnsListener` |
| `custom_components/sonoff2hue/engine` (the LAN view) | `registry.py` is `Web/Monitoring/DeviceRegistry.cs`; `lan_monitor.py` is `Web/Monitoring/LanMonitor.cs`, which runs the config entry's mDNS listener; `activity.py` is `Web/Monitoring/LogBuffer.cs`, as a `logging.Handler` (decision 17). Their tests use `VirtualTime` from `tests/engine/fakes.py`, which makes the event loop's clock jump to the next timer whenever the loop would wait |
| `custom_components/sonoff2hue/engine` (the bridge's state) | `status.py` is `Web/Monitoring/StatusTracker.cs`, the state that the entities and diagnostics show; `supervisor.py` is `Web/Management/BridgeSupervisor.cs`, which runs the runner for one configuration (decision 21). Their tests run on `VirtualTime`, with `InMemoryBridge` for the bridge and `FakeNetwork` for the listener |
| `tests` | `hue.py` runs the fake Hue bridge on 127.0.0.1 with a manual clock (the .NET tests' `FakeHueTestHost`), and has `InMemoryBridge`, which answers and records requests without sockets (the .NET tests' `FakeHttpHandler`); `conftest.py` has its fixtures, `fake_hue` and `fake_hue_pressed`, which enable sockets |
| `tests` (the integration) | `test_init.py` and `test_config_flow.py` run the integration in Home Assistant, with pytest-homeassistant's `hass` fixture and `MockConfigEntry`; `test_brand.py` checks the brand images |
| `tests/tools` | Tests of the fake Hue bridge (the .NET app's `Sonoff2Hue.FakeHue.Tests`) |
| `tests/engine/fixtures` | `dotnet_text.json` and `dotnet_engine.json`, made by `tools/dotnet_oracle` from .NET and from the .NET app's own code (decision 7) |
| `tools/fake_hue` | The fake Hue bridge, a port of the .NET app's `tests/local/FakeHue` (decision 12): `bridge.py` (state, `Fault`s), `server.py` (endpoints, HTTPS, certificate), `__main__.py` (`python -m tools.fake_hue`, settings `FAKE_HUE_BIND`, `FAKE_HUE_PORT`, `FAKE_HUE_LINK_BUTTON`) |
| `tools/dotnet_oracle` | The generator of those fixtures: a .NET 10 console app (`Program.cs`, `Engine.cs`, and `Yaml.cs`, which runs the YAML cases in child processes, since some end .NET's process) that references the app's `Sonoff2Hue.csproj`, the cases it evaluates (`engine_cases.py`, with `yaml_cases.py`) and `generate.sh` |
| `tools/js_oracle` | The generator of `tests/engine/fixtures/js_picker.json` (decision 20): `oracle.mjs` runs the .NET app's web UI modules `targets.js` and `format.js` in Node on the cases of `picker_cases.py`, and records JavaScript's `trim()` and `toLowerCase()` for every code point; `generate.sh` |
| `tools/develop` | The development Home Assistant's configuration folder (decision 18): `python -m tools.develop <folder>` copies `configuration.yaml` once and writes Home Assistant's HTTP store, `.storage/http`, on 127.0.0.1 (port 8123 or `SONOFF2HUE_DEV_PORT`), and refuses a port in use. `tests/tools/test_develop.py` loads that store with Home Assistant's own code, and checks the pins of `requirements_dev.txt` against its manifests |
| `tools/send-ewelink-announcements.py` | The simulated wall switch: the .NET repository's `.github/scripts/send-ewelink-announcements.py`, copied unchanged, so both repositories simulate presses the same way. ruff (with `force-exclude`) and mypy leave it out, as they would ask for changes to it |
| `scripts` | `setup` (the virtual environment) and `check` (lint, formatting, types, tests); `develop` (decision 18) runs a local Home Assistant with this checkout's integration from `.dev/venv` (`requirements_dev.txt`), and the fake Hue bridge and the simulated wall switch from `.venv`, with its commands `run`, `hass`, `fake-hue`, `setup`, `press` and `lights`; its state is in `.dev/`, which git ignores |
| `.github/scripts` | `check-commits.sh` (the .NET repository's, unchanged) and `test-each-commit.sh` |
| `.github/workflows` | `ci.yml` (*Lint*, *Test*) and `commits.yml` (per-commit checks, *All commits pass*) |
| `.github/workflows/validate.yml` | *hassfest* and *HACS*, the validation the Marketplace requires (decision 16) |
| `.vscode` | The VS Code debug setup (decision 18): `launch.json` runs Home Assistant and the fake Hue bridge under debugpy, alone or together; `tasks.json` runs `scripts/develop setup`, `press` and `lights`; `extensions.json` recommends the extensions. `.gitignore` keeps everything else in `.vscode/` out |

## Parity with the .NET app

The C# tests are ported as the executable form of this table, with recognizable names.

| Area | What stays exactly the same | .NET source |
|---|---|---|
| mDNS socket | IPv4 224.0.0.251:5353, bound to 0.0.0.0. Options: SO_REUSEADDR+SO_REUSEPORT, multicast TTL 255, loopback on. 64 KiB receive buffer. A malformed packet is dropped at trace level, and a failing handler is logged without stopping the loop | `Mdns/MdnsListener.cs` |
| Interfaces | Joins every interface that is up, multicast-capable and not loopback, on its IPv4 addresses. A failed join is skipped. With none joined, it fails, warns "mDNS socket error (…); retrying in 5s." and retries every 5 s | same |
| Network change | Address changes, debounced 3 s, close the socket and rejoin. Each (re)start resets the trackers and queries again | same |
| Queries | PTR `_ewelink._tcp.local` 0, 1 and 3 s after each (re)start (waits of 0, 1 and 2 s). The LAN monitor repeats it every 2 min after the third (123 s, 243 s, …). The runner repeats it every 30 s while a mapped switch is unseen, warning "Not seen on the LAN yet: …" | `Bridge/Sonoff2HueRunner.cs`, `Web/Monitoring/LanMonitor.cs` |
| Announcements | Responses only. TXT records with TTL > 0 named `*._ewelink._tcp.local` whose instance starts with `ewelink`. The id is TXT `id`, or the instance from character 8, lowercased. `seq` is a long. `data1`..`data4` are concatenated. Encrypted when `encrypt=true`. The address comes from SRV→A, or else the sender | `Ewelink/EwelinkAnnouncement.cs` |
| Rejoin | A query with authority records for `eWeLink_<id>…` (a probe) resets that device's baseline | same, `Ewelink/SwitchTracker.cs` |
| Press rule | Baseline / Duplicate / SequenceReset / RelayChanged / SettingsChanged / Press, decided as `PressDetector` does: its setting keys, relay parsing (`switches[]` and `switch`), the payload compare when `seq` is missing, and `relaySeparation` for detached mode | `Ewelink/PressDetector.cs` |
| Decryption | AES-128-CBC/PKCS7, key MD5(trimmed device key), 16-byte IV. The same error strings, e.g. "iv has N bytes instead of 16". Never raises | `Ewelink/EwelinkCrypto.cs` |
| Targets | Names or ids, case-insensitive, with an optional `room:`/`zone:` prefix. Exactly one match is required, and a group without `grouped_light` is an error. Duplicates are removed by (kind, id). The same messages, which list the known names in `StringComparer.OrdinalIgnoreCase` order (by code point) | `Bridge/TargetResolver.cs` |
| Toggle | If any target is on, all go off; otherwise all go on. It reads the light and/or grouped_light states, with one retry after 300 ms on a transient error. PUTs are sequential, targets already in the wanted state are skipped, and failures are counted per target | `Bridge/HueToggler.cs` |
| Runner | The names are resolved again every 10 s while the bridge's errors are transient; every name that doesn't resolve is listed. Toggles of one switch are serialized. A press that waited more than 5 s since it was received is dropped. Keep-alive `GET /clip/v2/resource/bridge` at once, then every 45 s. Reachability is reported on changes only. Stopping stops the queries, the keep-alive and every toggle, and waits for them (decision 14). The same log lines | `Bridge/Sonoff2HueRunner.cs`, `IRunnerObserver.cs` |
| HTTP | 5 s timeout, 4 s connect. Idle connections are kept 5 min by its own connector. Header `hue-application-key`, and no Accept, Accept-Encoding or User-Agent. Requests go to `Uri.IdnHost`, ids escaped as `Uri.EscapeDataString` does and dot segments removed. The pin is the SHA-256 of the leaf certificate's DER, normalized as `HueTls.Normalize` does. A 2xx with `errors[]` is a failure, and 401/403 means the key was rejected. Transient means no status, or 429/500/502/503/504. Answers are decoded as `ReadAsStringAsync` does (charset, byte order marks) and read as `JsonSerializer` with Web defaults reads `HueEnvelope`; a reason phrase the status line lacks is .NET's. Where .NET throws something other than `HueApiException` for a malformed answer, the port raises `UnexpectedError` with .NET's exception and message. The same messages | `Hue/HueClient.cs`, `Hue/HueTls.cs`, `Hue/HueModels.cs` |
| Supervisor | Its checks in .NET's order, each with its state, message and problems: not paired, no switch, `ValidateForRun`'s problems, a client that can't be made, then connecting. It pings the bridge every 10 s while its errors are transient, showing "Waiting for the Hue bridge at …: …" and warning once, then resolves the names, which retries every 10 s on the supervisor's logger. Names that aren't on the bridge, a rejected key and any other refusal wait for the next change; the unpinned-certificate warning comes before running. An unexpected stop is logged with its trace and shown as "The bridge stopped unexpectedly: …", and the bridge starts again 30 s later, as after a runner that returned. Any config change restarts it, through a reload. Stopping stops the runner and closes the client. The same messages, except where they said to pair the bridge (decision 21) | `Web/Management/BridgeSupervisor.cs` |
| Status | The service's state, message and problems, and "since", which moves only when one of them changes. Any state but running forgets the resolved targets, and running again with the same message changes them without a publish. The bridge's reachability, its message while unreachable and the time of each report, shown only while paired; a new configuration resets them only when the address, key or pin changes. Per switch, in the configuration's order: its LAN device (online for 5 min), the targets, and the last action and error, kept across configuration changes. Changes reach the listeners together, 500 ms after the first; a LAN device's only when it is a configured switch's | `Web/Monitoring/StatusTracker.cs` |
| Pairing | `GET /api/0/config` gives the bridge's id and the pin, read from the same connection, as `GetFromJsonAsync` reads it (keys without regard to case). `POST /api` with devicetype `sonoff2hue#<host up to its first dot, at most 19 letters and digits>` and `generateclientkey`, every 2 s for 60 s. Error 101 means keep waiting, and transient failures are retried; each attempt is reported, as the web UI showed it. After 60 s: "The link button was not pressed within 60 seconds. Try again." A changed certificate, or a refusal other than 101, aborts | `Hue/HueBridgeSetup.cs`, `Web/Management/HuePairing.cs` |
| Discovery | For 5 s, in parallel: mDNS `_hue._tcp.local` (queries at 0, 1 and 3 s) and `https://discovery.meethue.com/`. Deduplicated by IP and sorted ordinally, each with its source. The cloud's answer is read as `GetFromJsonAsync<JsonArray>` reads it; its failures are only logged at debug level, as .NET catches them, except an `ArgumentException`, which fails the discovery in both | `Hue/HueBridgeSetup.cs` |
| Bridge address | Accepted exactly when .NET's `Uri` reads `https://<address>/` as a host and a port, and normalized as it does (Research findings): IPv6 gets brackets, IPv4 forms such as `192.168.1` are written out, host names are lowercased only when they have an ASCII capital letter. A scheme, path, user info, query, fragment or spaces are rejected, with the same message | `Hue/HueBridgeAddress.cs` |
| Setup flow | The web UI's Hue bridge page and `hue-pair`: the 5 s search, the address check, reading the bridge with the same messages, `hue-pair`'s text about the bridge and the certificate to pin, the pairing's attempts as the web UI showed them ("The bridge did not answer (…); still trying."), its outcomes, messages and log lines, and Retry reading the bridge again. The bridge is saved as `HuePairing` saved it, the switches untouched | `Web/Management/HueEndpoints.cs`, `HuePairing.cs`, `Cli/Commands.cs` (`hue-pair`), the UI's `views/hue.js` |
| Fake Hue bridge | The same resources, ids, endpoints, answers and messages, the 30 s link-button window, the request and PUT logs, and the settings. Python adds `Fault`s for tests | `tests/local/FakeHue` |
| LAN registry | Per device: address, type, encrypted, seq, first and last seen, last event and its time, last press, detached, summary, decode error. A broadcast without an address, type or seq keeps the earlier one, and one that can't be decoded keeps the summary and detached mode; the decode error is always the last broadcast's. A repeat only moves the time last seen. Every broadcast but a repeat is reported, and a device's first. Ids match and sort as `OrdinalIgnoreCase` does. Online means heard within the last 5 min. The time of the last packet counts any packet | `Web/Monitoring/DeviceRegistry.cs` |
| LAN monitor | Runs as long as the integration, also before a switch is mapped, with a tracker of its own; a config change gives it a new one. The registry is listening while the listener runs, also while its socket retries. An unexpected stop of the listener is logged at error level as "The LAN monitor stopped; restarting it in 10 s.", and it starts again 10 s later | `Web/Monitoring/LanMonitor.cs` |
| Activity | The last 1,000 log entries: id, time, level, category (the logger's name after its last dot), message, and the exception as "Type: message", without the stack trace, which stays in the log. `get(after, limit)` gives the newest `limit` entries after `after`, oldest first | `Web/Monitoring/LogBuffer.cs` |
| Switch picker | A light is saved by its name if no other light has that name, else by its id; a room or zone as `room:<name>` or `zone:<name>` if its name is unique among its kind, else by its id. Entries that still resolve to a selected resource keep their spelling and are saved once; entries that don't resolve stay unless removed. Names match after JavaScript's `trim()` and `toLowerCase()`, not .NET's rules | the UI's `targets.js` |
| Switch form | Before the server sees it, with JavaScript's string rules: a device chosen or typed (and trimmed), of 4-40 ASCII letters and digits, not used by another switch; a name of at most 64 UTF-16 code units once trimmed; at least one target; a new key when replacing the current one. The dialog's messages, all at once. It sends the name or null, the key null to keep it, `""` to remove it, else trimmed, and typed entries split at commas. The server's errors are joined per field as the dialog showed them, the picker's field taking those of `targets`, `lights` and `groups` | the UI's `views/switcheditor.js`, `format.js`, `ui.js` |
| Switch validation | After trimming with .NET's `Trim()` and counting UTF-16 code units: a name of at most 64 characters, without control characters; an id of 4-40 ASCII letters and digits, lowercased with `ToLowerInvariant` first; a key of at most 128 characters, without white space or control characters. Each list holds at most 50 entries, counted before duplicates are removed without regard to case, each of 1-200 characters without control characters. At least one target. The same messages, in the same order, under the same keys (`name`, `deviceId`, `deviceKey`, `lights`, `groups`, `targets`). A new switch's device id must be free (409), and a changed switch must exist (404) and may not take another's id (409); both are checked before and again after the bridge is asked. The entries are checked against the bridge when it is paired and reachable, and only warned about otherwise. A key of null keeps the current one, `""` or white space removes it, and a changed device id drops a kept key | `Web/Management/ConfigEndpoints.cs`, the UI's `switcheditor.js` |
| YAML import | What `AppConfig.Parse` and YamlDotNet 18.1.0 accept and refuse, the values they read, and their messages, with line and column in UTF-16 code units: camelCase keys; an unknown key names its section and the valid keys; duplicate keys; `AppConfig`'s three messages for a value of the wrong kind; null sections, lists and values normalized, and an empty `switches` entry refused; scalars such as `1000e12345`, `yes` and `0x10` kept as written; a document of a single scalar read again; comments, styles, anchors, tags, directives, documents, a byte order mark, CRLF and tabs as YamlDotNet takes them (decision 15). `ValidateForRun` is `config.py`'s `validate_for_run` | `Configuration/AppConfig.cs` |

Deliberate differences, documented in the README:

- One UDP 5353 socket serves both the LAN monitor and the runner, where the .NET backend opens two. Each keeps its
  own tracker, reset as in .NET, so baselines and press decisions are the same.
- A config change reloads the whole config entry, the listener included. So every device re-baselines, as the .NET
  LAN monitor's new tracker does.
- Home Assistant's default log level hides info lines. The logbook, diagnostics and debug logging show them.
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
    duplicates included, so it would show the bridge's address for the switch after each press. Open for the LAN view.
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
    socket, another wildcard socket on the port.
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
| 19 | The listener keeps its own UDP 5353 socket (decision 8), with .NET's choice of interfaces: the mDNS spike confirmed it on the user's Home Assistant OS on 2026-11-25, so the fallback, a passive listener on Home Assistant's zeroconf, isn't needed. On Home Assistant OS the socket also joins Docker's bridges, `docker0` and `hassio`; on `hassio`, the Supervisor's multicast plugin repeats every packet from the LAN, and the tracker takes the copies as duplicates. The spike was a throwaway custom integration, `sonoff2hue_spike`, on the branch `tmp/haos-spike`, which never reached `main`; it ran the engine's own modules, copied unchanged. It had to be started from configuration.yaml: it was installed over SSH, in the Terminal & SSH app, which lacks `homeassistant_api`, so that app can't add a config entry through the Supervisor's proxy to Core's API. Open: the multicast plugin's copy of a press carries no address records, so its sender, the `hassio` bridge's address, becomes the switch's address in the LAN registry, as it would in .NET (Research findings) | On Home Assistant OS 18.4 with Core 2026.11.3, the socket bound at the first attempt next to Home Assistant's 3 zeroconf sockets and 10 of other processes, and in 3 minutes received the broadcasts of all 21 eWeLink devices and each of the 5 presses of the user's switch as a new `seq`, with no malformed packet, socket error or network change. Home Assistant's zeroconf kept receiving, with the same 21 devices, unicast replies still reach it, and SonoffLAN, which listens through it, wrote its entities' state within 5 s after every press. A listener on Home Assistant's zeroconf would also lose the probes, which reset a device's baseline: python-zeroconf hands its record listeners only the records of responses |
| 20 | The switch subentry flow keeps the .NET web UI's switch editor, whose rules are ported without Home Assistant imports. `picker.py` is the UI's `targets.js`: resolving `lights` and `groups` entries, the picker's selection and how it saves them, with JavaScript's string rules (`jstext.py`), as the browser ran them, and objects compared by identity. `tools/js_oracle` pins it to the real module: `picker_cases.py` builds the cases, `oracle.mjs` runs them with `targets.js` in Node and writes `tests/engine/fixtures/js_picker.json`, and `test_picker.py` and `test_jstext.py` replay it. After a change to the web UI, run `tools/js_oracle/generate.sh <checkout>` with Node 20 or later. `switch_rules.py` is the server's part, `ConfigEndpoints.cs`, with .NET's rules: `tools/dotnet_oracle` runs `SwitchValidation.Validate` and the endpoints that add and change a switch through reflection, the endpoints one step after the other on a config file of their own (sections `switchValidation` and `switchWrites`), rather than only porting `WebConfigTests`. The check against the bridge uses `targets.resolve`, a parameter that tests can replace, and takes the bridge as an async context manager (`connect`), so that the flow can pass a `HueClient`. The picker's resources keep the server's order, `OrderBy` with `OrdinalIgnoreCase` (`text.compare_ignore_case`); the browser's second sort by `localeCompare` isn't ported, which only changes the order in which new selections are saved. The message about a malformed device id names the made-up `1000abcdef`, where the app's names an id that could be one of the user's devices; the oracle replaces it the same way. `switch_form.py` is the dialog's own logic, read from `views/switcheditor.js`, which imports the DOM helpers and can't run outside a browser: its checks before sending, the body it sent, the key's modes and warning, identify-by-press, and how the server's errors map to its fields; `sameId` and `switchLabel` run in the JavaScript oracle. UI only, not ported: the dialog's elements, texts and focus, the inputs' maximum lengths, the order of the device list and of the picker's lists (the browser's collation), listing a device the LAN hasn't reported, and the answers to a stale ETag (412) or a deleted switch (404), which Home Assistant handles | The flow must store and check switches as the web UI did. The browser matched names with `trim()` and `toLowerCase()`, which differ from .NET's rules and from Python's (Research findings), so the picker resolves some entries that the server's check doesn't, and the reverse. CI has neither Node nor .NET, so the fixture is committed, as the .NET oracle's are. `str.lower()` stands for `toLowerCase()`: with Unicode 16.0 on both sides they agree for every code point, and the test fails if the fixture's Unicode version isn't Python's. Through reflection the oracle checks the order of the checks, their messages and the device key's rules against the app itself; its bridges are never reachable (not paired, or settings that can't be used), so it needs no network, and the errors for entries that don't resolve are `TargetResolver`'s, which the runner's port checks. This repository is public |
| 21 | The bridge supervisor and the status tracker are ported as `engine/supervisor.py` and `engine/status.py` (layout). `BridgeSupervisor(config, listener, status, *, hue_clients, on_key_rejected, clock)` runs one `EngineConfig` until it is cancelled: .NET's `ConfigStore` restarted the run after every change, and Home Assistant reloads the config entry instead, which cancels the supervisor and starts one for the new configuration. Its `config_changed()` logs "Configuration changed; restarting the bridge." where .NET did, so not during the 30 s wait after an unexpected stop; the runtime calls it from the entry's update listener, before the reload. .NET's load error ("The config file can't be read.") has no counterpart, and `hue_clients` is `HueClientFactory`. The runner runs on the shared listener with the supervisor's clock: stopping the supervisor stops the runner and closes the client while the listener goes on, and a failing listener is the LAN monitor's to restart (decision 17), where .NET's supervisor restarted the runner, which had a listener of its own, 30 s later. A rejected key is one callback for the runtime, `on_key_rejected`, which starts Home Assistant's reauthentication. It covers both cases: the supervisor stopping in the error state on a 401 or 403, and, while the runner runs, each keep-alive request or toggle that a 401 or 403 ends (decision 14), where the bridge goes on running and is shown as unreachable, as in .NET. It runs on the event loop (`call_soon`), so one that raises reaches the loop's exception handler instead of the supervisor or the runner. The runner's observer is then a forwarder in the supervisor, which passes every report to the tracker and a rejected key to the hook as well; a test checks that it forwards each `RunnerObserver` method, and a tracker that raised would be logged as observer `_Observer`, where .NET named `StatusTracker`. Deliberate message differences, with `PAIR_AGAIN`, as reconfiguring pairs again (decision 5): "The Hue bridge no longer accepts sonoff2hue's application key. Reconfigure the sonoff2hue integration to pair the bridge again."; the warning "hue.certificateSha256 is not set, so the bridge certificate is not verified. Reconfigure the sonoff2hue integration to pair the bridge again and pin it."; and "Pair the Hue bridge to get started: reconfigure the sonoff2hue integration.", which Home Assistant's flows give no reason to show. "Add a switch: pick a MINIR4M and the Hue lights it should toggle." stays: adding a switch is the switch subentry's own action. The `StatusTracker(config, registry, clock)` is the runner's observer. Its `StatusSnapshot` is .NET's `StatusDto` without the version, which is the manifest's, and the config and password files' paths, which were the web interface's; listeners replace the `EventHub`. `ServiceState`'s values are the status sensor's options: `not_configured`, `invalid_config`, `connecting`, `running` and `error`. It keeps .NET's debounce: listeners get one snapshot 500 ms after the first change, with every change until then (`loop.call_later`), and one that raises stops the rest and reaches the loop's exception handler. `close()` drops a pending snapshot and leaves the registry. `config_changed(config)` ports `OnConfigChanged`, for a runtime that keeps the tracker and its registry across a reload; a tracker made at each setup starts as .NET's did, connecting with "Starting.", and without the last actions and errors that .NET kept across changes. It is used on the event loop only, so it has no lock, and an injected clock replaces `TimeProvider` | Every change applies through a reload (decision 5), so the supervisor needs no change token, and each run checks the whole configuration from the start, as .NET's did, so the states and messages come out the same; only the update listener knows that a reload is for a change. A callback is explicit and comes at once, and Home Assistant's `async_start_reauth` does nothing while a reauth or reconfigure flow of the entry runs, so the hook may come again, after every refused request and after a restart; a state to watch would be known only by its message, and would come 500 ms late. The messages name the Home Assistant equivalent of the .NET app's command line and pages (How to work). The supervisor and the runner report one event in several calls, such as the bridge's reachability and then the state, and the debounce hands the entities one consistent snapshot of them, as the dashboard got it, while it keeps Home Assistant's state writes, which the recorder stores, to two a second. The sensor's options double as its translation keys |
| 22 | The setup flow, reconfigure and reauth (`config_flow.py`), in place of the skeleton's placeholder. **Steps:** `user` is a menu, *Find bridges* or *Enter the bridge address* (`SETUP_MENU`, which the import of a `sonoff2hue.yaml` will join), and `reconfigure` the same two. `discover` is a progress step that runs the engine's `discover` for 5 s, with Home Assistant's shared aiohttp session for the cloud. It leads to `pick`, a list of `IP — bridge id — source` (leaving out a missing id) and *Another address*, which preselects the first bridge, or when reconfiguring the paired one if it was found again (by bridge id, without regard to case); to `no_bridges`, the web UI's message with *Search again* or the address; or to `discovery_failed`, with the error. `address` trims the input as .NET's `Trim` does and checks it with `hue_address`, whose message goes on the field; when reconfiguring it suggests the stored address. Then the bridge is read (`get_bridge_info`): `cannot_connect` for a `HueApiError`, `invalid_address` for a `ConfigError`, `unexpected` for an `UnexpectedError`, which is logged with its traceback. The flow's unique id becomes the bridge id, so another flow with it is aborted (`already_in_progress`), and reauth aborts with `wrong_bridge` for another bridge id. `link` is a progress step with `hue-pair`'s text about the bridge (name, model, bridge id, firmware, address) and the certificate to pin, and "Press the round link button on top of the bridge now. Waiting up to 60 seconds..."; it runs the engine's `pair`, with `HuePairing`'s log lines. It ends in `paired`, `link_timeout` (`PairingTimeoutError`'s message) or `link_failed` (the message of a `HueApiError` or `ConfigError`, else "Pairing failed: …"); these two menus offer *Retry*, which reads the bridge at the same address again as the web UI's Retry did, and *Choose another bridge* (the first menu), and in reauth only *Retry*. `reauth` asks for a confirmation (`reauth_confirm`) before it reads the stored bridge and links. **Data:** the entry's `data` is `bridge_ip` (the address entered or chosen, trimmed), `bridge_id`, `app_key` and `certificate_sha256`, the field names of the engine's `HueSettings` (`CONF_*` in `const.py`); its unique id is the bridge id and its title `sonoff2hue` (`ENTRY_TITLE`). A switch is a subentry of type `switch` (`SUBENTRY_SWITCH`): its unique id is `SwitchSettings.normalized_device_id`, its title its name, or that id without one, and its `data` holds `device_id`, `name`, `device_key`, `lights` and `groups` (`SwitchSettings`' names) with the values as given. The flow doesn't declare the type yet (Research findings). Reconfigure saves with `async_update_reload_and_abort` (the unique id and `data_updates`), so the switches stay; reauth the same, without the unique id. **Messages:** errors, aborts, menus and progress texts are translation keys, and where .NET shows a dynamic message, the engine's exact text is the placeholder `{error}`. **Progress:** the text changes whenever an attempt's outcome does, by `progress_action` (`link` or `link_retrying`) and `async_notify_flow_changed()`: "The bridge did not answer (…); still trying." while the bridge doesn't answer, nothing more while it waits for the button. There is no countdown. Tasks start after their step returns (`eager_start=False`). **Resources:** `async_remove` cancels the search and the pairing, whose connections and mDNS socket close as they stop, and a flow closed while the bridge is read starts no pairing (`UnknownFlow`). **Deliberate differences from the web UI:** closing the dialog stops the pairing, which ran on for 60 s on the server and survived a reload of the page; instead of one pairing at a time, a second flow for the same bridge is aborted, and the flow that adds the entry aborts the others; there is no countdown; there is no *Forget bridge* (deleting the entry deletes the switches too, and reconfigure replaces the bridge); the paired bridge is preselected instead of marked *Current*; an unexpected failure shows its own message, where the web UI showed a 500's; the search's hint says "the same network as Home Assistant"; the timeout's log line ends "Start the pairing again in the sonoff2hue setup." | The flow does what the web UI's Hue bridge page and `hue-pair` did, with their rules and words, in Home Assistant's own dialogs. The title: the entry stands for the whole setup, one bridge and its switches, as the .NET app's config file did, and there is only one; an imported `sonoff2hue.yaml` names no bridge (`HueSettings` has no name) and may be imported while the bridge can't be reached; and reconfigure can move it to another bridge without renaming it or overwriting a title the user chose. The bridge's name, model and firmware are shown while linking, and belong to the bridge's device. Reauth asks first because Home Assistant runs its first step as soon as the runtime starts it, and the 60 seconds must run while the user is at the bridge. A progress step can't count down without a timer of its own, and `async_update_progress` shows a percentage, which reads as work done rather than time left. A step still reading the bridge when its flow is removed would start a pairing that Home Assistant never cancels, and an eager task would run, or end, inside the step that starts it |

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
- [ ] Home Assistant layer: skeleton (done), runtime, setup flow with reconfigure and reauth (done), import flow, switch
  subentries, entities, diagnostics, logbook
- [ ] Development tooling (done) and end-to-end CI with a real Home Assistant, the fake bridge and a simulated switch
- [ ] README for the Marketplace
- [ ] Live test on the user's Home Assistant OS
- [ ] Release 1.0.0
