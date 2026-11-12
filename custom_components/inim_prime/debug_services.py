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

from inim.prime.native.const import CommandOperation, FrameOperation
from inim.prime.native.operations.terminals.get_terminal_statuses import get_terminal_statuses
from inim.prime.native.utils import Interval

from . import native_compat
from .const import DOMAIN

SERVICE_READ_TERMINALS = "debug_read_terminals"
SERVICE_WATCH_TERMINALS = "debug_watch_terminals"
SERVICE_READ_MEMORY = "debug_read_memory"
SERVICE_RAW_COMMAND = "debug_raw_command"
SERVICE_MEMORY_DIFF = "debug_memory_diff"
SERVICE_WRITE_MEMORY = "debug_write_memory"
SERVICE_PROBE_COMMANDS = "debug_probe_commands"
SERVICE_PRIMELAN_OUTPUTS = "debug_primelan_outputs"
SERVICE_ARM_DROP_TEST = "debug_arm_drop_test"
_SNAPSHOTS: dict[tuple[int, int], bytes] = {}

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


MEMORY_DIFF_SCHEMA = vol.Schema({
    vol.Required("address"): vol.Coerce(int),
    vol.Required("length"): vol.All(vol.Coerce(int), vol.Range(min = 1, max = 262144)),
    vol.Optional("snapshot", default = False): cv.boolean,
    vol.Optional("max_changes", default = 200): vol.All(vol.Coerce(int), vol.Range(min = 1, max = 2000)),
})


WRITE_MEMORY_SCHEMA = vol.Schema({
    vol.Required("address"): vol.Coerce(int),
    vol.Required("data"): cv.string,
    vol.Optional("session", default = True): cv.boolean,
})

# Seen in Prime/STUDIO "write only changes": a command 0x13 (data 00 = begin,
# 01 = end) around memory writes. A write is a read request with the 2-byte
# flag after the lengths set to 01 00, followed by the raw data; the panel
# answers with one byte, the 8-bit sum of the data.
_SESSION_BEGIN = bytes.fromhex("13 00 00 00 00")
_SESSION_END = bytes.fromhex("13 00 00 00 01")


def _write_request(address: int, data: bytes) -> bytes:
    header = (
        address.to_bytes(8, "little")
        + len(data).to_bytes(4, "little")
        + len(data).to_bytes(4, "little")
        + b"\x01\x00\x11"
    )
    return header + bytes([sum(header) & 0xFF]) + data


PROBE_COMMANDS_SCHEMA = vol.Schema({
    vol.Required("operations"): vol.All(cv.ensure_list, [vol.All(vol.Coerce(int), vol.Range(min = 0, max = 0xFFFF))]),
    vol.Optional("data", default = ""): cv.string,
    vol.Optional("response_length"): vol.All(vol.Coerce(int), vol.Range(min = 1, max = 1024)),
    vol.Optional("delay", default = 0.3): vol.All(vol.Coerce(float), vol.Range(min = 0, max = 5)),
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


async def _read_block(client, address: int, length: int) -> bytes:
    data = bytearray()
    for offset in range(0, length, 1024):
        size = min(1024, length - offset)
        async with client._lock:
            data += await client._protocol.read_memory(address + offset, size)
    return bytes(data)


async def _memory_diff(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """First call with snapshot=true stores the block, later calls list what changed."""
    client = _native_client(hass)
    address, length = call.data["address"], call.data["length"]
    current = await _read_block(client, address, length)
    key = (address, length)
    if call.data["snapshot"] or key not in _SNAPSHOTS:
        _SNAPSHOTS[key] = current
        return {"snapshot": True, "bytes": length}
    previous = _SNAPSHOTS[key]
    changes = []
    i = 0
    while i < length and len(changes) < call.data["max_changes"]:
        if current[i] != previous[i]:
            j = i
            while j < length and (current[j] != previous[j] or (j + 1 < length and current[j + 1] != previous[j + 1])):
                j += 1
            changes.append({
                "address": hex(address + i),
                "offset": i,
                "from": previous[i:j].hex(" "),
                "to": current[i:j].hex(" "),
            })
            i = j
        else:
            i += 1
    _SNAPSHOTS[key] = current
    return {"changes": changes}


async def _write_memory(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    client = _native_client(hass)
    try:
        data = bytes.fromhex(call.data["data"].replace(" ", ""))
    except ValueError as err:
        raise ServiceValidationError("data must be hex bytes, e.g. 'f7'") from err
    if not data or len(data) > 1024:
        raise ServiceValidationError("data must be 1-1024 bytes")
    protocol = client._protocol
    result = {}
    async with client._lock:
        if call.data["session"]:
            result["begin"] = (await protocol.exchange_payload(_SESSION_BEGIN, FrameOperation.COMMAND, 5)).hex(" ")
        try:
            ack = await protocol.exchange_payload(
                _write_request(call.data["address"], data), FrameOperation.READ, 1,
            )
            result["ack"] = ack.hex(" ")
            result["expected_ack"] = f"{sum(data) & 0xFF:02x}"
        finally:
            if call.data["session"]:
                result["end"] = (await protocol.exchange_payload(_SESSION_END, FrameOperation.COMMAND, 5)).hex(" ")
    return result


async def _probe_commands(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Send plain commands (4-byte operation + data, no PIN) like Prime/STUDIO's
    monitoring does (e.g. 0x0d + 00 = system status) and return the answers.
    Stops at the first connection error."""
    client = _native_client(hass)
    try:
        data = bytes.fromhex(call.data["data"].replace(" ", ""))
    except ValueError as err:
        raise ServiceValidationError("data must be hex bytes") from err
    results = {}
    for operation in call.data["operations"]:
        payload = operation.to_bytes(4, "little") + data
        try:
            async with client._lock:
                response = await client._protocol.exchange_payload(
                    payload, FrameOperation.COMMAND, call.data.get("response_length"),
                )
            results[f"{operation:#04x}"] = response.hex(" ")
        except Exception as err:  # noqa: BLE001 - report and stop
            results[f"{operation:#04x}"] = f"error: {err}"
            break
        await asyncio.sleep(call.data["delay"])
    return {"results": results}


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


def _primelan_client(hass: HomeAssistant):
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        gateway = getattr(entry.runtime_data, "gateway", None)
        primelan = getattr(gateway, "_primelan", None) if gateway else None
        if primelan is not None:
            return primelan._client
    raise ServiceValidationError("No loaded Inim Prime entry with PrimeLAN")


async def _primelan_outputs(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Dump the PrimeLAN output list (what the linked user has access to), to
    compare it against the native terminals and decide which outputs to show."""
    client = _primelan_client(hass)
    statuses = await client.get_outputs_status()
    outputs = {}
    for pid, out in statuses.items():
        outputs[str(pid)] = {
            "terminal": getattr(out, "terminal", None),
            "name": (getattr(out, "name", "") or "").strip(),
            "state": getattr(out, "state", None),
            "type": getattr(getattr(out, "type", None), "name", str(getattr(out, "type", ""))),
        }
    return {"count": len(outputs), "outputs": outputs}


ARM_DROP_TEST_SCHEMA = vol.Schema({
    vol.Required("output_id"): vol.All(vol.Coerce(int), vol.Range(min = 0, max = 1009)),
    vol.Optional("taps", default = 2): vol.All(vol.Coerce(int), vol.Range(min = 1, max = 5)),
    vol.Optional("burst", default = False): cv.boolean,
})


async def _arm_drop_test(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    """Beta test only: at the next connection drop turn an output on N times,
    to check that the command is held and the repeated taps are dropped."""
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        gateway = getattr(entry.runtime_data, "gateway", None)
        native = getattr(gateway, "_native", None) if gateway else None
        if native is not None:
            if call.data["burst"]:
                drop_at = native.arm_burst_test(call.data["output_id"])
                return {"armed": True, "output_id": call.data["output_id"], "burst": True, "drop_at": drop_at}
            native.arm_drop_test(call.data["output_id"], call.data["taps"])
            return {"armed": True, "output_id": call.data["output_id"], "taps": call.data["taps"]}
    raise ServiceValidationError("No loaded Inim Prime entry with the native protocol")


def async_register(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_READ_TERMINALS):
        return
    for name, handler, schema in (
        (SERVICE_READ_TERMINALS, _read_terminals, READ_TERMINALS_SCHEMA),
        (SERVICE_WATCH_TERMINALS, _watch_terminals, WATCH_TERMINALS_SCHEMA),
        (SERVICE_READ_MEMORY, _read_memory, READ_MEMORY_SCHEMA),
        (SERVICE_RAW_COMMAND, _raw_command, RAW_COMMAND_SCHEMA),
        (SERVICE_MEMORY_DIFF, _memory_diff, MEMORY_DIFF_SCHEMA),
        (SERVICE_WRITE_MEMORY, _write_memory, WRITE_MEMORY_SCHEMA),
        (SERVICE_PROBE_COMMANDS, _probe_commands, PROBE_COMMANDS_SCHEMA),
        (SERVICE_PRIMELAN_OUTPUTS, _primelan_outputs, vol.Schema({})),
        (SERVICE_ARM_DROP_TEST, _arm_drop_test, ARM_DROP_TEST_SCHEMA),
    ):
        async def _handler(call: ServiceCall, _h = handler) -> ServiceResponse:
            return await _h(hass, call)
        hass.services.async_register(DOMAIN, name, _handler, schema = schema,
                                     supports_response = SupportsResponse.ONLY)
