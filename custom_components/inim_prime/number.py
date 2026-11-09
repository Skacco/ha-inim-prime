"""Per-shutter travel-time settings (like Prime/STUDIO's salita/discesa times).

Each roller shutter gets two editable numbers -- open ("Tempo salita") and close
("Tempo discesa") time in seconds. The cover reads these live (shared dict in
runtime_data), so each tapparella can be tuned independently. Values persist
across restarts via RestoreNumber.
"""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, INIM_PRIME_DEVICE_MANUFACTURER
from .cover import default_times, discover_shutters
from .runtime_data import InimPrimeConfigEntry

_KIND_NAME = {"open": "Tempo salita", "close": "Tempo discesa"}


async def async_setup_entry(hass, entry: InimPrimeConfigEntry, async_add_entities) -> None:
    coordinator = entry.runtime_data.coordinators.outputs
    if coordinator is None:
        return
    shutters = discover_shutters(dict(coordinator.gateway.outputs or {}))
    shutter_times = entry.runtime_data.shutter_times
    entities = []
    for module in sorted(shutters):
        times = shutter_times.setdefault(module, default_times())
        for kind in ("open", "close"):
            entities.append(
                InimPrimeShutterTimeNumber(coordinator.serial_number, module, kind, times)
            )
    async_add_entities(entities)


class InimPrimeShutterTimeNumber(RestoreNumber):
    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 1
    _attr_native_max_value = 180
    _attr_native_step = 0.5
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:timer-cog-outline"

    def __init__(self, serial_number: str, module: int, kind: str, times: dict[str, float]) -> None:
        self._kind = kind
        self._times = times
        self._attr_name = _KIND_NAME[kind]
        self._attr_unique_id = f"{serial_number}_shutter_{module}_{kind}_time"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{serial_number}_shutter_{module}")},
            name=f"Tapparella {module:02d}",
            model="Prime Roller Shutter",
            manufacturer=INIM_PRIME_DEVICE_MANUFACTURER,
        )

    @property
    def native_value(self) -> float:
        return self._times[self._kind]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self._times[self._kind] = float(last.native_value)

    async def async_set_native_value(self, value: float) -> None:
        self._times[self._kind] = float(value)
        self.async_write_ha_state()
