"""Workarounds for the pinned inim-prime-native library.

The library reads output labels only for the panel's on-board outputs
(terminals 1005-1009). On panels where expansion/flex terminals are
programmed as outputs, building those outputs raised ``KeyError`` and the
whole native connection failed. Here missing labels resolve to an empty
string; the integration fills them from PrimeLAN when available, otherwise
the entity is named "Output <id>".
"""
from __future__ import annotations

from inim.prime.native.helpers import terminals as _terminals


class _LabelsWithDefault(dict):
    def __missing__(self, key):  # noqa: D105
        return ""


_original_get_output_labels = _terminals.get_output_labels


async def _get_output_labels_with_default(*args, **kwargs):
    return _LabelsWithDefault(await _original_get_output_labels(*args, **kwargs))


def apply() -> None:
    if _terminals.get_output_labels is not _get_output_labels_with_default:
        _terminals.get_output_labels = _get_output_labels_with_default


apply()
