"""Second zone of a double-zone terminal, by firmware.

Firmware 4 keeps it at <terminal> + 1005, firmware 5 at <terminal> + 1505
(500 video/radio/"and" zones were inserted in between). Values taken from a
Prime PR060 5.00, where the second zones came up without a name.
"""
import asyncio
from unittest.mock import patch

from inim.prime.native.client import Client
from inim.prime.native.helpers import terminals as t
from inim.prime.native.models.terminals import TerminalType
from inim.prime.native.operations.zones.const import get_zone_1_id_offset
from inim.prime.native.operations.zones.set_zone_bypass import set_zone_bypass


class _Status:
    def __init__(self, typ):
        self.type = typ


class _Protocol:
    """Stands for the panel connection; only its identity is used here."""


async def _initialize(description: str):
    seen: dict = {}

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

    firmware = description.split(" ", 1)[0]
    with patch.object(t, "get_terminal_statuses", statuses), \
         patch.object(t, "get_zone_labels", labels), \
         patch.object(t, "get_zone_settings", settings), \
         patch.object(t, "get_output_labels", no_outputs), \
         patch.object(t.DoubleZone, "decode", lambda **kw: kw):
        terminals = await t.initialize_terminals(_Protocol(), None, get_zone_1_id_offset(firmware))
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
    # and the layout of other panels is untouched
    assert t.ZONE_1_ID_OFFSET == 1005


async def test_firmware_4_is_unchanged():
    terminals, seen = await _initialize("4.07 PR240L")

    assert terminals[18]["zone_1_id"] == 1023
    assert terminals[18]["zone_1_label"] == "Z. Video  19"  # whatever is at +1005
    assert seen["labels_interval"].end == 2009


def test_offset_by_firmware_version():
    assert get_zone_1_id_offset("5.00") == 1505
    assert get_zone_1_id_offset("5.10") == 1505
    assert get_zone_1_id_offset("4.07") == 1005
    assert get_zone_1_id_offset("") == 1005


class _Protocol:
    def __init__(self):
        self.commands = []

    async def execute_command_with_pin(self, operation, data = None, pin = None, **_):
        self.commands.append((int(operation), data.hex(" ")))
        return b""


async def test_second_zone_bypass_uses_terminal_and_value():
    protocol = _Protocol()
    await set_zone_bypass(protocol, 19, True, None, True)
    await set_zone_bypass(protocol, 19, False, None, True)
    await set_zone_bypass(protocol, 19, True)
    await set_zone_bypass(protocol, 19, False)
    assert protocol.commands == [
        (9, "13 00 01 00"),   # second zone of terminal 19: exclude
        (9, "13 00 03 00"),   # ... include
        (9, "13 00 00 00"),   # first zone: unchanged
        (9, "13 00 02 00"),
    ]


def _client(offset: int, protocol: _Protocol) -> Client:
    client = object.__new__(Client)
    client._zones = {15: object(), 15 + offset: object()}
    client._zone_1_id_offset = offset
    client._zone_to_terminal = {15: 15, 15 + offset: 15}
    client._lock = asyncio.Lock()
    client._protocol = protocol
    client._pin = None
    client._initialized = True
    return client


async def test_client_excludes_the_second_zone_through_its_terminal_on_every_firmware():
    """The panel refuses the second zone's own id (answer 05 00), on firmware 4
    as on firmware 5: seen on a Prime120 4.07 with zone 1020 of terminal 15."""
    for offset in (1005, 1505):
        protocol = _Protocol()
        client = _client(offset, protocol)
        await client.set_zone_bypass(15 + offset, True)
        await client.set_zone_bypass(15 + offset, False)
        await client.set_zone_bypass(15, True)
        assert protocol.commands == [
            (9, "0f 00 01 00"),
            (9, "0f 00 03 00"),
            (9, "0f 00 00 00"),
        ], offset
