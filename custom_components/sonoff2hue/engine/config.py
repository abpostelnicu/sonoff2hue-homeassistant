"""The settings the engine works with: the Hue bridge and the switches.

A port of the model in the .NET app's ``Configuration/AppConfig.cs``. Home Assistant keeps these settings in the config
entry and its subentries; ``yaml_import`` reads them from a ``sonoff2hue.yaml``.
"""

from dataclasses import dataclass, field

from . import text


class ConfigError(Exception):
    """Settings that can't be used (``ConfigException`` in .NET)."""


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
    lights: list[str] = field(default_factory=list)
    """Hue lights, by name or resource id."""
    groups: list[str] = field(default_factory=list)
    """Hue rooms or zones, by name or resource id, optionally prefixed with ``room:`` or ``zone:``."""

    @property
    def normalized_device_id(self) -> str:
        return text.lower_invariant(text.trim(self.device_id))

    @property
    def display_name(self) -> str:
        """The name used in log messages."""
        if text.is_null_or_white_space(self.name):
            return self.normalized_device_id
        return f"{self.name} ({self.normalized_device_id})"


@dataclass(slots=True)
class EngineConfig:
    hue: HueSettings = field(default_factory=HueSettings)
    switches: list[SwitchSettings] = field(default_factory=list)

    def find_switch(self, device_id: str) -> SwitchSettings | None:
        return next(
            (s for s in self.switches if text.equals_ignore_case(s.normalized_device_id, device_id)),
            None,
        )

    def validate_for_run(self) -> None:
        """Checks everything the bridge needs to run; raises :class:`ConfigError` listing all problems."""
        errors: list[str] = []
        if text.is_null_or_white_space(self.hue.bridge_ip):
            errors.append("hue.bridgeIp is missing (pair the bridge).")
        if text.is_null_or_white_space(self.hue.app_key):
            errors.append("hue.appKey is missing (pair the bridge).")
        if not self.switches:
            errors.append("No switches configured. Add at least one entry to 'switches'.")

        seen: set[str] = set()
        for i, switch in enumerate(self.switches):
            label = f"switches[{i}]" if text.is_null_or_white_space(switch.name) else f"switches[{i}] ('{switch.name}')"
            if text.is_null_or_white_space(switch.device_id):
                errors.append(f"{label}: deviceId is missing.")
            elif (key := text.fold(switch.normalized_device_id)) in seen:
                errors.append(
                    f"{label}: deviceId '{switch.device_id}' is configured more than once; put all its lights in one entry."
                )
            else:
                seen.add(key)

            if not switch.lights and not switch.groups:
                errors.append(f"{label}: add at least one entry to 'lights' or 'groups'.")
            if any(text.is_null_or_white_space(name) for name in [*switch.lights, *switch.groups]):
                errors.append(f"{label}: empty light/group name.")

        if errors:
            raise ConfigError("Invalid configuration:\n" + "\n".join("  - " + error for error in errors))
