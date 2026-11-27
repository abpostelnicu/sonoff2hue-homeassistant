# sonoff2hue for Home Assistant

A Home Assistant integration that toggles lights from Sonoff **MINIR4M** wall switches. The wall switch is wired to a
MINIR4M in detached relay mode. When you operate it, sonoff2hue toggles the lights that you mapped to that MINIR4M:
any of Home Assistant's lights, such as the lights, rooms and zones of its Philips Hue integration. Presses stay on your
LAN: sonoff2hue needs no eWeLink cloud and no Matter.

```
wall switch ──S1/S2──> MINIR4M ──mDNS broadcast──> sonoff2hue ──light actions──> Hue integration ──> bridge ──> lights
               (detached relay mode)     (LAN)    (Home Assistant)              (or another light's integration)
```

- Each MINIR4M can toggle any number of lights.
- Toggle rule: if any of a switch's lights is on, all of them are turned off; otherwise all are turned on.
- It works with both rocker and push-button wall switches. Every operation of the switch toggles.

You set sonoff2hue up and use it in Home Assistant's own UI: its dialogs map the switches to your lights with Home
Assistant's own picker, each switch is a device with its entities, and Repairs, the Activity panel and the diagnostics
show what it does. It listens to the switches on its own, so it doesn't need SonoffLAN. It switches the lights through
Home Assistant, so Hue lights need Home Assistant's Philips Hue integration, set up with your bridge.

Coming from sonoff2hue 1.x, which paired the Hue bridge itself? Upgrading from sonoff2hue 1.x, under Setup, says what
changes.

## How it works

In detached relay mode, the MINIR4M's relay stays closed, so the lights always have power. The wall switch on S1/S2 no
longer cuts the power; it only reports to the MINIR4M.

eWeLink devices announce their state on the LAN with mDNS, as a `_ewelink._tcp` service. The announcement carries a
sequence number (`seq`) and the device's parameters, encrypted with its device key. The MINIR4M sends the button event
itself (`{"key":0}`) only to the eWeLink cloud. On the LAN, each operation of the wall switch shows up as a new
announcement: `seq` goes up by one, and the relay stays as it was. See
[AlexxIT/SonoffLAN#1227](https://github.com/AlexxIT/SonoffLAN/issues/1227) and
[#1880](https://github.com/AlexxIT/SonoffLAN/issues/1880).

sonoff2hue listens for these announcements with an mDNS socket of its own, next to Home Assistant's, so it doesn't need
Home Assistant's zeroconf. It never sends commands to the switches: its only mDNS packets are standard queries, which
ask which eWeLink devices are on the LAN.

- A new `seq` is a press, unless the relay or a setting changed. Telling those apart needs the switch's device key.
- Repeated announcements are ignored. When a switch restarts or rejoins Wi-Fi, its next announcement only sets a new
  baseline.
- On a press, sonoff2hue looks at the states of the switch's lights in Home Assistant, and turns them all on or all off
  with Home Assistant's actions `light.turn_on` and `light.turn_off`: one call per light, all at the same time, each
  within 5 seconds. A light that is already so is left alone, and one that is unavailable counts as failed.
- Each light's own integration switches it: for Hue lights, Home Assistant's Hue integration, over its connection to
  the bridge. A Hue room or zone is a light too, which switches all its lights with one command.
- The presses of one switch are toggled one after another. A press that waited more than 5 seconds for the one before
  it is dropped.

## Requirements

- Home Assistant 2026.11 or later: Home Assistant OS, Container or Core.
- Home Assistant on the same subnet as the MINIR4Ms, because mDNS doesn't cross subnets: not on a guest network or
  another VLAN. sonoff2hue listens on all of the host's network interfaces that have an IPv4 address, whatever Home
  Assistant's network settings say.
  - Home Assistant Container needs the host's network (`network_mode: host`), as Home Assistant's installation guide
    sets it up.
  - In a virtual machine, the network adapter has to be bridged to the LAN: NAT doesn't pass on the LAN's multicast.
- The MINIR4Ms added to the eWeLink app, each with **Detach relay** (detached relay mode) turned on in its settings.
- The lights in Home Assistant. For Philips Hue, that's Home Assistant's Philips Hue integration, set up with your Hue
  bridge; sonoff2hue doesn't connect to the bridge itself. Any other light works too, such as a Matter or Zigbee light.
- Nothing else that toggles the same lights when the switches are pressed, such as sonoff2hue in a second Home
  Assistant. With both running, every press would toggle the lights twice, and they would end up as they were.

<!-- ha-marketplace:exclude-start -->

## Installation

### From the Marketplace

[![Open your Home Assistant instance and open this repository in the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=abpostelnicu&repository=sonoff2hue-homeassistant&category=integration)

1. In Home Assistant, go to Settings → Marketplace, search for sonoff2hue, and open it. Or select the button above,
   which opens it in your Home Assistant's Marketplace.
2. On sonoff2hue's page in the Marketplace, select *Install*.
3. Select *Restart Home Assistant*. Then set sonoff2hue up, as Setup describes.

The Marketplace offers each new release as an update, with its release notes.

### By hand

1. Download the source code of the newest release from the
   [Releases](https://github.com/abpostelnicu/sonoff2hue-homeassistant/releases) page.
2. Copy its `custom_components/sonoff2hue` folder into the `custom_components` folder of Home Assistant's
   configuration folder, the one with `configuration.yaml`. Create `custom_components` if it isn't there.
3. Restart Home Assistant.

To update, replace the folder with the one of the new release, and restart Home Assistant again.

<!-- ha-marketplace:exclude-end -->

## Setup

[![Open your Home Assistant instance and start setting up sonoff2hue.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=sonoff2hue)

1. Set up the lights first. For Hue lights, add Home Assistant's Philips Hue integration with your bridge, if it isn't
   there yet: it adds the bridge's lights, rooms and zones.
2. Go to Settings → Devices & services → *Add integration*, choose **sonoff2hue**, and select *Submit* in *Set up
   sonoff2hue*. There is nothing to pair.
3. sonoff2hue then opens *Add switch*, to add your first switch, as Switches describes.

### Upgrading from sonoff2hue 1.x

sonoff2hue 1.x paired the Hue bridge itself, and its switches named the bridge's lights, rooms and zones. From 2.0, it
toggles Home Assistant's lights instead. Before you update:

1. Set up Home Assistant's Philips Hue integration with your bridge, if it isn't there yet, and check that it shows
   your lights, rooms and zones.
2. Take a backup of Home Assistant. The update can't be undone: Home Assistant doesn't set sonoff2hue 1.x up with the
   configuration that 2.0 leaves, so going back means restoring the backup.

When Home Assistant starts with 2.0, sonoff2hue moves its configuration over by itself:

- Each switch keeps its MINIR4M, its name and its device key. Its lights, rooms and zones become the Hue integration's
  lights, rooms and zones in Home Assistant: those that sonoff2hue 1.x last found on the bridge, by their Hue ids, or
  else those of that name, without regard to case, when exactly one light, or one room or zone, has it.
- What it can't find, it leaves to you: the repair *Choose the lights of … again* lists it and opens *Edit switch*.
  Until then, the switch toggles the lights that it found.
- The bridge's address, sonoff2hue's application key and the certificate pin are deleted, and so is the store of the
  lights, rooms and zones that sonoff2hue 1.x remembered.
- The *Hue bridge* device becomes sonoff2hue's service, with *Status* and *eWeLink devices seen*, which keep their
  entity ids and history. *Reachable* is removed, as sonoff2hue no longer talks to the bridge.
- The import of a `sonoff2hue.yaml` is gone, and so are *Reconfigure* and pairing the bridge again: the Hue integration
  does that now.

## Switches

Each switch maps one MINIR4M to the lights that its wall switch toggles.

### Adding a switch

On the integration's page, select *Add switch*, and find the MINIR4M that the wall switch is wired to:

- *Press the wall switch*: operate the wall switch within 60 seconds. The device that reports a press is selected. A
  device that another switch uses is named and skipped.
- *Pick a device seen on the LAN*: the eWeLink devices heard so far, those that no switch uses first. Each shows its id,
  address and type, whether it is online, its last press, and the switch that uses it.
- *Enter the device ID*: 4 to 40 letters and digits, such as `1000abcdef`.

Then, in *Switch settings*:

- *Name*: optional, up to 64 characters. It names the switch and its device; without a name, the device id does.
- *Lights*: what the switch toggles, chosen with Home Assistant's own picker, which searches your lights and shows their
  areas. Any light works, and the Hue integration's rooms and zones are lights too. Choose at least one, and at most
  50.
- *Advanced*: the device key, described below.

When the switch would toggle two or more lights, none of them a room or zone, and a room or zone of the Hue integration
has exactly those lights, *Use a room or zone?* offers it before sonoff2hue saves the switch:

- *Use room "…"* (or *Use zone "…"*) saves the switch with the room or zone instead of the lights.
- *Keep the lights* saves the switch with the lights as you chose them.

A room or zone switches all its lights at once, with one command, where single lights take one command each. It also
switches the lights that you add to it later in the Hue app. To have a zone of exactly a switch's lights, create it in
the Hue app: the Hue integration adds it to Home Assistant, and editing the switch then offers it.

Once saved, the switch works within seconds: sonoff2hue starts its switches again with the new one.

The switch keeps its lights by Home Assistant's entity registry, so renaming a light, in the Hue app or in Home
Assistant, and changing its entity id change nothing for the switch. When a light is removed from Home Assistant, or
disabled, the switch goes on toggling the others, and the repair *Some lights of … are missing* says which one. The
other switches aren't affected. When the light comes back, or is enabled again, the switch toggles it again at once.

### Device keys (optional)

Without a device key, every new announcement of the MINIR4M counts as a press. Normally only the wall switch changes
the state of a detached MINIR4M, so this works without a key. But toggling the relay or changing a setting (LED,
power-on state, ...) in the eWeLink app would then toggle the lights too.

With the device key, sonoff2hue decrypts the announcements. Then it:

- ignores relay toggles and setting changes;
- reports a repair when the MINIR4M isn't in detached relay mode.

The eWeLink cloud assigns the key. You can read it from the eWeLink cloud API: `GET /v2/device/thing` returns
`thingList[].itemData.devicekey`, and you need an app id from the [eWeLink developer portal](https://dev.ewelink.cc).
Any other tool that shows eWeLink device keys works too.

Enter it as *Device key* under *Advanced* in *Switch settings*. For a switch that has a key, *A key is set* offers
*Keep it*, *Replace it* (with *New device key*) and *Remove it*. A key belongs to its device: when you change a switch's
device, replace or remove its key, or it is dropped.

### Changing or deleting a switch

The integration's page lists the switches. A switch's *Edit switch* button, the cog, offers *Keep device …* or another
device, then *Switch settings*, with the switch's lights; it names those that are no longer in Home Assistant. Its ⋮
menu has *Delete*, which deletes the switch with its device and entities.

Changes apply at once, without a reload: sonoff2hue starts its switches again with the new settings.

## Devices and entities

sonoff2hue adds a service named sonoff2hue, and a device for each switch, named as the switch: a SONOFF MINIR4M with its
device id as serial number.

sonoff2hue's service:

- *Status*: what sonoff2hue is doing: *Running*, *Error*, *Invalid configuration* or *Not configured*. Its attributes
  are the *Message*, the *Problems*, and *Since*, the time either of them or the state last changed. While it runs, the
  *Message* counts the switches and their lights, such as "2 wall switches toggle 5 lights; 1 is missing.", and the
  *Problems* say what is missing.
- *eWeLink devices seen*: how many eWeLink devices sonoff2hue has heard on the LAN, mapped or not. Its attributes are
  *Listening*, whether it listens, and the time of the *Last packet*.

Each switch's device:

- *Press*: an event entity that fires at each press that sonoff2hue acts on, with the announcement's *Sequence
  number*.
- *Last action*: what came of the last press: *Turned on*, *Turned off*, *Failed* or *Dropped*, the newer of the last
  toggle and the last error. Its attributes are the *Time*; for a toggle, how many lights *Changed*, were *Unchanged* or
  *Failed*, and the *Elapsed (ms)*; the *Message* of a failure; and the lights that the switch *Toggles*, by their
  entity ids, such as `light.ceiling`.
- *Online*: *Online* if the MINIR4M was heard in the last 5 minutes, else *Offline*, also when it was never heard. Its
  attributes are what sonoff2hue knows of it: its *Address*, *Last seen*, *Last press* and *Sequence number*, whether it
  is *Encrypted*, *Device key set*, *Detached relay*, and a *Decode error*.
- *Test toggle*: a button that toggles the switch's lights once, as a press does: those that are in Home Assistant, and
  the log line names the others. If it can't toggle any, for example because none of them is in Home Assistant any
  more, the button reports why.

*eWeLink devices seen* and *Online* are diagnostic entities, and *Test toggle* a configuration entity. *Press* fires
only while sonoff2hue runs the switches, while *Status* is *Running*. *Online*'s *Last press* counts every press that
the LAN heard. After a restart or a reload, *Last action* is unknown until the next press.

When you give a switch another name or another MINIR4M, its device and entities stay, with their entity ids, history
and automations.

## Presses in automations

To do something on a press, an automation can trigger on the switch's *Press*: in the automation editor, with the
trigger *Event received*, the switch's *Press* entity and the event type *Press*. In YAML, for a switch named Hall:

```yaml
triggers:
  - trigger: event.received
    target:
      entity_id: event.hall_press
    options:
      event_type:
        - press
actions:
  - action: switch.toggle
    target:
      entity_id: switch.porch_light
```

The same presses fire the event `sonoff2hue_press` on Home Assistant's event bus, and what came of each fires
`sonoff2hue_toggle`. An automation can trigger on them too, for example on the MINIR4M's device id:

```yaml
triggers:
  - trigger: event
    event_type: sonoff2hue_press
    event_data:
      switch_id: 1000abcdef
```

The events' data:

- both: `device_id`, the id of the switch's Home Assistant device; `switch_id`, the MINIR4M's device id in lowercase;
  and `name`, the switch's name, or null;
- `sonoff2hue_press`: `seq`, the announcement's sequence number;
- `sonoff2hue_toggle`: `targets`, the switch's lights by their entity ids, as *Last action* shows them, and `result`:
  - `toggled`, with `turned_on` (true or false), `changed` and `unchanged` (how many lights it switched, and how many
    were already so), `failed` (how many it couldn't switch) and `elapsed_ms`;
  - `failed`, when the toggle couldn't run, or `dropped`, when the press waited more than 5 seconds for the one before
    it, each with `message`.

## Repairs

sonoff2hue reports these in Settings → System → Repairs:

- *The configuration has problems*: the switches can't be used as they are, and it lists why, such as a device id that
  two switches have, or a switch without its device id. Until it's fixed, no switch toggles anything. Fix the switch
  with *Edit switch*, or delete it.
- *Some lights of … are missing*: some of the switch's lights were removed from Home Assistant or are disabled, and it
  lists them. The switch goes on toggling its other lights, and when none is left, each press fails. The other switches
  aren't affected. Select *Submit* in the repair to edit the switch and choose its lights again. The repair goes away
  once the switch has all its lights again: when they come back or are enabled again, or when you save the switch
  without them.
- *Choose the lights of … again*: after the update from sonoff2hue 1.x, sonoff2hue found no light in Home Assistant for
  some of the switch's lights, rooms or zones, and it lists them as 1.x named them. Select *Submit* in the repair to
  edit the switch and choose their lights. The repair goes away once you save the switch.
- *… is not in detached relay mode*: the MINIR4M reports that its relay isn't detached, so the wall switch cuts the
  power to the lights. sonoff2hue can tell when it decrypts the announcements with the switch's device key. Turn on
  **Detach relay** in the eWeLink app; the repair goes away with the switch's next announcement.
- *Can't decode the broadcasts of …*: the device key doesn't decrypt the switch's announcements, or they are malformed.
  sonoff2hue then falls back to sequence numbers: presses still toggle the lights, but so do relay and setting changes.
  Correct or remove the key under *Edit switch* → *Advanced*.

## Activity, log and diagnostics

**Activity.** Home Assistant's Activity panel, the logbook, shows the changes of sonoff2hue's entities, and each
device's page shows those of its own. Each press shows as the switch's *Press*, and what came of it as the switch's
*Last action*: *Turned on*, *Turned off*, *Failed* or *Dropped*. The logbook lists *Last action* only when its state
changes, so a press with the same outcome as the one before shows as *Press* alone. Changes of *Online* and *Status*
show too, and so do the lights' own changes, with the switch's *Press* as their cause, or *Test toggle* for its
toggles. The details are in the entities' attributes: each press's *Sequence number* in *Press*, and the last outcome's
time, counts and message in *Last action*. Home Assistant's log and the diagnostics' activity have a line for each press
and each toggle, once the info lines are on.

**Log.** Home Assistant's log, in Settings → System → Logs, shows sonoff2hue's warnings and errors. Its other lines,
such as each press, each toggle and each ignored relay or setting change, are info and debug lines, which Home
Assistant's default log level hides. To see them:

- On the integration's page, the ⋮ menu at the top has *Enable debug logging*. Reproduce the problem, then select
  *Disable debug logging*. Home Assistant then downloads the log, where it can.
- Or, to keep the info lines, add this to `configuration.yaml` and restart Home Assistant:

  ```yaml
  logger:
    logs:
      custom_components.sonoff2hue: info
  ```

**Diagnostics.** The ⋮ menu of the sonoff2hue entry has *Download diagnostics*. Besides Home Assistant's own
information, the file has:

- `config`: the switches, as stored;
- `status`: what sonoff2hue is doing, and each switch with its MINIR4M, the lights that it toggles and the problems with
  those that it can't, and its last action and error;
- `lan_devices`: every eWeLink device heard on the LAN, with its address, type, `seq`, last press and whether it is
  online;
- `activity`: sonoff2hue's last 1,000 log lines, at the levels that the log shows. Turn on debug logging before you
  reproduce a problem, so that they include the info and debug lines.

The device keys show as `**REDACTED**`, or as null when none is set. The file still names your switches, their device
ids and addresses, and your lights: look through it before you share it.

## Troubleshooting

- **No eWeLink devices are seen.** *eWeLink devices seen* stays at 0, and *Pick a device seen on the LAN* says *No
  devices seen*.
  - Check that Home Assistant is on the same subnet as the switches, not on a guest network or another VLAN, and that
    the Wi-Fi doesn't isolate its clients. Some access points drop multicast: look for "multicast to unicast" or IGMP
    snooping settings.
  - A firewall on the host has to allow inbound UDP 5353. Home Assistant Container needs the host's network, and a
    virtual machine a bridged network adapter. If avahi-daemon runs on the host, keep its default
    `disallow-other-stacks=no` in `/etc/avahi/avahi-daemon.conf`.
  - A MINIR4M announces itself when its state changes, or when asked. sonoff2hue asks when it starts, then every 2
    minutes, and every 30 seconds, by multicast and at the switch's address, while a switch's last answer is more than
    2½ minutes old, so operate the wall switch, or wait.
  - When sonoff2hue can't listen on the network, the log warns "mDNS socket error (…); retrying in 5s."
- **A switch is *Offline*, or the log warns "Not seen on the LAN yet: …".** sonoff2hue hasn't heard that MINIR4M in
  the last 5 minutes, or since it started its switches. It asks for it every 30 seconds while it hasn't heard it since
  then, and while its last answer is more than 2½ minutes old, until it is *Offline*. Check that the MINIR4M has power
  and Wi-Fi, and its device id.
- **A switch goes *Offline* for a few minutes, about once an hour.** Some MINIR4Ms stop receiving multicast for a while,
  for example when the Wi-Fi access point renews the network's group key, every hour by default on some. Such a MINIR4M
  still sends, so its presses toggle the lights, but it doesn't hear sonoff2hue's multicast queries. So when a switch's
  last answer is more than 2½ minutes old, sonoff2hue also asks it at its address, which it does hear, and it answers;
  with debug logging on, the log says "…: not heard for more than 150 s; asking it directly at …". If the switch still
  goes *Offline*, it didn't answer at its address either. Check its Wi-Fi, and in the access point's settings for that
  network, look for a group rekey interval (GTK rekeying) to lengthen or turn off, or a multicast-to-unicast option
  (multicast enhancement) to turn on. A press right after sonoff2hue starts, in such a time, only sets the switch's
  baseline.
- **Presses don't toggle the lights.**
  - Check that the MINIR4M is in detached relay mode. With the device key, a press that toggles the relay is ignored as
    a relay change, and a repair says that the switch isn't detached.
  - Check that the presses arrive: the switch's *Online* has the time of the *Last press* that the LAN heard. With
    debug logging on, the log names each new announcement of a mapped switch and what sonoff2hue made of it: online,
    wall switch operated, or ignored as a relay change, a settings change or a restart.
  - Check sonoff2hue's *Status*. It runs the switches only while it is *Running*, and its *Message*, its *Problems* and
    Repairs say why it isn't. *Press* fires for each press that it acts on, and *Last action* says what came of it.
  - Check Repairs for *Some lights of … are missing* and *Choose the lights of … again*: the switch toggles only the
    lights that it finds in Home Assistant. When it finds none, *Last action* is *Failed*, with the *Message* "None of
    the lights of this switch is in Home Assistant."
  - Check that nothing else toggles the same lights on each press, such as sonoff2hue in a second Home Assistant: two
    toggles per press leave the lights as they were.
- **A light doesn't switch.** The log warns "Could not switch light.… on: …" with the reason, *Last action*'s *Failed*
  counts the light, and its *Message* says how many couldn't be switched, such as "1 of 2 lights could not be
  switched." The reasons:
  - "it is unavailable": Home Assistant has the light, but can't reach it now, for example because the Hue bulb has no
    power, or the Hue integration can't reach the bridge. Check the light's own integration.
  - "it didn't answer within 5 s": the light's integration didn't switch it within 5 seconds.
  - Any other reason is the one that the light's integration gave, such as "Request failed: …" from the Hue
    integration.

  *Test toggle* toggles the switch's lights as a press does, so you can try them without the wall switch.
- **The lights toggle when you change something in the eWeLink app.** Add the switch's device key.
- ***Add switch* says "The sonoff2hue integration isn't loaded".** Reload the integration from the ⋮ menu of the
  sonoff2hue entry, then try again.

## Limitations

- Single, double and long presses can't be told apart: that information only goes to the eWeLink cloud.
- A press while sonoff2hue starts, after a change of its switches, or while the network reconnects only sets the
  baseline.
- A press that waited more than 5 seconds for the one before it is dropped, not replayed later.
- The lights switch as fast as their integration switches them. The Hue integration keeps a connection to the bridge
  open for 15 seconds after each request. Measured on one Hue Bridge v2: a room switched in about 30 ms within those 15
  seconds, and in about 0.2 seconds after a longer pause, as the integration first connects to the bridge again.
  sonoff2hue 1.x kept a connection of its own open, and switched a room in about 60 ms either way.
- Creating a Hue room or zone isn't possible from sonoff2hue: Home Assistant has no action for it. Create it in the Hue
  app.

## Development

Run `scripts/setup` once: it makes `.venv` with Python 3.14 and the pinned tools, with [uv](https://docs.astral.sh/uv/).
Then `scripts/check` runs what CI runs: ruff, its formatter, mypy and the tests.

- The tests need no network, light or MINIR4M. They build the switches' mDNS packets (`tests/engine/packets.py`) and use
  fake sockets, and Home Assistant gets lights of the tests' own, whose `light.turn_on` and `light.turn_off` actions
  record their calls (`tests/fakes.py`).
- The mDNS layer, `engine/dns.py`, `engine/mdns.py` and the announcements of `engine/ewelink.py`, keeps its behavior
  exactly. Its recorded cases have to pass unchanged: `tests/engine/mdns_packets.json` (packets, with the messages,
  announcements and probes read from them) and `tests/engine/mdns_text.json` (its own rules for white space and letter
  case, for every character).
- sonoff2hue listens with its own mDNS socket. `tests/test_own_mdns.py` fails when a module imports python-zeroconf,
  Home Assistant's `zeroconf` or `network` integration, or the resolver that answers `.local` names with zeroconf; when
  the engine (`custom_components/sonoff2hue/engine`) imports Home Assistant; or when the manifest has a zeroconf or
  HomeKit matcher, or depends on `zeroconf` or `network`.

### Running Home Assistant locally

`scripts/develop` runs a local Home Assistant 2026.11 with the integration from this checkout, three lights of its own
and a simulated wall switch, so you can work without hardware. It needs `scripts/setup` done.

```sh
scripts/develop            # Home Assistant on http://127.0.0.1:8123, until Ctrl+C
scripts/develop setup      # only prepare Home Assistant's environment and configuration folder
scripts/develop press      # one press of the simulated wall switch (press 3: three presses)
```

- The first run makes `.dev/venv` with uv: the Home Assistant release that `requirements_test.txt` pins, and the
  packages it would otherwise install at its first start (`requirements_dev.txt`), most of all its 117 MB frontend.
  After that, Home Assistant starts in a few seconds and installs nothing. Ctrl+C stops it.
- Open http://127.0.0.1:8123 and create a throwaway user. The integration is under Settings → Devices & services →
  *Add integration* → sonoff2hue: select *Submit*, and *Add switch* opens.
- In *Add switch*, choose *Press the wall switch*, then run `scripts/develop press`, and the simulated switch
  `1000abcdef` is selected (or *Enter the device ID* takes it). *Switch settings* offers Home Assistant's lights, among
  them the configuration's three, Hallway, Living room and Porch: template lights, each kept on or off by an input
  boolean. Once the switch is added, `scripts/develop press` toggles its lights. *Edit switch* changes one, and its
  menu deletes it.
- Home Assistant listens on 127.0.0.1 only. `SONOFF2HUE_DEV_PORT=8124 scripts/develop` moves it to another port, also
  later.
- It loads `custom_components/sonoff2hue` from this checkout, with nothing copied or linked: it runs from the
  repository root with `python -m homeassistant`, which puts the root first on Python's module path. So it doesn't load
  other custom integrations from `.dev/config/custom_components`. After changing the integration's code, restart Home
  Assistant; when it is restarted from its UI, `scripts/develop` starts it again.
- Its configuration is `.dev/config/configuration.yaml`, a copy of `tools/develop/configuration.yaml`: the logbook, the
  integration's log at debug level, and the lights. Home Assistant's log goes to the terminal and to
  `.dev/config/home-assistant.log`.
- Home Assistant reports that it couldn't set up `assist_pipeline` and `assist_satellite`: their requirements,
  pymicro-vad and pyspeex-noise, have no wheels for Python 3.14 and need a C++ compiler, so `scripts/develop` has it
  skip them. Without ffmpeg and libjpeg-turbo on the machine, it also logs errors about those. sonoff2hue needs none of
  them.
- `scripts/develop press` sends the eWeLink announcements of a press of the made-up MINIR4M `1000abcdef`
  (`SONOFF2HUE_DEV_DEVICE` chooses another): the last `seq` again, then the next, which `.dev/seq` keeps. Their
  multicast TTL is 0, so they loop back to this machine and never reach the network. They come from
  `tools/send-ewelink-announcements.py`, a script on Python's standard library that announces a MINIR4M's state as a
  device in DIY mode does, unencrypted, with its SRV and A records; ruff and mypy check it as they check the rest.
- Home Assistant itself holds UDP 5353 too: it opens python-zeroconf for name resolution even without the `zeroconf`
  integration, so the integration shares the port with it, as on a real installation.
- On macOS, allow your terminal and VS Code under System Settings → Privacy & Security → Local Network: without that,
  macOS refuses the multicast of the simulated switch and of the integration's mDNS listener with "No route to host".
  WSL 2's default NAT network doesn't pass on the LAN's multicast, so real switches aren't seen there.
- **Start over:** delete `.dev/config` to start again from onboarding, or `.dev` for everything, Home Assistant's
  environment included. `SONOFF2HUE_DEV_CONFIG` chooses another configuration folder than `.dev/config`, made the same
  way.

### The end-to-end test

`scripts/e2e` tests the integration end to end, as CI's *End-to-end* job does: a real Home Assistant with the
integration from this checkout, the development configuration's lights and the simulated wall switch, all on this
machine. It needs `scripts/setup` done.

```sh
scripts/e2e
```

- It sets up the development Home Assistant as `scripts/develop setup` does, but in a configuration folder of its own,
  `.dev/e2e/config`, and starts it on 127.0.0.1, on a port that is free at that moment, so it runs next to
  `scripts/develop`.
- Through Home Assistant's API, as its frontend does, it onboards a throwaway user, sets up sonoff2hue with its one
  confirmation, and adds a switch in *Add switch*, which the setup opens: a made-up MINIR4M, new at each run, which a
  press of the simulated switch identifies, with two of the lights.
- It presses the switch twice and checks the lights' states, the integration's `sonoff2hue_press` and
  `sonoff2hue_toggle` events, its status in its diagnostics, its entities *Status*, *Last action* and *Press*, and its
  log. Then, through the entity registry's API, as Home Assistant's UI does, it gives one light another entity id and
  disables the other: the switch must follow the new entity id without starting again, and leave the disabled light out
  with a repair. It restarts Home Assistant: the switch's last `seq` again must be no press, and the next one a press.
- Last, with Home Assistant stopped, it turns the stored configuration into sonoff2hue 1.x's, starts Home Assistant
  again, and checks the migration: the entry and its switch, the service device and its entities, *Reachable* gone,
  and the repair *Choose the lights of … again*. It fixes the repair as a user does, through the repair and *Edit
  switch*, which the repair opens, and presses the switch.
- Once `.dev/venv` exists, it takes about 20 seconds. A pass deletes `.dev/e2e`. A failure prints the integration's
  lines of Home Assistant's log and keeps the folder: `hass.log` (Home Assistant's output) and
  `config/home-assistant.log*`.

### Debugging in VS Code

[.vscode/launch.json](.vscode/launch.json) runs Home Assistant under the debugger, on Linux (WSL 2 included) and macOS.
It needs the Python and Python Debugger extensions, which VS Code recommends when you open the folder, and
`scripts/setup` done.

- **Home Assistant** starts it. Before it starts, the task *Set up the development Home Assistant* runs
  `scripts/develop setup`; once it listens, VS Code opens it in your browser.
- Breakpoints in `custom_components/sonoff2hue` are hit, as these are the files that run. Just My Code is off, so you
  can step into Home Assistant's code too.
- Stop sends Ctrl+C: Home Assistant shuts down and saves its state, and a second Stop kills it. While it is still
  starting, as its UI says, Ctrl+C or Stop abandons the start instead, without saving.
- A restart from Home Assistant's UI ends the debug session: start it again to load code changes.
- **Press the simulated wall switch** (Terminal → Run Task) runs `scripts/develop press`. The state is in `.dev/`, as
  with `scripts/develop`.

## Releases

Each version is a GitHub Release, "sonoff2hue X.Y.Z" on the tag `vX.Y.Z`. Home Assistant's Marketplace installs and
updates the integration from these Releases, and shows their notes before an update. It shows this README as it is at
the tag of the Release it offers, so a change to the README reaches the Marketplace with the next release. A published
Release can't be undone: its tag can't be moved or deleted, so a fix comes as the next version. Versions are released
in rising order.

To release, open a release pull request that raises `version` in `custom_components/sonoff2hue/manifest.json` to X.Y.Z
and adds the release's notes as `.github/release-notes/vX.Y.Z.md`, written for users: they become the Release's notes
as they are. Once it is merged and CI passes on `main`, the *Tag the release* workflow tags the merged commit `vX.Y.Z`,
and the *Release* workflow checks the tag and publishes the Release with those notes.

CI has to pass on the release commit itself. If it fails there, or is cancelled because something else was pushed to
`main` while it ran, the release commit isn't tagged, and the next commit of `main` whose CI passes, which still has the
notes, would get the tag instead: the release would include the changes merged after it. So if CI fails or is
cancelled on the release commit, re-run it (`gh run rerun <id>`, or *Re-run jobs* on its page) before merging anything
else: when the re-run passes, the release commit itself is tagged.

Pushing a tag by hand still works: tag the merged commit on `main` and push the tag, and the Release workflow checks it
and publishes the Release, with the commit's notes file if it has one, or else notes that GitHub generates.

```sh
git switch main && git pull --ff-only
git tag -a vX.Y.Z -m "sonoff2hue X.Y.Z"
git push origin vX.Y.Z
```

### The Release agent

The *Release* agent prepares a release as described above, writes its notes, and checks the published Release. It
follows the procedure in `.github/skills/release/SKILL.md`.

- **In VS Code's chat or in Copilot CLI:** `/release 1.2.0`, `/release patch`, `minor` or `major`, or `/release` to
  have the version proposed. It opens the release pull request, waits for its checks, asks you before it merges, and
  then checks the published Release and records it.
- **On GitHub, without your computer:** start Copilot cloud agent with the *Release* agent, from the Agents page of
  github.com or GitHub Mobile, by assigning an issue to Copilot, or with
  `gh agent-task create "/release 1.2.0" --custom-agent release`. It opens a draft release pull request. A session can
  stop to ask you to approve a tool, in its conversation on github.com or GitHub Mobile: approve or deny it there, and
  it goes on. Then approve the pull request's workflow runs if GitHub asks, mark it ready for review, rename it
  `Release 1.2.0` if it's titled otherwise, and merge it with *Squash and merge*: once CI has passed on `main`, the
  release is published. Then `/release check 1.2.0` checks it and records it.
- **`--dry-run`** stops at a draft pull request that adds only the notes, so that merging it would release nothing.
  Close it without merging.

## License

[Apache License 2.0](LICENSE).
