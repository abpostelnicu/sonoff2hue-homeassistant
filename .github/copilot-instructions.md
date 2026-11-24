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
  (`git push origin <sha>:main`), which keeps the commit hashes; GitHub then marks the pull request as merged. A
  ruleset on `main` (id 28003079) requires *Lint*, *Test* and *All commits pass*; repository admins can bypass it. It
  matches the checks by their job names, so rename such a job only together with the ruleset.
- CI (`.github/workflows/ci.yml`) runs *Lint* (ruff, the formatter, mypy) and *Test* (pytest with coverage) on `main`
  and on the merge result of each pull request. *Commits* (`commits.yml`) checks every commit of a pull request on its
  own: the message rules and `git diff --check`, then `scripts/check` per commit, summed up by *All commits pass*. Both
  install uv 0.13.7 with `astral-sh/setup-uv@v10.4.0` (it publishes only full-version tags) and run `scripts/setup`,
  with uv's cache keyed on `requirements_test.txt`.

| Folder | Contents |
|--------|----------|
| `custom_components/sonoff2hue` | The integration |
| `custom_components/sonoff2hue/engine` | Ports of the .NET engine, without Home Assistant imports. `text.py` holds the .NET string and number rules (trim, ordinal case-insensitive comparison and ordering, `ToLowerInvariant`, `TryParse`). `dns.py` is `Mdns/DnsMessage.cs`; `mdns.py` is `Mdns/MdnsListener.cs` with the parts of .NET's `NetworkInterface` and `NetworkChange` it uses; `ewelink.py` is `Ewelink/EwelinkAnnouncement.cs` and `EwelinkCrypto.cs`; `press.py` is `Ewelink/PressDetector.cs` and `SwitchTracker.cs`; `config.py` is the model of `Configuration/AppConfig.cs`; `jsonnode.py` reads JSON as System.Text.Json's `JsonNode` does. `hue_address.py` is `Hue/HueBridgeAddress.cs` with the parts of .NET's `Uri`, `IPAddress` and `IdnMapping` it relies on; `hue_client.py` is `Hue/HueClient.cs`, `HueTls.cs` and `HueModels.cs`; `hue_setup.py` is `Hue/HueBridgeSetup.cs` with the pairing loop of `Web/Management/HuePairing.cs`; `errors.py` holds `UnexpectedError` (decision 9). `targets.py` is `Bridge/TargetResolver.cs`; `toggler.py` is `Bridge/HueToggler.cs`; `runner.py` is `Bridge/Sonoff2HueRunner.cs` and `IRunnerObserver.cs` (decision 14) |
| `tests/engine` | Tests of the engine, ported from the .NET tests. `packets.py` builds raw DNS packets and MINIR4M broadcasts (the .NET tests' `TestPackets.cs`) and `MdnsPacket`s; `fakes.py` has fake multicast transports for `MdnsListener` |
| `tests` | `hue.py` runs the fake Hue bridge on 127.0.0.1 with a manual clock (the .NET tests' `FakeHueTestHost`), and has `InMemoryBridge`, which answers and records requests without sockets (the .NET tests' `FakeHttpHandler`); `conftest.py` has its fixtures, `fake_hue` and `fake_hue_pressed`, which enable sockets |
| `tests/tools` | Tests of the fake Hue bridge (the .NET app's `Sonoff2Hue.FakeHue.Tests`) |
| `tests/engine/fixtures` | `dotnet_text.json` and `dotnet_engine.json`, made by `tools/dotnet_oracle` from .NET and from the .NET app's own code (decision 7) |
| `tools/fake_hue` | The fake Hue bridge, a port of the .NET app's `tests/local/FakeHue` (decision 12): `bridge.py` (state, `Fault`s), `server.py` (endpoints, HTTPS, certificate), `__main__.py` (`python -m tools.fake_hue`, settings `FAKE_HUE_BIND`, `FAKE_HUE_PORT`, `FAKE_HUE_LINK_BUTTON`) |
| `tools/dotnet_oracle` | The generator of those fixtures: a .NET 10 console app (`Program.cs`, `Engine.cs`) that references the app's `Sonoff2Hue.csproj`, the cases it evaluates (`engine_cases.py`) and `generate.sh` |
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
| Queries | PTR `_ewelink._tcp.local` 0, 1 and 3 s after each (re)start (waits of 0, 1 and 2 s). The LAN monitor repeats it every 2 min. The runner repeats it every 30 s while a mapped switch is unseen, warning "Not seen on the LAN yet: …" | `Bridge/Sonoff2HueRunner.cs`, `Web/Monitoring/LanMonitor.cs` |
| Announcements | Responses only. TXT records with TTL > 0 named `*._ewelink._tcp.local` whose instance starts with `ewelink`. The id is TXT `id`, or the instance from character 8, lowercased. `seq` is a long. `data1`..`data4` are concatenated. Encrypted when `encrypt=true`. The address comes from SRV→A, or else the sender | `Ewelink/EwelinkAnnouncement.cs` |
| Rejoin | A query with authority records for `eWeLink_<id>…` (a probe) resets that device's baseline | same, `Ewelink/SwitchTracker.cs` |
| Press rule | Baseline / Duplicate / SequenceReset / RelayChanged / SettingsChanged / Press, decided as `PressDetector` does: its setting keys, relay parsing (`switches[]` and `switch`), the payload compare when `seq` is missing, and `relaySeparation` for detached mode | `Ewelink/PressDetector.cs` |
| Decryption | AES-128-CBC/PKCS7, key MD5(trimmed device key), 16-byte IV. The same error strings, e.g. "iv has N bytes instead of 16". Never raises | `Ewelink/EwelinkCrypto.cs` |
| Targets | Names or ids, case-insensitive, with an optional `room:`/`zone:` prefix. Exactly one match is required, and a group without `grouped_light` is an error. Duplicates are removed by (kind, id). The same messages, which list the known names in `StringComparer.OrdinalIgnoreCase` order (by code point) | `Bridge/TargetResolver.cs` |
| Toggle | If any target is on, all go off; otherwise all go on. It reads the light and/or grouped_light states, with one retry after 300 ms on a transient error. PUTs are sequential, targets already in the wanted state are skipped, and failures are counted per target | `Bridge/HueToggler.cs` |
| Runner | The names are resolved again every 10 s while the bridge's errors are transient; every name that doesn't resolve is listed. Toggles of one switch are serialized. A press that waited more than 5 s since it was received is dropped. Keep-alive `GET /clip/v2/resource/bridge` at once, then every 45 s. Reachability is reported on changes only. Stopping stops the queries, the keep-alive and every toggle, and waits for them (decision 14). The same log lines | `Bridge/Sonoff2HueRunner.cs`, `IRunnerObserver.cs` |
| HTTP | 5 s timeout, 4 s connect. Idle connections are kept 5 min by its own connector. Header `hue-application-key`, and no Accept, Accept-Encoding or User-Agent. Requests go to `Uri.IdnHost`, ids escaped as `Uri.EscapeDataString` does and dot segments removed. The pin is the SHA-256 of the leaf certificate's DER, normalized as `HueTls.Normalize` does. A 2xx with `errors[]` is a failure, and 401/403 means the key was rejected. Transient means no status, or 429/500/502/503/504. Answers are decoded as `ReadAsStringAsync` does (charset, byte order marks) and read as `JsonSerializer` with Web defaults reads `HueEnvelope`; a reason phrase the status line lacks is .NET's. Where .NET throws something other than `HueApiException` for a malformed answer, the port raises `UnexpectedError` with .NET's exception and message. The same messages | `Hue/HueClient.cs`, `Hue/HueTls.cs`, `Hue/HueModels.cs` |
| Supervisor | The same states and messages. It retries the bridge every 10 s and restarts 30 s after an unexpected stop. Any config change restarts it | `Web/Management/BridgeSupervisor.cs` |
| Pairing | `GET /api/0/config` gives the bridge's id and the pin, read from the same connection, as `GetFromJsonAsync` reads it (keys without regard to case). `POST /api` with devicetype `sonoff2hue#<host up to its first dot, at most 19 letters and digits>` and `generateclientkey`, every 2 s for 60 s. Error 101 means keep waiting, and transient failures are retried; each attempt is reported, as the web UI showed it. After 60 s: "The link button was not pressed within 60 seconds. Try again." A changed certificate, or a refusal other than 101, aborts | `Hue/HueBridgeSetup.cs`, `Web/Management/HuePairing.cs` |
| Discovery | For 5 s, in parallel: mDNS `_hue._tcp.local` (queries at 0, 1 and 3 s) and `https://discovery.meethue.com/`. Deduplicated by IP and sorted ordinally, each with its source. The cloud's answer is read as `GetFromJsonAsync<JsonArray>` reads it; its failures are only logged at debug level, as .NET catches them, except an `ArgumentException`, which fails the discovery in both | `Hue/HueBridgeSetup.cs` |
| Bridge address | Accepted exactly when .NET's `Uri` reads `https://<address>/` as a host and a port, and normalized as it does (Research findings): IPv6 gets brackets, IPv4 forms such as `192.168.1` are written out, host names are lowercased only when they have an ASCII capital letter. A scheme, path, user info, query, fragment or spaces are rejected, with the same message | `Hue/HueBridgeAddress.cs` |
| Fake Hue bridge | The same resources, ids, endpoints, answers and messages, the 30 s link-button window, the request and PUT logs, and the settings. Python adds `Fault`s for tests | `tests/local/FakeHue` |
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
- Where .NET's message comes from its JSON parser, such as for a bridge whose config isn't JSON, the text after "No Hue
  bridge answered at …:" is Python's. `JsonException` names a path, line and byte position that Python's parser
  doesn't.

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
| 8 | The listener is `engine/mdns.py`, on asyncio. It owns its UDP 5353 socket (SO_REUSEADDR, SO_REUSEPORT, TTL 255, loopback), joins 224.0.0.251 on the interfaces that .NET would choose (`interface_states`: SIOCGIFFLAGS, SIOCGIFHWADDR and ETHTOOL_GLINK ioctls, with the IPv4 addresses from `ifaddr`), and watches IPv4 address changes on a netlink socket of its own, debounced 3 s, with polling outside Linux. One listener per config entry serves both the LAN monitor and the runner: each adds a packet handler and a started callback, which runs at once if the socket is open, as each had a listener of its own in .NET. Removing a started callback is awaited: it also cancels the callback if it is running and waits for it, so the runner can stop and start again, as the supervisor restarts it, while the listener and the LAN monitor go on. Sockets are behind a small transport interface, so the tests drive joins, errors and datagrams without the network | It keeps decision 3 of the .NET app (no mDNS library): the engine needs the raw TXT updates and the probes, which python-zeroconf's service browser hides. One socket sends and receives the same packets as the .NET backend's two did, and logs a socket error once instead of twice. Whether it binds UDP 5353 next to Home Assistant's own zeroconf on Home Assistant OS is the spike's question |
| 9 | Where the .NET app's code throws an exception it doesn't catch as an expected failure (`NullReferenceException`, `ArgumentException`, `InvalidOperationException` and the like), the port raises `errors.UnexpectedError`, which names .NET's exception and has its message; `jsonnode.JsonAccessError` is one. Lights and groups keep a null id or name where .NET's do | Callers then fail where the app failed: a toggle that `HueToggler` doesn't count as failed, pairing that ends with "Pairing failed: …", discovery that fails as a whole. Python's own exceptions in their place would differ in type and message |
| 10 | `hue_address.py` ports `HueBridgeAddress` with the parts of `Uri`, `IPAddress.TryParse` and invariant `IdnMapping` it relies on, function by function and on UTF-16 code units, instead of approximating them with `ipaddress` and Python's IDNA codec | The address is typed by the user, stored, and used for every request. The oracle's first 285 addresses showed that Python's libraries differ in a dozen places (`0x`, `+1.1.1.1`, `@bridge`, zones, `::ffff:` forms, case, IDNA 2003); the literal port matched all of them on its first run |
| 11 | The engine's `pair` asks for a key every 2 s until 60 s have passed, as `HuePairing` does, reports each attempt that didn't pair (`on_attempt`), and ends with the web UI's message, not the command line's | The setup flow replaces the web UI's pairing page, whose message it shows while waiting |
| 12 | `tools/fake_hue` ports the .NET app's fake bridge, with its ids, endpoints, answers, messages and settings, and adds `Fault`s (a status, body or delay for matching requests) for tests. `tests/hue.py` runs it on 127.0.0.1 over HTTPS with a certificate made at every start, and a manual clock | The client is tested over real HTTPS with a pinned certificate, which the .NET tests did in memory. Development and end-to-end CI need the same bridge without hardware |
| 13 | From 2026-11-24 the remaining work runs in parallel, one agent per task: each works in its own git worktree, `.worktrees/<task>` (in `.git/info/exclude`), on its own branch from `main`, and opens a pull request without merging it. The coordinating session reviews the pull requests, merges them by fast-forwarding `main` one at a time, and asks the others to rebase. Decision numbers are reserved per task: 14 the runner, 15 the YAML import, 16 the Home Assistant skeleton, 17 the LAN monitor and device registry, 18 development tooling, 19 the mDNS spike. In shared files each agent changes only its own lines (its table rows, its verification entry, its status item); the oracle's fixture is regenerated after a rebase, never merged by hand | The user asked to split the work across agents working in parallel. Separate worktrees let them build, test and commit at the same time without picking up each other's edits, as in the .NET repository's decision 30 |
| 14 | `engine/runner.py` ports `Sonoff2HueRunner` and `IRunnerObserver` on asyncio. The observer is a base class, `RunnerObserver`, whose methods do nothing, like the C# interface's default methods; one that raises is logged and ignored. The runner opens no socket: `run(listener)` adds a packet handler and a started callback to the config entry's `MdnsListener` (decision 8) and sends the keep-alive at once. When cancelled, it removes the handler and cancels the keep-alive and the toggles first, then awaits the started callback's remover and them, as cancelling the .NET runner's token stops everything before anything is awaited. Toggles are tasks behind a per-switch `asyncio.Lock`, which queues in order like `SemaphoreSlim`. `stop_toggles()` stands for the token that the .NET tests pass to `HandlePacket`, and `when_toggles_complete()` waits with `asyncio.wait`, so cancelling the wait doesn't stop them. The age of a press is measured with an injected clock (`datetime.now(UTC)` by default), the time a toggle took with `time.perf_counter`, as with .NET's `Stopwatch`. `resolve_bindings` takes a logger, as the .NET method does. Deliberate differences: "Listening for wall switch presses." drops "Press Ctrl+C to stop.", and a toggle starts on the event loop's next turn instead of inside `HandlePacket`, which can change only the order of the log lines of one packet | The listener is shared, and the runner has to stop and start again, as the supervisor restarts it, while the LAN monitor goes on. Python has no cancellation tokens, and `asyncio.gather` would cancel the toggles when its caller is cancelled, where .NET's `WhenAll` doesn't. The injected clock tests the 5 s rule without waiting |

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

## Status

- [x] Repository started: the license, this memory, the tools (`scripts/setup`, `scripts/check`) and the commit checks
- [ ] CI: lint, tests and per-commit checks, required for `main` by ruleset 28003079 (done); hassfest and HACS
  validation once the manifest exists, then required too
- [ ] mDNS spike on the user's Home Assistant OS: own UDP 5353 socket next to Home Assistant's zeroconf
- [ ] Engine port with its tests: DNS, eWeLink with press detection, the mDNS listener, the Hue client and pairing,
  the fake Hue bridge, the runner (done), YAML import
- [ ] Home Assistant layer: skeleton, runtime, setup and import flow, switch subentries, entities, diagnostics,
  logbook
- [ ] Development tooling and end-to-end CI with a real Home Assistant, the fake bridge and a simulated switch
- [ ] README for the Marketplace
- [ ] Live test on the user's Home Assistant OS
- [ ] Release 1.0.0
