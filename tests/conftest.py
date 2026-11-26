"""Fixtures shared by the tests."""

from collections.abc import AsyncIterator

import pytest

from custom_components.sonoff2hue import runtime

from .fakes import Bridge, Finder, Lan
from .hue import FakeHueHost


@pytest.fixture
async def fake_hue(socket_enabled: None) -> AsyncIterator[FakeHueHost]:
    """The fake Hue bridge on 127.0.0.1, its link button released."""
    host = await FakeHueHost.start()
    yield host
    await host.stop()


@pytest.fixture
async def fake_hue_pressed(socket_enabled: None) -> AsyncIterator[FakeHueHost]:
    """The fake Hue bridge on 127.0.0.1, its link button always pressed."""
    host = await FakeHueHost.start(link_button_always_pressed=True)
    yield host
    await host.stop()


@pytest.fixture
def lan(monkeypatch: pytest.MonkeyPatch) -> Lan:
    """The config entry's LAN on a fake network: the runtime's mDNS listener uses it instead of the network."""
    fake = Lan()
    monkeypatch.setattr(runtime, "new_listener", fake.listener)
    return fake


@pytest.fixture
def bridge(monkeypatch: pytest.MonkeyPatch) -> Bridge:
    """A Hue bridge in memory, which the runtime's clients reach whatever the configured address."""
    fake = Bridge()
    monkeypatch.setattr(runtime, "create_hue_client", fake.clients())
    return fake


@pytest.fixture
def finder(monkeypatch: pytest.MonkeyPatch) -> Finder:
    """The runtime's search for its bridge on a network of the test's own, in place of ``runtime.new_bridge_search``
    (decision 33)."""
    fake = Finder()
    monkeypatch.setattr(runtime, "new_bridge_search", fake.search)
    return fake
