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

    await coordinator.async_refresh()
    assert coordinator.last_update_success, "a short drop must not make entities unavailable"
    assert coordinator.data == {"zone": "ok"}

    await coordinator.async_refresh()
    assert coordinator.data == {"zone": "new"}


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
