"""Maps the light, room and zone names (or ids) of a switch to Hue resources: a port of the .NET app's
``Bridge/TargetResolver.cs``."""

from collections.abc import Iterable
from functools import cmp_to_key

from . import errors as dotnet, text
from .config import SwitchSettings
from .hue_client import HueGroup, HueLight, HueTarget, HueTargetKind


def resolve(
    settings: SwitchSettings, lights: list[HueLight], groups: list[HueGroup], errors: list[str]
) -> list[HueTarget]:
    """The targets of a switch. A name or id that matches no resource, or several, is added to ``errors`` with the
    names that would match; a room or zone without a grouped light is too."""
    targets: list[HueTarget] = []
    for reference in (_trim(light) for light in settings.lights):
        matches = [light for light in lights if _matches(reference, light.id, light.name)]
        if len(matches) == 1:
            targets.append(HueTarget(HueTargetKind.LIGHT, matches[0].id, matches[0].name))
        elif not matches:
            known = _list(light.name for light in lights)
            errors.append(f"{settings.display_name}: no Hue light named '{reference}'. Known lights: {known}")
        else:
            ids = _list(match.id for match in matches)
            errors.append(
                f"{settings.display_name}: {len(matches)} lights are named '{reference}'; use the light id instead ({ids})."
            )

    for raw in (_trim(group) for group in settings.groups):
        # Optional "room:" / "zone:" prefix when a room and a zone share a name.
        kind, reference = _split_kind(raw)
        found = [g for g in groups if (kind is None or g.kind == kind) and _matches(reference, g.id, g.name)]
        if len(found) != 1:
            if not found:
                known = _list(f"{g.kind}:{_str(g.name)}" for g in groups)
                errors.append(f"{settings.display_name}: no Hue room or zone named '{raw}'. Known: {known}")
            else:
                names = _list(f"{g.kind}:{_str(g.name)}" for g in found)
                errors.append(f"{settings.display_name}: '{raw}' matches {names}; prefix it with 'room:' or 'zone:'.")
            continue
        group = found[0]
        if group.grouped_light_id is None:
            errors.append(f"{settings.display_name}: {group.kind} '{_str(group.name)}' has no lights.")
            continue
        targets.append(HueTarget(HueTargetKind.GROUPED_LIGHT, group.grouped_light_id, _str(group.name)))

    distinct: dict[tuple[HueTargetKind, str | None], HueTarget] = {}
    for target in targets:
        distinct.setdefault((target.kind, target.id), target)
    return list(distinct.values())


def _trim(value: str | None) -> str:
    """``value.Trim()``, which throws for a null entry of an imported ``sonoff2hue.yaml``."""
    if value is None:
        raise dotnet.null_reference()
    return text.trim(value)


def _matches(reference: str, resource_id: str | None, name: str | None) -> bool:
    if resource_id is not None and text.equals_ignore_case(reference, resource_id):
        return True
    if name is None:
        raise dotnet.null_reference()  # name.Trim()
    return text.equals_ignore_case(reference, text.trim(name))


def _split_kind(value: str) -> tuple[str | None, str]:
    for kind in ("room", "zone"):
        if text.starts_with_ignore_case(value, kind + ":"):
            return kind, text.trim(value[len(kind) + 1 :])
    return None, value


def _list(values: Iterable[str | None]) -> str:
    """``values.Distinct().Order(StringComparer.OrdinalIgnoreCase)``, quoted; ``(none)`` for none. Both sorts are
    stable."""
    ordered = sorted(dict.fromkeys(values), key=cmp_to_key(text.compare_ignore_case))
    return "(none)" if not ordered else ", ".join(f"'{_str(value)}'" for value in ordered)


def _str(value: str | None) -> str:
    """A string in .NET's interpolation, where null is empty."""
    return "" if value is None else value
