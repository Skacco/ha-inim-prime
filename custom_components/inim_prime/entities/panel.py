import asyncio

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.components.button import ButtonEntity
from homeassistant.components.event import EventEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from ..const import INIM_PRIME_DEVICE_MANUFACTURER, DOMAIN
from ..coordinators import InimPrimePanelLogEventsCoordinator, InimPrimeSystemFaultsUpdateCoordinator, \
    InimPrimeZonesUpdateCoordinator, InimPrimePartitionsUpdateCoordinator
from ..models.log_events import UnifiedLogEvent
from ..models.system_faults import UnifiedSystemFault


def create_panel_device_info(
        serial_number: str,
        domain: str = DOMAIN,
) -> DeviceInfo:
    return DeviceInfo(
        identifiers = {(domain, serial_number)},
        name = "Inim Centrale",
        model = "Centrale Inim Prime",
        manufacturer = INIM_PRIME_DEVICE_MANUFACTURER,
        serial_number = serial_number,
    )


class BackendConnectivityBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Connectivity of one backend (Native or PrimeLAN), from whether its
    coordinator's last poll succeeded.

    Stays visible (never "unavailable") when the link drops, so you can see it is
    off, and records the last successful poll time in the 'ultimo_ok' attribute.
    Lives on the panel device as a diagnostic entity."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, serial_number: str, name: str, suffix: str) -> None:
        super().__init__(coordinator)
        self._attr_name = name
        self._attr_unique_id = f"{serial_number}_{suffix}"
        self._attr_device_info = create_panel_device_info(serial_number)
        self._last_ok = dt_util.utcnow() if coordinator.last_update_success else None

    @property
    def available(self) -> bool:
        # Report on/off even when the backend is down (don't inherit the
        # coordinator's availability, which would hide the sensor on failure).
        return True

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.last_update_success)

    @callback
    def _handle_coordinator_update(self) -> None:
        if self.coordinator.last_update_success:
            self._last_ok = dt_util.utcnow()
        super()._handle_coordinator_update()

    @property
    def extra_state_attributes(self) -> dict:
        return {"ultimo_ok": self._last_ok.isoformat() if self._last_ok else None}


class BackendLastUpdateSensor(CoordinatorEntity, SensorEntity):
    """Timestamp of the last successful poll of one backend (Native or PrimeLAN).

    Stays frozen at the last good moment while the link is down. Diagnostic, on
    the panel device. Companion of the connectivity binary sensor, as a visible
    entity (handy in cards/graphs)."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, serial_number: str, name: str, suffix: str) -> None:
        super().__init__(coordinator)
        self._attr_name = name
        self._attr_unique_id = f"{serial_number}_{suffix}"
        self._attr_device_info = create_panel_device_info(serial_number)
        self._last_ok = dt_util.utcnow() if coordinator.last_update_success else None

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self):
        return self._last_ok

    @callback
    def _handle_coordinator_update(self) -> None:
        if self.coordinator.last_update_success:
            self._last_ok = dt_util.utcnow()
        super()._handle_coordinator_update()


SYSTEM_FAULT_NAMES: dict[UnifiedSystemFault, str] = {
    UnifiedSystemFault.LOW_BATTERY: "Batteria scarica",
    UnifiedSystemFault.NETWORK_FAULT: "Guasto rete",
    UnifiedSystemFault.NO_TELEPHONE_LINE: "Linea telefonica assente",
    UnifiedSystemFault.RADIO_JAMMING: "Disturbo radio",
    UnifiedSystemFault.LOW_BATTERY_WIRELESS: "Batteria dispositivo wireless scarica",
    UnifiedSystemFault.WIRELESS_DEVICE_DISAPPEARANCE: "Dispositivo wireless assente",
    UnifiedSystemFault.GSM_FAULT: "Guasto GSM",
    UnifiedSystemFault.SENSOR_DIRTY: "Sensore sporco",
    UnifiedSystemFault.ZONE_FAULT: "Guasto zona",
    UnifiedSystemFault.SIRENS_FAULT: "Guasto sirene",
    UnifiedSystemFault.POWER_SUPPLY_FAULT: "Guasto alimentazione",
    UnifiedSystemFault.RADIO_KEYBOARDS_FAULT: "Guasto tastiere radio",
    UnifiedSystemFault.SABOTAGE_FAULT: "Sabotaggio",
    UnifiedSystemFault.INTERNET_FAULT: "Guasto Internet",
}

SYSTEM_FAULT_ICONS: dict[UnifiedSystemFault, str] = {
    UnifiedSystemFault.LOW_BATTERY: "mdi:battery-alert",
    UnifiedSystemFault.NETWORK_FAULT: "mdi:lan-disconnect",
    UnifiedSystemFault.NO_TELEPHONE_LINE: "mdi:phone-off",
    UnifiedSystemFault.RADIO_JAMMING: "mdi:signal-off",
    UnifiedSystemFault.LOW_BATTERY_WIRELESS: "mdi:battery-alert-variant",
    UnifiedSystemFault.WIRELESS_DEVICE_DISAPPEARANCE: "mdi:access-point-off",
    UnifiedSystemFault.GSM_FAULT: "mdi:signal-off",
    UnifiedSystemFault.SENSOR_DIRTY: "mdi:spray",
    UnifiedSystemFault.ZONE_FAULT: "mdi:map-marker-alert",
    UnifiedSystemFault.SIRENS_FAULT: "mdi:alarm-light-outline",
    UnifiedSystemFault.POWER_SUPPLY_FAULT: "mdi:flash-alert",
    UnifiedSystemFault.RADIO_KEYBOARDS_FAULT: "mdi:keyboard-off",
    UnifiedSystemFault.SABOTAGE_FAULT: "mdi:alert-octagon",
    UnifiedSystemFault.INTERNET_FAULT: "mdi:web-off",
}


class SystemFaultBinarySensor(
    CoordinatorEntity[InimPrimeSystemFaultsUpdateCoordinator],
    BinarySensorEntity,
):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
            self,
            coordinator: InimPrimeSystemFaultsUpdateCoordinator,
            fault: UnifiedSystemFault,
    ):
        super().__init__(coordinator)

        self._fault = fault

        self._attr_name = SYSTEM_FAULT_NAMES.get(
            self._fault,
            self._fault.name.replace("_", " ").title()
        )

        self._attr_unique_id = f"{self.coordinator.serial_number}_panel_system_fault_{self._fault.name.lower()}"

        self._attr_icon = SYSTEM_FAULT_ICONS.get(
            self._fault,
            "mdi:alert-circle",
        )

        self._attr_device_info = create_panel_device_info(self.coordinator.serial_number)

    @property
    def is_on(self) -> bool | None:
        system_faults = self.coordinator.gateway.system_faults
        return system_faults.has_fault(self._fault) if system_faults else None


class PanelSupplyVoltageSensor(
    CoordinatorEntity[InimPrimeSystemFaultsUpdateCoordinator],
    SensorEntity,
):
    _attr_name = "Tensione alimentazione"
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = "V"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_suggested_display_precision = 1

    def __init__(
            self,
            coordinator: InimPrimeSystemFaultsUpdateCoordinator,
    ):
        super().__init__(coordinator)
        self._attr_unique_id = f"{self.coordinator.serial_number}_panel_supply_voltage"
        self._attr_device_info = create_panel_device_info(self.coordinator.serial_number)

    @property
    def native_value(self) -> float | None:
        """Return the supply voltage."""
        system_faults = self.coordinator.gateway.system_faults
        return system_faults.supply_voltage if system_faults else None


class PanelLogEventsEvent(
    CoordinatorEntity[InimPrimePanelLogEventsCoordinator],
    EventEntity,
):
    _attr_name = "Registro eventi"
    _attr_event_types = ["generic"]
    # The full list is shown in the UI but not written to the recorder on
    # every event (it would duplicate the whole log in the database).
    _unrecorded_attributes = frozenset({"events"})

    def __init__(
            self,
            coordinator: InimPrimePanelLogEventsCoordinator,
    ):
        super().__init__(coordinator)

        self._attr_unique_id = f"{self.coordinator.serial_number}_panel_log_events"
        self._attr_device_info = create_panel_device_info(self.coordinator.serial_number)

    @property
    def extra_state_attributes(self) -> dict:
        """Last N panel log events (N = panel_log_events_fetch_limit), newest first."""
        limit = self.coordinator.panel_log_events_fetch_limit
        events = list(self.coordinator.last_panel_log_events)[-limit:]
        return {
            "events": [
                {
                    "timestamp": e.timestamp.isoformat(),
                    "type": e.type,
                    "location": e.location,
                    "agent": e.agent,
                }
                for e in reversed(events)
            ]
        }

    async def handle_events(self, log_events: list[UnifiedLogEvent]) -> None:
        for log_event in log_events:
            self._trigger_event(
                event_type = "generic",
                event_attributes = {
                    "timestamp": log_event.timestamp.isoformat(),
                    "type": log_event.type,
                    "agent": log_event.agent,
                    "location": log_event.location,
                }
            )
            # Multiple events fired at the same time are only visible if self.async_write_ha_state() is triggered each time after waiting some time.
            self.async_write_ha_state()
            await asyncio.sleep(0.01)


class BypassedZonesCountSensor(
    CoordinatorEntity[InimPrimeZonesUpdateCoordinator],
    SensorEntity,
):
    _attr_name = "Zone escluse"
    _attr_icon = "mdi:cancel"

    def __init__(
            self,
            coordinator: InimPrimeZonesUpdateCoordinator,
    ):
        super().__init__(coordinator)
        self._attr_unique_id = f"{self.coordinator.serial_number}_count_bypassed_zones"
        self._attr_device_info = create_panel_device_info(self.coordinator.serial_number)

    @property
    def native_value(self) -> int:
        return self.coordinator.gateway.count_bypassed_zones


class DisableAllZoneBypassesButton(
    CoordinatorEntity[InimPrimeZonesUpdateCoordinator],
    ButtonEntity,
):
    _attr_name = "Annulla esclusioni zone"
    _attr_icon = "mdi:check-all"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
            self,
            coordinator: InimPrimeZonesUpdateCoordinator,
    ):
        super().__init__(coordinator)

        self._attr_unique_id = f"{self.coordinator.serial_number}_disable_all_zone_bypasses"

        self._attr_device_info = create_panel_device_info(
            serial_number = self.coordinator.serial_number,
        )

    async def async_press(self) -> None:
        await self.coordinator.gateway.set_all_zone_bypasses(False)
        await self.coordinator.async_request_refresh()


class ResetAllPartitionMemoriesButton(
    CoordinatorEntity[InimPrimePartitionsUpdateCoordinator],
    ButtonEntity,
):
    _attr_name = "Cancella memorie aree"
    _attr_icon = "mdi:alarm-light-off"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
            self,
            coordinator: InimPrimePartitionsUpdateCoordinator,
    ):
        super().__init__(coordinator)

        self._attr_unique_id = f"{self.coordinator.serial_number}_reset_all_partition_memories"

        self._attr_device_info = create_panel_device_info(
            serial_number = self.coordinator.serial_number,
        )

    async def async_press(self) -> None:
        await self.coordinator.gateway.reset_all_partition_memories()
        await self.coordinator.async_request_refresh()


class ZonesAlarmMemoryCountSensor(
    CoordinatorEntity[InimPrimeZonesUpdateCoordinator],
    SensorEntity,
):
    _attr_name = "Memorie allarme zone"
    _attr_icon = "mdi:alarm-light"

    def __init__(
            self,
            coordinator: InimPrimeZonesUpdateCoordinator,
    ):
        super().__init__(coordinator)

        self._attr_unique_id = f"{self.coordinator.serial_number}_count_zone_alarm_memories"
        self._attr_device_info = create_panel_device_info(self.coordinator.serial_number)

    @property
    def native_value(self) -> int:
        return self.coordinator.gateway.count_zone_alarm_memories


class PartitionsAlarmMemoryCountSensor(
    CoordinatorEntity[InimPrimePartitionsUpdateCoordinator],
    SensorEntity,
):
    _attr_name = "Memorie allarme aree"
    _attr_icon = "mdi:alarm-light"

    def __init__(
            self,
            coordinator: InimPrimePartitionsUpdateCoordinator,
    ):
        super().__init__(coordinator)

        self._attr_unique_id = f"{self.coordinator.serial_number}_count_partition_alarm_memories"
        self._attr_device_info = create_panel_device_info(self.coordinator.serial_number)

    @property
    def native_value(self) -> int:
        return self.coordinator.gateway.count_partition_alarm_memories
