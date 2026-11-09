"""Roller shutters (tapparelle) driven through the Smart modules' motor relays.

Each shutter module exposes two outputs: T04 (open) and T03 (close). The panel
has no readable position over the local protocols -- it lives only in the cloud
-- so HA drives the motor relays directly (set_output) and estimates the
position from the programmed travel times.

Commanded moves are time-driven and self-contained: HA turns the relay on and
schedules a precise stop, so they never depend on reading the relay back (which
lags the command by up to a poll). A full open/close always runs the whole
travel time plus a margin, so it reaches -- and recalibrates -- the end even
when the position is still unknown. Reading the relays (via the outputs
coordinator) is used only to follow moves started elsewhere (wall button,
timer) while HA itself is idle.

Only `set_output` and relay reads are used -- the same safe, proven operations
as the output switches. Safeguards: the two relays are never on together, every
commanded move has a hard-stop backup timer, and the relays are released when
the entity is removed.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    INIM_PRIME_DEVICE_MANUFACTURER,
    SHUTTER_CLOSE_TIME_MS,
    SHUTTER_EXTRA_TIME_MS,
    SHUTTER_OPEN_TIME_MS,
)
from .coordinators import InimPrimeOutputsUpdateCoordinator
from .runtime_data import InimPrimeConfigEntry

# Default-named shutter-module motor terminals, e.g. "Mod.dom 01 T03" /
# "ModDomot 08 T04". A module is a shutter only when BOTH T03 and T04 are still
# default-named: on light modules T03/T04 are renamed (e.g. "Luce sala"), so
# they don't match and are skipped automatically.
_SHUTTER_LABEL = re.compile(r"(?i)^mod\.?dom(?:ot)?\s*0*(\d+)\s*t0*([34])\b")

_CLOSE_TERMINAL = 3
_OPEN_TERMINAL = 4


def discover_shutters(outputs: dict) -> dict[int, dict[int, int]]:
    """Map module number -> {3: close_output_id, 4: open_output_id}."""
    modules: dict[int, dict[int, int]] = {}
    for output_id, output in outputs.items():
        label = (output.label or "").replace("\x00", "").strip()
        match = _SHUTTER_LABEL.match(label)
        if not match:
            continue
        module = int(match.group(1))
        terminal = int(match.group(2))
        modules.setdefault(module, {})[terminal] = output_id
    return {
        module: terminals
        for module, terminals in modules.items()
        if _CLOSE_TERMINAL in terminals and _OPEN_TERMINAL in terminals
    }


async def async_setup_entry(hass, entry: InimPrimeConfigEntry, async_add_entities) -> None:
    coordinator = entry.runtime_data.coordinators.outputs
    if coordinator is None:
        return
    shutters = discover_shutters(dict(coordinator.gateway.outputs or {}))
    async_add_entities(
        InimPrimeShutterCover(
            coordinator,
            module,
            open_id=terminals[_OPEN_TERMINAL],
            close_id=terminals[_CLOSE_TERMINAL],
        )
        for module, terminals in sorted(shutters.items())
    )


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


class InimPrimeShutterCover(
    CoordinatorEntity[InimPrimeOutputsUpdateCoordinator],
    CoverEntity,
):
    _attr_has_entity_name = True
    _attr_name = None  # the cover IS the device; avoids "Tapparella 01 Tapparella 01"
    _attr_device_class = CoverDeviceClass.SHUTTER
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )

    def __init__(
        self,
        coordinator: InimPrimeOutputsUpdateCoordinator,
        module: int,
        *,
        open_id: int,
        close_id: int,
    ) -> None:
        super().__init__(coordinator)
        self._module = module
        self._open_id = open_id
        self._close_id = close_id
        self._open_time = SHUTTER_OPEN_TIME_MS / 1000
        self._close_time = SHUTTER_CLOSE_TIME_MS / 1000
        self._extra = SHUTTER_EXTRA_TIME_MS / 1000

        serial_number = coordinator.serial_number
        self._attr_unique_id = f"{serial_number}_shutter_{module}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{serial_number}_shutter_{module}")},
            name=f"Tapparella {module:02d}",
            model="Prime Roller Shutter",
            manufacturer=INIM_PRIME_DEVICE_MANUFACTURER,
        )

        # 0 = closed, 100 = open. Unknown until a full travel calibrates it.
        self._position: float = 50.0
        self._calibrated = False
        self._moving = 0  # +1 opening, -1 closing, 0 stopped
        self._commanded = False  # True while a timer-driven HA move is running
        self._ref_time = time.monotonic()
        self._ref_pos = 50.0
        self._stop_handle: asyncio.TimerHandle | None = None
        self._hard_stop_handle: asyncio.TimerHandle | None = None

    # ------------------------------------------------------------------
    # Position estimate
    # ------------------------------------------------------------------
    def _travel(self, direction: int) -> float:
        return self._open_time if direction > 0 else self._close_time

    def _position_now(self) -> float:
        if self._moving == 0:
            return self._position
        elapsed = time.monotonic() - self._ref_time
        return _clamp(self._ref_pos + self._moving * elapsed / self._travel(self._moving) * 100)

    def _relay_direction(self) -> int:
        outputs = self.coordinator.gateway.outputs or {}
        open_output = outputs.get(self._open_id)
        close_output = outputs.get(self._close_id)
        open_on = bool(open_output and open_output.state)
        close_on = bool(close_output and close_output.state)
        if open_on and not close_on:
            return 1
        if close_on and not open_on:
            return -1
        return 0

    # ------------------------------------------------------------------
    # Timers
    # ------------------------------------------------------------------
    def _cancel_timers(self) -> None:
        for handle in (self._stop_handle, self._hard_stop_handle):
            if handle is not None:
                handle.cancel()
        self._stop_handle = self._hard_stop_handle = None

    # ------------------------------------------------------------------
    # Relay control with safeguards
    # ------------------------------------------------------------------
    async def _release_relays(self) -> None:
        # Both off: never leave a motor energised.
        await self.coordinator.gateway.set_output(self._open_id, False)
        await self.coordinator.gateway.set_output(self._close_id, False)

    async def _move_to(self, target: float) -> None:
        target = _clamp(target)
        full = target in (0.0, 100.0)

        # Settle any move in progress before starting a new one.
        self._position = self._position_now()
        self._cancel_timers()

        direction = 1 if target > self._position else -1 if target < self._position else 0
        if full:
            # Always run a full open/close even from an unknown position, so it
            # reaches and recalibrates the end.
            direction = 1 if target == 100.0 else -1
        if direction == 0:
            return

        start_id = self._open_id if direction > 0 else self._close_id
        other_id = self._close_id if direction > 0 else self._open_id
        # Opposite relay off first, then drive -- never both on together.
        await self.coordinator.gateway.set_output(other_id, False)
        await self.coordinator.gateway.set_output(start_id, True)

        self._moving = direction
        self._commanded = True
        self._ref_time = time.monotonic()
        self._ref_pos = self._position

        travel = self._travel(direction)
        if full:
            duration = travel + self._extra
        else:
            duration = abs(target - self._position) / 100 * travel
        self._stop_handle = self.hass.loop.call_later(
            duration, lambda: self.hass.async_create_task(self._finish(target, full))
        )
        self._hard_stop_handle = self.hass.loop.call_later(
            travel + self._extra + 1.0,
            lambda: self.hass.async_create_task(self._finish(target, full)),
        )
        self.async_write_ha_state()

    async def _finish(self, target: float, full: bool) -> None:
        self._cancel_timers()
        await self._release_relays()
        self._moving = 0
        self._commanded = False
        if full:
            self._position = target
            self._calibrated = True
        else:
            self._position = _clamp(target)
        self.async_write_ha_state()

    async def _stop(self) -> None:
        self._position = self._position_now()
        self._cancel_timers()
        await self._release_relays()
        self._moving = 0
        self._commanded = False
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # Following moves started elsewhere (wall button / timer), when idle
    # ------------------------------------------------------------------
    @callback
    def _handle_coordinator_update(self) -> None:
        if not self._commanded:
            relay_direction = self._relay_direction()
            if relay_direction != self._moving:
                self._position = self._position_now()
                self._moving = relay_direction
                self._ref_time = time.monotonic()
                self._ref_pos = self._position
            elif self._moving != 0:
                pos = self._position_now()
                if pos in (0.0, 100.0):
                    self._calibrated = True
                self._position = pos
        super()._handle_coordinator_update()

    # ------------------------------------------------------------------
    # Cover interface
    # ------------------------------------------------------------------
    @property
    def current_cover_position(self) -> int:
        return int(round(self._position_now()))

    @property
    def is_closed(self) -> bool | None:
        if not self._calibrated:
            return None
        return self._position_now() <= 1

    @property
    def is_opening(self) -> bool:
        return self._moving > 0

    @property
    def is_closing(self) -> bool:
        return self._moving < 0

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._move_to(100.0)

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._move_to(0.0)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        await self._move_to(float(kwargs[ATTR_POSITION]))

    async def async_stop_cover(self, **kwargs: Any) -> None:
        await self._stop()

    async def async_will_remove_from_hass(self) -> None:
        self._cancel_timers()
        if self._moving != 0 and self._commanded:
            await self._release_relays()
