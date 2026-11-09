"""Roller shutters (tapparelle) driven through the Smart modules' motor relays.

Each shutter module exposes two outputs: T04 (open) and T03 (close). The panel
has no readable position over the local protocols -- it lives only in the cloud
-- so HA controls the motor relays directly (set_output) and estimates the
position from the programmed travel times. Reading the relay states (via the
outputs coordinator, polled through the native protocol) lets HA follow moves
started from anywhere: HA, the wall button, or a timer.

Only `set_output` and relay reads are used -- the same safe, proven operations
as the output switches. Safeguards: the two relays are never on together, a
hard-stop timer always cuts the motor after the travel time plus a margin, and
the relays are released when the entity is removed.
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
        self._hard_stop_delay = max(self._open_time, self._close_time) + SHUTTER_EXTRA_TIME_MS / 1000 + 1.0

        serial_number = coordinator.serial_number
        self._attr_unique_id = f"{serial_number}_shutter_{module}"
        self._attr_name = f"Tapparella {module:02d}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{serial_number}_shutter_{module}")},
            name=f"Tapparella {module:02d}",
            model="Prime Roller Shutter",
            manufacturer=INIM_PRIME_DEVICE_MANUFACTURER,
        )

        # Position 0 = closed, 100 = open. Unknown until a full travel calibrates
        # it, so is_closed stays None (unknown) while _calibrated is False.
        self._position: float = 50.0
        self._calibrated = False
        self._direction = 0  # +1 opening, -1 closing, 0 stopped
        self._ref_time = time.monotonic()
        self._ref_pos = 50.0
        self._target: float | None = None
        self._hard_stop: asyncio.TimerHandle | None = None

    # ------------------------------------------------------------------
    # Relay reading / position integration
    # ------------------------------------------------------------------
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

    def _advance(self) -> None:
        """Update the estimated position from elapsed motor time and relays."""
        now = time.monotonic()

        if self._direction != 0:
            travel = self._open_time if self._direction > 0 else self._close_time
            self._position = _clamp(
                self._ref_pos + self._direction * (now - self._ref_time) / travel * 100
            )

        relay_direction = self._relay_direction()
        if relay_direction != self._direction:
            # A move started or stopped -- from HA, the wall button or a timer.
            self._direction = relay_direction
            self._ref_time = now
            self._ref_pos = self._position
            if relay_direction == 0:
                self._target = None

        if self._direction > 0 and self._position >= 100:
            self._position = 100.0
            self._calibrated = True
            self._request_stop()
        elif self._direction < 0 and self._position <= 0:
            self._position = 0.0
            self._calibrated = True
            self._request_stop()
        elif self._target is not None and self._direction != 0:
            reached = (
                (self._direction > 0 and self._position >= self._target)
                or (self._direction < 0 and self._position <= self._target)
            )
            if reached:
                self._position = self._target
                self._request_stop()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._advance()
        super()._handle_coordinator_update()

    # ------------------------------------------------------------------
    # Relay control with safeguards
    # ------------------------------------------------------------------
    def _cancel_hard_stop(self) -> None:
        if self._hard_stop is not None:
            self._hard_stop.cancel()
            self._hard_stop = None

    def _schedule_hard_stop(self) -> None:
        self._cancel_hard_stop()
        self._hard_stop = self.hass.loop.call_later(
            self._hard_stop_delay,
            lambda: self.hass.async_create_task(self._stop()),
        )

    def _request_stop(self) -> None:
        """Stop from a sync context (coordinator callback)."""
        self._direction = 0
        self._target = None
        self._cancel_hard_stop()
        self.hass.async_create_task(self._release_relays())

    async def _release_relays(self) -> None:
        # Both off: never leave a motor energised.
        await self.coordinator.gateway.set_output(self._open_id, False)
        await self.coordinator.gateway.set_output(self._close_id, False)

    async def _move_to(self, target: float) -> None:
        self._advance()
        target = _clamp(target)
        direction = 1 if target > self._position else -1 if target < self._position else 0
        if direction == 0:
            return

        start_id = self._open_id if direction > 0 else self._close_id
        other_id = self._close_id if direction > 0 else self._open_id
        # Opposite relay off first, then drive -- never both on together.
        await self.coordinator.gateway.set_output(other_id, False)
        await self.coordinator.gateway.set_output(start_id, True)

        self._direction = direction
        self._ref_time = time.monotonic()
        self._ref_pos = self._position
        self._target = target
        self._schedule_hard_stop()
        self.async_write_ha_state()

    async def _stop(self) -> None:
        self._advance()
        self._direction = 0
        self._target = None
        self._cancel_hard_stop()
        await self._release_relays()
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # Cover interface
    # ------------------------------------------------------------------
    @property
    def current_cover_position(self) -> int:
        return int(round(self._position))

    @property
    def is_closed(self) -> bool | None:
        if not self._calibrated:
            return None
        return self._position <= 1

    @property
    def is_opening(self) -> bool:
        return self._direction > 0

    @property
    def is_closing(self) -> bool:
        return self._direction < 0

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._move_to(100.0)

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._move_to(0.0)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        await self._move_to(float(kwargs[ATTR_POSITION]))

    async def async_stop_cover(self, **kwargs: Any) -> None:
        await self._stop()

    async def async_will_remove_from_hass(self) -> None:
        self._cancel_hard_stop()
        if self._direction != 0:
            await self._release_relays()
