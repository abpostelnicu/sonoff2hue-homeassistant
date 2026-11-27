"""Tests of the runner: presses in, toggles of the lights out, with what it reports and logs, and the runner on a
listener (``run``) on a fake network."""

import asyncio
import base64
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
import json
import logging
import re

import pytest

from custom_components.sonoff2hue.engine import ewelink, mdns, runner
from custom_components.sonoff2hue.engine.config import EngineConfig, SwitchSettings
from custom_components.sonoff2hue.engine.dns import DnsRecordType, build_query
from custom_components.sonoff2hue.engine.lights import LightError, ToggleResult
from custom_components.sonoff2hue.engine.mdns import MdnsListener, MdnsPacket
from custom_components.sonoff2hue.engine.runner import Runner, RunnerObserver, SwitchBinding

from .fakes import FakeNetwork, FakeTransport, VirtualTime, until
from .packets import DnsPacketBuilder, a, announcement, name, probe, srv, to_packet, txt
from .test_lights import FakeLights

DEVICE_ID = "1000abcdef"
OTHER_DEVICE_ID = "1000999999"
DEVICE_KEY = "qa-only-device-key"
ADDRESS = "192.0.2.5"
OTHER_ADDRESS = "192.0.2.6"
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
QUERY = build_query("_ewelink._tcp.local", DnsRecordType.PTR)
CEILING = "light.ceiling"
DESK = "light.desk"
LAMP = "light.lamp"
NO_TARGETS = "None of the lights of this switch is in Home Assistant."
NO_DEVICE_KEY = (
    "Hall (1000abcdef): no deviceKey configured, so every new state broadcast counts as a press (relay/setting changes "
    "cannot be filtered)."
)


class RecordingObserver(RunnerObserver):
    def __init__(self) -> None:
        self.events: list[str] = []
        self.pressed: list[SwitchBinding] = []
        """The binding of each press, as the observer got it."""

    def on_press(self, binding: SwitchBinding, seq: int | None) -> None:
        self.events.append(f"press {binding.settings.name} seq={seq}")
        self.pressed.append(binding)

    def on_toggled(self, binding: SwitchBinding, result: ToggleResult, elapsed: timedelta) -> None:
        state = "on" if result.turned_on else "off"
        self.events.append(
            f"toggled {binding.settings.name} {state} changed={result.changed} unchanged={result.unchanged} "
            f"failed={result.failed}"
        )

    def on_toggle_failed(self, binding: SwitchBinding, message: str) -> None:
        self.events.append(f"toggle failed {binding.settings.name}: {message}")

    def on_press_dropped(self, binding: SwitchBinding) -> None:
        self.events.append(f"dropped {binding.settings.name}")


class ThrowingObserver(RunnerObserver):
    def __init__(self) -> None:
        self.calls = 0

    def on_press(self, binding: SwitchBinding, seq: int | None) -> None:
        self._fail()

    def on_toggled(self, binding: SwitchBinding, result: ToggleResult, elapsed: timedelta) -> None:
        self._fail()

    def on_toggle_failed(self, binding: SwitchBinding, message: str) -> None:
        self._fail()

    def on_press_dropped(self, binding: SwitchBinding) -> None:
        self._fail()

    def _fail(self) -> None:
        self.calls += 1
        raise RuntimeError("observer failure")


class SilentObserver(RunnerObserver):
    pass


class UnreadableLights(FakeLights):
    """Lights whose states can't be read, with an error that the toggler doesn't expect."""

    def is_on(self, light: str) -> bool | None:
        raise RuntimeError("the states broke")


def hall(device_key: str | None = None, *lights: str) -> SwitchBinding:
    """The switch "Hall", which toggles ``lights``, else the light Ceiling."""
    targets = list(lights or [CEILING])
    return SwitchBinding(SwitchSettings(DEVICE_ID, "Hall", device_key, targets), targets)


def create_runner(
    lights: FakeLights,
    observer: RunnerObserver | None = None,
    binding: SwitchBinding | None = None,
    clock: Callable[[], datetime] = lambda: NOW,
) -> Runner:
    binding = binding or hall()
    return Runner(EngineConfig(switches=[binding.settings]), [binding], lights, observer, clock)


def plain(seq: int, device_id: str = DEVICE_ID, received_at: datetime = NOW) -> MdnsPacket:
    """Without a device key every new sequence number of a configured switch is a press."""
    data = announcement(device_id, seq, f"payload{seq}", "iv", ip=ADDRESS)
    return to_packet(data, ADDRESS, received_at)


def with_other_device(seq: int, other_seq: int) -> MdnsPacket:
    """One packet with the broadcast of the switch, then that of another device."""
    packet = DnsPacketBuilder()
    for device_id, number, ip in ((DEVICE_ID, seq, ADDRESS), (OTHER_DEVICE_ID, other_seq, OTHER_ADDRESS)):
        instance, host = f"eWeLink_{device_id}._ewelink._tcp.local", f"eWeLink_{device_id}.local"
        entries = [f"id={device_id}", "type=plug", f"seq={number}", "encrypt=true", "iv=iv", f"data1=payload{number}"]
        packet.answer("_ewelink._tcp.local", DnsRecordType.PTR, name(instance))
        packet.answer(instance, DnsRecordType.TXT, txt(*entries), cache_flush=True)
        packet.additional(instance, DnsRecordType.SRV, srv(8081, host))
        packet.additional(host, DnsRecordType.A, a(ip))
    return to_packet(packet.build(), ADDRESS, NOW)


def encrypted(
    seq: int,
    relay: str = "on",
    led: str = "on",
    rssi: int = -70,
    ttl: int = 4500,
    device_id: str = DEVICE_ID,
    received_at: datetime = NOW,
    separation: int = 1,
) -> MdnsPacket:
    params = {
        "switches": [{"outlet": 0, "switch": relay}],
        "relaySeparation": separation,
        "sledOnline": led,
        "rssi": rssi,
    }
    data, iv = ewelink.encrypt(json.dumps(params, separators=(",", ":")), DEVICE_KEY)
    return to_packet(announcement(device_id, seq, data, iv, ip=ADDRESS, ttl=ttl), ADDRESS, received_at)


def runner_log(caplog: pytest.LogCaptureFixture) -> list[tuple[int, str]]:
    """The runner's log lines with their levels; the time a toggle took is N."""
    return [
        (record.levelno, re.sub(r" in \d+ ms", " in N ms", record.getMessage()))
        for record in caplog.records
        if record.name == runner.__name__
    ]


@pytest.fixture
async def time() -> AsyncIterator[VirtualTime]:
    virtual = VirtualTime(asyncio.get_running_loop())
    yield virtual
    virtual.restore()


# Presses, toggles and what the observer hears of them.


async def test_a_press_toggles_the_lights_and_reports_each_step() -> None:
    lights = FakeLights({CEILING: False})
    observer = RecordingObserver()
    switch = create_runner(lights, observer)

    switch.handle_packet(plain(100))
    switch.handle_packet(plain(100))
    switch.handle_packet(plain(5, OTHER_DEVICE_ID))
    switch.handle_packet(plain(6, OTHER_DEVICE_ID))
    switch.handle_packet(plain(101))
    await switch.when_toggles_complete()

    assert observer.events == ["press Hall seq=101", "toggled Hall on changed=1 unchanged=0 failed=0"]
    assert lights.calls == [(CEILING, True)]


async def test_lights_that_are_not_switched_are_the_failed_part_of_the_toggle() -> None:
    lights = FakeLights({CEILING: False, LAMP: False})
    lights.errors[LAMP] = LightError("Request failed: the light didn't answer")
    observer = RecordingObserver()
    switch = create_runner(lights, observer, hall(None, CEILING, DESK, LAMP))

    switch.handle_packet(plain(100))
    switch.handle_packet(plain(101))
    await switch.when_toggles_complete()

    # The toggle ran: the light that is unavailable and the one that failed are part of its outcome.
    assert observer.events == ["press Hall seq=101", "toggled Hall on changed=1 unchanged=0 failed=2"]
    assert lights.states == {CEILING: True, LAMP: False}


async def test_a_stale_press_is_dropped_without_switching_the_lights() -> None:
    lights = FakeLights({CEILING: False})
    observer = RecordingObserver()
    switch = create_runner(lights, observer)

    switch.handle_packet(plain(100))
    switch.handle_packet(plain(101, received_at=NOW - timedelta(seconds=10)))
    await switch.when_toggles_complete()

    assert observer.events == ["press Hall seq=101", "dropped Hall"]
    assert lights.calls == []


async def test_a_stale_press_is_dropped_before_handle_packet_returns(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger=runner.__name__)
    lights = FakeLights({CEILING: False})
    observer = RecordingObserver()
    switch = create_runner(lights, observer, clock=lambda: NOW + timedelta(milliseconds=5001))
    switch.handle_packet(plain(100))

    switch.handle_packet(plain(101))

    # Nothing awaited: the toggle's task started eagerly and ended before handle_packet returned.
    assert observer.events == ["press Hall seq=101", "dropped Hall"]
    assert runner_log(caplog)[-2:] == [
        (logging.INFO, "Hall (1000abcdef): wall switch operated (seq 101)."),
        (logging.WARNING, "Hall (1000abcdef): dropped a press that waited more than 5s for the lights."),
    ]
    assert lights.calls == []


async def test_a_toggle_that_switches_nothing_ends_before_the_next_event_of_its_packet(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """None of the lights is available, so the toggle asks none of them and has nothing to wait for."""
    caplog.set_level(logging.DEBUG, logger=runner.__name__)
    observer = RecordingObserver()
    switch = create_runner(FakeLights(), observer)
    switch.handle_packet(plain(100))

    switch.handle_packet(with_other_device(101, 5))

    assert observer.events == ["press Hall seq=101", "toggled Hall on changed=0 unchanged=0 failed=1"]
    assert runner_log(caplog)[1:] == [
        (logging.INFO, "Hall (1000abcdef): wall switch operated (seq 101)."),
        (logging.ERROR, "Could not switch light.ceiling on: it is unavailable"),
        (logging.INFO, "Hall (1000abcdef): turned ON light.ceiling in N ms (1 failed)."),
        (logging.DEBUG, "Ignoring unconfigured eWeLink device 1000999999 at 192.0.2.6."),
    ]


async def test_stopping_ends_running_and_queued_toggles_without_reports() -> None:
    lights = FakeLights({CEILING: False})
    lights.hold(CEILING)  # never answers
    observer = RecordingObserver()
    switch = create_runner(lights, observer)

    switch.handle_packet(plain(100))
    switch.handle_packet(plain(101))
    await until(lambda: lights.calls == [(CEILING, True)])
    switch.handle_packet(plain(102))
    idle = asyncio.create_task(switch.when_toggles_complete())
    await asyncio.sleep(0)
    assert not idle.done()

    await switch.stop_toggles()
    await asyncio.wait_for(idle, 10)
    await switch.stop_toggles()  # nothing is left to stop

    assert observer.events == ["press Hall seq=101", "press Hall seq=102"]
    assert lights.cancelled == [CEILING]
    assert lights.calls == [(CEILING, True)]


async def test_cancelling_a_wait_for_the_toggles_does_not_stop_them(time: VirtualTime) -> None:
    lights = FakeLights({CEILING: False})
    answer = lights.hold(CEILING)
    observer = RecordingObserver()
    switch = create_runner(lights, observer)
    switch.handle_packet(plain(100))
    switch.handle_packet(plain(101))

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(switch.when_toggles_complete(), 1)
    answer.set()
    await asyncio.wait_for(switch.when_toggles_complete(), 1)

    assert observer.events == ["press Hall seq=101", "toggled Hall on changed=1 unchanged=0 failed=0"]


async def test_observer_exceptions_do_not_stop_the_toggle(caplog: pytest.LogCaptureFixture) -> None:
    lights = FakeLights({CEILING: False})
    observer = ThrowingObserver()
    switch = create_runner(lights, observer)

    switch.handle_packet(plain(100))
    switch.handle_packet(plain(101))
    await switch.when_toggles_complete()

    assert observer.calls == 2
    assert lights.states[CEILING] is True
    failures = [
        record for record in caplog.records if record.getMessage() == "Runner observer ThrowingObserver failed."
    ]
    assert len(failures) == 2
    assert all(record.levelno == logging.WARNING and record.exc_info for record in failures)


async def test_an_observer_without_overrides_uses_the_empty_defaults() -> None:
    lights = FakeLights({CEILING: False})
    porch = SwitchBinding(SwitchSettings(OTHER_DEVICE_ID, "Porch", None, [LAMP]), [])
    bindings = [hall(), porch]
    switch = Runner(
        EngineConfig(switches=[b.settings for b in bindings]), bindings, lights, SilentObserver(), lambda: NOW
    )

    # A toggle, a dropped press and a toggle that can't run, each reported to the observer.
    switch.handle_packet(plain(100))
    switch.handle_packet(plain(101))
    switch.handle_packet(plain(102, received_at=NOW - timedelta(seconds=10)))
    switch.handle_packet(plain(5, OTHER_DEVICE_ID))
    switch.handle_packet(plain(6, OTHER_DEVICE_ID))
    await switch.when_toggles_complete()

    assert lights.calls == [(CEILING, True)]


async def test_a_switch_without_lights_reports_its_presses_and_fails_each_toggle(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Home Assistant has none of the switch's lights, or the switch has none, as one migrated from version 1 can be
    (decision 32): the press is still reported, and its toggle fails with a message that says why, without asking any
    light."""
    caplog.set_level(logging.INFO, logger=runner.__name__)
    lights = FakeLights({CEILING: False})
    observer = RecordingObserver()
    removed = "Hall (1000abcdef): a light was removed from Home Assistant."
    nothing = SwitchBinding(SwitchSettings(DEVICE_ID, "Hall", None, ["0123456789abcdef"]), [], [removed])
    switch = create_runner(lights, observer, nothing)

    switch.handle_packet(plain(100))
    switch.handle_packet(plain(101))
    switch.handle_packet(plain(102))
    await switch.when_toggles_complete()

    assert observer.events == [
        "press Hall seq=101",
        f"toggle failed Hall: {NO_TARGETS}",
        "press Hall seq=102",
        f"toggle failed Hall: {NO_TARGETS}",
    ]
    assert lights.calls == []
    assert runner_log(caplog)[-2:] == [
        (logging.INFO, "Hall (1000abcdef): wall switch operated (seq 102)."),
        (logging.ERROR, f"Hall (1000abcdef): could not toggle the lights: {NO_TARGETS}"),
    ]


# New lights while the runner runs (decision 32).


async def test_new_lights_reach_a_running_runner_which_keeps_its_baselines_and_the_switchs_turns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A toggle that a press started before goes on with the lights that it had, and the next one waits for it."""
    caplog.set_level(logging.INFO, logger=runner.__name__)
    lights = FakeLights({CEILING: False, DESK: False})
    release = lights.hold(CEILING)
    observer = RecordingObserver()
    switch = create_runner(lights, observer)
    switch.handle_packet(plain(100))
    switch.handle_packet(plain(101))
    switch.handle_packet(plain(102))
    await until(lambda: lights.calls == [(CEILING, True)])
    caplog.clear()

    removed = "Hall (1000abcdef): light.ceiling was removed from Home Assistant."
    switch.update_targets([SwitchBinding(hall().settings, [DESK], [removed])])
    switch.update_targets([SwitchBinding(hall().settings, [DESK], [removed])])
    switch.handle_packet(plain(103))
    release.set()
    await switch.when_toggles_complete()

    # The presses before the new lights toggle the light they had, in turn; the one after toggles the new one.
    assert lights.calls == [(CEILING, True), (CEILING, False), (DESK, True)]
    assert observer.events == [
        "press Hall seq=101",
        "press Hall seq=102",
        "press Hall seq=103",
        "toggled Hall on changed=1 unchanged=0 failed=0",
        "toggled Hall off changed=1 unchanged=0 failed=0",
        "toggled Hall on changed=1 unchanged=0 failed=0",
    ]
    assert [(binding.targets, binding.problems) for binding in observer.pressed] == [
        ([CEILING], []),
        ([CEILING], []),
        ([DESK], [removed]),
    ]
    assert runner_log(caplog)[0] == (logging.INFO, "Hall (1000abcdef) -> light.desk")
    assert [message for _, message in runner_log(caplog)].count("Hall (1000abcdef) -> light.desk") == 1


async def test_new_problems_alone_are_taken_quietly_and_switches_that_the_runner_lacks_are_left_out(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=runner.__name__)
    lights = FakeLights({CEILING: False, DESK: False})
    observer = RecordingObserver()
    switch = create_runner(lights, observer)
    disabled = "Hall (1000abcdef): light.desk is disabled."

    switch.update_targets(
        [
            SwitchBinding(hall().settings, [CEILING], [disabled]),
            SwitchBinding(SwitchSettings(OTHER_DEVICE_ID, "Porch", None, [DESK]), [DESK]),
        ]
    )
    for packet in (plain(100), plain(101), plain(5, OTHER_DEVICE_ID), plain(6, OTHER_DEVICE_ID)):
        switch.handle_packet(packet)
    await switch.when_toggles_complete()

    assert [(binding.targets, binding.problems) for binding in observer.pressed] == [([CEILING], [disabled])]
    assert lights.calls == [(CEILING, True)]
    assert not any(" -> " in message for _, message in runner_log(caplog))


# Encrypted broadcasts, queued presses and the age of a press.


@pytest.mark.parametrize("ceiling_on", [False, True])
@pytest.mark.parametrize("desk_on", [False, True])
@pytest.mark.parametrize("lamp_on", [False, True])
async def test_encrypted_presses_toggle_the_lights_without_false_presses(
    ceiling_on: bool, desk_on: bool, lamp_on: bool
) -> None:
    settings = SwitchSettings(" 1000ABCDEF ", "Hall", DEVICE_KEY, [CEILING, DESK, LAMP])
    config = EngineConfig(switches=[settings])
    config.validate_for_run()
    initial = {CEILING: ceiling_on, DESK: desk_on, LAMP: lamp_on}
    lights = FakeLights(initial)
    switch = Runner(config, [SwitchBinding(settings, [CEILING, DESK, LAMP])], lights, clock=lambda: NOW)

    switch.handle_packet(encrypted(100))
    switch.handle_packet(encrypted(100))
    switch.handle_packet(encrypted(101, ttl=0))
    switch.handle_packet(encrypted(500, device_id=OTHER_DEVICE_ID))
    switch.handle_packet(encrypted(501, device_id=OTHER_DEVICE_ID))
    switch.handle_packet(encrypted(101))
    await switch.when_toggles_complete()

    first_on = not any(initial.values())
    first_calls = [(light, first_on) for light, on in initial.items() if on != first_on]
    assert lights.calls == first_calls
    assert all(on == first_on for on in lights.states.values())

    switch.handle_packet(encrypted(101))
    switch.handle_packet(encrypted(102, relay="off"))
    switch.handle_packet(encrypted(103, relay="off", led="off"))
    switch.handle_packet(encrypted(1, relay="off", led="off"))
    switch.handle_packet(to_packet(probe(DEVICE_ID, ADDRESS), ADDRESS, NOW))
    switch.handle_packet(encrypted(2, relay="off", led="off"))
    switch.handle_packet(encrypted(2, relay="off", led="off"))
    await switch.when_toggles_complete()
    assert lights.calls == first_calls

    switch.handle_packet(encrypted(3, relay="off", led="off", rssi=-50))
    await switch.when_toggles_complete()

    assert lights.calls == first_calls + [(light, not first_on) for light in initial]
    assert all(on != first_on for on in lights.states.values())


@pytest.mark.parametrize("first_fails", [False, True])
async def test_queued_presses_toggle_in_turn_from_the_state_that_the_one_before_left(first_fails: bool) -> None:
    lights = FakeLights({CEILING: False})
    if first_fails:
        lights.fail_once(CEILING, LightError("Request failed: the light didn't answer"))
    binding = hall(DEVICE_KEY)
    switch = create_runner(lights, binding=binding)

    await binding.gate.acquire()
    try:
        switch.handle_packet(encrypted(100))
        switch.handle_packet(encrypted(101))
        switch.handle_packet(encrypted(102))
        waiting = asyncio.create_task(switch.when_toggles_complete())
        await asyncio.sleep(0)
        assert lights.calls == []
        assert not waiting.done()
    finally:
        binding.gate.release()
    await waiting

    # After a failed first toggle the light is still off, so the second one turns it on again.
    assert lights.calls == [(CEILING, True), (CEILING, first_fails)]
    assert lights.states[CEILING] is first_fails


async def test_a_switch_waits_for_its_own_toggles_only() -> None:
    lights = FakeLights({CEILING: False, DESK: False})
    release = lights.hold(CEILING)
    observer = RecordingObserver()
    porch = SwitchBinding(SwitchSettings(OTHER_DEVICE_ID, "Porch", None, [DESK]), [DESK])
    bindings = [hall(), porch]
    switch = Runner(EngineConfig(switches=[b.settings for b in bindings]), bindings, lights, observer, lambda: NOW)

    for packet in (plain(100), plain(101), plain(102), plain(5, OTHER_DEVICE_ID), plain(6, OTHER_DEVICE_ID)):
        switch.handle_packet(packet)
    await until(lambda: lights.states[DESK] is True)

    # Porch's toggle ended while Hall's first one waits for its light, and Hall's second one for its turn.
    assert observer.events == [
        "press Hall seq=101",
        "press Hall seq=102",
        "press Porch seq=6",
        "toggled Porch on changed=1 unchanged=0 failed=0",
    ]
    release.set()
    await switch.when_toggles_complete()

    assert observer.events[4:] == [
        "toggled Hall on changed=1 unchanged=0 failed=0",
        "toggled Hall off changed=1 unchanged=0 failed=0",
    ]
    assert lights.calls == [(CEILING, True), (DESK, True), (CEILING, False)]


@pytest.mark.parametrize(("age", "dropped"), [(timedelta(milliseconds=5001), True), (timedelta(seconds=5), False)])
async def test_a_queued_press_just_beyond_five_seconds_is_dropped_and_releases_the_gate(
    age: timedelta, dropped: bool
) -> None:
    lights = FakeLights({CEILING: False})
    observer = RecordingObserver()
    binding = hall(DEVICE_KEY)
    switch = create_runner(lights, observer, binding)

    await binding.gate.acquire()
    try:
        switch.handle_packet(encrypted(100))
        switch.handle_packet(encrypted(101, received_at=NOW - age))
        switch.handle_packet(encrypted(102))
        await asyncio.sleep(0)
        assert lights.calls == []
    finally:
        binding.gate.release()
    await switch.when_toggles_complete()

    presses = ["press Hall seq=101", "press Hall seq=102"]
    turned_on = "toggled Hall on changed=1 unchanged=0 failed=0"
    if dropped:
        assert observer.events == [*presses, "dropped Hall", turned_on]
        assert lights.calls == [(CEILING, True)]
    else:  # exactly 5 s isn't more than 5 s
        assert observer.events == [*presses, turned_on, "toggled Hall off changed=1 unchanged=0 failed=0"]
        assert lights.calls == [(CEILING, True), (CEILING, False)]


async def test_an_unexpected_error_while_toggling_is_logged_with_its_trace_and_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    observer = RecordingObserver()
    switch = create_runner(UnreadableLights({CEILING: False}), observer)

    switch.handle_packet(plain(100))
    switch.handle_packet(plain(101))
    await switch.when_toggles_complete()

    assert observer.events == ["press Hall seq=101", "toggle failed Hall: the states broke"]
    [record] = [
        r for r in caplog.records if r.getMessage() == "Hall (1000abcdef): unexpected error while toggling the lights."
    ]
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None
    assert record.exc_info[0] is RuntimeError


def test_a_device_id_mapped_twice_is_refused() -> None:
    first = SwitchBinding(SwitchSettings(DEVICE_ID, "Hall"), [CEILING])
    second = SwitchBinding(SwitchSettings(" 1000ABCDEF ", "Hall again"), [CEILING])
    config = EngineConfig(switches=[first.settings, second.settings])

    with pytest.raises(ValueError, match=r"^More than one switch has the device id 1000abcdef\.$"):
        Runner(config, [first, second], FakeLights())


# What the runner logs.


async def test_the_runner_logs_what_each_broadcast_means(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(mdns.TRACE, logger=runner.__name__)
    switch = create_runner(FakeLights({CEILING: False}), binding=hall(DEVICE_KEY))

    switch.handle_packet(encrypted(100))
    switch.handle_packet(encrypted(100))
    switch.handle_packet(encrypted(101, relay="off"))
    switch.handle_packet(encrypted(102, relay="off", led="off"))
    switch.handle_packet(encrypted(1, relay="off", led="off"))
    switch.handle_packet(to_packet(probe(DEVICE_ID, ADDRESS), ADDRESS, NOW))
    switch.handle_packet(to_packet(probe(OTHER_DEVICE_ID, OTHER_ADDRESS), OTHER_ADDRESS, NOW))
    switch.handle_packet(encrypted(2, relay="off", led="off"))
    switch.handle_packet(encrypted(3, relay="off", led="off", rssi=-50))
    await switch.when_toggles_complete()
    switch.handle_packet(plain(5, OTHER_DEVICE_ID))
    switch.handle_packet(plain(6, OTHER_DEVICE_ID))

    # Only a configured switch's rejoin is worth a line.
    name = "Hall (1000abcdef)"
    assert runner_log(caplog) == [
        (logging.INFO, f"{name}: online at 192.0.2.5 (seq 100; relay0=on relaySeparation=1 rssi=-70)."),
        (mdns.TRACE, f"{name}: duplicate broadcast (seq 100)."),
        (logging.INFO, f"{name}: ignored, relay changed (relay 0 on -> off)."),
        (logging.INFO, f"{name}: ignored, settings changed (sledOnline)."),
        (logging.INFO, f"{name}: ignored, device restarted (seq went from 102 to 1)."),
        (logging.INFO, f"{name} (re)joined the network."),
        (logging.INFO, f"{name}: online at 192.0.2.5 (seq 2; relay0=off relaySeparation=1 rssi=-70)."),
        (logging.INFO, f"{name}: wall switch operated (seq 3)."),
        (logging.INFO, f"{name}: turned ON light.ceiling in N ms."),
        (logging.DEBUG, "Ignoring unconfigured eWeLink device 1000999999 at 192.0.2.5."),
    ]


def test_a_missing_seq_or_address_is_logged_as_none(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger=runner.__name__)
    switch = create_runner(FakeLights())

    # Without SRV and A records, a copy from this host gives no address.
    for device_id in (DEVICE_ID, OTHER_DEVICE_ID):
        copy = announcement(device_id, None, "payload", "iv", ip=None)
        switch.handle_packet(to_packet(copy, ADDRESS, NOW, from_this_host=True))

    assert runner_log(caplog) == [
        (logging.INFO, "Hall (1000abcdef): online at None (seq None; payload not decoded)."),
        (logging.DEBUG, "Ignoring unconfigured eWeLink device 1000999999 at None."),
    ]


def test_payload_problems_and_a_switch_that_is_not_detached_are_warned_about_when_they_change(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=runner.__name__)
    switch = create_runner(FakeLights(), binding=hall(DEVICE_KEY))
    short_iv = announcement(DEVICE_ID, 100, "AAAA", base64.b64encode(bytes(15)).decode(), ip=ADDRESS)

    switch.handle_packet(to_packet(short_iv, ADDRESS, NOW))
    switch.handle_packet(to_packet(short_iv, ADDRESS, NOW))
    switch.handle_packet(encrypted(100, separation=0))
    switch.handle_packet(to_packet(short_iv, ADDRESS, NOW))
    switch.handle_packet(encrypted(100, separation=0))

    name = "Hall (1000abcdef)"
    unreadable = (
        logging.WARNING,
        f"{name}: cannot read the LAN payload (iv has 15 bytes instead of 16); falling back to sequence numbers only.",
    )
    assert runner_log(caplog) == [
        unreadable,
        (logging.INFO, f"{name}: online at 192.0.2.5 (seq 100; payload not decoded)."),
        (
            logging.WARNING,
            f"{name} is not in detached relay mode, so the wall switch cuts power to the lights. Enable 'Detach relay' "
            "in the eWeLink app.",
        ),
        unreadable,
    ]


async def test_toggles_log_their_outcome(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger=runner.__name__)
    four = ["light.a", "light.b", "light.c", "light.d"]
    lights = FakeLights({"light.a": False, "light.b": False, "light.c": True})
    lights.fail_once("light.c", LightError("Request failed: the light didn't answer"))
    switch = create_runner(lights, binding=SwitchBinding(SwitchSettings(DEVICE_ID, "Hall", None, four), four))

    switch.handle_packet(plain(100))
    switch.handle_packet(plain(101))
    await switch.when_toggles_complete()
    switch.handle_packet(plain(102))
    await switch.when_toggles_complete()
    lights.states["light.d"] = False
    switch.handle_packet(plain(103))
    await switch.when_toggles_complete()
    switch.handle_packet(plain(104, received_at=NOW - timedelta(seconds=10)))
    await switch.when_toggles_complete()

    name = "Hall (1000abcdef)"
    unavailable = (logging.ERROR, "Could not switch light.d off: it is unavailable")
    all_four = "light.a, light.b, light.c, light.d"
    assert runner_log(caplog) == [
        (logging.INFO, f"{name}: online at 192.0.2.5 (seq 100; payload not decoded)."),
        (logging.INFO, f"{name}: wall switch operated (seq 101)."),
        unavailable,
        (logging.ERROR, "Could not switch light.c off: Request failed: the light didn't answer"),
        (logging.INFO, f"{name}: turned OFF {all_four} in N ms (2 already off, 2 failed)."),
        (logging.INFO, f"{name}: wall switch operated (seq 102)."),
        unavailable,
        (logging.INFO, f"{name}: turned OFF {all_four} in N ms (2 already off, 1 failed)."),
        (logging.INFO, f"{name}: wall switch operated (seq 103)."),
        (logging.INFO, f"{name}: turned ON {all_four} in N ms."),
        (logging.INFO, f"{name}: wall switch operated (seq 104)."),
        (logging.WARNING, f"{name}: dropped a press that waited more than 5s for the lights."),
    ]


# The runner on a listener, which it shares with the LAN monitor (decision 8).


class Lan:
    """The config entry's mDNS listener on a fake network, as the LAN monitor runs it; on the wall clock without a
    ``clock``."""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self.network = FakeNetwork()
        self.listener = MdnsListener(self.network.open, self.network.watch, clock=clock)
        self.listening: asyncio.Task[None] | None = None

    async def open(self) -> FakeTransport:
        self.listening = asyncio.create_task(self.listener.run())
        return await self.network.next_open()

    async def restart(self) -> FakeTransport:
        """A network change, after which the listener opens a new socket."""
        self.network.network_changed()
        return await self.network.next_open()

    def broadcast(self, seq: int) -> None:
        self.network.deliver(announcement(DEVICE_ID, seq, f"payload{seq}", "iv", ip=ADDRESS), (ADDRESS, 5353))

    async def close(self) -> None:
        if self.listening is not None:
            await _stop(self.listening)


@pytest.fixture
async def lan(time: VirtualTime) -> AsyncIterator[Lan]:
    """The listener on virtual time; it closes after the test."""
    lan = Lan(time.utc)
    yield lan
    await lan.close()


async def _stop(task: asyncio.Task[None]) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_run_asks_for_the_switches_at_0_1_and_3_seconds_then_every_30_while_one_is_unseen(
    time: VirtualTime, lan: Lan, caplog: pytest.LogCaptureFixture
) -> None:
    """Once every switch was seen, no switch can cause a false press, and the runner stops asking."""
    caplog.set_level(logging.INFO, logger=runner.__name__)
    transport = await lan.open()
    running = asyncio.create_task(create_runner(FakeLights({CEILING: False}), clock=time.utc).run(lan.listener))

    counts = []
    for seconds in (0.5, 1.5, 3.5, 32.5, 33.5):
        await asyncio.sleep(seconds - time.elapsed)
        counts.append(len(transport.sent))
    lan.broadcast(100)
    await asyncio.sleep(100)
    counts.append(len(transport.sent))

    assert counts == [1, 2, 3, 3, 4, 4]
    assert set(transport.sent) == {(QUERY, "192.0.2.1")}
    assert [line for line in runner_log(caplog) if line[0] == logging.WARNING] == [
        (logging.WARNING, "Not seen on the LAN yet: Hall (1000abcdef)")
    ]
    await _stop(running)


@pytest.mark.usefixtures("time")
async def test_run_toggles_on_presses_and_stopping_it_leaves_the_listener_running() -> None:
    """Without clocks, the listener stamps each packet with the wall clock, and the runner tells a press's age by it."""
    lan = Lan()
    try:
        transport = await lan.open()
        lights = FakeLights({CEILING: False})
        observer = RecordingObserver()
        binding = hall()
        switch = Runner(EngineConfig(switches=[binding.settings]), [binding], lights, observer)
        running = asyncio.create_task(switch.run(lan.listener))
        await asyncio.sleep(5)

        lan.broadcast(100)
        lan.broadcast(101)
        await switch.when_toggles_complete()

        assert observer.events == ["press Hall seq=101", "toggled Hall on changed=1 unchanged=0 failed=0"]
        assert len(transport.sent) == 3

        await _stop(running)
        lan.broadcast(102)
        restarted = await lan.restart()
        await asyncio.sleep(60)

        assert restarted.sent == []  # the runner's started callback, which asks for the switches, is gone
        assert lan.listener.is_open
        assert observer.events == ["press Hall seq=101", "toggled Hall on changed=1 unchanged=0 failed=0"]
        assert lights.calls == [(CEILING, True)]
    finally:
        await lan.close()


async def test_stopping_the_runner_ends_its_queries_and_its_toggles_while_the_listener_goes_on(
    time: VirtualTime, lan: Lan
) -> None:
    """Every toggle is told to stop before the runner waits for anything: a light that answers just as the runner
    stops isn't switched."""
    transport = await lan.open()
    lights = FakeLights({CEILING: False})
    answer = lights.hold(CEILING)
    observer = RecordingObserver()
    running = asyncio.create_task(create_runner(lights, observer, clock=time.utc).run(lan.listener))
    await asyncio.sleep(1.5)
    lan.broadcast(100)
    lan.broadcast(101)
    await until(lambda: lights.calls == [(CEILING, True)])

    running.cancel()
    answer.set()
    with pytest.raises(asyncio.CancelledError):
        await running
    await asyncio.sleep(60)

    # The queries at 0 and 1 s went out, the one at 3 s never did; the toggle was stopped and reported nothing.
    assert len(transport.sent) == 2
    assert lights.cancelled == [CEILING]
    assert lights.states[CEILING] is False
    assert observer.events == ["press Hall seq=101"]
    assert lan.listening is not None
    assert not lan.listening.done()


async def test_every_restart_of_the_socket_takes_a_new_baseline(time: VirtualTime, lan: Lan) -> None:
    lights = FakeLights({CEILING: False})
    observer = RecordingObserver()
    switch = create_runner(lights, observer, clock=time.utc)
    running = asyncio.create_task(switch.run(lan.listener))  # before the socket is open: it asks once it is
    first = await lan.open()
    await asyncio.sleep(5)
    lan.broadcast(100)

    second = await lan.restart()
    await asyncio.sleep(5)
    lan.broadcast(101)
    await switch.when_toggles_complete()
    assert observer.events == []

    lan.broadcast(102)
    await switch.when_toggles_complete()

    assert (len(first.sent), len(second.sent)) == (3, 3)
    assert observer.events == ["press Hall seq=102", "toggled Hall on changed=1 unchanged=0 failed=0"]
    await _stop(running)


async def test_a_broadcast_right_after_a_restart_of_the_socket_is_a_new_baseline(time: VirtualTime, lan: Lan) -> None:
    await lan.open()
    lights = FakeLights({CEILING: False})
    observer = RecordingObserver()
    running = asyncio.create_task(create_runner(lights, observer, clock=time.utc).run(lan.listener))
    await asyncio.sleep(5)
    lan.broadcast(100)

    await lan.restart()
    lan.broadcast(101)

    # The new socket's started callback, which starts eagerly, reset the baselines before its first packet.
    assert observer.events == []
    assert lights.calls == []
    await _stop(running)


async def test_run_sends_its_first_query_before_it_first_waits(lan: Lan, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=runner.__name__)
    transport = await lan.open()
    switch = create_runner(FakeLights({CEILING: False}))

    running = asyncio.get_running_loop().create_task(switch.run(lan.listener), eager_start=True)

    # Nothing awaited: run() went on until it first waited, the first of three queries included.
    assert runner_log(caplog) == [
        (logging.INFO, "Hall (1000abcdef) -> light.ceiling"),
        (logging.INFO, NO_DEVICE_KEY),
        (logging.INFO, "Listening for wall switch presses."),
    ]
    assert transport.sent == [(QUERY, "192.0.2.1")]
    await _stop(running)


async def test_run_names_what_each_switch_toggles(lan: Lan, caplog: pytest.LogCaptureFixture) -> None:
    """Also a switch that has nothing left to toggle (decision 32)."""
    caplog.set_level(logging.INFO, logger=runner.__name__)
    removed = "1000999999: light.lamp was removed from Home Assistant."
    nothing = SwitchBinding(SwitchSettings(OTHER_DEVICE_ID, None, "key", [LAMP]), [], [removed])
    bindings = [hall(None, CEILING, DESK), nothing]
    switch = Runner(EngineConfig(switches=[b.settings for b in bindings]), bindings, FakeLights())

    running = asyncio.get_running_loop().create_task(switch.run(lan.listener), eager_start=True)

    assert runner_log(caplog) == [
        (logging.INFO, "Hall (1000abcdef) -> light.ceiling, light.desk"),
        (logging.INFO, NO_DEVICE_KEY),
        (logging.INFO, "1000999999 -> (none)"),
        (logging.INFO, "Listening for wall switch presses."),
    ]
    await _stop(running)
