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
  (`git push origin <sha>:main`), which keeps the commit hashes; GitHub then marks the pull request as merged.
- CI (`.github/workflows/ci.yml`) runs *Lint* (ruff, the formatter, mypy) and *Test* (pytest with coverage) on `main`
  and on the merge result of each pull request. *Commits* (`commits.yml`) checks every commit of a pull request on its
  own: the message rules and `git diff --check`, then `scripts/check` per commit, summed up by *All commits pass*. Both
  install uv 0.13.7 with `astral-sh/setup-uv@v10.4.0` (it publishes only full-version tags) and run `scripts/setup`,
  with uv's cache keyed on `requirements_test.txt`.

| Folder | Contents |
|--------|----------|
| `custom_components/sonoff2hue` | The integration |
| `custom_components/sonoff2hue/engine` | Ports of the .NET engine, without Home Assistant imports. `text.py` holds the .NET string and number rules (trim, ordinal case-insensitive comparison, `ToLowerInvariant`, `TryParse`); `dns.py` is `Mdns/DnsMessage.cs` |
| `tests/engine` | Tests of the engine, ported from the .NET tests. `packets.py` builds raw DNS packets and MINIR4M broadcasts (the .NET tests' `TestPackets.cs`) |
| `scripts` | `setup` (the virtual environment) and `check` (lint, formatting, types, tests) |
| `.github/scripts` | `check-commits.sh` (the .NET repository's, unchanged) and `test-each-commit.sh` |
| `.github/workflows` | `ci.yml` (*Lint*, *Test*) and `commits.yml` (per-commit checks, *All commits pass*) |

## Parity with the .NET app

The C# tests are ported as the executable form of this table, with recognizable names.

| Area | What stays exactly the same | .NET source |
|---|---|---|
| mDNS socket | IPv4 224.0.0.251:5353, bound to 0.0.0.0. Options: SO_REUSEADDR+SO_REUSEPORT, multicast TTL 255, loopback on. 64 KiB receive buffer. A malformed packet is dropped at trace level, and a failing handler is logged without stopping the loop | `Mdns/MdnsListener.cs` |
| Interfaces | Joins every interface that is up, multicast-capable and not loopback, on its IPv4 addresses. A failed join is skipped. With none joined, it fails, warns "mDNS socket error (…); retrying in 5s." and retries every 5 s | same |
| Network change | Address changes, debounced 3 s, close the socket and rejoin. Each (re)start resets the trackers and queries again | same |
| Queries | PTR `_ewelink._tcp.local` 0, 1 and 2 s after each (re)start. The LAN monitor repeats it every 2 min. The runner repeats it every 30 s while a mapped switch is unseen, warning "Not seen on the LAN yet: …" | `Bridge/Sonoff2HueRunner.cs`, `Web/Monitoring/LanMonitor.cs` |
| Announcements | Responses only. TXT records with TTL > 0 named `*._ewelink._tcp.local` whose instance starts with `ewelink`. The id is TXT `id`, or the instance from character 8, lowercased. `seq` is a long. `data1`..`data4` are concatenated. Encrypted when `encrypt=true`. The address comes from SRV→A, or else the sender | `Ewelink/EwelinkAnnouncement.cs` |
| Rejoin | A query with authority records for `eWeLink_<id>…` (a probe) resets that device's baseline | same, `Ewelink/SwitchTracker.cs` |
| Press rule | Baseline / Duplicate / SequenceReset / RelayChanged / SettingsChanged / Press, decided as `PressDetector` does: its setting keys, relay parsing (`switches[]` and `switch`), the payload compare when `seq` is missing, and `relaySeparation` for detached mode | `Ewelink/PressDetector.cs` |
| Decryption | AES-128-CBC/PKCS7, key MD5(trimmed device key), 16-byte IV. The same error strings, e.g. "iv has N bytes instead of 16". Never raises | `Ewelink/EwelinkCrypto.cs` |
| Targets | Names or ids, case-insensitive, with an optional `room:`/`zone:` prefix. Exactly one match is required, and a group without `grouped_light` is an error. Duplicates are removed by (kind, id). The same messages | `Bridge/TargetResolver.cs` |
| Toggle | If any target is on, all go off; otherwise all go on. It reads the light and/or grouped_light states, with one retry after 300 ms on a transient error. PUTs are sequential, targets already in the wanted state are skipped, and failures are counted per target | `Bridge/HueToggler.cs` |
| Runner | Toggles of one switch are serialized. A press that waited more than 5 s since it was received is dropped. Keep-alive `GET /clip/v2/resource/bridge` at once, then every 45 s. Reachability is reported on changes only. Stopping waits for the socket, the keep-alive and every toggle. The same log lines | `Bridge/Sonoff2HueRunner.cs` |
| HTTP | 5 s timeout, 4 s connect. Idle connections are kept 5 min by its own connector. Header `hue-application-key`. The pin is the SHA-256 of the leaf certificate's DER. A 2xx with `errors[]` is a failure, and 401/403 means the key was rejected. Transient means no status, or 429/500/502/503/504 | `Hue/HueClient.cs`, `Hue/HueTls.cs`, `Hue/HueModels.cs` |
| Supervisor | The same states and messages. It retries the bridge every 10 s and restarts 30 s after an unexpected stop. Any config change restarts it | `Web/Management/BridgeSupervisor.cs` |
| Pairing | `GET /api/0/config` gives the bridge's id and the pin, read from the same connection. `POST /api` with devicetype `sonoff2hue#<host, at most 19 letters and digits>` and `generateclientkey`, every 2 s for 60 s. Error 101 means keep waiting, and transient failures are retried; the last error goes into the timeout message. A changed certificate, or a refusal other than 101, aborts | `Hue/HueBridgeSetup.cs`, `Cli/Commands.cs`, `Web/Management/HuePairing.cs` |
| Discovery | For 5 s, in parallel: mDNS `_hue._tcp.local` (queries at 0, 1 and 2 s) and `https://discovery.meethue.com/`. Deduplicated by IP and sorted, each with its source | `Hue/HueBridgeSetup.cs` |
| Bridge address | host[:port]. IPv6 gets brackets. A scheme, path, user info, query, fragment or spaces are rejected, with the same message | `Hue/HueBridgeAddress.cs` |
| LAN registry | Per device: address, type, encrypted, seq, first and last seen, last event and its time, last press, detached, summary, decode error. Online means heard within the last 5 min | `Web/Monitoring/DeviceRegistry.cs` |
| Switch validation | Name of at most 64 characters, without control characters. Id of 4-40 ASCII letters and digits, lowercased. Key of at most 128 characters, without whitespace or control characters. Each list holds at most 50 entries of 1-200 characters, with case-insensitive duplicates removed. At least one target, and a unique device id. Targets are checked against the bridge when it can be reached, and only warned about otherwise | `Web/Management/ConfigEndpoints.cs`, the UI's `switcheditor.js` |
| YAML import | `AppConfig.Parse` and `ValidateForRun`: camelCase keys; unknown and duplicate keys are errors, with line and column; empty values are normalized | `Configuration/AppConfig.cs` |

Deliberate differences, documented in the README:

- One UDP 5353 socket serves both the LAN monitor and the runner, where the .NET backend opens two. Each keeps its
  own tracker, reset as in .NET, so baselines and press decisions are the same.
- A config change reloads the whole config entry, the listener included. So every device re-baselines, as the .NET
  LAN monitor's new tracker does.
- Home Assistant's default log level hides info lines. The logbook, diagnostics and debug logging show them.
- There is still exactly one bridge (`single_config_entry`).

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
- **HTTP.** aiohttp's `TCPConnector` closes idle connections after 15 s by default (`keepalive_timeout`), and Home
  Assistant's shared connector (`HomeAssistantTCPConnector`) keeps that default. A 45 s keep-alive therefore needs a
  connector of its own, with a longer `keepalive_timeout`, to keep the TLS connection to the bridge warm.
  `aiohttp.Fingerprint` takes a SHA-256 digest of the leaf certificate's DER (`getpeercert(binary_form=True)`), the
  value the .NET app pins (`GetRawCertData`). It turns off certificate validation and checks the digest instead.

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

## Status

- [x] Repository started: the license, this memory, the tools (`scripts/setup`, `scripts/check`) and the commit checks
- [ ] CI: lint, tests and per-commit checks (done); hassfest and HACS validation once the manifest exists; a ruleset
  on `main` once the checks have passed there
- [ ] mDNS spike on the user's Home Assistant OS: own UDP 5353 socket next to Home Assistant's zeroconf
- [ ] Engine port with its tests: DNS (done), eWeLink and press detection, mDNS listener, fake Hue bridge, Hue client
  and pairing, runner, YAML import
- [ ] Home Assistant layer: skeleton, runtime, setup and import flow, switch subentries, entities, diagnostics,
  logbook
- [ ] Development tooling and end-to-end CI with a real Home Assistant, the fake bridge and a simulated switch
- [ ] README for the Marketplace
- [ ] Live test on the user's Home Assistant OS
- [ ] Release 1.0.0
