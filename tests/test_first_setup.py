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
