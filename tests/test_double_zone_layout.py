"""Second zone of a double-zone terminal, by firmware.

Firmware 4 keeps it at <terminal> + 1005, firmware 5 at <terminal> + 1505
(500 video/radio/"and" zones were inserted in between). Values taken from a
Prime PR060 5.00, where the second zones came up without a name.
"""
from unittest.mock import patch

from inim.prime.native.helpers import terminals as t
from inim.prime.native.models.terminals import TerminalType

from custom_components.inim_prime import native_compat


class _Status:
    def __init__(self, typ):
        self.type = typ


class _Protocol:
    """Stands for the panel connection; only its identity is used here."""


async def _initialize(description: str):
    seen: dict = {}

    async def panel_info(protocol):
        firmware, model = description.split(" ", 1)
        return "SERIAL", firmware, model

    async def statuses(*a, **k):
        return {18: _Status(TerminalType.DOUBLE_ZONE), 19: _Status(TerminalType.DOUBLE_ZONE)}

    async def labels(protocol, interval = None, **k):
        seen["labels_interval"] = interval
        table = {18: "RETE OFF.FRIGO", 19: "RILEVATORE GAS",
                 # firmware 4 position: on firmware 5 these are video zones
                 1023: "Z. Video  19", 1024: "Z. Video  20",
                 # firmware 5 position
                 1523: "UPS.GUASTO", 1524: "ALLAGAMENTO"}
        return _Default(table)

    async def settings(protocol, interval = None, **k):
        seen["settings_interval"] = interval
        return _Default({})

    async def no_outputs(*a, **k):
        return {}

    protocol = _Protocol()
    with patch.object(native_compat, "_original_get_panel_info", panel_info), \
         patch.object(t, "get_terminal_statuses", statuses), \
         patch.object(native_compat, "_original_get_zone_labels", labels), \
         patch.object(native_compat, "_original_get_zone_settings", settings), \
         patch.object(native_compat, "_original_get_output_labels", no_outputs), \
         patch.object(t.DoubleZone, "decode", lambda **kw: kw):
        await native_compat._get_panel_info_recording(protocol)
        terminals = await native_compat._initialize_terminals_for_firmware(protocol)
    return terminals, seen


class _Default(dict):
    def __missing__(self, key):
        return ""


async def test_firmware_5_reads_the_second_zone_500_further():
    terminals, seen = await _initialize("5.00 PR060")

    assert terminals[18]["zone_0_label"] == "RETE OFF.FRIGO"
    assert terminals[18]["zone_1_id"] == 1523
    assert terminals[18]["zone_1_label"] == "UPS.GUASTO"
    assert terminals[19]["zone_1_id"] == 1524
    assert terminals[19]["zone_1_label"] == "ALLAGAMENTO"
    # the tables are read far enough to contain those zones
    assert seen["labels_interval"].end == 2509
    assert seen["settings_interval"].end == 2509
    # and the library is left as it was for the next panel
    assert t.ZONE_1_ID_OFFSET == 1005
    assert native_compat._zone_ids_interval is None


async def test_firmware_4_is_unchanged():
    terminals, seen = await _initialize("4.07 PR240L")

    assert terminals[18]["zone_1_id"] == 1023
    assert terminals[18]["zone_1_label"] == "Z. Video  19"  # whatever is at +1005
    assert seen["labels_interval"] is None, "the library's own interval is used"


def test_offset_by_firmware_text():
    assert native_compat.zone_1_id_offset("5.00 PR060") == 1505
    assert native_compat.zone_1_id_offset("5.10 Prime240L") == 1505
    assert native_compat.zone_1_id_offset("4.07 Prime120L") == 1005
    assert native_compat.zone_1_id_offset("") == 1005
