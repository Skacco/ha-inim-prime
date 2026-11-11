from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

_LOGGER = logging.getLogger(__name__)

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
    CodeFormat,
)
from homeassistant.exceptions import ServiceValidationError
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
    # How long after an arm command to keep showing "arming" while the
    # partitions still read disarmed (covers the panel's exit delay).
    _ARMING_GRACE = 45.0
    # How long a command waits for the panel to confirm it.
    _CONFIRM_TIMEOUT = 15.0
    # How long a commanded scenario is taken as active before it is re-read.
    _ASSUME_TIME = 20.0

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
        self._arming_until: float = 0.0
        # Bumped by every command: a command still finishing (its last reads can
        # take seconds) must not overwrite the state of a newer one.
        self._command_seq = 0
        # (scenario_id, command seq, expiry): see _active_scenario_id.
        self._assumed_scenario: tuple[int, int, float] | None = None
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
        assumed = self._assumed_scenario
        if assumed is not None and self._read_active_scenario_id() == assumed[0]:
            # The panel now reports the scenario we assumed: it is real.
            self._assumed_scenario = None
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
        if armed:
            # Partitions are armed: the arm window has done its job.
            self._arming_until = 0.0
        self._armed_snapshot = armed
        self.async_write_ha_state()

    def _armed_set(self) -> frozenset[int]:
        return frozenset(
            p.partition_id for p in self._partitions
            if p.arming_status not in (None, UnifiedArmingStatus.DISARMED)
        )

    def _armed_modes(self) -> dict:
        """Armed partitions with their arming mode ({partition_id: mode})."""
        return {
            p.partition_id: p.arming_status
            for p in self._partitions
            if p.arming_status not in (None, UnifiedArmingStatus.DISARMED)
        }

    async def _read_scenario_now(self) -> None:
        """Read the scenario for this command (routine polls are on hold)."""
        try:
            data = await self.coordinator.gateway.update_scenarios(force=True)
        except Exception:  # noqa: BLE001 - the next routine poll will retry
            _LOGGER.debug("Alarm: scenario read failed", exc_info=True)
            return
        self.coordinator.async_set_updated_data(data)

    def _assume_time(self) -> float:
        """Long enough for a couple of routine scenario polls to confirm."""
        interval = self.coordinator.update_interval
        seconds = interval.total_seconds() if interval else 0.0
        return max(self._ASSUME_TIME, seconds * 2 + 5)

    def _set_direction(self, command: str) -> None:
        """Remember which way an HA command is moving the panel.

        During an arm the state reads "arming" until the partitions report
        armed (they lag the scenario); a disarm cancels that window. Set
        together with _pending, before the request, so no update that lands
        while the request is in flight sees a stale direction.
        """
        self._arming_until = (
            0.0 if command == "disarm"
            else time.monotonic() + self._ARMING_GRACE
        )

    # ------------------------------------------------------------------
    @property
    def _partitions(self):
        return list(self.coordinator.gateway.partitions.values())

    def _active_scenario_id(self) -> int | None:
        assumed = self._assumed_scenario
        if assumed is not None:
            # Our own command, confirmed by the partitions, until the scenario
            # is read again (see _run / _run_scenario).
            if assumed[1] == self._command_seq and time.monotonic() < assumed[2]:
                return assumed[0]
            self._assumed_scenario = None
        return self._read_active_scenario_id()

    def _read_active_scenario_id(self) -> int | None:
        """The active scenario as last read from the panel."""
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
            if command == "disarm":
                # A disarm is done when the partitions are off (not when the
                # scenario flips: that can be read before the partitions).
                reached = not self._armed_set()
            else:
                # Both reads must agree: the scenario alone can be a stale read
                # from before the previous command (same mode armed again).
                reached = (
                    self._active_scenario_id() == self._commands.get(command)
                    and bool(self._armed_set())
                )
            if reached or time.monotonic() > deadline:
                self._pending = None
            else:
                # Immediate feedback while the panel works on the command (it
                # can take several seconds to be confirmed).
                return (
                    AlarmControlPanelState.DISARMING if command == "disarm"
                    else AlarmControlPanelState.ARMING
                )

        partitions = self._partitions
        if any(p.partition_state == UnifiedPartitionState.ALARM for p in partitions):
            return AlarmControlPanelState.TRIGGERED

        known = [p.arming_status for p in partitions if p.arming_status is not None]
        all_disarmed = all(status == UnifiedArmingStatus.DISARMED for status in known)

        # Direction of a change isn't in the state (an arm's exit delay looks
        # like a just-finished disarm: scenario armed, partitions off), so for
        # an HA command it comes from _arming_until. An external (keypad) change
        # has no command, so a short settle window debounces the scenario lag.
        arming = time.monotonic() < self._arming_until
        settling = time.monotonic() < self._settle_until

        mapped = self._scenario_to_state.get(self._active_scenario_id())
        if mapped is not None:
            if mapped == AlarmControlPanelState.DISARMED and not all_disarmed:
                return AlarmControlPanelState.ARMING if arming or settling else AlarmControlPanelState.ARMED_CUSTOM_BYPASS
            if mapped != AlarmControlPanelState.DISARMED and all_disarmed:
                return AlarmControlPanelState.ARMING if arming else AlarmControlPanelState.DISARMED
            return mapped

        if not known:
            return None
        if all_disarmed:
            return AlarmControlPanelState.DISARMED
        return AlarmControlPanelState.ARMING if arming or settling else AlarmControlPanelState.ARMED_CUSTOM_BYPASS

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
        self._set_direction(command)
        self._command_seq += 1
        seq = self._command_seq
        self._assumed_scenario = None
        before = self._armed_modes()
        # Keep the panel link free for this command's own reads.
        self.coordinator.gateway.hold_polling(self._CONFIRM_TIMEOUT)
        self.async_write_ha_state()
        started = time.monotonic()
        _LOGGER.debug("Alarm %s: sending scenario %s", command, target)
        try:
            await self.coordinator.gateway.activate_scenario(target)
        except Exception:
            _LOGGER.debug(
                "Alarm %s: command failed after %.2fs", command,
                time.monotonic() - started, exc_info=True,
            )
            if seq == self._command_seq:
                self.coordinator.gateway.release_polling()
                self._pending = None
                self.async_write_ha_state()
            raise
        # The panel needs a moment to switch scenario: poll it briefly.
        # Disarm is immediate on the panel, so confirm it on the fast partition
        # read rather than waiting for the (slower) scenario poll.
        disarm = command == "disarm"
        success = False
        deadline = time.monotonic() + self._CONFIRM_TIMEOUT
        attempt = 0
        confirmed_by = None
        _LOGGER.debug(
            "Alarm %s: panel accepted the command in %.2fs", command,
            time.monotonic() - started,
        )
        while time.monotonic() < deadline:
            if seq != self._command_seq:
                _LOGGER.debug("Alarm %s: superseded by a newer command", command)
                return  # superseded by a newer command
            # The partitions are the quick read, and all a disarm needs.
            read_started = time.monotonic()
            await self._partitions_coordinator.async_refresh()
            armed = self._armed_modes()
            _LOGGER.debug(
                "Alarm %s: partitions read #%d took %.2fs (armed: %s)", command,
                attempt + 1, time.monotonic() - read_started, sorted(armed),
            )
            if disarm:
                if not armed:
                    success = True
                    confirmed_by = "partitions"
                    break
            elif armed and armed != before:
                # The partitions moved to a new armed set right after our
                # command: that is the scenario we asked for. Show it now; the
                # (slow) scenario read confirms it in the background.
                success = True
                confirmed_by = "partitions"
                self._assumed_scenario = (
                    target, seq, time.monotonic() + self._assume_time()
                )
                break
            elif attempt >= 2:
                # Nothing to see on the partitions (e.g. the same mode armed
                # again): ask the scenario itself.
                read_started = time.monotonic()
                await self._read_scenario_now()
                _LOGGER.debug(
                    "Alarm %s: scenario read took %.2fs (active: %s)", command,
                    time.monotonic() - read_started, self._read_active_scenario_id(),
                )
                if self._active_scenario_id() == target:
                    success = True
                    confirmed_by = "scenario"
                    break
            attempt += 1
            await asyncio.sleep(0.5)
        _LOGGER.debug(
            "Alarm %s: %s after %.2fs in total", command,
            f"confirmed by the {confirmed_by}" if success else "NOT confirmed",
            time.monotonic() - started,
        )
        if seq == self._command_seq:
            self.coordinator.gateway.release_polling()

        result = {
            "command": command,
            "scenario_id": target,
            "success": success,
            "open_zones": [] if success else self._open_zones(),
        }
        self.hass.bus.async_fire(EVENT_COMMAND_RESULT, {
            "entity_id": self.entity_id,
            **result,
        })
        if seq != self._command_seq:
            # A newer command started while this one was finishing: its pending
            # state and direction are not ours to touch.
            return
        if not success and not disarm:
            # The arm didn't take (e.g. open zone): stop showing "arming".
            self._arming_until = 0.0
        self._pending = None
        self._last_command = result
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

    Works without PrimeLAN. Each arm command is a target: a {partition -> arming
    mode} map (Totale/Parziale/Istantaneo per partition); partitions not in the
    map are disarmed. Disarm turns every partition off. The state is read back
    from the partitions' arming status (ALARM -> triggered) and matched to the
    command whose target equals the current set, so mixed-mode setups (some
    partitions Totale, some Istantaneo in the same command) are reproduced.
    """

    _attr_has_entity_name = True
    _attr_name = "Allarme"
    _attr_code_arm_required = False
    _attr_code_format = None
    _PENDING_TIMEOUT = 20.0

    def __init__(
            self,
            partitions_coordinator: InimPrimePartitionsUpdateCoordinator,
            arm_targets: dict[str, dict[int, "UnifiedArmingStatus"]],
            arm_outputs: dict[str, list[int]] | None = None,
    ):
        super().__init__(partitions_coordinator)
        # command -> {partition_id: arming mode}; drop commands with no partitions.
        self._targets = {cmd: dict(target) for cmd, target in arm_targets.items() if target}
        # command -> outputs to switch on (disarm included); monostable outputs
        # self-release on the panel.
        self._outputs = {cmd: list(ids) for cmd, ids in (arm_outputs or {}).items() if ids}

        features = AlarmControlPanelEntityFeature(0)
        for command, feature in _COMMAND_FEATURE.items():
            if command in self._targets:
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

    def _current_armed(self) -> dict[int, "UnifiedArmingStatus"]:
        return {
            p.partition_id: p.arming_status
            for p in self._partitions
            if p.arming_status not in (None, UnifiedArmingStatus.DISARMED)
        }

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        partitions = self._partitions
        if any(p.partition_state == UnifiedPartitionState.ALARM for p in partitions):
            return AlarmControlPanelState.TRIGGERED

        current = self._current_armed()
        if self._pending is not None:
            command, deadline = self._pending
            target = self._targets.get(command, {})
            if current == target or time.monotonic() > deadline:
                self._pending = None
            else:
                # Immediate feedback while the panel works on the command (it
                # can take several seconds to be confirmed).
                return (
                    AlarmControlPanelState.DISARMING if command == "disarm"
                    else AlarmControlPanelState.ARMING
                )

        known = [p.arming_status for p in partitions if p.arming_status is not None]
        if not known:
            return None
        if not current:
            return AlarmControlPanelState.DISARMED
        for command, target in self._targets.items():
            if target == current:
                return _COMMAND_STATE[command]
        return AlarmControlPanelState.ARMED_CUSTOM_BYPASS

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "last_command": self._last_command,
            "armed_partitions": [
                f"{p.label} ({p.arming_status.name})" for p in self._partitions
                if p.arming_status not in (None, UnifiedArmingStatus.DISARMED)
            ],
        }

    async def _apply(self, command: str, target: dict[int, "UnifiedArmingStatus"]) -> None:
        self._pending = (command, time.monotonic() + self._PENDING_TIMEOUT)
        self.async_write_ha_state()
        statuses = {
            p.partition_id: target.get(p.partition_id, UnifiedArmingStatus.DISARMED)
            for p in self._partitions
        }
        try:
            await self.coordinator.gateway.set_partition_arming_statuses(statuses)
        except Exception:
            self._pending = None
            self.async_write_ha_state()
            raise
        # Fire the command's outputs (best effort: the arming already succeeded,
        # so an output error must not undo it). The panel self-releases them.
        for output_id in self._outputs.get(command, []):
            try:
                await self.coordinator.gateway.set_output(output_id, True)
            except Exception:  # noqa: BLE001 - outputs are an add-on to the arm
                _LOGGER.warning("Alarm panel: failed to activate output %s", output_id)
        await self.coordinator.async_refresh()
        self._last_command = {"command": command, "outputs": self._outputs.get(command, [])}
        self.async_write_ha_state()

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self._apply("disarm", {})

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self._apply("arm_away", self._targets.get("arm_away", {}))

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        await self._apply("arm_home", self._targets.get("arm_home", {}))

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        await self._apply("arm_night", self._targets.get("arm_night", {}))

    async def async_alarm_arm_vacation(self, code: str | None = None) -> None:
        await self._apply("arm_vacation", self._targets.get("arm_vacation", {}))


class HybridAlarmPanel(
    CoordinatorEntity[InimPrimeScenariosUpdateCoordinator],
    AlarmControlPanelEntity,
):
    """Whole-system panel mixing scenarios and direct partition arming.

    Used when both backends are configured. Each command is routed per the
    config: a command with a scenario id runs that scenario (PrimeLAN), keeping
    the panel's own checks (e.g. the open-zones alert); a command without one
    sets its partitions directly (Native) and fires its outputs, forcing the
    arm. This lets everyday commands stay on scenarios while a command like
    'vacation' uses a custom partition mix the scenarios can't express.

    State: ALARM on any partition -> triggered; otherwise a partition-path
    command whose target matches the armed set wins (specific mixes), then the
    active scenario's mode, then disarmed/custom-bypass as a fallback.
    """

    _attr_has_entity_name = True
    _attr_name = "Allarme"
    _attr_code_arm_required = False
    _attr_code_format = None
    _PENDING_TIMEOUT = 20.0
    _SETTLE_TIME = 5.0
    # How long after an arm command to keep showing "arming" while the
    # partitions still read disarmed (covers the panel's exit delay).
    _ARMING_GRACE = 45.0
    # How long a command waits for the panel to confirm it.
    _CONFIRM_TIMEOUT = 15.0
    # How long a commanded scenario is taken as active before it is re-read.
    _ASSUME_TIME = 20.0

    def __init__(
            self,
            coordinator: InimPrimeScenariosUpdateCoordinator,
            partitions_coordinator: InimPrimePartitionsUpdateCoordinator,
            scenario_map: dict[str, int],
            arm_targets: dict[str, dict[int, "UnifiedArmingStatus"]],
            arm_outputs: dict[str, list[int]] | None = None,
    ):
        super().__init__(coordinator)
        self._partitions_coordinator = partitions_coordinator
        # command -> scenario id (scenario path).
        self._scenarios = dict(scenario_map)
        # command -> {partition_id: mode} (partition path); only commands with a
        # non-empty partition set and NO scenario are driven by partitions.
        self._targets = {
            cmd: dict(target) for cmd, target in arm_targets.items()
            if target and cmd not in self._scenarios
        }
        # command -> outputs to switch on; only on the partition path (a scenario
        # already drives its own outputs on the panel). Disarm may have outputs.
        self._outputs = {
            cmd: list(ids) for cmd, ids in (arm_outputs or {}).items()
            if ids and cmd not in self._scenarios
        }
        self._scenario_to_state = {
            scenario_id: _COMMAND_STATE[command]
            for command, scenario_id in self._scenarios.items()
        }

        features = AlarmControlPanelEntityFeature(0)
        for command, feature in _COMMAND_FEATURE.items():
            if command in self._scenarios or command in self._targets:
                features |= feature
        self._attr_supported_features = features

        self._pending: tuple[str, float, str] | None = None  # (command, deadline, path)
        self._armed_snapshot: frozenset[int] | None = None
        self._settle_until: float = 0.0
        self._arming_until: float = 0.0
        # Bumped by every command: a command still finishing (its last reads can
        # take seconds) must not overwrite the state of a newer one.
        self._command_seq = 0
        # (scenario_id, command seq, expiry): see _active_scenario_id.
        self._assumed_scenario: tuple[int, int, float] | None = None
        self._last_command: dict[str, Any] | None = None
        self._attr_unique_id = f"{coordinator.serial_number}_alarm_panel"
        self._attr_device_info = create_panel_device_info(coordinator.serial_number)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self._partitions_coordinator.async_add_listener(self._handle_partitions_update)
        )
        self._armed_snapshot = self._armed_set()

    @callback
    def _handle_coordinator_update(self) -> None:
        assumed = self._assumed_scenario
        if assumed is not None and self._read_active_scenario_id() == assumed[0]:
            # The panel now reports the scenario we assumed: it is real.
            self._assumed_scenario = None
        self.async_write_ha_state()

    @callback
    def _handle_partitions_update(self) -> None:
        armed = self._armed_set()
        if self._armed_snapshot is not None and armed != self._armed_snapshot:
            self._settle_until = time.monotonic() + self._SETTLE_TIME
            self.hass.async_create_task(self.coordinator.async_request_refresh())
        if armed:
            # Partitions are armed: the arm window has done its job.
            self._arming_until = 0.0
        self._armed_snapshot = armed
        self.async_write_ha_state()

    def _armed_set(self) -> frozenset[int]:
        return frozenset(
            p.partition_id for p in self._partitions
            if p.arming_status not in (None, UnifiedArmingStatus.DISARMED)
        )

    def _armed_modes(self) -> dict:
        """Armed partitions with their arming mode ({partition_id: mode})."""
        return {
            p.partition_id: p.arming_status
            for p in self._partitions
            if p.arming_status not in (None, UnifiedArmingStatus.DISARMED)
        }

    async def _read_scenario_now(self) -> None:
        """Read the scenario for this command (routine polls are on hold)."""
        try:
            data = await self.coordinator.gateway.update_scenarios(force=True)
        except Exception:  # noqa: BLE001 - the next routine poll will retry
            _LOGGER.debug("Alarm: scenario read failed", exc_info=True)
            return
        self.coordinator.async_set_updated_data(data)

    def _assume_time(self) -> float:
        """Long enough for a couple of routine scenario polls to confirm."""
        interval = self.coordinator.update_interval
        seconds = interval.total_seconds() if interval else 0.0
        return max(self._ASSUME_TIME, seconds * 2 + 5)

    def _set_direction(self, command: str) -> None:
        """Remember which way an HA command is moving the panel.

        During an arm the state reads "arming" until the partitions report
        armed (they lag the scenario); a disarm cancels that window. Set
        together with _pending, before the request, so no update that lands
        while the request is in flight sees a stale direction.
        """
        self._arming_until = (
            0.0 if command == "disarm"
            else time.monotonic() + self._ARMING_GRACE
        )

    # ------------------------------------------------------------------
    @property
    def _partitions(self):
        return list(self._partitions_coordinator.gateway.partitions.values())

    def _active_scenario_id(self) -> int | None:
        assumed = self._assumed_scenario
        if assumed is not None:
            # Our own command, confirmed by the partitions, until the scenario
            # is read again (see _run / _run_scenario).
            if assumed[1] == self._command_seq and time.monotonic() < assumed[2]:
                return assumed[0]
            self._assumed_scenario = None
        return self._read_active_scenario_id()

    def _read_active_scenario_id(self) -> int | None:
        """The active scenario as last read from the panel."""
        for scenario in (self.coordinator.gateway.scenarios or {}).values():
            if scenario.active:
                return scenario.scenario_id
        return None

    def _current_armed(self) -> dict[int, "UnifiedArmingStatus"]:
        return {
            p.partition_id: p.arming_status
            for p in self._partitions
            if p.arming_status not in (None, UnifiedArmingStatus.DISARMED)
        }

    @property
    def available(self) -> bool:
        return super().available and self._partitions_coordinator.last_update_success

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        partitions = self._partitions
        if any(p.partition_state == UnifiedPartitionState.ALARM for p in partitions):
            return AlarmControlPanelState.TRIGGERED

        current = self._current_armed()
        if self._pending is not None:
            command, deadline, path = self._pending
            if command == "disarm":
                # Disarm is immediate on the panel; confirm on the partition read.
                reached = not current
            elif path == "scenario":
                # Both reads must agree: the scenario alone can be a stale read
                # from before the previous command (same mode armed again).
                reached = (
                    self._active_scenario_id() == self._scenarios.get(command)
                    and bool(current)
                )
            else:
                reached = current == self._targets.get(command, {})
            if reached or time.monotonic() > deadline:
                self._pending = None
            else:
                # Immediate feedback while the panel works on the command (it
                # can take several seconds to be confirmed).
                return (
                    AlarmControlPanelState.DISARMING if command == "disarm"
                    else AlarmControlPanelState.ARMING
                )

        known = [p.arming_status for p in partitions if p.arming_status is not None]
        if not known:
            return None
        all_disarmed = not current
        # Direction of a change isn't in the state (an arm's exit delay looks
        # like a just-finished disarm: scenario armed, partitions off), so for
        # an HA command it comes from _arming_until. An external (keypad) change
        # has no command, so a short settle window debounces the scenario lag.
        arming = time.monotonic() < self._arming_until
        settling = time.monotonic() < self._settle_until

        # A partition-path command whose exact mix is armed wins (e.g. vacation).
        if current:
            for command, target in self._targets.items():
                if target == current:
                    return _COMMAND_STATE[command]

        # Otherwise the active scenario decides (everyday away/home/night).
        mapped = self._scenario_to_state.get(self._active_scenario_id())
        if mapped is not None:
            if mapped == AlarmControlPanelState.DISARMED and not all_disarmed:
                return AlarmControlPanelState.ARMING if arming or settling else AlarmControlPanelState.ARMED_CUSTOM_BYPASS
            if mapped != AlarmControlPanelState.DISARMED and all_disarmed:
                return AlarmControlPanelState.ARMING if arming else AlarmControlPanelState.DISARMED
            return mapped

        if all_disarmed:
            return AlarmControlPanelState.DISARMED
        return AlarmControlPanelState.ARMING if arming or settling else AlarmControlPanelState.ARMED_CUSTOM_BYPASS

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        active = self._active_scenario_id()
        scenarios = self.coordinator.gateway.scenarios or {}
        return {
            "active_scenario_id": active,
            "active_scenario": scenarios[active].label if active in scenarios else None,
            "last_command": self._last_command,
            "armed_partitions": [
                f"{p.label} ({p.arming_status.name})" for p in self._partitions
                if p.arming_status not in (None, UnifiedArmingStatus.DISARMED)
            ],
        }

    # ------------------------------------------------------------------
    async def _execute(self, command: str) -> None:
        if command in self._scenarios:
            await self._run_scenario(command)
        else:
            await self._apply_partitions(command)

    async def _run_scenario(self, command: str) -> None:
        target = self._scenarios[command]
        self._pending = (command, time.monotonic() + self._PENDING_TIMEOUT, "scenario")
        self._set_direction(command)
        self._command_seq += 1
        seq = self._command_seq
        self._assumed_scenario = None
        before = self._armed_modes()
        # Keep the panel link free for this command's own reads.
        self.coordinator.gateway.hold_polling(self._CONFIRM_TIMEOUT)
        self.async_write_ha_state()
        started = time.monotonic()
        _LOGGER.debug("Alarm %s: sending scenario %s", command, target)
        try:
            await self.coordinator.gateway.activate_scenario(target)
        except Exception:
            _LOGGER.debug(
                "Alarm %s: command failed after %.2fs", command,
                time.monotonic() - started, exc_info=True,
            )
            if seq == self._command_seq:
                self.coordinator.gateway.release_polling()
                self._pending = None
                self.async_write_ha_state()
            raise
        # Disarm is immediate on the panel, so confirm it on the fast partition
        # read rather than waiting for the (slower) scenario poll.
        disarm = command == "disarm"
        success = False
        deadline = time.monotonic() + self._CONFIRM_TIMEOUT
        attempt = 0
        confirmed_by = None
        _LOGGER.debug(
            "Alarm %s: panel accepted the command in %.2fs", command,
            time.monotonic() - started,
        )
        while time.monotonic() < deadline:
            if seq != self._command_seq:
                _LOGGER.debug("Alarm %s: superseded by a newer command", command)
                return  # superseded by a newer command
            # The partitions are the quick read, and all a disarm needs.
            read_started = time.monotonic()
            await self._partitions_coordinator.async_refresh()
            armed = self._armed_modes()
            _LOGGER.debug(
                "Alarm %s: partitions read #%d took %.2fs (armed: %s)", command,
                attempt + 1, time.monotonic() - read_started, sorted(armed),
            )
            if disarm:
                if not armed:
                    success = True
                    confirmed_by = "partitions"
                    break
            elif armed and armed != before:
                # The partitions moved to a new armed set right after our
                # command: that is the scenario we asked for. Show it now; the
                # (slow) scenario read confirms it in the background.
                success = True
                confirmed_by = "partitions"
                self._assumed_scenario = (
                    target, seq, time.monotonic() + self._assume_time()
                )
                break
            elif attempt >= 2:
                # Nothing to see on the partitions (e.g. the same mode armed
                # again): ask the scenario itself.
                read_started = time.monotonic()
                await self._read_scenario_now()
                _LOGGER.debug(
                    "Alarm %s: scenario read took %.2fs (active: %s)", command,
                    time.monotonic() - read_started, self._read_active_scenario_id(),
                )
                if self._active_scenario_id() == target:
                    success = True
                    confirmed_by = "scenario"
                    break
            attempt += 1
            await asyncio.sleep(0.5)
        _LOGGER.debug(
            "Alarm %s: %s after %.2fs in total", command,
            f"confirmed by the {confirmed_by}" if success else "NOT confirmed",
            time.monotonic() - started,
        )
        if seq == self._command_seq:
            self.coordinator.gateway.release_polling()

        result = {
            "command": command,
            "scenario_id": target,
            "success": success,
            "open_zones": [] if success else self._open_zones(),
        }
        self.hass.bus.async_fire(EVENT_COMMAND_RESULT, {
            "entity_id": self.entity_id,
            **result,
        })
        if seq != self._command_seq:
            # A newer command started while this one was finishing: its pending
            # state and direction are not ours to touch.
            return
        if not success and not disarm:
            # The arm didn't take (e.g. open zone): stop showing "arming".
            self._arming_until = 0.0
        self._pending = None
        self._last_command = result
        self.async_write_ha_state()

    async def _apply_partitions(self, command: str) -> None:
        target = self._targets.get(command, {})
        self._pending = (command, time.monotonic() + self._PENDING_TIMEOUT, "partition")
        if command == "disarm":
            self._arming_until = 0.0
        self._command_seq += 1
        self.async_write_ha_state()
        statuses = {
            p.partition_id: target.get(p.partition_id, UnifiedArmingStatus.DISARMED)
            for p in self._partitions
        }
        try:
            await self._partitions_coordinator.gateway.set_partition_arming_statuses(statuses)
        except Exception:
            self._pending = None
            self.async_write_ha_state()
            raise
        for output_id in self._outputs.get(command, []):
            try:
                await self._partitions_coordinator.gateway.set_output(output_id, True)
            except Exception:  # noqa: BLE001 - outputs are an add-on to the arm
                _LOGGER.warning("Alarm panel: failed to activate output %s", output_id)
        await self._partitions_coordinator.async_refresh()
        self._last_command = {"command": command, "outputs": self._outputs.get(command, [])}
        self.async_write_ha_state()

    def _open_zones(self) -> list[str]:
        zones = getattr(self.coordinator.gateway, "zones", None) or {}
        return [
            zone.label or f"Zone {zone.zone_id}"
            for zone in zones.values()
            if zone.state == UnifiedZoneState.ALARM and not zone.bypass
        ]

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self._execute("disarm")

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self._execute("arm_away")

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        await self._execute("arm_home")

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        await self._execute("arm_night")

    async def async_alarm_arm_vacation(self, code: str | None = None) -> None:
        await self._execute("arm_vacation")


# ----------------------------------------------------------------------
# Optional second panel, protected by a PIN (code required to DISARM only).
# Same state and commands as the main panel; arming stays code-free. Meant
# for a wall tablet in a free-access room, while the main panel (no PIN)
# stays handy on a phone.
# ----------------------------------------------------------------------
class _PinDisarmMixin:
    """Mixin: require a PIN to disarm. Arming is left code-free."""

    def _make_pin_panel(self, pin: str) -> None:
        self._pin = str(pin)
        self._attr_code_format = CodeFormat.NUMBER
        self._attr_code_arm_required = False  # PIN only for disarm
        self._attr_name = "Allarme (PIN)"
        self._attr_unique_id = f"{self._attr_unique_id}_pin"

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        if self._pin and code != self._pin:
            raise ServiceValidationError("PIN errato")
        await super().async_alarm_disarm(code)


class ScenarioAlarmPanelPin(_PinDisarmMixin, ScenarioAlarmPanel):
    def __init__(self, *args, pin: str) -> None:
        super().__init__(*args)
        self._make_pin_panel(pin)


class PartitionAlarmPanelPin(_PinDisarmMixin, PartitionAlarmPanel):
    def __init__(self, *args, pin: str) -> None:
        super().__init__(*args)
        self._make_pin_panel(pin)


class HybridAlarmPanelPin(_PinDisarmMixin, HybridAlarmPanel):
    def __init__(self, *args, pin: str) -> None:
        super().__init__(*args)
        self._make_pin_panel(pin)
