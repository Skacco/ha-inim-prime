"""End to end: the first setup that follows the setup form, on a simulated panel.

The form parks its Native connection (native_handoff) and the setup that
starts right after picks it up. This runs that whole path inside Home
Assistant: config entry, gateway, coordinators, platforms.
"""
import asyncio

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from inim.prime.native.client import Client
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.inim_prime import native_handoff
from custom_components.inim_prime.const import DOMAIN

SERIAL = "PRIMETEST1"
HOST = "192.0.2.30"


class _FakeProtocol:
    is_connected = True

    async def read_memory(self, address, length):
        return bytes(length)

    async def exchange_payload(self, payload, operation, response_payload_length = 0, **_):
        return bytes(response_payload_length)


class _FakeNativeClient(Client):
    """The library's own client class (same properties), without a network."""

    def __init__(self):
        super().__init__(host = HOST, password = "x", use_outer_frame = False, port = 6004, pin = None)
        self._protocol = _FakeProtocol()
        self._lock = asyncio.Lock()
        self._initialized = True
        self._serial_number, self._firmware, self._model = SERIAL, "4.07", "Prime060S"
        self.connect_calls = 0

    async def connect(self):
        self.connect_calls += 1
        return self

    async def reconnect(self):
        return self

    def disconnect(self):
        pass

    async def ensure_initialized(self):
        return None

    async def update_zone_terminals(self):
        return {}

    async def update_partitions(self):
        return {}

    async def update_outputs(self):
        return {}


async def test_first_setup_after_the_form_loads(hass: HomeAssistant):
    client = _FakeNativeClient()
    # what the setup form does once the connection test has passed
    native_handoff.keep(HOST, 6004, client)

    entry = MockConfigEntry(
        domain = DOMAIN, version = 2, minor_version = 0, unique_id = SERIAL,
        data = {
            "serial_number": SERIAL, "host": HOST,
            "native": {"port": 6004, "password": "x", "use_outer_frame": False, "pin": None},
        },
        options = {"scan_intervals": {"zones": 500, "partitions": 500}},
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED, entry.reason
    assert entry.runtime_data.gateway._native._client is client, "the form's connection is reused"
    assert client.connect_calls == 0, "no second connection to the panel"

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


def _closed_without_answer() -> ConnectionError:
    try:
        raise asyncio.IncompleteReadError(b"", 12)
    except asyncio.IncompleteReadError as exc:
        err = ConnectionError("Panel closed the connection mid-response.")
        err.__cause__ = exc
        return err


class _RefusingClient(_FakeNativeClient):
    """A panel that closes the new connection at its first request."""

    async def ensure_initialized(self):
        raise _closed_without_answer()


def _native_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain = DOMAIN, version = 2, minor_version = 0, unique_id = SERIAL,
        data = {
            "serial_number": SERIAL, "host": HOST,
            "native": {"port": 6004, "password": "x", "use_outer_frame": False, "pin": None},
        },
        options = {"scan_intervals": {"zones": 500, "partitions": 500}},
    )
    entry.add_to_hass(hass)
    return entry


async def test_reload_waits_and_a_closed_new_connection_is_retried(hass: HomeAssistant, monkeypatch):
    """Right after our own close the panel may close the new connection
    without answering: that is retried, not taken for a refused password."""
    import custom_components.inim_prime as integration

    native_handoff.keep(HOST, 6004, _FakeNativeClient())
    entry = _native_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    waits = []
    real_sleep = asyncio.sleep

    async def _sleep(seconds):
        waits.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(integration.asyncio, "sleep", _sleep)
    monkeypatch.setattr(integration, "NativeClient", lambda **_: _RefusingClient())
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert any(0 < wait <= integration.NATIVE_REOPEN_DELAY for wait in waits), "the panel is given time"
    assert entry.state is ConfigEntryState.SETUP_RETRY, "tried again, not blocked"
    await hass.config_entries.async_unload(entry.entry_id)


async def test_refused_password_with_no_recent_close_still_blocks(hass: HomeAssistant, monkeypatch):
    import custom_components.inim_prime as integration

    monkeypatch.setattr(integration, "NativeClient", lambda **_: _RefusingClient())
    entry = _native_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_entry_with_the_cycle_pause_on_unloads(hass: HomeAssistant):
    """The pause task is stopped at unload without breaking the unload."""
    native_handoff.keep(HOST, 6004, _FakeNativeClient())
    entry = _native_entry(hass)
    hass.config_entries.async_update_entry(entry, options = {
        **entry.options, "native_cycle_pause": True,
        "native_cycle_pause_before": 1.0, "native_cycle_pause_after": 2.0,
    })
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
