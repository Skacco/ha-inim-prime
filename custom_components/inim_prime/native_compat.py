"""Workarounds for the pinned inim-prime-native library.

The library reads output labels only for the panel's on-board outputs
(terminals 1005-1009). On panels where expansion/flex terminals are
programmed as outputs, building those outputs raised ``KeyError`` and the
whole native connection failed.

In the panel every terminal has a single description, stored in the same
label table the library already reads for zones. So a missing output label
is taken from there (read during the same initialization, no extra
traffic). If even that is empty the label is "" and the integration falls
back to PrimeLAN, or to "Output <id>".
"""
from __future__ import annotations

import asyncio
import re
import struct

from inim.prime.native import client as _client
from inim.prime.native.const import CommandOperation as _CommandOperation
from inim.prime.native.helpers import terminals as _terminals
from inim.prime.native.utils import Interval as _Interval
from inim.prime.native.models import terminals as _terminal_models
from inim.prime.native.wire import header as _header

# The library maps only terminal type 0x01 to an output. Virtual/command
# terminals (the panel's "uscite virtuali": APERTURA CASA, Apri/Chiudi tenda,
# scenario triggers, ...) use type 0x02 and were dropped as UNKNOWN, so they
# never appeared as outputs even though the panel exposes and labels them.
# Treat 0x02 as an output too: it decodes with the same layout (state in the
# second byte) and the label falls back to the terminal label like 0x01 does.
_VIRTUAL_OUTPUT_TYPE_BYTE = 0x02
_TerminalType = _terminal_models.TerminalType
_TerminalStatus = _terminal_models.TerminalStatus
_original_decode_type = _TerminalStatus.decode_type


def _decode_type_with_virtual(raw_bytes: bytes) -> "_TerminalType":
    if raw_bytes and raw_bytes[0] == _VIRTUAL_OUTPUT_TYPE_BYTE:
        return _TerminalType.OUTPUT
    return _original_decode_type(raw_bytes)

# Firmware 5.x panels (e.g. Prime PR060 5.00) tag the inner frame of their
# RESPONSES with magic 0x5054 instead of 0x5050. Everything else -- CRC
# (same CRC-16/ARC), lengths, operation, encrypted payload -- is identical,
# verified against a live 5.00 capture. The library (built on the 4.x branch)
# hard-rejects anything but 0x5050, so we widen the inner-magic check to accept
# both. Requests still use 0x5050, which both firmwares accept.
_VALID_INNER_MAGICS = (b"\x50\x50", b"\x50\x54")


def _inner_magic_is_valid(self) -> bool:
    return self.magic in _VALID_INNER_MAGICS

# Terminal labels read by the last get_zone_labels() call. initialize_terminals
# gathers zone and output labels together and only looks them up afterwards,
# so this is filled by the time a missing output label is requested.
_terminal_labels: dict[int, str] = {}


class _LabelsWithDefault(dict):
    def __missing__(self, key):  # noqa: D105
        return (_terminal_labels.get(key) or "").strip()


_original_get_output_labels = _terminals.get_output_labels
_original_get_zone_labels = _terminals.get_zone_labels


async def _get_output_labels_with_default(*args, **kwargs):
    return _LabelsWithDefault(await _original_get_output_labels(*args, **kwargs))


async def _get_zone_labels_recording(*args, **kwargs):
    if _zone_ids_interval is not None:
        kwargs.setdefault("interval", _zone_ids_interval)
    labels = await _original_get_zone_labels(*args, **kwargs)
    _terminal_labels.clear()
    _terminal_labels.update(labels)
    return labels


# Firmware 5 added 500 zones (video, radio and "and" zones) to the zone
# tables, right after the 1005 terminal zones. Everything that followed moved
# forward by 500: the second zone of a double-zone terminal, which firmware 4
# keeps at <terminal> + 1005 (read on a PR120 4.07), is at <terminal> + 1505
# on firmware 5 (read on two PR060 5.00, where the second zones came up with
# no name and could not be told apart). The library only knows the firmware 4
# layout, so the offset and the size of the tables it reads are chosen here
# from the firmware the panel reports.
_ZONE_TERMINALS = 1005
_FIRMWARE_4_ZONE_1_ID_OFFSET = _terminals.ZONE_1_ID_OFFSET
_FIRMWARE_5_ZONE_1_ID_OFFSET = _FIRMWARE_4_ZONE_1_ID_OFFSET + 500


def zone_1_id_offset(panel_description: str) -> int:
    """Offset of a double zone's second zone for the given firmware/model text
    (e.g. "5.00 PR060"); the firmware 4 layout when the version is not found."""
    version = re.search(r"(?<![\d.])(\d+)\.\d+", panel_description or "")
    if version and int(version.group(1)) >= 5:
        return _FIRMWARE_5_ZONE_1_ID_OFFSET
    return _FIRMWARE_4_ZONE_1_ID_OFFSET


# Firmware/model text reported by each panel, between the two steps of the
# library's initialize() (panel info first, terminals right after).
_panel_descriptions: dict[int, str] = {}
# Zone ids to read while terminals are being initialised (None: the library's
# own default). Set only inside _layout_lock.
_zone_ids_interval: _Interval | None = None
_layout_lock = asyncio.Lock()

# Protocols of the panels on the firmware 5 layout -> their second-zone offset.
_second_zone_offsets: dict[int, int] = {}

# Firmware 5 does not accept a second zone's own id in the bypass command (it
# answers 05, "refused", to <terminal> + 1505 and to <terminal> + 1005). The
# second zone is addressed with the terminal id and a different value: 1
# excludes it, 3 includes it again (0 and 2 do the same for the first zone).
# Found on a PR060 5.00 and confirmed on a second one.
_SECOND_ZONE_ENABLE_BYPASS_VALUE = 1
_SECOND_ZONE_DISABLE_BYPASS_VALUE = 3

_original_get_panel_info = _client.get_panel_info
_original_set_zone_bypass = _client.set_zone_bypass
_original_initialize_terminals = _client.initialize_terminals
_original_get_zone_settings = _terminals.get_zone_settings


async def _get_panel_info_recording(protocol):
    serial_number, firmware, model = await _original_get_panel_info(protocol)
    _panel_descriptions[id(protocol)] = f"{firmware} {model}"
    return serial_number, firmware, model


async def _get_zone_settings_for_layout(*args, **kwargs):
    if _zone_ids_interval is not None:
        kwargs.setdefault("interval", _zone_ids_interval)
    return await _original_get_zone_settings(*args, **kwargs)


async def _initialize_terminals_for_firmware(protocol, pin = None):
    global _zone_ids_interval
    offset = zone_1_id_offset(_panel_descriptions.pop(id(protocol), ""))
    if offset == _FIRMWARE_4_ZONE_1_ID_OFFSET:
        _second_zone_offsets.pop(id(protocol), None)
        return await _original_initialize_terminals(protocol, pin)
    _second_zone_offsets[id(protocol)] = offset
    # The library reads the offset from a module constant: hold a lock so a
    # second panel on another firmware cannot be initialised with it.
    async with _layout_lock:
        _terminals.ZONE_1_ID_OFFSET = offset
        _zone_ids_interval = _Interval(0, offset + _ZONE_TERMINALS - 1)
        try:
            return await _original_initialize_terminals(protocol, pin)
        finally:
            _terminals.ZONE_1_ID_OFFSET = _FIRMWARE_4_ZONE_1_ID_OFFSET
            _zone_ids_interval = None


async def _set_zone_bypass_for_firmware(protocol, zone_id, bypass, pin = None):
    offset = _second_zone_offsets.get(id(protocol))
    if offset is None or zone_id < offset:
        return await _original_set_zone_bypass(protocol, zone_id, bypass, pin)
    value = _SECOND_ZONE_ENABLE_BYPASS_VALUE if bypass else _SECOND_ZONE_DISABLE_BYPASS_VALUE
    await protocol.execute_command_with_pin(
        operation = _CommandOperation.SET_ZONE_BYPASS,
        data = struct.pack("<HH", zone_id - offset, value),
        pin = pin,
    )


def apply() -> None:
    if _client.set_zone_bypass is not _set_zone_bypass_for_firmware:
        _client.set_zone_bypass = _set_zone_bypass_for_firmware
    if _client.get_panel_info is not _get_panel_info_recording:
        _client.get_panel_info = _get_panel_info_recording
    if _client.initialize_terminals is not _initialize_terminals_for_firmware:
        _client.initialize_terminals = _initialize_terminals_for_firmware
    if _terminals.get_zone_settings is not _get_zone_settings_for_layout:
        _terminals.get_zone_settings = _get_zone_settings_for_layout
    if _terminals.get_output_labels is not _get_output_labels_with_default:
        _terminals.get_output_labels = _get_output_labels_with_default
    if _terminals.get_zone_labels is not _get_zone_labels_recording:
        _terminals.get_zone_labels = _get_zone_labels_recording
    if getattr(_header.InnerHeader.is_magic_valid, "fget", None) is not _inner_magic_is_valid:
        _header.InnerHeader.is_magic_valid = property(_inner_magic_is_valid)
    if _TerminalStatus.decode_type is not _decode_type_with_virtual:
        _TerminalStatus.decode_type = staticmethod(_decode_type_with_virtual)


apply()
