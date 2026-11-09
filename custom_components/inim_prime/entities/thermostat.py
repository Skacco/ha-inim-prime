from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..const import DOMAIN, INIM_PRIME_DEVICE_MANUFACTURER


class ThermostatTemperatureSensor(CoordinatorEntity, SensorEntity):
    """Live temperature of one panel thermostat (temperature sensor)."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, index: int, name: str) -> None:
        super().__init__(coordinator)
        self._index = index
        serial_number = coordinator.serial_number
        # Each thermostat is one entity named by its room, all grouped under a
        # single "Thermostats" device (like the Zones / Partitions / Timers ones).
        self._attr_name = name or f"Thermostat {index}"
        self._attr_unique_id = f"{serial_number}_thermostat_{index}"
        self._attr_device_info = DeviceInfo(
            identifiers = {(DOMAIN, f"{serial_number}_thermostats")},
            name = "Thermostats",
            model = "Prime Thermostats",
            manufacturer = INIM_PRIME_DEVICE_MANUFACTURER,
        )

    @property
    def native_value(self) -> float | None:
        thermostat = self.coordinator.gateway.thermostats.get(self._index)
        return thermostat.temperature if thermostat is not None else None
