"""Shutter discovery, fw 5.00 magic / type-0x02 tolerance, pulse control."""
import asyncio
from types import SimpleNamespace

from custom_components.inim_prime import native_compat
from custom_components.inim_prime.cover import discover_shutters, InimPrimeShutterCover


def _out(label, state=False):
    return SimpleNamespace(label=label, state=state)


def test_discover_shutters_pairs_default_named_modules():
    outputs = {
        587: _out("Mod.dom 01 T03"), 588: _out("Mod.dom 01 T04"),
        591: _out("Mod.dom 02 T03"), 592: _out("Mod.dom 02 T04"),
        599: _out("Mod.dom 04 T03"), 600: _out("ModDomot 04 T04"),
        # light module: T03/T04 renamed -> excluded
        603: _out("Luce palla"), 604: _out("Luce sala"),
        # only one default terminal -> excluded
        615: _out("Luce garage"), 616: _out("ModDomot 08 T04"),
    }
    shutters = discover_shutters(outputs)
    assert set(shutters) == {1, 2, 4}
    assert shutters[1] == {3: 587, 4: 588}
    assert shutters[4] == {3: 599, 4: 600}


def test_inner_magic_accepts_firmware_5_variant():
    native_compat.apply()
    from inim.prime.native.wire.header import InnerHeader
    assert InnerHeader(magic=b"\x50\x50").is_magic_valid      # 4.x
    assert InnerHeader(magic=b"\x50\x54").is_magic_valid      # 5.00
    assert not InnerHeader(magic=b"\x50\x53").is_magic_valid  # outer magic


def test_virtual_type_02_decoded_as_output():
    native_compat.apply()
    from inim.prime.native.models.terminals import TerminalStatus, TerminalType
    assert TerminalStatus.decode_type(bytes([0x02, 0, 8, 0, 1])) == TerminalType.OUTPUT
    assert TerminalStatus.decode_type(bytes([0x01, 0, 0, 0, 0])) == TerminalType.OUTPUT
    assert TerminalStatus.decode_type(bytes([0x00, 0, 8, 0, 1])) == TerminalType.SINGLE_ZONE
    assert TerminalStatus.decode_type(bytes([0x03, 0, 8, 0, 1])) == TerminalType.DOUBLE_ZONE


class _FakeGateway:
    def __init__(self):
        self.calls = []

    async def set_output(self, output_id, state, level=None):
        self.calls.append((output_id, state))


def _make_cover(open_cmd, close_cmd, stop_cmd=None, presets=None):
    gateway = _FakeGateway()
    coordinator = SimpleNamespace(gateway=gateway, serial_number="SER", last_update_success=True)
    cover = InimPrimeShutterCover(coordinator, 1, open_cmd=open_cmd, close_cmd=close_cmd,
                                  stop_cmd=stop_cmd, presets=presets)
    cover._pulse = 0  # no real delay in tests
    import types
    cover.async_write_ha_state = lambda: None
    return cover, gateway


async def test_open_close_stop_pulse_mapped_commands():
    cover, gateway = _make_cover(open_cmd=998, close_cmd=997, stop_cmd=575)
    await cover.async_open_cover()
    assert gateway.calls == [(998, True), (998, False)]   # pulse on the open command
    gateway.calls.clear()
    await cover.async_close_cover()
    assert gateway.calls == [(997, True), (997, False)]   # pulse on the close command
    gateway.calls.clear()
    await cover.async_stop_cover()
    assert gateway.calls == [(575, True), (575, False)]   # pulse on the stop command
    assert cover.is_closed is True                        # last command was close (0%)


def test_stop_feature_only_when_mapped():
    from homeassistant.components.cover import CoverEntityFeature
    with_stop, _ = _make_cover(open_cmd=998, close_cmd=997, stop_cmd=575)
    assert with_stop.supported_features & CoverEntityFeature.STOP
    without_stop, _ = _make_cover(open_cmd=998, close_cmd=997)
    assert not (without_stop.supported_features & CoverEntityFeature.STOP)


def test_cover_unavailable_without_full_mapping():
    cover, _ = _make_cover(open_cmd=998, close_cmd=None)
    assert cover.available is False
    cover2, _ = _make_cover(open_cmd=998, close_cmd=997)
    assert cover2.available is True


async def test_set_position_snaps_to_nearest_preset():
    from homeassistant.components.cover import CoverEntityFeature
    cover, gateway = _make_cover(open_cmd=998, close_cmd=997,
                                 presets={25: 575, 50: 576, 75: 999})
    assert cover.supported_features & CoverEntityFeature.SET_POSITION
    await cover.async_set_cover_position(position=60)   # nearest is 50 -> cmd 576
    assert gateway.calls == [(576, True), (576, False)]
    assert cover.current_cover_position == 50
    gateway.calls.clear()
    await cover.async_set_cover_position(position=90)   # nearest is 100 (open) -> 998
    assert gateway.calls == [(998, True), (998, False)]
    assert cover.current_cover_position == 100


def test_no_set_position_without_presets():
    from homeassistant.components.cover import CoverEntityFeature
    cover, _ = _make_cover(open_cmd=998, close_cmd=997)
    assert not (cover.supported_features & CoverEntityFeature.SET_POSITION)
