"""Roller shutters (tapparelle) driven through the panel's command outputs.

The shutter modules are "Modulo tapparella Smart": they hold their own
up/down travel times and run a full open/close from a single trigger. So HA
does not time the motor relays itself (that never worked for the down
direction -- the module reclaims the relay). Instead each shutter is mapped,
in the integration options, to the panel's own command outputs: one "open"
and one "close" virtual output (e.g. "Apri tenda sala" / "Chiudi ... sala").

Opening/closing sends a short pulse to that command output; the module does
the rest. There is no stop command and no position feedback, so the cover
exposes only open and close.

Only `set_output` is used -- the same safe operation as the output switches.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    INIM_PRIME_DEVICE_MANUFACTURER,
    SHUTTER_PULSE_MS,
    SHUTTER_FIELD_OPEN,
    SHUTTER_FIELD_CLOSE,
    SHUTTER_PRESET_FIELDS,
    SHUTTER_COMMAND_FIELDS,
)
from .coordinators import InimPrimeOutputsUpdateCoordinator
from .runtime_data import InimPrimeConfigEntry

# Default-named shutter-module motor terminals, e.g. "Mod.dom 01 T03" /
# "ModDomot 08 T04". Used only to enumerate the shutter modules (one cover per
# module) and to hide those raw motor relays from the switch platform.
_SHUTTER_LABEL = re.compile(r"(?i)^mod\.?dom(?:ot)?\s*0*(\d+)\s*t0*([34])\b")

_CLOSE_TERMINAL = 3
_OPEN_TERMINAL = 4

# Options key holding the per-shutter command mapping:
#   {"1": {"open": <output_id>, "close": <output_id>}, ...}
OPT_SHUTTER_COMMANDS = "shutter_commands"


def discover_shutters(outputs: dict) -> dict[int, dict[int, int]]:
    """Map shutter module number -> {3: close_relay_id, 4: open_relay_id}.

    A module counts as a shutter only when BOTH its T03 and T04 motor terminals
    are still default-named (on light modules they are renamed).
    """
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


def _shutter_commands(entry: InimPrimeConfigEntry) -> dict[int, dict[str, int]]:
    """Read the per-shutter open/close command mapping from the options."""
    raw = entry.options.get(OPT_SHUTTER_COMMANDS) or {}
    mapping: dict[int, dict] = {}
    for module, cmds in raw.items():
        try:
            m = int(module)
        except (TypeError, ValueError):
            continue
        entry_map: dict = {}
        for kind in SHUTTER_COMMAND_FIELDS:
            value = (cmds or {}).get(kind)
            if value not in (None, ""):
                try:
                    entry_map[kind] = int(value)
                except (TypeError, ValueError):
                    pass
        name = (cmds or {}).get("name")
        if name:
            entry_map["name"] = str(name).strip()
        if entry_map:
            mapping[m] = entry_map
    return mapping


async def async_setup_entry(hass, entry: InimPrimeConfigEntry, async_add_entities) -> None:
    coordinator = entry.runtime_data.coordinators.outputs
    if coordinator is None:
        return
    # Discover from the full output set: the Mod.dom motor terminals that mark a
    # shutter module aren't on PrimeLAN, so `outputs` (filtered) would hide them.
    shutters = discover_shutters(dict(coordinator.gateway.all_outputs or {}))
    mapping = _shutter_commands(entry)
    # Auto-name from the panel's module table (e.g. "TENDA SALA"); the manual
    # name in the options, if set, wins.
    # Only expose shutters that are actually mapped (open + close commands set in
    # the options). An unmapped shutter can't be driven, so showing it as an
    # unavailable cover is just noise -- it stays out until it's configured.
    mapped = [
        module for module in sorted(shutters)
        if mapping.get(module, {}).get(SHUTTER_FIELD_OPEN) is not None
        and mapping.get(module, {}).get(SHUTTER_FIELD_CLOSE) is not None
    ]
    module_names: dict[int, str] = {}
    if mapped:
        module_names = await coordinator.gateway.module_labels(max(mapped))
    entities = [
        InimPrimeShutterCover(
            coordinator,
            module,
            open_cmd = mapping.get(module, {}).get(SHUTTER_FIELD_OPEN),
            close_cmd = mapping.get(module, {}).get(SHUTTER_FIELD_CLOSE),
            presets = {
                percent: mapping.get(module, {}).get(field)
                for field, percent in SHUTTER_PRESET_FIELDS.items()
            },
            name = mapping.get(module, {}).get("name") or module_names.get(module),
        )
        for module in mapped
    ]
    async_add_entities(entities)


class InimPrimeShutterCover(
    CoordinatorEntity[InimPrimeOutputsUpdateCoordinator],
    CoverEntity,
):
    # Full name (no has_entity_name) so the grouped "Shutters" device is not
    # prepended: the friendly name stays the shutter's own name (e.g. "TENDA SALA").
    _attr_device_class = CoverDeviceClass.SHUTTER

    def __init__(
        self,
        coordinator: InimPrimeOutputsUpdateCoordinator,
        module: int,
        *,
        open_cmd: int | None,
        close_cmd: int | None,
        presets: dict[int, int | None] | None = None,
        name: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._module = module
        self._open_cmd = open_cmd
        self._close_cmd = close_cmd
        self._pulse = SHUTTER_PULSE_MS / 1000
        # Map target position (%) -> command output. 0 = close, 100 = open,
        # plus any configured partial-open presets (e.g. 25/50/75 %).
        self._positions: dict[int, int] = {}
        if close_cmd is not None:
            self._positions[0] = close_cmd
        if open_cmd is not None:
            self._positions[100] = open_cmd
        for percent, cmd in (presets or {}).items():
            if cmd is not None:
                self._positions[percent] = cmd
        # Optimistic position (no feedback from the Smart module).
        self._position: int | None = None

        # These Smart modules have no stop (INIM Home has none either): a
        # command runs the full travel and can't be interrupted. So only
        # open/close, plus the position slider when partial presets exist.
        features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
        if any(p not in (0, 100) for p in self._positions):
            features |= CoverEntityFeature.SET_POSITION
        self._attr_supported_features = features

        serial_number = coordinator.serial_number
        # Each shutter is one cover entity named after the shutter, all grouped
        # under a single "Shutters" device (like Zones / Partitions / Timers).
        self._attr_name = name.strip() if name and name.strip() else f"Tapparella {module:02d}"
        # entity_id derives from the shutter name (e.g. cover.tenda_sala), not
        # from the module number.
        self._attr_unique_id = f"{serial_number}_shutter_{module}"
        self._attr_device_info = DeviceInfo(
            identifiers = {(DOMAIN, f"{serial_number}_shutters")},
            name = "Inim Tapparelle",
            model = "Inim Prime Tapparelle",
            manufacturer = INIM_PRIME_DEVICE_MANUFACTURER,
        )


    @property
    def available(self) -> bool:
        # Unmapped shutters can't be driven; show them as unavailable so it's
        # clear they still need an open/close command set in the options.
        return (
            super().available
            and self._open_cmd is not None
            and self._close_cmd is not None
        )

    @property
    def current_cover_position(self) -> int | None:
        # Optimistic: the last commanded position (no feedback from the module).
        return self._position

    @property
    def is_closed(self) -> bool | None:
        # Known only after a command set it; the module gives no feedback.
        if self._position is None:
            return None
        return self._position == 0

    async def _trigger(self, output_id: int | None) -> None:
        if output_id is None:
            return
        await self.coordinator.gateway.set_output(output_id, True)
        await asyncio.sleep(self._pulse)
        await self.coordinator.gateway.set_output(output_id, False)

    async def _go_to(self, position: int) -> None:
        self._position = position
        await self._trigger(self._positions.get(position))
        self.async_write_ha_state()

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._go_to(100)

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._go_to(0)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        # Snap the requested position to the nearest configured command.
        target = int(kwargs[ATTR_POSITION])
        nearest = min(self._positions, key = lambda p: abs(p - target))
        await self._go_to(nearest)
