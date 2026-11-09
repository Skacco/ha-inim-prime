from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..const import DOMAIN, INIM_PRIME_DEVICE_MANUFACTURER
from ..coordinators import InimPrimeTimersUpdateCoordinator
from ..models.timers import UnifiedTimer


class TimerActiveBinarySensor(
    CoordinatorEntity[InimPrimeTimersUpdateCoordinator],
    BinarySensorEntity,
):
    """On while the panel timer is active (inside its time slot)."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator: InimPrimeTimersUpdateCoordinator, timer: UnifiedTimer):
        super().__init__(coordinator)
        self.timer_id = timer.timer_id
        serial_number = coordinator.serial_number
        self._attr_name = timer.label
        self._attr_unique_id = f"{serial_number}_timer_{self.timer_id}"
        self._attr_device_info = DeviceInfo(
            identifiers = {(DOMAIN, f"{serial_number}_timers")},
            name = "Timers",
            model = "Prime Timers",
            manufacturer = INIM_PRIME_DEVICE_MANUFACTURER,
        )

    def _timer(self) -> UnifiedTimer | None:
        timers = self.coordinator.gateway.timers or {}
        return timers.get(self.timer_id)

    @property
    def available(self) -> bool:
        return super().available and self._timer() is not None

    @property
    def is_on(self) -> bool | None:
        timer = self._timer()
        return timer.active if timer else None

    @property
    def extra_state_attributes(self) -> dict:
        timer = self._timer()
        return {"enabled": timer.enabled} if timer else {}
