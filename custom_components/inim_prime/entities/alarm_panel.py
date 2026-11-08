from __future__ import annotations

import asyncio
import time
from typing import Any

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .panel import create_panel_device_info
from ..coordinators import (
    InimPrimePartitionsUpdateCoordinator,
    InimPrimeScenariosUpdateCoordinator,
)
from ..models.partitions import UnifiedArmingStatus, UnifiedPartitionState

_COMMAND_STATE: dict[str, AlarmControlPanelState] = {
    "disarm": AlarmControlPanelState.DISARMED,
    "arm_away": AlarmControlPanelState.ARMED_AWAY,
    "arm_home": AlarmControlPanelState.ARMED_HOME,
    "arm_night": AlarmControlPanelState.ARMED_NIGHT,
    "arm_vacation": AlarmControlPanelState.ARMED_VACATION,
    "arm_custom_bypass": AlarmControlPanelState.ARMED_CUSTOM_BYPASS,
}

_COMMAND_FEATURE: dict[str, AlarmControlPanelEntityFeature] = {
    "arm_away": AlarmControlPanelEntityFeature.ARM_AWAY,
    "arm_home": AlarmControlPanelEntityFeature.ARM_HOME,
    "arm_night": AlarmControlPanelEntityFeature.ARM_NIGHT,
    "arm_vacation": AlarmControlPanelEntityFeature.ARM_VACATION,
    "arm_custom_bypass": AlarmControlPanelEntityFeature.ARM_CUSTOM_BYPASS,
}


class ScenarioAlarmPanel(
    CoordinatorEntity[InimPrimeScenariosUpdateCoordinator],
    AlarmControlPanelEntity,
):
    """Whole-system alarm panel driven by panel scenarios.

    Works like the Sdomotica panel: each command activates the scenario
    configured for it. The state is the mode whose scenario is active on the
    panel, cross-checked with the partitions:
      - any partition in alarm            -> triggered
      - active scenario mapped to a mode  -> that mode
        (but "disarmed" while some partition is armed -> custom bypass,
         and an armed mode while every partition is disarmed -> disarmed)
      - otherwise: disarmed if every partition is disarmed, else custom bypass
    """

    _attr_has_entity_name = True
    _attr_name = "Alarm"
    _attr_code_arm_required = False
    _attr_code_format = None

    # After a command, how long to wait for the panel to report the new
    # scenario before falling back to the plain partition-based state.
    _PENDING_TIMEOUT = 20.0

    def __init__(
            self,
            coordinator: InimPrimeScenariosUpdateCoordinator,
            partitions_coordinator: InimPrimePartitionsUpdateCoordinator,
            commands: dict[str, int],
    ):
        super().__init__(coordinator)
        self._partitions_coordinator = partitions_coordinator
        self._commands = dict(commands)
        self._scenario_to_state = {
            scenario_id: _COMMAND_STATE[command]
            for command, scenario_id in self._commands.items()
        }

        features = AlarmControlPanelEntityFeature(0)
        for command, feature in _COMMAND_FEATURE.items():
            if command in self._commands:
                features |= feature
        self._attr_supported_features = features

        self._pending: tuple[str, float] | None = None  # (command, deadline)
        self._armed_snapshot: frozenset[int] | None = None
        self._attr_unique_id = f"{self.coordinator.serial_number}_alarm_panel"
        self._attr_device_info = create_panel_device_info(self.coordinator.serial_number)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self._partitions_coordinator.async_add_listener(self._handle_partitions_update)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    @callback
    def _handle_partitions_update(self) -> None:
        # Partitions (native) update much faster than the active scenario
        # (PrimeLAN). When the armed set changes, re-read the scenario right
        # away so the state doesn't briefly show "custom bypass".
        armed = frozenset(
            p.partition_id for p in self._partitions
            if p.arming_status not in (None, UnifiedArmingStatus.DISARMED)
        )
        if self._armed_snapshot is not None and armed != self._armed_snapshot:
            self.hass.async_create_task(self.coordinator.async_request_refresh())
        self._armed_snapshot = armed
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    @property
    def _partitions(self):
        return list(self.coordinator.gateway.partitions.values())

    def _active_scenario_id(self) -> int | None:
        for scenario in (self.coordinator.gateway.scenarios or {}).values():
            if scenario.active:
                return scenario.scenario_id
        return None

    @property
    def available(self) -> bool:
        return super().available and self._partitions_coordinator.last_update_success

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        if self._pending is not None:
            command, deadline = self._pending
            if self._active_scenario_id() == self._commands.get(command) or time.monotonic() > deadline:
                self._pending = None
            else:
                return (
                    AlarmControlPanelState.DISARMING if command == "disarm"
                    else AlarmControlPanelState.ARMING
                )

        partitions = self._partitions
        if any(p.partition_state == UnifiedPartitionState.ALARM for p in partitions):
            return AlarmControlPanelState.TRIGGERED

        known = [p.arming_status for p in partitions if p.arming_status is not None]
        all_disarmed = all(status == UnifiedArmingStatus.DISARMED for status in known)

        mapped = self._scenario_to_state.get(self._active_scenario_id())
        if mapped is not None:
            if mapped == AlarmControlPanelState.DISARMED and not all_disarmed:
                return AlarmControlPanelState.ARMED_CUSTOM_BYPASS
            if mapped != AlarmControlPanelState.DISARMED and all_disarmed:
                return AlarmControlPanelState.DISARMED
            return mapped

        if not known:
            return None
        return AlarmControlPanelState.DISARMED if all_disarmed else AlarmControlPanelState.ARMED_CUSTOM_BYPASS

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        active = self._active_scenario_id()
        scenarios = self.coordinator.gateway.scenarios or {}
        return {
            "active_scenario_id": active,
            "active_scenario": scenarios[active].label if active in scenarios else None,
            "armed_partitions": [
                p.label for p in self._partitions
                if p.arming_status not in (None, UnifiedArmingStatus.DISARMED)
            ],
        }

    # ------------------------------------------------------------------
    async def _run(self, command: str) -> None:
        target = self._commands[command]
        await self.coordinator.gateway.activate_scenario(target)
        self._pending = (command, time.monotonic() + self._PENDING_TIMEOUT)
        self.async_write_ha_state()
        # The panel needs a moment to switch scenario: poll it briefly.
        for _ in range(8):
            await self.coordinator.async_refresh()
            if self._active_scenario_id() == target:
                break
            await asyncio.sleep(1)
        await self._partitions_coordinator.async_refresh()
        self.async_write_ha_state()

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self._run("disarm")

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self._run("arm_away")

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        await self._run("arm_home")

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        await self._run("arm_night")

    async def async_alarm_arm_vacation(self, code: str | None = None) -> None:
        await self._run("arm_vacation")

    async def async_alarm_arm_custom_bypass(self, code: str | None = None) -> None:
        await self._run("arm_custom_bypass")
