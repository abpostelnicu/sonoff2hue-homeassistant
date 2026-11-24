"""Fixtures shared by the tests."""

from collections.abc import AsyncIterator

import pytest

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
