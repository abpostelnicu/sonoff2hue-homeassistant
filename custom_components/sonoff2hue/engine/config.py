"""The settings the engine works with: the switches, and the lights that each one toggles.

Home Assistant keeps these settings in the config entry's ``switch`` subentries.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field


class ConfigError(Exception):
    """Settings that can't be used."""


def _blank(value: str | None) -> bool:
    return not value or value.isspace()


@dataclass(slots=True)
class SwitchSettings:
    device_id: str = ""
    """The eWeLink device id, e.g. ``10020abcde``."""
    name: str | None = None
    device_key: str | None = None
    """The optional eWeLink device key. It lets the engine decrypt the broadcasts and ignore relay toggles and setting
    changes; without it, every new broadcast counts as a press."""
    targets: Sequence[str] = field(default_factory=list)
    """The lights that the switch toggles, as Home Assistant's entity registry ids, or entity ids for lights that the
    registry doesn't have (decision 40). The engine doesn't read them: Home Assistant resolves them to its lights."""

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
    switches: list[SwitchSettings] = field(default_factory=list)

    def find_switch(self, device_id: str) -> SwitchSettings | None:
        """The switch with ``device_id``, without regard to case."""
        return next((switch for switch in self.switches if switch.has_device_id(device_id)), None)

    def validate_for_run(self) -> None:
        """Checks what the switches need to run: at least one switch, each with a device id that no other switch has,
        without regard to case, and no blank light; raises :class:`ConfigError` listing all problems. A switch without
        any light, as one migrated from version 1 can be, isn't a problem: it runs, and each of its presses fails, as
        for a switch whose lights Home Assistant no longer has, while the other switches toggle theirs (decision 32).
        The switch rules still ask for a light when a switch is saved."""
        errors: list[str] = []
        if not self.switches:
            errors.append("No switches configured. Add a switch.")

        seen: set[str] = set()
        for i, switch in enumerate(self.switches):
            label = f"switches[{i}]" if _blank(switch.name) else f"switches[{i}] ('{switch.name}')"
            if _blank(switch.device_id):
                errors.append(f"{label}: deviceId is missing.")
            elif (key := switch.normalized_device_id.casefold()) in seen:
                errors.append(
                    f"{label}: deviceId '{switch.device_id}' is configured more than once; put all its lights in one "
                    "switch."
                )
            else:
                seen.add(key)

            if any(_blank(target) for target in switch.targets):
                errors.append(f"{label}: empty light.")

        if errors:
            raise ConfigError("Invalid configuration:\n" + "\n".join("  - " + error for error in errors))
