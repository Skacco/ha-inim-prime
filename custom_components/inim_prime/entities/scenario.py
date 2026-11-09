from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .panel import create_panel_device_info
from ..coordinators import InimPrimeScenariosUpdateCoordinator
from ..models.scenarios import UnifiedScenario


class ScenarioButton(
    CoordinatorEntity[InimPrimeScenariosUpdateCoordinator],
    ButtonEntity,
):
    """Activates a panel scenario (same role as the Sdomotica scenario buttons)."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:play-box-outline"

    def __init__(
            self,
            coordinator: InimPrimeScenariosUpdateCoordinator,
            scenario: UnifiedScenario,
    ):
        super().__init__(coordinator)

        self.scenario_id = scenario.scenario_id
        self._attr_name = f"Scenario {scenario.label or scenario.scenario_id}"
        self._attr_unique_id = f"{self.coordinator.serial_number}_scenario_{self.scenario_id}_activate"
        self._attr_device_info = create_panel_device_info(self.coordinator.serial_number)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        scenarios = self.coordinator.gateway.scenarios or {}
        scenario = scenarios.get(self.scenario_id)
        return {
            "scenario_id": self.scenario_id,
            "active": scenario.active if scenario else None,
        }

    async def async_press(self) -> None:
        await self.coordinator.gateway.activate_scenario(self.scenario_id)
        await self.coordinator.async_refresh()


class ActiveScenarioSensor(
    CoordinatorEntity[InimPrimeScenariosUpdateCoordinator],
    SensorEntity,
):
    """Label of the scenario currently active on the panel."""

    _attr_has_entity_name = True
    _attr_name = "Scenario attivo"
    _attr_icon = "mdi:play-box-multiple-outline"

    def __init__(self, coordinator: InimPrimeScenariosUpdateCoordinator):
        super().__init__(coordinator)
        self._attr_unique_id = f"{self.coordinator.serial_number}_active_scenario"
        self._attr_device_info = create_panel_device_info(self.coordinator.serial_number)

    def _active(self) -> UnifiedScenario | None:
        for scenario in (self.coordinator.gateway.scenarios or {}).values():
            if scenario.active:
                return scenario
        return None

    @property
    def native_value(self) -> str | None:
        scenario = self._active()
        if scenario is None:
            return None
        return scenario.label or str(scenario.scenario_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        scenario = self._active()
        return {"scenario_id": scenario.scenario_id if scenario else None}
