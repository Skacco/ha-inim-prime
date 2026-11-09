from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..const import DOMAIN, INIM_PRIME_DEVICE_MANUFACTURER
from ..coordinators import InimPrimeTimersUpdateCoordinator
from ..models.timers import UnifiedTimer


class _TimerEntity(CoordinatorEntity[InimPrimeTimersUpdateCoordinator]):
    _attr_has_entity_name = True
    _unique_suffix = ""

    def __init__(self, coordinator: InimPrimeTimersUpdateCoordinator, timer: UnifiedTimer):
        super().__init__(coordinator)
        self.timer_id = timer.timer_id
        serial_number = coordinator.serial_number
        self._attr_unique_id = f"{serial_number}_timer_{self.timer_id}{self._unique_suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers = {(DOMAIN, f"{serial_number}_timers")},
            name = "Timers",
            model = "Inim Prime Timers",
            manufacturer = INIM_PRIME_DEVICE_MANUFACTURER,
        )

    def _timer(self) -> UnifiedTimer | None:
        timers = self.coordinator.gateway.timers or {}
        return timers.get(self.timer_id)

    @property
    def available(self) -> bool:
        return super().available and self._timer() is not None


class TimerActiveBinarySensor(_TimerEntity, BinarySensorEntity):
    """On while the panel timer is active (inside its time slot)."""

    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator: InimPrimeTimersUpdateCoordinator, timer: UnifiedTimer):
        super().__init__(coordinator, timer)
        self._attr_name = timer.label

    @property
    def is_on(self) -> bool | None:
        timer = self._timer()
        return timer.active if timer else None

    @property
    def extra_state_attributes(self) -> dict:
        timer = self._timer()
        return {"enabled": timer.enabled} if timer else {}


class TimerEnabledSwitch(_TimerEntity, SwitchEntity):
    """Enable/disable the panel timer (like the keypad's user menu)."""

    _attr_icon = "mdi:timer-cog-outline"
    _unique_suffix = "_enabled"

    def __init__(self, coordinator: InimPrimeTimersUpdateCoordinator, timer: UnifiedTimer):
        super().__init__(coordinator, timer)
        self._attr_name = f"{timer.label} enabled"

    @property
    def is_on(self) -> bool | None:
        timer = self._timer()
        return timer.enabled if timer else None

    async def _set(self, enabled: bool) -> None:
        await self.coordinator.gateway.set_timer_enabled(self.timer_id, enabled)
        await self.coordinator.async_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)
