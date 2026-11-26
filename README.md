# sonoff2hue for Home Assistant

A Home Assistant integration that toggles Philips Hue lights from Sonoff **MINIR4M** wall switches. The wall switch is
wired to a MINIR4M in detached relay mode. When you operate it, sonoff2hue toggles the Hue lights, rooms and zones that
you mapped to that MINIR4M. Presses and toggles stay on your LAN: it needs no eWeLink or Hue cloud, and no Matter.

```
wall switch ──S1/S2──> MINIR4M ──mDNS broadcast──> Home Assistant (sonoff2hue) ──HTTPS (CLIP v2)──> Hue bridge ──> lights
               (detached relay mode)     (LAN)
```

- Each MINIR4M can toggle any number of Hue lights, rooms and zones.
- Toggle rule: if any of a switch's lights is on, all of them are turned off; otherwise all are turned on.
- It works with both rocker and push-button wall switches. Every operation of the switch toggles.

You set sonoff2hue up and use it in Home Assistant's own UI: its dialogs pair the bridge and map the switches, each
switch is a device with its entities, and Repairs, the Activity panel and the diagnostics show what it does. It talks to
the Hue bridge and listens to the switches on its own, so it needs neither Home Assistant's Hue integration nor
SonoffLAN.

## How it works

In detached relay mode, the MINIR4M's relay stays closed, so the Hue bulbs always have power. The wall switch on S1/S2
no longer cuts the power; it only reports to the MINIR4M.

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
- On a press, sonoff2hue reads the state of the mapped lights, rooms and zones from the bridge, and switches them all on
  or all off. A room or a zone takes one command.
- It keeps its HTTPS connection to the bridge open, with a request every 45 seconds, so that a press doesn't wait for a
  new connection. A press that waited more than 5 seconds for the bridge is dropped.
- It pins the bridge's certificate when it pairs, and from then on talks only to a bridge with that certificate. The
  usual check of a certificate can't work here: the bridge's certificate names its bridge id, not its address.

## Requirements

- Home Assistant 2026.11 or later: Home Assistant OS, Container or Core.
- Home Assistant on the same subnet as the MINIR4Ms, because mDNS doesn't cross subnets: not on a guest network or
  another VLAN. sonoff2hue listens on all of the host's network interfaces that have an IPv4 address, whatever Home
  Assistant's network settings say.
  - Home Assistant Container needs the host's network (`network_mode: host`), as Home Assistant's installation guide
    sets it up.
  - In a virtual machine, the network adapter has to be bridged to the LAN: NAT doesn't pass on the LAN's multicast.
- The MINIR4Ms added to the eWeLink app, each with **Detach relay** (detached relay mode) turned on in its settings.
- A Philips Hue bridge with the local CLIP v2 API, the Hue Bridge v2 (the square one) or the Hue Bridge Pro, which Home
  Assistant can reach over HTTPS.
- Nothing else that toggles the same lights when the switches are pressed, such as sonoff2hue in a second Home
  Assistant. With both running, every press would toggle the lights twice, and they would end up as they were.

<!-- ha-marketplace:exclude-start -->

## Installation

### From the Marketplace

[![Open your Home Assistant instance and open this repository in the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=abpostelnicu&repository=sonoff2hue-homeassistant&category=integration)

1. Select the button above to open sonoff2hue in your Home Assistant's Marketplace. Or go to Settings → Marketplace →
   *Add from link*, and enter `https://github.com/abpostelnicu/sonoff2hue-homeassistant` as the *GitHub link*, with
   *Integration* as its *Type*. Adding a repository from its link needs a GitHub connection, which the Marketplace asks
   for.
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

Go to Settings → Devices & services → *Add integration*, and choose **sonoff2hue**. It pairs the Hue bridge first, and
you add the switches after that. sonoff2hue works with one Hue bridge.

### Pairing the Hue bridge

1. Choose how to find the bridge:
   - *Find bridges* searches your network for about 5 seconds, with mDNS and with Philips Hue's discovery service,
     `discovery.meethue.com`. Choose the bridge, which shows its address, its bridge id and how it was found, or
     *Another address*.
   - *Enter the bridge address*: its IP address or host name, optionally with `:port`.
2. sonoff2hue reads the bridge and shows its name, model, bridge id, firmware and address, and the SHA-256 of the
   certificate that it pins from now on.
3. Press the round link button on top of the bridge within 60 seconds. If the time runs out, choose *Retry* or *Choose
   another bridge*. Closing the dialog stops the pairing.

Then add your switches, as Switches describes.

### Importing a sonoff2hue.yaml

When you add the integration, you can import the bridge and the switches from a `sonoff2hue.yaml` instead of pairing
the bridge and adding the switches one by one: choose *Import sonoff2hue.yaml*, and paste the whole file. If something
else already toggles the lights for these switches, stop it first: with both running, every press would toggle the
lights twice.

The file holds the Hue bridge under `hue`, and one entry per MINIR4M under `switches`. Its keys are case-sensitive, and
each value is text, as written:

```yaml
hue:
  bridgeIp: 192.0.2.10              # its IP address or host name, optionally with :port
  appKey: <application key>         # the key that pairing with the bridge gave
  certificateSha256: <SHA-256>      # optional: the pin of the bridge's certificate
  bridgeId: 001788FFFE000001        # optional: sonoff2hue reads it from the bridge

switches:
  - deviceId: 1000abcdef            # the MINIR4M's eWeLink device id
    name: Hall                      # optional
    deviceKey: <device key>         # optional, as Device keys describes
    lights:                         # Hue lights, by name or id
      - Ceiling
      - Floor lamp
    groups:                         # rooms and zones, by name or id
      - room:Living room
```

- `certificateSha256` is the SHA-256 of the bridge's certificate: 64 hexadecimal digits, with or without colons.
- `groups` names rooms and zones as `lights` names lights, by name or id, without regard to case. Where a room and a
  zone have the same name, `room:` or `zone:` says which one.
- A setting that is missing, empty, `~` or `null` isn't set, but each entry of `lights` and `groups` has to name
  something. Quote a value with `: ` or ` #` in it, which YAML would read otherwise: `name: "Spot #2"`.

The import reads the text, then checks it:

- It takes one YAML document with the keys above, each once, and refuses anything else, such as `bridgeip` for
  `bridgeIp`, a key given twice, a list where a text belongs, or an empty entry of `lights` or `groups`. It reports
  such a mistake where it is, such as `Config, line 4, column 3: duplicate key 'appKey'.` or
  `Config, line 5, column 9: an entry of 'lights' is empty. Write the light's name or id, or remove the '-'.`
- Then it checks the switches as sonoff2hue does before it runs them: at least one switch, each with a `deviceId` that
  no other entry has, and with at least one entry in `lights` or `groups`, none of them blank. It reports these
  problems all at once, each with the switch's place in the list, such as `switches[1]: deviceId is missing.`
- With the bridge's `bridgeIp` and `appKey` in the file, sonoff2hue connects to the bridge with them, and with its
  `certificateSha256` if the file has one. If that fails, it offers *Try again*, *Pair the bridge again and keep the
  switches* or *Paste the file again*. Without them, you pair the bridge next.
- After an error, paste the whole file again: the dialog never shows the text back, as it holds the keys.

Once the file is imported:

- sonoff2hue keeps the bridge's address, application key and pin, and every switch with its `deviceId`, `name`,
  `deviceKey`, `lights` and `groups` as written. The bridge id comes from the bridge itself. Home Assistant keeps the
  application key and the device keys, and never shows them again.
- It looks up the lights, rooms and zones on the bridge when it starts, not during the import, and a repair lists the
  names that it doesn't find.
- A file without `certificateSha256` is imported too. Then the repair *The Hue bridge's certificate is not pinned*
  offers to pair the bridge again, which pins it.
- sonoff2hue doesn't read the file again. Change the switches in Home Assistant instead.

### Pairing again or another bridge

On the integration's page (Settings → Devices & services → sonoff2hue), the ⋮ menu of the sonoff2hue entry has
*Reconfigure*: *Find bridges* or *Enter the bridge address*, then the link button, as above. Your switch mappings are
kept. Use it to pair the bridge again, for example to pin its certificate or after its address changed, or to pair
another bridge. Deleting the integration instead would delete its switches too.

When the bridge no longer accepts sonoff2hue's application key, Home Assistant asks you to pair it again. Repairs shows
*Authentication expired for sonoff2hue*, and Settings → Devices & services shows a sonoff2hue card with *Reconfigure*.
Select it, select *Submit* in *Pair the Hue bridge again*, and press the bridge's link button within 60 seconds. This
pairs the bridge at the address that sonoff2hue knows, and only the bridge it paired before; to pair another one, use
*Reconfigure* from the menu.

## Switches

Each switch maps one MINIR4M to the Hue lights, rooms and zones that its wall switch toggles.

### Adding a switch

On the integration's page, select *Add switch*, and find the MINIR4M that the wall switch is wired to:

- *Press the wall switch*: operate the wall switch within 60 seconds. The device that reports a press is selected. A
  device that another switch uses is named and skipped.
- *Pick a device seen on the LAN*: the eWeLink devices heard so far, those that no switch uses first. Each shows its id,
  address and type, whether it is online, its last press, and the switch that uses it.
- *Enter the device ID*: 4 to 40 letters and digits, such as `1000abcdef`.

Then, in *Switch settings*:

- *Name*: optional, up to 64 characters. It names the switch and its device; without a name, the device id does.
- *Lights*, *Rooms* and *Zones*: what the switch toggles, as the bridge lists them. Choose at least one. A light shows
  whether it is on or off, and a room or zone without lights says "no lights".
- *Advanced*: the device key, described below.

When the switch would toggle two or more single lights, sonoff2hue offers a room or zone in their place before it
saves the switch:

- If a room or zone on the bridge has exactly those lights, *Use room "…"* (or *Use zone "…"*) saves the switch with
  it instead of the lights.
- Otherwise, *Create zone "…" on the bridge* creates a zone of those lights and saves the switch with it. The zone is
  named after the switch, or after its device id without a name: cut to the bridge's 32 characters, and with " 2",
  " 3" and so on when a room or zone has that name already. If the bridge refuses the zone or can't be reached,
  *Couldn't create the zone* says why, with *Try again* and *Keep the lights*.
- *Keep the lights* saves the switch with the lights as you chose them.

The switch's other rooms and zones stay. Nothing is offered when one of them has exactly those lights already, and
editing a switch offers the same, so a switch of single lights can move to a room or zone.

Tip: a room or zone switches all its lights at once, with one command, where single lights are switched one after
another, with one command each. It also switches the lights that you add to it later in the Hue app.

Once saved, the switch works within seconds: sonoff2hue restarts its connection to the bridge with the new switch.

The switch keeps the names of its lights, rooms and zones, or their ids where two have the same name, and finds them on
the bridge by those, without regard to case. sonoff2hue also remembers which light, room or zone each of them found, in
Home Assistant's storage (`.storage/sonoff2hue.<entry id>`, deleted with the integration):

- When you rename one in the Hue app, the switch still finds it, and sonoff2hue saves the new name in the switch, as
  *Switch settings* would save it: the new name, or the id when another one of its kind has that name. It then
  restarts its connection to the bridge with the new name. It notices a rename when it starts, when its bridge or its
  switches change, and within 15 minutes while it runs.
- When it no longer finds one, for example because it was deleted in the Hue app, the switch goes on toggling the
  others, and the repair *Some lights, rooms or zones of … are missing* says which one and why. The other switches
  aren't affected. sonoff2hue looks for it again every minute, and notices a deletion at the next press, when the
  bridge says that it no longer has that light, room or zone.

If the bridge can't list its lights, because it isn't paired or doesn't answer, choose *Try again* or *Enter them by
hand*: light names or ids, and rooms and zones as `room:Name` or `zone:Name` (or ids), separated by commas. If the
bridge can't check the names when you save, *Save without checking?* asks first.

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
device, then *Switch settings*. Its ⋮ menu has *Delete*, which deletes the switch with its device and entities.

Changes apply at once, without a reload: sonoff2hue restarts its connection to the bridge with the new switches.

## Devices and entities

sonoff2hue adds a device named Hue bridge, and a device for each switch, named as the switch: a SONOFF MINIR4M with its
device id as serial number.

The Hue bridge's device:

- *Status*: what sonoff2hue is doing: *Running*, *Connecting*, *Error*, *Invalid configuration* or *Not configured*. Its
  attributes are the *Message*, the *Problems*, and *Since*, the time either of them or the state last changed. While it
  runs, the *Message* counts the switches and what they toggle, such as "2 wall switches toggle 5 Hue lights, rooms
  and zones; 1 is missing.", and the *Problems* say what is missing.
- *Reachable*: *Reachable* or *Unreachable*, whether sonoff2hue could use the bridge when it last checked, and unknown
  before the first check. Its attributes are the bridge's *Address*, the *Message* while it is unreachable, and the
  time it was *Checked*.
- *eWeLink devices seen*: how many eWeLink devices sonoff2hue has heard on the LAN, mapped or not. Its attributes are
  *Listening*, whether it listens, and the time of the *Last packet*.

Each switch's device:

- *Press*: an event entity that fires at each press that sonoff2hue acts on, with the announcement's *Sequence
  number*.
- *Last action*: what came of the last press: *Turned on*, *Turned off*, *Failed* or *Dropped*, the newer of the last
  toggle and the last error. Its attributes are the *Time*; for a toggle, how many lights, rooms and zones *Changed*,
  were *Unchanged* or *Failed*, and the *Elapsed (ms)*; the *Message* of a failure; and what the switch *Toggles*,
  such as `light 'Ceiling'` and `group 'Hallway'`. A light, room or zone whose name another one of its kind has
  shows its id after the name too, as *Switch settings* lists it.
- *Online*: *Online* if the MINIR4M was heard in the last 5 minutes, else *Offline*, also when it was never heard. Its
  attributes are what sonoff2hue knows of it: its *Address*, *Last seen*, *Last press* and *Sequence number*, whether it
  is *Encrypted*, *Device key set*, *Detached relay*, and a *Decode error*.
- *Test toggle*: a button that toggles the switch's lights, rooms and zones once, as a press does: those that are on the
  bridge, and the log line names the others. If it can't toggle any, for example because none of them is on the bridge,
  the button reports why.

*Reachable*, *eWeLink devices seen* and *Online* are diagnostic entities, and *Test toggle* a configuration entity.
*Press* fires only while sonoff2hue runs the switches, which it starts once the bridge answers. *Online*'s *Last press*
counts every press that the LAN heard, also before that. After a restart or a reload, *Last action* is unknown until
the next press.

When you give a switch another name or another MINIR4M, or pair another bridge, the devices and entities stay, with
their entity ids, history and automations.

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
- `sonoff2hue_toggle`: `targets`, such as `light 'Ceiling'` or `group 'Hallway'`, as *Last action* shows them, and
  `result`:
  - `toggled`, with `turned_on` (true or false), `changed` and `unchanged` (how many targets it switched, and how many
    were already so), `failed` (how many the bridge didn't switch) and `elapsed_ms`;
  - `failed`, when the toggle couldn't run, or `dropped`, when the press waited more than 5 seconds for the bridge,
    each with `message`.

## Repairs

sonoff2hue reports these in Settings → System → Repairs:

- *The Hue bridge's certificate is not pinned*: sonoff2hue doesn't check the bridge's certificate, because the imported
  `sonoff2hue.yaml` had no `certificateSha256`. Select *Submit* in the repair, which opens *Reconfigure*, and pair the
  bridge again.
- *The Hue bridge's certificate changed*: the bridge's certificate isn't the pinned one, so sonoff2hue doesn't talk to
  it. Either the bridge was replaced or reset, or another device now has its address. For a replaced or reset bridge,
  select *Submit* in the repair and pair it again. Otherwise, *Reconfigure* with the bridge's new address.
- *The configuration has problems*: the switches or the bridge's settings can't be used as they are, and it lists why,
  such as a switch without any light, room or zone, or a device id that two switches have. Until it's fixed, no switch
  toggles anything. Fix the switch with *Edit switch*, or pair the bridge again with *Reconfigure*.
- *Some lights, rooms or zones of … are missing*: the switch can't toggle some of its lights, rooms or zones, and it
  lists why: one isn't on the bridge, for example because it was deleted in the Hue app, a name matches several of them,
  or a room or zone has no lights. The switch goes on toggling the others, and when none is left, each press fails.
  The other switches aren't affected. Edit the switch to choose them again, or fix them in the Hue app: sonoff2hue
  looks for them again every minute, and the repair goes once it finds them.
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
changes, so a press with the same outcome as the one before shows as *Press* alone. Changes of *Online*, *Status* and
*Reachable* show too. The details are in the entities' attributes: each press's *Sequence number* in *Press*, and the
last outcome's time, counts and message in *Last action*. Home Assistant's log and the diagnostics' activity have a line
for each press and each toggle, once the info lines are on.

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

- `config`: the bridge and the switches, as stored;
- `status`: what sonoff2hue is doing, whether the bridge is reachable, and each switch with its MINIR4M, the lights,
  rooms and zones found on the bridge and the problems with those that aren't, and its last action and error;
- `lan_devices`: every eWeLink device heard on the LAN, with its address, type, `seq`, last press and whether it is
  online;
- `activity`: sonoff2hue's last 1,000 log lines, at the levels that the log shows. Turn on debug logging before you
  reproduce a problem, so that they include the info and debug lines.

The application key, the certificate pin and the device keys show as `**REDACTED**`, or as null when none is set. The
file still names your switches, their device ids and addresses, the bridge's address, and your lights, rooms and zones:
look through it before you share it.

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
    minutes, and every 30 seconds while a switch's last answer is more than 2½ minutes old, so operate the wall
    switch, or wait.
  - When sonoff2hue can't listen on the network, the log warns "mDNS socket error (…); retrying in 5s."
- **A switch is *Offline*, or the log warns "Not seen on the LAN yet: …".** sonoff2hue hasn't heard that MINIR4M in
  the last 5 minutes, or since it connected to the bridge. It asks for it every 30 seconds while it hasn't heard it
  since then, and while its last answer is more than 2½ minutes old, until it is *Offline*. Check that the MINIR4M has
  power and Wi-Fi, and its device id.
- **Presses don't toggle the lights.**
  - Check that the MINIR4M is in detached relay mode. With the device key, a press that toggles the relay is ignored as
    a relay change, and a repair says that the switch isn't detached.
  - Check that the presses arrive: the switch's *Online* has the time of the *Last press* that the LAN heard. With
    debug logging on, the log names each new announcement of a mapped switch and what sonoff2hue made of it: online,
    wall switch operated, or ignored as a relay change, a settings change or a restart.
  - Check the bridge's *Status*. sonoff2hue runs the switches only while it is *Running*, and its *Message*, its
    *Problems* and Repairs say why it isn't. *Press* fires for each press that it acts on, and *Last action* says what
    came of it.
  - Check Repairs for *Some lights, rooms or zones of … are missing*: the switch toggles only what it finds on the
    bridge. When it finds none, *Last action* is *Failed*, with the *Message* "None of the lights, rooms and zones of
    this switch is on the Hue bridge."
  - Check that nothing else toggles the same lights on each press, such as sonoff2hue in a second Home Assistant: two
    toggles per press leave the lights as they were.
- **The lights toggle when you change something in the eWeLink app.** Add the switch's device key.
- **The bridge can't be reached.** *Reachable* is *Unreachable*, with the reason as its *Message*, such as "Hue bridge
  not reachable: No route to host (192.0.2.10:443)" or "Hue bridge request timed out.", and the log warns "Hue bridge
  at … not reachable (…); retrying every 10 s." or "Hue bridge unavailable: …". Check the bridge's power and network,
  and that Home Assistant reaches it over HTTPS. If its address changed, reserve an address for it in your router's DHCP
  settings, and *Reconfigure* sonoff2hue with it. Presses that wait more than 5 seconds for the bridge are dropped.
- **"The Hue bridge TLS certificate does not match the pinned certificateSha256."** The bridge was replaced or reset, or
  another device now has its address: see the repair *The Hue bridge's certificate changed*.
- **Pairing doesn't work.** If *Find bridges* finds none, enter the bridge's address. "No Hue bridge answered at …"
  gives the reason, such as "Connection refused (192.0.2.10:443)", "The request timed out." or "HTTP 404 Not Found":
  check the address, and that Home Assistant reaches the bridge over HTTPS. Then press the round link button on top of
  the bridge within 60 seconds after sonoff2hue shows the bridge; if the time runs out, choose *Retry*.
- ***Add switch* says "The sonoff2hue integration isn't loaded".** Reload the integration from the ⋮ menu of the
  sonoff2hue entry, then try again.

## Limitations

- Single, double and long presses can't be told apart: that information only goes to the eWeLink cloud.
- A press while sonoff2hue starts, after a change of its bridge or switches, or while the network reconnects only sets
  the baseline.
- A press that waited more than 5 seconds for an unreachable bridge is dropped, not replayed later.
- One Hue bridge per Home Assistant.

## Development

Run `scripts/setup` once: it makes `.venv` with Python 3.14 and the pinned tools, with [uv](https://docs.astral.sh/uv/).
Then `scripts/check` runs what CI runs: ruff, its formatter, mypy and the tests.

- The tests need no network, Hue bridge or MINIR4M. They build the switches' mDNS packets (`tests/engine/packets.py`),
  run a fake Hue bridge on 127.0.0.1 (`tools/fake_hue`), and use fake sockets.
- The mDNS layer, `engine/dns.py`, `engine/mdns.py`, the announcements of `engine/ewelink.py` and the mDNS part of Hue
  discovery in `engine/hue_setup.py`, keeps its behavior exactly. Its recorded cases have to pass unchanged:
  `tests/engine/mdns_packets.json` (packets, with the messages, announcements and probes read from them) and
  `tests/engine/mdns_text.json` (its own rules for white space and letter case, for every character).
- sonoff2hue listens with its own mDNS socket. `tests/test_own_mdns.py` fails when a module imports python-zeroconf,
  Home Assistant's `zeroconf` or `network` integration, or the resolver that answers `.local` names with zeroconf; when
  the engine (`custom_components/sonoff2hue/engine`) imports Home Assistant; or when the manifest has a zeroconf or
  HomeKit matcher, or depends on `zeroconf` or `network`.

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
- Open http://127.0.0.1:8123 and create a throwaway user. The integration is under Settings → Devices & services →
  *Add integration* → sonoff2hue: choose *Enter the bridge address* and enter the fake bridge's, `127.0.0.1:18443`.
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
  `https://127.0.0.1:18443/_fake/` lists its test endpoints. `POST /_fake/rename/{id}?name=…` and
  `POST /_fake/delete/{id}` change a light, room or zone as the Hue app does, until the fake bridge starts again, so
  you can watch the integration follow a rename or skip what is gone. The ids are fixed: `FakeIds` in
  `tools/fake_hue/bridge.py`.
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
  `sonoff2hue_press` and `sonoff2hue_toggle` events, its status in its diagnostics, its entities *Status*, *Last
  action* and *Press*, and its log. Then it renames the light on the fake bridge, as the Hue app does, and restarts
  Home Assistant: the integration must follow the rename and save the light's new name in the switch, the switch's
  last `seq` again must be no press, and the next one a press that switches the light by its new name. Last, it
  deletes the light: the next press must switch the room and leave the light out, *Status* must stay running and name
  the missing light, Repairs must list the switch's issue, and the press after that must switch the room alone.
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
- **Press the simulated wall switch** and **Show the fake Hue bridge's lights** (Terminal → Run Task) run
  `scripts/develop press` and `scripts/develop lights`. The state is in `.dev/`, as with `scripts/develop`.

## Releases

Each version is a GitHub Release, "sonoff2hue X.Y.Z" on the tag `vX.Y.Z`. Home Assistant's Marketplace installs and
updates the integration from these Releases, and shows their notes before an update. It shows this README as it is at
the tag of the Release it offers, so a change to the README reaches the Marketplace with the next release. A published
Release never changes: a fix comes as the next version.

To release, raise `version` in `custom_components/sonoff2hue/manifest.json` through a pull request, then tag the merged
commit on `main` and push the tag. The Release workflow checks the tag and creates the Release.

```sh
git switch main && git pull --ff-only
git tag -a vX.Y.Z -m "sonoff2hue X.Y.Z"
git push origin vX.Y.Z
```

## License

[Apache License 2.0](LICENSE).
