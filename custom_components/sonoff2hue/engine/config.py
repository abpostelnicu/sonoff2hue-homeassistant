"""The settings the engine works with: the Hue bridge and the switches.

Home Assistant keeps these settings in the config entry and its subentries; ``yaml_import`` reads them from a
``sonoff2hue.yaml``.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field


class ConfigError(Exception):
    """Settings that can't be used."""


def _blank(value: str | None) -> bool:
    return not value or value.isspace()


@dataclass(slots=True)
class HueSettings:
    bridge_ip: str | None = None
    bridge_id: str | None = None
    app_key: str | None = None
    certificate_sha256: str | None = None
    """SHA-256 of the bridge's TLS certificate, pinned when pairing (trust on first use)."""


@dataclass(slots=True)
class SwitchSettings:
    device_id: str = ""
    """The eWeLink device id, e.g. ``10020abcde``."""
    name: str | None = None
    device_key: str | None = None
    """The optional eWeLink device key. It lets the engine decrypt the broadcasts and ignore relay toggles and setting
    changes; without it, every new broadcast counts as a press."""
    lights: Sequence[str] = field(default_factory=list)
    """Hue lights, by name or resource id."""
    groups: Sequence[str] = field(default_factory=list)
    """Hue rooms or zones, by name or resource id, optionally prefixed with ``room:`` or ``zone:``."""

    @property
    def normalized_device_id(self) -> str:
        """The device id, stripped and lowercased."""
        return self.device_id.strip().lower()

    @property
    def display_name(self) -> str:
        """The name used in log messages."""
        if _blank(self.name):
            return self.normalized_device_id
        return f"{self.name} ({self.normalized_device_id})"

    def has_device_id(self, device_id: str) -> bool:
        """Whether ``device_id`` is this switch's device id, without regard to case."""
        return self.normalized_device_id.casefold() == device_id.casefold()


@dataclass(slots=True)
class EngineConfig:
    hue: HueSettings = field(default_factory=HueSettings)
    switches: list[SwitchSettings] = field(default_factory=list)

    def find_switch(self, device_id: str) -> SwitchSettings | None:
        """The switch with ``device_id``, without regard to case."""
        return next((switch for switch in self.switches if switch.has_device_id(device_id)), None)

    def validate_for_run(self) -> None:
        """Checks everything the bridge needs to run; raises :class:`ConfigError` listing all problems."""
        errors: list[str] = []
        if _blank(self.hue.bridge_ip):
            errors.append("hue.bridgeIp is missing (pair the bridge).")
        if _blank(self.hue.app_key):
            errors.append("hue.appKey is missing (pair the bridge).")
        if not self.switches:
            errors.append("No switches configured. Add at least one entry to 'switches'.")

        seen: set[str] = set()
        for i, switch in enumerate(self.switches):
            label = f"switches[{i}]" if _blank(switch.name) else f"switches[{i}] ('{switch.name}')"
            if _blank(switch.device_id):
                errors.append(f"{label}: deviceId is missing.")
            elif (key := switch.normalized_device_id.casefold()) in seen:
                errors.append(
                    f"{label}: deviceId '{switch.device_id}' is configured more than once; put all its lights in one entry."
                )
            else:
                seen.add(key)

            if not switch.lights and not switch.groups:
                errors.append(f"{label}: add at least one entry to 'lights' or 'groups'.")
            if any(_blank(name) for name in [*switch.lights, *switch.groups]):
                errors.append(f"{label}: empty light/group name.")

        if errors:
            raise ConfigError("Invalid configuration:\n" + "\n".join("  - " + error for error in errors))
