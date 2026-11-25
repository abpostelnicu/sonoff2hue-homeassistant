# sonoff2hue for Home Assistant

Toggle Philips Hue lights from Sonoff **MINIR4M** wall switches, locally over your Wi-Fi LAN, with a Home Assistant
integration. It needs no Matter and no cloud.

```
wall switch ──S1/S2──> MINIR4M ──mDNS broadcast──> Home Assistant (sonoff2hue) ──HTTPS (CLIP v2)──> Hue Bridge ──> lights
               (detached relay mode)     (LAN)
```

- Each MINIR4M can control any number of Hue lights, rooms and zones.
- Toggle rule: if any of a switch's lights is on, all of them are turned off; otherwise all are turned on.
- It works with both rocker and push-button wall switches. Every operation of the switch toggles.

**Status: in development.** This integration ports the sonoff2hue app, a .NET service, to Home Assistant 2026.11 and
later, with the same behavior. It isn't ready for use yet.

## Development

Run `scripts/setup` once: it makes `.venv` with Python 3.14 and the pinned tools, with [uv](https://docs.astral.sh/uv/).
Then `scripts/check` runs what CI runs: ruff, its formatter, mypy and the tests.

### Running Home Assistant locally

`scripts/develop` runs a local Home Assistant 2026.11 with the integration from this checkout, and a fake Hue bridge
(`tools/fake_hue`) and a simulated wall switch, so you can work without hardware. It needs `scripts/setup` done, and
curl.

```sh
scripts/develop            # Home Assistant on http://127.0.0.1:8123, the fake Hue bridge in the background
scripts/develop hass       # Home Assistant alone, for example with the fake bridge in another terminal
scripts/develop fake-hue   # the fake Hue bridge alone, in the foreground
scripts/develop press      # one press of the simulated wall switch (press 3: three presses)
scripts/develop lights     # the fake bridge's lights, rooms and zones, and every PUT it received
```

- The first run makes `.dev/venv` with uv: the Home Assistant release that `requirements_test.txt` pins, and the
  packages it would otherwise install at its first start (`requirements_dev.txt`), most of all its 117 MB frontend.
  After that, Home Assistant starts in a few seconds and installs nothing. Ctrl+C stops it, and the fake bridge with it.
- Open http://127.0.0.1:8123 and create a throwaway user. The integration is under Settings › Devices & services ›
  Add integration › sonoff2hue: choose *Enter the bridge address* and enter the fake bridge's, `127.0.0.1:18443`.
- *Add switch* on the integration's page maps a MINIR4M to lights: choose *Press the wall switch*, then run
  `scripts/develop press`, and the simulated switch `1000abcdef` is selected (or *Enter the device ID* takes it). The
  fake bridge's lights, rooms and zones are offered, and once the switch is added, `scripts/develop press` toggles its
  lights. *Edit switch* changes one, and its menu deletes it.
- Home Assistant listens on 127.0.0.1 only. `SONOFF2HUE_DEV_PORT=8124 scripts/develop` moves it to another port, also
  later.
- It loads `custom_components/sonoff2hue` from this checkout, with nothing copied or linked: it runs from the
  repository root with `python -m homeassistant`, which puts the root first on Python's module path. So it doesn't load
  other custom integrations from `.dev/config/custom_components`. After changing the integration's code, restart Home
  Assistant; when it is restarted from its UI, `scripts/develop` starts it again.
- Its configuration is `.dev/config/configuration.yaml`, a copy of `tools/develop/configuration.yaml`: the logbook, and
  the integration's log at debug level. Home Assistant's log goes to the terminal and to
  `.dev/config/home-assistant.log`.
- Home Assistant reports that it couldn't set up `assist_pipeline` and `assist_satellite`: their requirements,
  pymicro-vad and pyspeex-noise, have no wheels for Python 3.14 and need a C++ compiler, so `scripts/develop` has it
  skip them. Without ffmpeg and libjpeg-turbo on the machine, it also logs errors about those. sonoff2hue needs none of
  them.
- The fake Hue bridge listens on https://127.0.0.1:18443, with its link button always pressed: pairing with
  `127.0.0.1:18443` succeeds at once. It makes a new certificate at every start, so pair again after restarting it.
  Its log is `.dev/fake-hue.log`; `FAKE_HUE_PORT` and `FAKE_HUE_BIND` change where it listens.
- `scripts/develop press` sends the eWeLink announcements of a press of the made-up MINIR4M `1000abcdef`
  (`SONOFF2HUE_DEV_DEVICE` chooses another): the last `seq` again, then the next, which `.dev/seq` keeps. Their
  multicast TTL is 0, so they loop back to this machine and never reach the network. They come from
  `tools/send-ewelink-announcements.py`, the .NET app's simulated switch, copied unchanged.
- Home Assistant itself holds UDP 5353 too: it opens python-zeroconf for name resolution even without the `zeroconf`
  integration, so the integration shares the port with it, as on a real installation.
- On macOS, allow your terminal and VS Code under System Settings › Privacy & Security › Local Network: without that,
  macOS refuses the multicast of the simulated switch and of the integration's mDNS listener with "No route to host".
  WSL 2's default NAT network doesn't pass on the LAN's multicast, so real switches aren't seen there.
- **Start over:** delete `.dev/config` to start again from onboarding, or `.dev` for everything, Home Assistant's
  environment included. `SONOFF2HUE_DEV_CONFIG` chooses another configuration folder than `.dev/config`, made the same
  way.

### The end-to-end test

`scripts/e2e` tests the integration end to end, as CI's *End-to-end* job does: a real Home Assistant with the
integration from this checkout, the fake Hue bridge and the simulated wall switch, all on this machine. It needs
`scripts/setup` done.

```sh
scripts/e2e
```

- It sets up the development Home Assistant as `scripts/develop setup` does, but in a configuration folder of its own,
  `.dev/e2e/config`, and starts it and the fake bridge on 127.0.0.1, on ports that are free at that moment, so it runs
  next to `scripts/develop`.
- Through Home Assistant's API, as its frontend does, it onboards a throwaway user, sets up sonoff2hue with the fake
  bridge's address and its link button pressed, and adds a switch with *Add switch*: a made-up MINIR4M, new at each
  run, which a press of the simulated switch identifies, with the light `Hallway` and the room `Living room` picked
  from the bridge's lists.
- It presses the switch twice and checks what the fake bridge was asked to switch (on, then off), the integration's
  `sonoff2hue_press` and `sonoff2hue_toggle` events, its status in its diagnostics, its entities Status, Last action and
  Press, and its log. Then it restarts Home Assistant: the switch's last `seq` again must be no press, and the next one
  a press.
- Once `.dev/venv` exists, it takes about 15 seconds. A pass deletes `.dev/e2e`. A failure prints the integration's
  lines of Home Assistant's log and keeps the folder: `hass.log` (Home Assistant's output), `fake-hue.log` and
  `config/home-assistant.log*`.

### Debugging in VS Code

[.vscode/launch.json](.vscode/launch.json) runs Home Assistant and the fake Hue bridge under the debugger, on Linux (WSL
2 included) and macOS. It needs the Python and Python Debugger extensions, which VS Code recommends when you open the
folder, and `scripts/setup` done.

- **sonoff2hue: Home Assistant and fake Hue bridge** starts both and stops them together. Before Home Assistant starts,
  the task *Set up the development Home Assistant* runs `scripts/develop setup`; once it listens, VS Code opens it in
  your browser.
- Breakpoints in `custom_components/sonoff2hue` and `tools/fake_hue` are hit, as these are the files that run. Just My
  Code is off for Home Assistant, so you can step into its code too.
- Stop sends Ctrl+C: Home Assistant shuts down and saves its state, and a second Stop kills it. While it is still
  starting, as its UI says, Ctrl+C or Stop abandons the start instead, without saving.
- A restart from Home Assistant's UI ends the debug session: start it again to load code changes.
- **Press the simulated wall switch** and **Show the fake Hue bridge's lights** (Terminal › Run Task) run
  `scripts/develop press` and `scripts/develop lights`. The state is in `.dev/`, as with `scripts/develop`.

## Releases

Each version is a GitHub Release, "sonoff2hue X.Y.Z" on the tag `vX.Y.Z`. Home Assistant's Marketplace installs and
updates the integration from these Releases, and shows their notes before an update. A published Release never
changes: a fix comes as the next version. There is no release yet; the first, 1.0.0, follows the live test on Home
Assistant OS.

To release, raise `version` in `custom_components/sonoff2hue/manifest.json` through a pull request, then tag the merged
commit on `main` and push the tag. The Release workflow checks the tag and creates the Release.

```sh
git switch main && git pull --ff-only
git tag -a vX.Y.Z -m "sonoff2hue X.Y.Z"
git push origin vX.Y.Z
```

## License

[Apache License 2.0](LICENSE).
