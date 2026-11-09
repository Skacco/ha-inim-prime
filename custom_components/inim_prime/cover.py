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
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, INIM_PRIME_DEVICE_MANUFACTURER, SHUTTER_PULSE_MS
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
        for kind in ("open", "close", "stop"):
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
    shutters = discover_shutters(dict(coordinator.gateway.outputs or {}))
    mapping = _shutter_commands(entry)
    # Auto-name from the panel's module table (e.g. "TENDA SALA"); the manual
    # name in the options, if set, wins.
    module_names: dict[int, str] = {}
    if shutters:
        module_names = await coordinator.gateway.module_labels(max(shutters))
    entities = [
        InimPrimeShutterCover(
            coordinator,
            module,
            open_cmd = mapping.get(module, {}).get("open"),
            close_cmd = mapping.get(module, {}).get("close"),
            stop_cmd = mapping.get(module, {}).get("stop"),
            name = mapping.get(module, {}).get("name") or module_names.get(module),
        )
        for module in sorted(shutters)
    ]
    async_add_entities(entities)


class InimPrimeShutterCover(
    CoordinatorEntity[InimPrimeOutputsUpdateCoordinator],
    CoverEntity,
):
    _attr_has_entity_name = True
    _attr_name = None  # the cover IS the device
    _attr_device_class = CoverDeviceClass.SHUTTER

    def __init__(
        self,
        coordinator: InimPrimeOutputsUpdateCoordinator,
        module: int,
        *,
        open_cmd: int | None,
        close_cmd: int | None,
        stop_cmd: int | None = None,
        name: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._module = module
        self._open_cmd = open_cmd
        self._close_cmd = close_cmd
        self._stop_cmd = stop_cmd
        self._pulse = SHUTTER_PULSE_MS / 1000

        features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
        if stop_cmd is not None:
            features |= CoverEntityFeature.STOP
        self._attr_supported_features = features

        serial_number = coordinator.serial_number
        device_name = name.strip() if name and name.strip() else f"Tapparella {module:02d}"
        self._attr_unique_id = f"{serial_number}_shutter_{module}"
        self._attr_device_info = DeviceInfo(
            identifiers = {(DOMAIN, f"{serial_number}_shutter_{module}")},
            name = device_name,
            model = "Prime Roller Shutter",
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
    def is_closed(self) -> bool | None:
        # No position feedback from a Smart module.
        return None

    async def _trigger(self, output_id: int | None) -> None:
        if output_id is None:
            return
        await self.coordinator.gateway.set_output(output_id, True)
        await asyncio.sleep(self._pulse)
        await self.coordinator.gateway.set_output(output_id, False)

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._trigger(self._open_cmd)

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._trigger(self._close_cmd)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        await self._trigger(self._stop_cmd)
