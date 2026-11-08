from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .panel import create_panel_device_info
from ..coordinators import InimPrimeOutputsUpdateCoordinator
from ..models.outputs import UnifiedOutput


def _level_to_brightness(level: int) -> int:
    return round(level * 255 / 100)


def _brightness_to_level(brightness: int) -> int:
    return max(1, min(100, round(brightness * 100 / 255)))


class OutputLight(
    CoordinatorEntity[InimPrimeOutputsUpdateCoordinator],
    LightEntity,
):
    """A panel output (relay, home-automation output or dimmer) as a light.

    Exposed as `light` like the outputs of the Sdomotica add-on, so existing
    automations and dashboards keep working after renaming the entity_ids.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:electric-switch"

    def __init__(
            self,
            coordinator: InimPrimeOutputsUpdateCoordinator,
            output: UnifiedOutput,
    ):
        super().__init__(coordinator)

        self.output_id = output.output_id
        self._attr_name = output.label or f"Output {output.output_id}"
        self._attr_unique_id = f"{self.coordinator.serial_number}_output_{self.output_id}"
        self._attr_device_info = create_panel_device_info(self.coordinator.serial_number)

        if output.dimmable:
            self._attr_color_mode = ColorMode.BRIGHTNESS
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
            self._attr_icon = "mdi:lightbulb"
        else:
            self._attr_color_mode = ColorMode.ONOFF
            self._attr_supported_color_modes = {ColorMode.ONOFF}

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
    def brightness(self) -> int | None:
        output = self._output
        if output is None or not output.dimmable or output.level is None:
            return None
        return _level_to_brightness(output.level)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"output_id": self.output_id}

    async def async_turn_on(self, **kwargs: Any) -> None:
        level = None
        if ATTR_BRIGHTNESS in kwargs:
            level = _brightness_to_level(kwargs[ATTR_BRIGHTNESS])
        await self.coordinator.gateway.set_output(self.output_id, True, level)
        await self.coordinator.async_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.gateway.set_output(self.output_id, False)
        await self.coordinator.async_refresh()
