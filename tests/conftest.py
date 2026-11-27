"""Fixtures shared by the tests."""

from homeassistant.core import HomeAssistant
import pytest

from custom_components.sonoff2hue import runtime

from .fakes import Lan, Lights


@pytest.fixture
def lan(monkeypatch: pytest.MonkeyPatch) -> Lan:
    """The config entry's LAN on a fake network: the runtime's mDNS listener uses it instead of the network."""
    fake = Lan()
    monkeypatch.setattr(runtime, "new_listener", fake.listener)
    return fake


@pytest.fixture
def lights(hass: HomeAssistant) -> Lights:
    """Lights in Home Assistant's state machine, with ``light`` actions of their own, which the runtime calls."""
    return Lights(hass)
