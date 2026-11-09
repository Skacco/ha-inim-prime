"""Roller-shutter discovery, fw 5.00 magic tolerance, and time-driven moves."""
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
    timers = []
    cover.hass = SimpleNamespace(
        loop=SimpleNamespace(
            call_later=lambda delay, cb: timers.append((delay, cb))
            or SimpleNamespace(cancel=lambda: None),
        ),
        async_create_task=lambda coro: asyncio.ensure_future(coro),
    )
    monkeypatch.setattr(cover, "async_write_ha_state", lambda: None)
    return cover, gateway, timers


async def test_full_close_runs_full_travel_and_calibrates(monkeypatch):
    clock = {"t": 0.0}
    cover, gateway, timers = _make_cover(monkeypatch, clock)
    # position unknown (50, not calibrated)
    await cover.async_close_cover()
    # open relay off first, then close relay on
    assert gateway.calls == [(588, False), (587, True)]
    assert cover.is_closing
    # a precise stop is scheduled for the full close travel (16.5 s) + margin
    assert any(abs(delay - (16.5 + 1.5)) < 0.01 for delay, _ in timers)

    # fire the precise stop timer
    clock["t"] = 18.0
    stop_cb = timers[0][1]
    stop_cb()
    await asyncio.sleep(0)
    assert cover.current_cover_position == 0
    assert cover.is_closed is True
    assert gateway.calls[-2:] == [(588, False), (587, False)]  # relays released


async def test_open_tracks_position_until_stop(monkeypatch):
    clock = {"t": 0.0}
    cover, gateway, timers = _make_cover(monkeypatch, clock)
    cover._position = 0.0
    cover._calibrated = True
    await cover.async_open_cover()
    assert (588, True) in gateway.calls
    assert cover.is_opening
    # half the 17 s open travel -> ~50 %
    clock["t"] = 8.5
    assert 45 <= cover.current_cover_position <= 55
    # full travel elapsed, fire stop -> fully open
    clock["t"] = 19.0
    timers[0][1]()
    await asyncio.sleep(0)
    assert cover.current_cover_position == 100
    assert cover._moving == 0


async def test_follows_external_move_when_idle(monkeypatch):
    clock = {"t": 0.0}
    cover, gateway, _ = _make_cover(monkeypatch, clock)
    cover._position = 0.0
    cover._calibrated = True
    # wall button opens: open relay turns on without an HA command
    gateway.outputs[588].state = True
    cover._handle_coordinator_update()
    assert cover.is_opening
    clock["t"] = 8.5
    assert 45 <= cover.current_cover_position <= 55
