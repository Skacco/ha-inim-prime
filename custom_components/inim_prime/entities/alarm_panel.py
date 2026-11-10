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
from ..const import DOMAIN
from ..models.partitions import UnifiedArmingStatus, UnifiedPartitionState
from ..models.zones import UnifiedZoneState

# Fired when the panel doesn't switch to the requested scenario
# (typically because some zones are open and not bypassed).
EVENT_COMMAND_RESULT = f"{DOMAIN}_alarm_command_result"

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
    _attr_name = "Allarme"
    _attr_code_arm_required = False
    _attr_code_format = None

    # After a command, how long to wait for the panel to report the new
    # scenario before falling back to the plain partition-based state.
    _PENDING_TIMEOUT = 20.0
    # When partitions change on their own (keypad, app, timers) the scenario
    # is read again; until then, mismatches are shown as arming/disarming.
    _SETTLE_TIME = 5.0

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
        self._settle_until: float = 0.0
        self._last_command: dict[str, Any] | None = None
        self._attr_unique_id = f"{self.coordinator.serial_number}_alarm_panel"
        self._attr_device_info = create_panel_device_info(self.coordinator.serial_number)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self._partitions_coordinator.async_add_listener(self._handle_partitions_update)
        )
        self._armed_snapshot = self._armed_set()

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    @callback
    def _handle_partitions_update(self) -> None:
        # Partitions (native) update much faster than the active scenario
        # (PrimeLAN). When the armed set changes, re-read the scenario right
        # away so the state doesn't briefly show "custom bypass".
        armed = self._armed_set()
        if self._armed_snapshot is not None and armed != self._armed_snapshot:
            self._settle_until = time.monotonic() + self._SETTLE_TIME
            self.hass.async_create_task(self.coordinator.async_request_refresh())
        self._armed_snapshot = armed
        self.async_write_ha_state()

    def _armed_set(self) -> frozenset[int]:
        return frozenset(
            p.partition_id for p in self._partitions
            if p.arming_status not in (None, UnifiedArmingStatus.DISARMED)
        )

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

        settling = time.monotonic() < self._settle_until
        transition = (
            AlarmControlPanelState.DISARMING if all_disarmed
            else AlarmControlPanelState.ARMING
        )

        mapped = self._scenario_to_state.get(self._active_scenario_id())
        if mapped is not None:
            if mapped == AlarmControlPanelState.DISARMED and not all_disarmed:
                return transition if settling else AlarmControlPanelState.ARMED_CUSTOM_BYPASS
            if mapped != AlarmControlPanelState.DISARMED and all_disarmed:
                return transition if settling else AlarmControlPanelState.DISARMED
            return mapped

        if not known:
            return None
        if all_disarmed:
            return AlarmControlPanelState.DISARMED
        return transition if settling else AlarmControlPanelState.ARMED_CUSTOM_BYPASS

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        active = self._active_scenario_id()
        scenarios = self.coordinator.gateway.scenarios or {}
        return {
            "active_scenario_id": active,
            "active_scenario": scenarios[active].label if active in scenarios else None,
            "last_command": self._last_command,
            "armed_partitions": [
                p.label for p in self._partitions
                if p.arming_status not in (None, UnifiedArmingStatus.DISARMED)
            ],
        }

    # ------------------------------------------------------------------
    async def _run(self, command: str) -> None:
        target = self._commands[command]
        # Set before sending: native partitions can change while the request
        # is still in flight.
        self._pending = (command, time.monotonic() + self._PENDING_TIMEOUT)
        self.async_write_ha_state()
        try:
            await self.coordinator.gateway.activate_scenario(target)
        except Exception:
            self._pending = None
            self.async_write_ha_state()
            raise
        # The panel needs a moment to switch scenario: poll it briefly.
        success = False
        for _ in range(10):
            await self.coordinator.async_refresh()
            if self._active_scenario_id() == target:
                success = True
                break
            await asyncio.sleep(1)
        await self._partitions_coordinator.async_refresh()

        open_zones = [] if success else self._open_zones()
        self._pending = None
        self._last_command = {
            "command": command,
            "scenario_id": target,
            "success": success,
            "open_zones": open_zones,
        }
        self.hass.bus.async_fire(EVENT_COMMAND_RESULT, {
            "entity_id": self.entity_id,
            **self._last_command,
        })
        self.async_write_ha_state()

    def _open_zones(self) -> list[str]:
        """Zones currently open and not bypassed (likely why arming failed)."""
        zones = getattr(self.coordinator.gateway, "zones", None) or {}
        return [
            zone.label or f"Zone {zone.zone_id}"
            for zone in zones.values()
            if zone.state == UnifiedZoneState.ALARM and not zone.bypass
        ]

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


class PartitionAlarmPanel(
    CoordinatorEntity[InimPrimePartitionsUpdateCoordinator],
    AlarmControlPanelEntity,
):
    """Whole-system alarm panel driven directly by the partitions (Native).

    Works without PrimeLAN: each arm command sets a configured set of partitions
    to ARM_AWAY and the rest to disarmed; disarm turns every partition off. The
    state comes from the partitions' arming status (and ALARM -> triggered), with
    the armed set matched back to the configured command.
    """

    _attr_has_entity_name = True
    _attr_name = "Allarme"
    _attr_code_arm_required = False
    _attr_code_format = None
    _PENDING_TIMEOUT = 20.0
    _ARM_MODE = UnifiedArmingStatus.ARM_AWAY

    def __init__(
            self,
            partitions_coordinator: InimPrimePartitionsUpdateCoordinator,
            arm_map: dict[str, frozenset[int]],
    ):
        super().__init__(partitions_coordinator)
        self._arm_map = {cmd: frozenset(pids) for cmd, pids in arm_map.items() if pids}

        features = AlarmControlPanelEntityFeature(0)
        for command, feature in _COMMAND_FEATURE.items():
            if command in self._arm_map:
                features |= feature
        self._attr_supported_features = features

        self._pending: tuple[str, float] | None = None
        self._last_command: dict[str, Any] | None = None
        self._attr_unique_id = f"{partitions_coordinator.serial_number}_alarm_panel_native"
        self._attr_device_info = create_panel_device_info(partitions_coordinator.serial_number)

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    @property
    def _partitions(self):
        return list(self.coordinator.gateway.partitions.values())

    def _armed_set(self) -> frozenset[int]:
        return frozenset(
            p.partition_id for p in self._partitions
            if p.arming_status not in (None, UnifiedArmingStatus.DISARMED)
        )

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        partitions = self._partitions
        if any(p.partition_state == UnifiedPartitionState.ALARM for p in partitions):
            return AlarmControlPanelState.TRIGGERED

        armed = self._armed_set()
        if self._pending is not None:
            command, deadline = self._pending
            target = self._arm_map.get(command, frozenset())
            if armed == target or time.monotonic() > deadline:
                self._pending = None
            else:
                return (
                    AlarmControlPanelState.DISARMING if command == "disarm"
                    else AlarmControlPanelState.ARMING
                )

        known = [p.arming_status for p in partitions if p.arming_status is not None]
        if not known:
            return None
        if not armed:
            return AlarmControlPanelState.DISARMED
        for command, pids in self._arm_map.items():
            if pids == armed:
                return _COMMAND_STATE[command]
        return AlarmControlPanelState.ARMED_CUSTOM_BYPASS

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "last_command": self._last_command,
            "armed_partitions": [
                p.label for p in self._partitions
                if p.arming_status not in (None, UnifiedArmingStatus.DISARMED)
            ],
        }

    async def _apply(self, command: str, armed_pids: frozenset[int]) -> None:
        self._pending = (command, time.monotonic() + self._PENDING_TIMEOUT)
        self.async_write_ha_state()
        statuses = {
            p.partition_id: (
                self._ARM_MODE if p.partition_id in armed_pids
                else UnifiedArmingStatus.DISARMED
            )
            for p in self._partitions
        }
        try:
            await self.coordinator.gateway.set_partition_arming_statuses(statuses)
        except Exception:
            self._pending = None
            self.async_write_ha_state()
            raise
        await self.coordinator.async_refresh()
        self._last_command = {"command": command, "armed_partitions": sorted(armed_pids)}
        self.async_write_ha_state()

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self._apply("disarm", frozenset())

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self._apply("arm_away", self._arm_map.get("arm_away", frozenset()))

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        await self._apply("arm_home", self._arm_map.get("arm_home", frozenset()))

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        await self._apply("arm_night", self._arm_map.get("arm_night", frozenset()))

    async def async_alarm_arm_vacation(self, code: str | None = None) -> None:
        await self._apply("arm_vacation", self._arm_map.get("arm_vacation", frozenset()))
