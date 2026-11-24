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

## License

[Apache License 2.0](LICENSE).
