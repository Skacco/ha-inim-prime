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

from inim.prime.native.helpers import terminals as _terminals
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
    labels = await _original_get_zone_labels(*args, **kwargs)
    _terminal_labels.clear()
    _terminal_labels.update(labels)
    return labels


def apply() -> None:
    if _terminals.get_output_labels is not _get_output_labels_with_default:
        _terminals.get_output_labels = _get_output_labels_with_default
    if _terminals.get_zone_labels is not _get_zone_labels_recording:
        _terminals.get_zone_labels = _get_zone_labels_recording
    if getattr(_header.InnerHeader.is_magic_valid, "fget", None) is not _inner_magic_is_valid:
        _header.InnerHeader.is_magic_valid = property(_inner_magic_is_valid)
    if _TerminalStatus.decode_type is not _decode_type_with_virtual:
        _TerminalStatus.decode_type = staticmethod(_decode_type_with_virtual)


apply()
