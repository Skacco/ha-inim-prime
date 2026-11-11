from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..const import DOMAIN, INIM_PRIME_DEVICE_MANUFACTURER
from ..coordinators import InimPrimeOutputsUpdateCoordinator
from ..models.outputs import UnifiedOutput


def create_outputs_device_info(serial_number: str) -> DeviceInfo:
    # All outputs share one "Uscite" device (like Zona / Partizione / Timer).
    return DeviceInfo(
        identifiers = {(DOMAIN, f"{serial_number}_outputs")},
        name = "Inim Uscite",
        model = "Inim Prime Uscite",
        manufacturer = INIM_PRIME_DEVICE_MANUFACTURER,
    )


def _level_to_brightness(level: int) -> int:
    return round(level * 255 / 100)


def _brightness_to_level(brightness: int) -> int:
    return max(1, min(100, round(brightness * 100 / 255)))


class _OutputEntity(CoordinatorEntity[InimPrimeOutputsUpdateCoordinator]):
    """Shared state/commands for a panel output."""

    _attr_has_entity_name = True

    def __init__(
            self,
            coordinator: InimPrimeOutputsUpdateCoordinator,
            output: UnifiedOutput,
    ):
        super().__init__(coordinator)

        self.output_id = output.output_id
        self._attr_name = output.label or f"Uscita {output.output_id}"
        self._attr_unique_id = f"{self.coordinator.serial_number}_output_{self.output_id}"
        self._attr_device_info = create_outputs_device_info(self.coordinator.serial_number)

    @property
    def _output(self) -> UnifiedOutput | None:
        outputs = self.coordinator.gateway.outputs
        return outputs.get(self.output_id) if outputs is not None else None

    @property
    def available(self) -> bool:
        return super().available and self._output is not None

    @property
    def is_on(self) -> bool | None:
        output = self._output
        return output.state if output else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"output_id": self.output_id}

    async def _set(self, state: bool, level: int | None = None) -> None:
        await self.coordinator.gateway.set_output(self.output_id, state, level)
        await self.coordinator.async_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)


class OutputSwitch(_OutputEntity, SwitchEntity):
    """On/off panel output (relay or home-automation output)."""

    _attr_icon = "mdi:electric-switch"

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)


class OutputLight(_OutputEntity, LightEntity):
    """Dimmable panel output."""

    _attr_icon = "mdi:lightbulb"
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    @property
    def brightness(self) -> int | None:
        output = self._output
        if output is None or output.level is None:
            return None
        return _level_to_brightness(output.level)

    async def async_turn_on(self, **kwargs: Any) -> None:
        level = None
        if ATTR_BRIGHTNESS in kwargs:
            level = _brightness_to_level(kwargs[ATTR_BRIGHTNESS])
        await self._set(True, level)
