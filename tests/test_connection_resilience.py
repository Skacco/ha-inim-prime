"""Short connection drops must not flip entities to unavailable, and a
command issued while the panel is unreachable waits for it to come back."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.inim_prime.adapters import native_adapter as na
from custom_components.inim_prime.coordinators import base_coordinator as bc


async def _no_sleep(_):
    return None


def _coordinator(hass, results):
    """A coordinator whose reads return/raise the queued results in order."""

    class _Coordinator(bc.InimPrimeBaseCoordinator):
        def __init__(self):
            # skip the real init (needs a config entry and a gateway)
            bc.DataUpdateCoordinator.__init__(
                self, hass, bc._LOGGER, name = "test", config_entry = None,
                update_interval = timedelta(seconds = 1),
            )

        async def _async_update_data(self):
            result = results.pop(0)
            if isinstance(result, Exception):
                raise UpdateFailed(result)
            return result

    return _Coordinator()


async def test_failed_read_within_grace_keeps_last_data(hass):
    coordinator = _coordinator(hass, [{"zone": "ok"}, ConnectionError("down"), {"zone": "new"}])

    await coordinator.async_refresh()
    assert coordinator.last_update_success and coordinator.data == {"zone": "ok"}

    assert coordinator.backend_reachable

    await coordinator.async_refresh()
    assert coordinator.last_update_success, "a short drop must not make entities unavailable"
    assert coordinator.data == {"zone": "ok"}
    assert not coordinator.backend_reachable, "the last-poll diagnostics must still see the drop"
    assert coordinator.last_drop_at is None, "the drop is reported once it is over"

    await coordinator.async_refresh()
    assert coordinator.data == {"zone": "new"}
    assert coordinator.backend_reachable
    assert coordinator.last_drop_at is not None
    assert coordinator.last_drop_duration is not None and coordinator.last_drop_duration >= 0


async def test_failed_read_after_grace_is_reported(hass, monkeypatch):
    coordinator = _coordinator(hass, [{"zone": "ok"}, ConnectionError("down")])
    await coordinator.async_refresh()

    now = bc.time.monotonic()
    monkeypatch.setattr(bc.time, "monotonic", lambda: now + bc.CONNECTION_GRACE_PERIOD + 1)
    await coordinator.async_refresh()
    assert not coordinator.last_update_success


async def test_first_read_failure_is_reported(hass):
    coordinator = _coordinator(hass, [ConnectionError("down")])
    await coordinator.async_refresh()
    assert not coordinator.last_update_success


def test_periodic_drop_cadence():
    # 2026-11-12 07:34:13.6 CET, one of the drops seen on two installations
    assert na.matches_periodic_drop_cadence(1794465253.6)
    assert na.matches_periodic_drop_cadence(1794465253.6 - na.PERIODIC_DROP_PERIOD)
    # 02:41:44 the same night was a drop off the cadence
    assert not na.matches_periodic_drop_cadence(1794465253.6 - 17549)


class _FlakyClient:
    """Refuses connections `refusals` times, then works."""

    def __init__(self, refusals: int):
        self.refusals = refusals
        self.is_connected = False
        self.sent = []

    async def reconnect(self):
        if self.refusals:
            self.refusals -= 1
            raise ConnectionRefusedError("refused")
        self.is_connected = True

    async def set_output(self, output_id, state):
        if not self.is_connected:
            raise ConnectionError("not active")
        self.sent.append((output_id, state))


async def test_command_waits_for_the_panel(monkeypatch):
    monkeypatch.setattr(na, "_sleep", _no_sleep)
    monkeypatch.setattr(na, "NATIVE_RECONNECT_MIN_INTERVAL", 0)
    client = _FlakyClient(refusals = 3)
    adapter = na.NativeAdapter(client)
    adapter._outage_started = asyncio.get_running_loop().time()

    await adapter.set_output(7, True)

    assert client.sent == [(7, True)], "the command is sent once, after the panel is back"
    assert adapter._outage_started is None


async def test_command_gives_up_after_the_window(monkeypatch):
    monkeypatch.setattr(na, "_sleep", _no_sleep)
    monkeypatch.setattr(na, "NATIVE_RECONNECT_MIN_INTERVAL", 0)
    monkeypatch.setattr(na, "NATIVE_COMMAND_RETRY_WINDOW", 0)
    client = _FlakyClient(refusals = 10**6)
    adapter = na.NativeAdapter(client)
    adapter._outage_started = asyncio.get_running_loop().time()

    with pytest.raises(ConnectionError):
        await adapter.set_output(7, True)
    assert client.sent == []


async def test_second_tap_while_held_is_dropped(monkeypatch):
    """Pressing again because "nothing happened" must not send two pulses."""
    released = asyncio.Event()

    async def _wait(_):
        await released.wait()
    monkeypatch.setattr(na, "_sleep", _wait)
    monkeypatch.setattr(na, "NATIVE_RECONNECT_MIN_INTERVAL", 0)
    client = _FlakyClient(refusals = 1)
    adapter = na.NativeAdapter(client)
    adapter._outage_started = asyncio.get_running_loop().time()

    first = asyncio.create_task(adapter.set_output(7, True))
    await asyncio.sleep(0)          # the first tap is now waiting for the panel
    await adapter.set_output(7, True)   # second tap: dropped, returns at once
    assert client.sent == []

    released.set()
    await first
    assert client.sent == [(7, True)]

    # once nothing is waiting, the same command goes through again
    await adapter.set_output(7, True)
    assert client.sent == [(7, True), (7, True)]


def _primelan_adapter(failures: list):
    from aiohttp import ClientConnectorError
    from aiohttp.client_reqrep import ConnectionKey
    from custom_components.inim_prime.adapters import primelan_adapter as pa

    key = ConnectionKey("192.168.1.98", 443, True, None, None, None, None)
    sent = []

    async def activate_scenario(request):
        if failures:
            kind = failures.pop(0)
            if kind == "refused":
                raise ClientConnectorError(key, ConnectionResetError("reset by peer"))
            raise TimeoutError("no answer")
        sent.append(request.scenario_id)

    adapter = pa.PrimelanAdapter(SimpleNamespace(activate_scenario = activate_scenario))
    adapter._scenarios = {2: object()}
    return pa, adapter, sent


async def test_primelan_command_retried_while_connection_is_refused(monkeypatch):
    pa, adapter, sent = _primelan_adapter(["refused", "refused"])
    monkeypatch.setattr(pa, "_sleep", _no_sleep)

    await adapter.activate_scenario(2)

    assert sent == [2], "sent once, after the panel accepts connections again"
    assert adapter._held_commands == set()


async def test_primelan_command_not_repeated_when_it_may_have_run(monkeypatch):
    pa, adapter, sent = _primelan_adapter(["timeout"])
    monkeypatch.setattr(pa, "_sleep", _no_sleep)

    with pytest.raises(TimeoutError):
        await adapter.activate_scenario(2)
    assert sent == []


async def test_primelan_command_gives_up_after_the_window(monkeypatch):
    pa, adapter, sent = _primelan_adapter(["refused"] * 5)
    monkeypatch.setattr(pa, "_sleep", _no_sleep)
    monkeypatch.setattr(pa, "PRIMELAN_COMMAND_RETRY_WINDOW", 0)

    with pytest.raises(Exception):
        await adapter.activate_scenario(2)
    assert sent == []


async def test_primelan_second_tap_while_held_is_dropped(monkeypatch):
    pa, adapter, sent = _primelan_adapter(["refused"])
    released = asyncio.Event()

    async def _wait(_):
        await released.wait()
    monkeypatch.setattr(pa, "_sleep", _wait)

    first = asyncio.create_task(adapter.activate_scenario(2))
    await asyncio.sleep(0)
    await adapter.activate_scenario(2)
    assert sent == []

    released.set()
    await first
    assert sent == [2]


async def test_armed_drop_test_sends_one_command(monkeypatch):
    """The beta drop test: two taps at the drop become one command."""
    monkeypatch.setattr(na, "NATIVE_RECONNECT_MIN_INTERVAL", 0)

    async def _yield(_):
        await asyncio.sleep(0)      # a real wait lets the second tap run
    monkeypatch.setattr(na, "_sleep", _yield)
    client = _FlakyClient(refusals = 3)
    adapter = na.NativeAdapter(client)
    adapter.arm_drop_test(615, 2)

    adapter._note_outage(ConnectionError("down"))
    await adapter._drop_test_task

    assert client.sent == [(615, True)]
    assert adapter._drop_test is None, "one shot"


async def test_reconnect_slows_down_on_a_long_outage():
    adapter = na.NativeAdapter(_FlakyClient(refusals = 0))
    now = asyncio.get_running_loop().time()

    assert adapter._reconnect_interval(now) == na.NATIVE_RECONNECT_MIN_INTERVAL

    adapter._outage_started = now - 5
    assert adapter._reconnect_interval(now) == na.NATIVE_RECONNECT_MIN_INTERVAL, (
        "a short drop is retried every second"
    )

    adapter._outage_started = now - na.NATIVE_RECONNECT_FAST_WINDOW
    assert adapter._reconnect_interval(now) == na.NATIVE_RECONNECT_SLOW_INTERVAL, (
        "a long outage is retried less often"
    )
