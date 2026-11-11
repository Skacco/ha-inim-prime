from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..const import DOMAIN, INIM_PRIME_DEVICE_MANUFACTURER
from ..coordinators import InimPrimeGSMUpdateCoordinator


def create_gsm_device_info(
        serial_number: str,
        domain: str = DOMAIN,
        sw_version: str | None = None
) -> DeviceInfo:
    return DeviceInfo(
        identifiers = {(domain, f"{serial_number}_gsm")},
        name = "GSM",
        model = "Inim Prime GSM",
        manufacturer = INIM_PRIME_DEVICE_MANUFACTURER,
        sw_version = sw_version,
    )


class GSMSupplyVoltageSensor(
    CoordinatorEntity[InimPrimeGSMUpdateCoordinator],
    SensorEntity,
):
    _attr_name = "Tensione alimentazione"
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = "V"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_suggested_display_precision = 1

    def __init__(
            self,
            coordinator: InimPrimeGSMUpdateCoordinator,
    ):
        super().__init__(coordinator)

        self._attr_unique_id = f"{self.coordinator.serial_number}_gsm_supply_voltage"

        self._attr_device_info = create_gsm_device_info(
            serial_number = self.coordinator.serial_number,
            sw_version = self.coordinator.data.firmware_version if self.coordinator.data else None,
        )

    @property
    def native_value(self) -> float | None:
        gsm = self.coordinator.gateway.gsm
        return gsm.supply_voltage if gsm else None


class GSMOperatorSensor(
    CoordinatorEntity[InimPrimeGSMUpdateCoordinator],
    SensorEntity,
):
    _attr_name = "Operatore"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:cellphone-wireless"

    def __init__(
            self,
            coordinator: InimPrimeGSMUpdateCoordinator,
    ):
        super().__init__(coordinator)

        self._attr_unique_id = f"{self.coordinator.serial_number}_gsm_operator"

        self._attr_device_info = create_gsm_device_info(
            serial_number = self.coordinator.serial_number,
            sw_version = self.coordinator.data.firmware_version if self.coordinator.data else None
        )

    @property
    def native_value(self) -> str | None:
        gsm = self.coordinator.gateway.gsm
        return gsm.operator if gsm else None



class GSMSignalStrengthSensor(
    CoordinatorEntity[InimPrimeGSMUpdateCoordinator],
    SensorEntity,
):
    _attr_name = "Potenza segnale"
    _attr_icon = "mdi:signal"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
            self,
            coordinator: InimPrimeGSMUpdateCoordinator,
    ):
        super().__init__(coordinator)

        self._attr_unique_id = f"{self.coordinator.serial_number}_gsm_signal_strength"

        self._attr_device_info = create_gsm_device_info(
            serial_number = self.coordinator.serial_number,
            sw_version = self.coordinator.data.firmware_version if self.coordinator.data else None,
        )

    @property
    def native_value(self) -> float | None:
        gsm = self.coordinator.gateway.gsm
        return gsm.signal_strength if gsm else None


class GSMCreditSensor(
    CoordinatorEntity[InimPrimeGSMUpdateCoordinator],
    SensorEntity,
):
    _attr_name = "Credito"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:cash-multiple"

    def __init__(
            self,
            coordinator: InimPrimeGSMUpdateCoordinator,
    ):
        super().__init__(coordinator)

        self._attr_unique_id = f"{self.coordinator.serial_number}_gsm_credit"

        self._attr_device_info = create_gsm_device_info(
            serial_number = self.coordinator.serial_number,
            sw_version = self.coordinator.data.firmware_version if self.coordinator.data else None,
        )

    @property
    def native_value(self) -> str | None:
        gsm = self.coordinator.gateway.gsm
        return gsm.credit if gsm else None
