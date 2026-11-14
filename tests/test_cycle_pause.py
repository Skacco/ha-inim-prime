"""Experimental pause of the Native connection across the panel's cycle."""
import asyncio

import pytest

from custom_components.inim_prime import cycle_pause as cp
from custom_components.inim_prime.adapters import native_adapter as na
from custom_components.inim_prime.const import PERIODIC_DROP_PERIOD, PERIODIC_DROP_PHASE


class _Client:
    def __init__(self):
        self.is_connected = True
        self.connects = 0
        self.disconnects = 0
        self.reconnects = 0

    async def connect(self):
        self.connects += 1
        self.is_connected = True

    def disconnect(self):
        self.disconnects += 1
        self.is_connected = False

    async def reconnect(self):
        self.reconnects += 1
        self.is_connected = True


def _adapter():
    client = _Client()
    return na.NativeAdapter(client, connected = True), client


def test_next_cycle_instant_is_on_the_cadence():
    now = 1_794_000_000.0
    instant = cp.next_cycle_instant(now)
    assert now < instant <= now + PERIODIC_DROP_PERIOD
    cycles = (instant - PERIODIC_DROP_PHASE) / PERIODIC_DROP_PERIOD
    assert cycles == pytest.approx(round(cycles), abs = 1e-6)
    assert na.matches_periodic_drop_cadence(instant)
    assert cp.next_cycle_instant(instant) == pytest.approx(instant + PERIODIC_DROP_PERIOD)


async def test_requests_wait_for_the_pause_and_no_drop_is_recorded():
    adapter, client = _adapter()
    assert await adapter.pause(idle_timeout = 1)
    assert client.disconnects == 1 and adapter.paused

    calls = []

    async def _read():
        assert client.is_connected, "no request may reach a closed connection"
        calls.append("read")
        return "data"

    request = asyncio.create_task(adapter._call_with_reconnect(_read))
    await asyncio.sleep(0.05)
    assert not request.done() and not calls, "the request waits, it does not reconnect"
    assert client.reconnects == 0

    await adapter.resume()
    assert await request == "data"
    assert client.connects == 1 and client.reconnects == 0
    assert adapter._outage_started is None, "a pause is not a drop"
    assert not adapter._fresh_connection, "a failure after the pause is not a refused password"


async def test_command_issued_during_the_pause_is_sent_once_after_it():
    adapter, client = _adapter()
    await adapter.pause(idle_timeout = 1)
    sent = []

    async def _command():
        sent.append(client.is_connected)

    command = asyncio.create_task(adapter._call_command(_command, ("output", 1, True)))
    await asyncio.sleep(0.05)
    assert not sent
    await adapter.resume()
    await command
    assert sent == [True]


async def test_pause_waits_for_the_request_in_progress():
    adapter, client = _adapter()
    release = asyncio.Event()

    async def _slow_read():
        await release.wait()
        assert client.is_connected, "the connection was closed under a request"
        return "data"

    request = asyncio.create_task(adapter._call_with_reconnect(_slow_read))
    await asyncio.sleep(0)
    pause = asyncio.create_task(adapter.pause(idle_timeout = 1))
    await asyncio.sleep(0.05)
    assert client.disconnects == 0
    release.set()
    assert await request == "data"
    assert await pause is True and client.disconnects == 1
    await adapter.resume()


async def test_pause_is_skipped_when_a_request_does_not_end_in_time():
    adapter, client = _adapter()
    release = asyncio.Event()

    async def _stuck_read():
        await release.wait()

    request = asyncio.create_task(adapter._call_with_reconnect(_stuck_read))
    await asyncio.sleep(0)
    assert await adapter.pause(idle_timeout = 0.05) is False
    assert client.disconnects == 0 and not adapter.paused
    release.set()
    await request


async def test_no_pause_during_an_outage_or_when_not_connected():
    adapter, client = _adapter()
    client.is_connected = False
    assert await adapter.pause(idle_timeout = 0.05) is False
    client.is_connected = True
    adapter._outage_started = 1.0
    assert await adapter.pause(idle_timeout = 0.05) is False
    assert client.disconnects == 0


async def test_failed_reopen_falls_back_to_the_usual_reconnection():
    adapter, client = _adapter()
    await adapter.pause(idle_timeout = 1)

    async def _refused():
        raise ConnectionRefusedError("panel restarting")

    client.connect = _refused
    await adapter.resume()
    assert not adapter.paused, "requests must not stay blocked"

    async def _read():
        if not client.is_connected:
            raise ConnectionError("not connected")
        return "data"

    assert await adapter._call_with_reconnect(_read) == "data"
    assert client.reconnects == 1


async def test_close_during_the_pause_does_not_reopen():
    adapter, client = _adapter()
    await adapter.pause(idle_timeout = 1)
    await adapter.close()
    assert not adapter.paused
    await adapter.resume()
    assert client.connects == 0


async def test_cycle_task_closes_before_and_reopens_after(monkeypatch):
    adapter, client = _adapter()
    monkeypatch.setattr(cp, "NATIVE_CYCLE_PAUSE_DRAIN", 3.0)
    clock = {"now": cp.next_cycle_instant(1_794_000_000.0) - 100.0}
    instant = cp.next_cycle_instant(clock["now"])
    events = []

    real_sleep = asyncio.sleep

    async def _sleep(seconds):
        clock["now"] += seconds
        await real_sleep(0)

    monkeypatch.setattr(cp.time, "time", lambda: clock["now"])
    monkeypatch.setattr(cp.asyncio, "sleep", _sleep)
    disconnect, connect = client.disconnect, client.connect

    def _disconnect():
        events.append(("closed", round(clock["now"] - instant, 1)))
        disconnect()

    async def _connect():
        events.append(("opened", round(clock["now"] - instant, 1)))
        await connect()
        raise asyncio.CancelledError   # one cycle is enough

    client.disconnect, client.connect = _disconnect, _connect
    with pytest.raises(asyncio.CancelledError):
        await cp.run_cycle_pause(adapter, before = 2.0, after = 3.0)
    # nothing was queued: closed at the start of the head start, 2 + 3 s ahead
    assert events == [("closed", -5.0), ("opened", 3.0)]


def test_margins_are_clamped():
    assert cp._margin(None, 5.0) == 5.0
    assert cp._margin("x", 5.0) == 5.0
    assert cp._margin(0, 5.0) == cp.NATIVE_CYCLE_PAUSE_MIN
    assert cp._margin(99, 5.0) == cp.NATIVE_CYCLE_PAUSE_MAX
    assert cp._margin(2, 5.0) == 2.0


async def test_pause_waits_only_for_the_request_in_flight_not_for_the_queue():
    """The requests queued behind the one in flight are not sent: the
    connection closes as soon as that one ends, and they go after the pause."""
    adapter, client = _adapter()
    release = asyncio.Event()
    sent = []

    def _read(name, wait = False):
        async def _do():
            assert client.is_connected, f"{name} reached a closed connection"
            sent.append(name)
            if wait:
                await release.wait()
            return name
        return _do

    in_flight = asyncio.create_task(adapter._call_with_reconnect(_read("zones", wait = True)))
    await asyncio.sleep(0)
    queued = [
        asyncio.create_task(adapter._call_with_reconnect(_read(name)))
        for name in ("partitions", "outputs", "timers")
    ]
    await asyncio.sleep(0)
    assert sent == ["zones"]

    pause = asyncio.create_task(adapter.pause(idle_timeout = 1))
    await asyncio.sleep(0.02)
    assert client.disconnects == 0, "never in the middle of a request"
    release.set()
    assert await in_flight == "zones"
    assert await pause is True
    assert client.disconnects == 1
    assert sent == ["zones"], "the queued requests were not sent before closing"
    assert not any(task.done() for task in queued)

    assert await adapter.resume() is True
    assert await asyncio.gather(*queued) == ["partitions", "outputs", "timers"]
    assert sent == ["zones", "partitions", "outputs", "timers"]
    assert client.reconnects == 0 and adapter._outage_started is None


async def test_resume_reports_a_refused_reopening():
    adapter, client = _adapter()
    await adapter.pause(idle_timeout = 1)

    async def _refused():
        raise ConnectionRefusedError("panel restarting")

    client.connect = _refused
    assert await adapter.resume() is False


async def test_raw_memory_read_waits_for_the_pause():
    adapter, client = _adapter()
    reads = []

    class _Protocol:
        async def read_memory(self, address, length):
            assert client.is_connected
            reads.append(address)
            return bytes(length)

    client._lock = asyncio.Lock()
    client._protocol = _Protocol()
    await adapter.pause(idle_timeout = 1)
    read = asyncio.create_task(adapter.read_memory(0x10, 4))
    await asyncio.sleep(0.02)
    assert not reads
    await adapter.resume()
    assert await read == bytes(4) and reads == [0x10]
