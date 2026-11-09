"""Low-level native protocol services, for reverse-engineering only.

They expose the raw terminal status table, raw memory reads and raw
commands of the native protocol, so features the library does not support
yet (roller blinds, timers, thermostats) can be studied from Home Assistant
without extra tools. Every call goes through the integration's own native
connection and its lock, so it never races the coordinators.
"""
from __future__ import annotations

import asyncio
import time

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from inim.prime.native.const import CommandOperation
from inim.prime.native.operations.terminals.get_terminal_statuses import get_terminal_statuses
from inim.prime.native.utils import Interval

from . import native_compat
from .const import DOMAIN

SERVICE_READ_TERMINALS = "debug_read_terminals"
SERVICE_WATCH_TERMINALS = "debug_watch_terminals"
SERVICE_READ_MEMORY = "debug_read_memory"
SERVICE_RAW_COMMAND = "debug_raw_command"

_TERMINAL_RANGE = vol.All(vol.Coerce(int), vol.Range(min = 0, max = 1009))

READ_TERMINALS_SCHEMA = vol.Schema({
    vol.Optional("start", default = 0): _TERMINAL_RANGE,
    vol.Optional("end", default = 1009): _TERMINAL_RANGE,
    vol.Optional("only_labelled", default = True): cv.boolean,
})

WATCH_TERMINALS_SCHEMA = vol.Schema({
    vol.Optional("start", default = 0): _TERMINAL_RANGE,
    vol.Optional("end", default = 1009): _TERMINAL_RANGE,
    vol.Optional("duration", default = 30): vol.All(vol.Coerce(float), vol.Range(min = 1, max = 120)),
    vol.Optional("interval", default = 0.5): vol.All(vol.Coerce(float), vol.Range(min = 0.2, max = 10)),
})

READ_MEMORY_SCHEMA = vol.Schema({
    vol.Required("address"): vol.Coerce(int),
    vol.Optional("length", default = 16): vol.All(vol.Coerce(int), vol.Range(min = 1, max = 4096)),
})

RAW_COMMAND_SCHEMA = vol.Schema({
    vol.Required("operation"): vol.All(vol.Coerce(int), vol.Range(min = 0, max = 255)),
    vol.Optional("data", default = ""): cv.string,
})


def _native_client(hass: HomeAssistant):
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        gateway = getattr(entry.runtime_data, "gateway", None)
        native = getattr(gateway, "_native", None) if gateway else None
        if native is not None:
            return native._client
    raise ServiceValidationError("No loaded Inim Prime entry with the native protocol")


def _labels() -> dict[int, str]:
    return {k: (v or "").replace("\x00", "").strip() for k, v in native_compat._terminal_labels.items()}


async def _read_statuses(client, start: int, end: int) -> dict[int, bytes]:
    async with client._lock:
        statuses = await get_terminal_statuses(client._protocol, Interval(start, end), client._pin)
    return {t_id: s.raw for t_id, s in statuses.items()}


async def _read_terminals(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    client = _native_client(hass)
    start, end = call.data["start"], call.data["end"]
    raw = await _read_statuses(client, start, end)
    labels = _labels()
    terminals = {}
    for t_id, data in raw.items():
        label = labels.get(t_id, "")
        if call.data["only_labelled"] and not label:
            continue
        terminals[str(t_id)] = {"label": label, "raw": data.hex(" ")}
    return {"terminals": terminals}


async def _watch_terminals(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    client = _native_client(hass)
    start, end = call.data["start"], call.data["end"]
    labels = _labels()
    began = time.monotonic()
    previous = await _read_statuses(client, start, end)
    changes = []
    while time.monotonic() - began < call.data["duration"]:
        await asyncio.sleep(call.data["interval"])
        current = await _read_statuses(client, start, end)
        for t_id, data in current.items():
            if previous.get(t_id) != data:
                changes.append({
                    "t": round(time.monotonic() - began, 1),
                    "terminal": t_id,
                    "label": labels.get(t_id, ""),
                    "from": previous[t_id].hex(" ") if t_id in previous else None,
                    "to": data.hex(" "),
                })
        previous = current
    return {"changes": changes}


async def _read_memory(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    client = _native_client(hass)
    async with client._lock:
        data = await client._protocol.read_memory(call.data["address"], call.data["length"])
    return {"hex": data.hex(" "), "ascii": data.decode("latin-1").replace("\x00", ".")}


async def _raw_command(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    client = _native_client(hass)
    try:
        operation = CommandOperation(call.data["operation"])
    except ValueError as err:
        raise ServiceValidationError(f"Unknown operation {call.data['operation']}") from err
    try:
        data = bytes.fromhex(call.data["data"].replace(" ", ""))
    except ValueError as err:
        raise ServiceValidationError("data must be hex bytes, e.g. '4b 02 02 00'") from err
    async with client._lock:
        response = await client._protocol.execute_command_with_pin(operation, data or None, client._pin)
    return {"response": response.hex(" ") if response else ""}


def async_register(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_READ_TERMINALS):
        return
    for name, handler, schema in (
        (SERVICE_READ_TERMINALS, _read_terminals, READ_TERMINALS_SCHEMA),
        (SERVICE_WATCH_TERMINALS, _watch_terminals, WATCH_TERMINALS_SCHEMA),
        (SERVICE_READ_MEMORY, _read_memory, READ_MEMORY_SCHEMA),
        (SERVICE_RAW_COMMAND, _raw_command, RAW_COMMAND_SCHEMA),
    ):
        async def _handler(call: ServiceCall, _h = handler) -> ServiceResponse:
            return await _h(hass, call)
        hass.services.async_register(DOMAIN, name, _handler, schema = schema,
                                     supports_response = SupportsResponse.ONLY)
