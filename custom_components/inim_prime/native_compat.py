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


apply()
