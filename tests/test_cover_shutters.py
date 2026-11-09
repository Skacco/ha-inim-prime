"""Roller-shutter discovery, fw 5.00 magic tolerance, and position tracking."""
import asyncio
from types import SimpleNamespace

import pytest

from custom_components.inim_prime import native_compat
from custom_components.inim_prime.cover import discover_shutters, InimPrimeShutterCover


def _out(label, state=False):
    return SimpleNamespace(label=label, state=state)


def test_discover_shutters_pairs_default_named_modules():
    outputs = {
        587: _out("Mod.dom 01 T03"), 588: _out("Mod.dom 01 T04"),
        591: _out("Mod.dom 02 T03"), 592: _out("Mod.dom 02 T04"),
        595: _out("Mod.dom 03 T03"), 596: _out("Mod.dom 03 T04"),
        599: _out("Mod.dom 04 T03"), 600: _out("ModDomot 04 T04"),
        # light module: T03/T04 renamed -> excluded
        603: _out("Luce palla"), 604: _out("Luce sala"),
        # module with only one default terminal -> excluded
        615: _out("Luce garage"), 616: _out("ModDomot 08 T04"),
    }
    shutters = discover_shutters(outputs)
    assert set(shutters) == {1, 2, 3, 4}
    assert shutters[1] == {3: 587, 4: 588}
    assert shutters[4] == {3: 599, 4: 600}


def test_inner_magic_accepts_firmware_5_variant():
    native_compat.apply()
    from inim.prime.native.wire.header import InnerHeader
    assert InnerHeader(magic=b"\x50\x50").is_magic_valid      # 4.x
    assert InnerHeader(magic=b"\x50\x54").is_magic_valid      # 5.00
    assert not InnerHeader(magic=b"\x50\x53").is_magic_valid  # outer magic, invalid


class _FakeGateway:
    def __init__(self, outputs):
        self.outputs = outputs
        self.calls = []

    async def set_output(self, output_id, state, level=None):
        self.calls.append((output_id, state))
        self.outputs[output_id].state = state


def _make_cover(monkeypatch, clock):
    outputs = {588: _out("Mod.dom 01 T04"), 587: _out("Mod.dom 01 T03")}
    gateway = _FakeGateway(outputs)
    coordinator = SimpleNamespace(gateway=gateway, serial_number="SER", last_update_success=True)
    cover = InimPrimeShutterCover(coordinator, 1, open_id=588, close_id=587)
    monkeypatch.setattr("custom_components.inim_prime.cover.time.monotonic", lambda: clock["t"])
    cover.hass = SimpleNamespace(
        loop=SimpleNamespace(call_later=lambda d, cb: SimpleNamespace(cancel=lambda: None)),
        async_create_task=lambda coro: asyncio.ensure_future(coro),
    )
    monkeypatch.setattr(cover, "async_write_ha_state", lambda: None)
    return cover, gateway


async def test_open_drives_relay_and_tracks_position(monkeypatch):
    clock = {"t": 0.0}
    cover, gateway = _make_cover(monkeypatch, clock)
    cover._position = 0.0
    cover._calibrated = True

    await cover.async_open_cover()
    # close relay off first, then open relay on
    assert gateway.calls == [(587, False), (588, True)]
    assert cover.is_opening

    # half of the 17 s open travel -> ~50 %
    clock["t"] = 8.5
    cover._advance()
    assert 45 <= cover.current_cover_position <= 55
    assert not cover.is_closed

    # full travel -> calibrates to 100 % and asks to stop
    clock["t"] = 20.0
    cover._advance()
    assert cover.current_cover_position == 100
    assert cover._direction == 0


async def test_set_position_stops_at_target(monkeypatch):
    clock = {"t": 0.0}
    cover, gateway = _make_cover(monkeypatch, clock)
    cover._position = 0.0
    cover._calibrated = True

    await cover.async_set_cover_position(**{"position": 50})
    assert (588, True) in gateway.calls
    clock["t"] = 9.0  # just past 50 %
    cover._advance()
    await asyncio.sleep(0)                 # let the scheduled relay-release run
    assert cover._direction == 0          # stopped at target
    assert abs(cover.current_cover_position - 50) <= 1
    assert gateway.calls[-2:] == [(588, False), (587, False)]  # relays released


async def test_external_move_followed_via_relays(monkeypatch):
    clock = {"t": 0.0}
    cover, gateway = _make_cover(monkeypatch, clock)
    cover._position = 0.0
    cover._calibrated = True
    # wall button opens: open relay turns on without an HA command
    gateway.outputs[588].state = True
    cover._advance()
    assert cover.is_opening
    clock["t"] = 8.5
    cover._advance()
    assert 45 <= cover.current_cover_position <= 55
