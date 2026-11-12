"""Adapter that exposes inim.prime.native.Client through the unified interface.

Only wraps what the native gateway currently supports: zones (status +
bypass), partitions (status + arming + alarm-memory reset), and outputs
(on/off only, no dimming). GSM, system faults, log events and scenarios are
not implemented in the native library yet, so this adapter simply has no
methods for them -- InimPrimeGateway is what decides whether those features
are available at all (gated on whether PrimelanAdapter is configured).
"""
from __future__ import annotations

from dataclasses import replace

import asyncio
import logging
import re
import time
from types import MappingProxyType
from typing import Optional

from inim.prime.native.client import Client as NativeClient
from inim.prime.native.const import FrameOperation
from inim.prime.native.models.partitions import (
    ArmingStatus as NativeArmingStatus,
    Partition as NativePartition,
    PartitionState as NativePartitionState,
)
from inim.prime.native.models.outputs import (
    Output as NativeOutput,
)
from inim.prime.native.models.zones import (
    Zone as NativeZone,
    ZoneState as NativeZoneState,
)
from inim.prime.native.models.terminals import (
    Terminal as NativeTerminal,
)
from ..models.timers import UnifiedTimer
from ..models.outputs import (
    UnifiedOutput,
)
from ..models.partitions import (
    UnifiedArmingStatus,
    UnifiedPartition,
    UnifiedPartitionState,
)
from ..models.zones import (
    UnifiedZoneState,
    UnifiedZone,
)
from ..const import (
    PERIODIC_DROP_PERIOD,
    PERIODIC_DROP_PHASE,
    PERIODIC_DROP_TOLERANCE,
    NATIVE_COMMAND_RETRY_WINDOW,
    NATIVE_RECONNECT_FAST_WINDOW,
    NATIVE_RECONNECT_MIN_INTERVAL,
    NATIVE_RECONNECT_SLOW_INTERVAL,
)


_ZONE_STATE_MAP: dict[NativeZoneState, UnifiedZoneState] = {
    NativeZoneState.STANDBY: UnifiedZoneState.STANDBY,
    NativeZoneState.ALARM: UnifiedZoneState.ALARM,
    NativeZoneState.TAMPER: UnifiedZoneState.TAMPER,
    NativeZoneState.SHORT_CIRCUIT: UnifiedZoneState.SHORT_CIRCUIT,
    NativeZoneState.UNKNOWN: UnifiedZoneState.UNKNOWN,
}

_PARTITION_STATE_MAP: dict[NativePartitionState, UnifiedPartitionState] = {
    NativePartitionState.OK: UnifiedPartitionState.OK,
    NativePartitionState.ALARM: UnifiedPartitionState.ALARM,
    NativePartitionState.TAMPER: UnifiedPartitionState.TAMPER,
}

_ARMING_STATUS_MAP: dict[NativeArmingStatus, UnifiedArmingStatus] = {
    NativeArmingStatus.ARM_AWAY: UnifiedArmingStatus.ARM_AWAY,
    NativeArmingStatus.ARM_STAY: UnifiedArmingStatus.ARM_STAY,
    NativeArmingStatus.ARM_INSTANT: UnifiedArmingStatus.ARM_INSTANT,
    NativeArmingStatus.DISARMED: UnifiedArmingStatus.DISARMED,
}
_UNIFIED_ARMING_STATUS_MAP: dict[UnifiedArmingStatus, NativeArmingStatus] = {v: k for k, v in _ARMING_STATUS_MAP.items()}


def _zone_to_unified(zone: NativeZone, terminal: NativeTerminal) -> UnifiedZone:
    unified_zone: UnifiedZone

    if zone.zone_status is None:
        unified_zone = UnifiedZone(
            zone_id = zone.zone_id,
            terminal_id = terminal.terminal_id,
            label = zone.label,
            partitions = zone.zone_setting.partitions,
            state = None,
            bypass = None,
            alarm_memory = None,
            native_status_bytes = None,
        )
    else:
        unified_zone = UnifiedZone(
            zone_id = zone.zone_id,
            terminal_id = terminal.terminal_id,
            label = zone.label,
            partitions = zone.zone_setting.partitions,
            state = _ZONE_STATE_MAP.get(zone.zone_status.state, UnifiedZoneState.UNKNOWN),
            bypass = zone.zone_status.bypass,
            alarm_memory = zone.zone_status.alarm_memory,
            native_status_bytes = terminal.terminal_status.raw,
            native_state_str = zone.zone_status.state.name,
        )

    return unified_zone


def _partition_to_unified(partition: NativePartition) -> UnifiedPartition:
    unified_partition: UnifiedPartition

    if partition.status is None:
        unified_partition = UnifiedPartition(
            partition_id = partition.partition_id,
            label = partition.label,
            zones = partition.zones,
            arming_status = None,
            partition_state = None,
            alarm_memory = None,
        )
    else:
        unified_partition = UnifiedPartition(
            partition_id = partition.partition_id,
            label = partition.label,
            zones = partition.zones,
            arming_status = _ARMING_STATUS_MAP[partition.status.arming_status],
            partition_state = _PARTITION_STATE_MAP[partition.status.partition_state],
            alarm_memory = partition.status.alarm_memory,
        )

    return unified_partition

def _output_to_unified(output: NativeOutput) -> UnifiedOutput:
    unified_output: UnifiedOutput

    if output.output_status is None:
        unified_output = UnifiedOutput(
            output_id = output.terminal_id,
            label = (output.label or "").strip(),
            state = None,
        )
    else:
        unified_output = UnifiedOutput(
            output_id = output.terminal_id,
            label = (output.label or "").strip(),
            state = output.output_status.state,
        )
    return unified_output


# Timers (read like Prime/STUDIO's monitoring page does): 40 timers, one bit
# each in two 6-byte masks, labels in a 16-byte label table.
TIMERS_COUNT = 40
# 0x1403DEC0 = 335797248. The previous value pointed 512 bytes earlier, into
# the arming-scenario label table, so timers were read as "SCENARIO 019..050".
TIMER_LABELS_ADDRESS = 335797248
TIMER_LABEL_SIZE = 16
TIMERS_ACTIVE_ADDRESS = 0x10089C40
TIMERS_ENABLED_ADDRESS = 0x000057DC
TIMERS_MASK_SIZE = 6


_sleep = asyncio.sleep

_LOGGER = logging.getLogger(__name__)


def _partial_response_bytes(err: BaseException) -> int:
    """Bytes of the panel's answer that had arrived when the connection was
    closed (0 when it was closed before answering at all)."""
    seen = set()
    while err is not None and id(err) not in seen:
        seen.add(id(err))
        if isinstance(err, asyncio.IncompleteReadError):
            return len(err.partial)
        err = err.__cause__ or err.__context__
    return 0


def matches_periodic_drop_cadence(wall_time: float) -> bool:
    """True when wall_time falls on the periodic drop cadence (see const)."""
    offset = (wall_time - PERIODIC_DROP_PHASE) % PERIODIC_DROP_PERIOD
    return min(offset, PERIODIC_DROP_PERIOD - offset) <= PERIODIC_DROP_TOLERANCE

# Default (unconfigured) timer slot, e.g. "TIMER      004". Used to confirm we
# are reading the timer-label table and not, on another firmware layout, the
# codes or scenario labels.
_DEFAULT_TIMER_SLOT = re.compile(r"(?i)^TIMER\s+\d+$")


def _bit(mask: bytes, index: int) -> bool:
    return bool(mask[index // 8] & (1 << (index % 8)))


class NativeAdapter:
    """Wraps a connected inim.prime.native.Client instance."""
    __slots__ = (
        "_client",
        "_zones",
        "_partitions",
        "_outputs",
        "_already_connected",
        "_reconnect_lock",
        "_last_reconnect_attempt",
        "_outage_started",
        "_held_commands",
        "_timer_labels",
        "_timers",
        "_thermostat_names",
        "_thermostats",
    )

    def __init__(self, client: NativeClient, *, connected: bool = False) -> None:
        self._client = client
        # True when the client was handed over already connected (setup form).
        self._already_connected = connected

        self._zones: dict[int, UnifiedZone] = {}
        self._partitions: dict[int, UnifiedPartition] = {}
        self._outputs: dict[int, UnifiedOutput] = {}

        self._reconnect_lock = asyncio.Lock()
        self._last_reconnect_attempt: float | None = None
        self._outage_started: float | None = None
        # Commands currently waiting for the panel to come back (see _call_command).
        self._held_commands: set = set()
        self._timer_labels: dict[int, str] | None = None
        self._timers: dict[int, UnifiedTimer] = {}
        self._thermostat_names: tuple[dict[int, str], dict[int, str]] | None = None
        self._thermostats: dict = {}

    async def _call_with_reconnect(self, coro_factory, resend_started_answer: bool = True):
        try:
            result = await coro_factory()
        except (ConnectionError, TimeoutError, OSError) as err:
            self._note_outage(err)
            if resend_started_answer is False and _partial_response_bytes(err):
                raise
            await self._ensure_reconnected()
            result = await coro_factory()
        self._note_recovered()
        return result

    async def _call_command(self, coro_factory, key = None):
        """Run a command, waiting for the panel if it is unreachable right now.

        While the connection is known to be down the command is held (up to
        NATIVE_COMMAND_RETRY_WINDOW) and sent once the panel is back, instead
        of failing. It is only held *before* being sent: a command that fails
        halfway is not blindly repeated here, since the panel may have
        executed it (an output pulse sent twice would e.g. stop a gate).

        `key` identifies the command: the same command issued again while the
        first is still held is dropped (a second tap because "nothing
        happened" must not become a second pulse once the panel is back).
        """
        if key is not None and key in self._held_commands:
            _LOGGER.debug(
                "Command %s ignored: the same command is already waiting for the panel", key,
            )
            return None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + NATIVE_COMMAND_RETRY_WINDOW
        held = False
        try:
            while self._outage_started is not None and not self._client.is_connected:
                if not held:
                    held = True
                    if key is not None:
                        self._held_commands.add(key)
                    _LOGGER.debug("Command %s held: the panel is unreachable, waiting", key)
                try:
                    await self._ensure_reconnected()
                except (ConnectionError, TimeoutError, OSError):
                    pass
                if self._client.is_connected:
                    break
                if loop.time() >= deadline:
                    raise ConnectionError(
                        "Panel unreachable: the command was not sent"
                    )
                await _sleep(NATIVE_RECONNECT_MIN_INTERVAL)
        finally:
            self._held_commands.discard(key)
        if held:
            _LOGGER.debug("Command %s sent: the panel is back", key)
        try:
            return await self._call_with_reconnect(coro_factory, resend_started_answer = False)
        except ConnectionError as err:
            partial = _partial_response_bytes(err)
            if not partial:
                raise
            # The panel was already answering when it closed the connection:
            # it had received and executed the command. Not an error, and
            # sending it again would repeat it.
            _LOGGER.debug(
                "Command %s: the panel closed the connection after %d bytes of "
                "its answer, the command is considered executed", key, partial,
            )
            return None

    def _note_outage(self, err: BaseException) -> None:
        if self._outage_started is not None:
            return
        self._outage_started = asyncio.get_running_loop().time()
        if matches_periodic_drop_cadence(time.time()):
            _LOGGER.debug(
                "The panel closed the local connection (%s, %d bytes of the "
                "answer received). This matches the "
                "periodic drop seen every ~71.6 minutes (2^32 microseconds), "
                "when the panel restarts its own network connections: it is "
                "how the panel behaves, not a fault. Reconnecting, last state "
                "is kept",
                err, _partial_response_bytes(err),
            )
        else:
            _LOGGER.debug(
                "Connection to the panel lost (%s, %d bytes of the answer "
                "received). Reconnecting, last state is kept",
                err, _partial_response_bytes(err),
            )

    def _note_recovered(self) -> None:
        if self._outage_started is None:
            return
        elapsed = asyncio.get_running_loop().time() - self._outage_started
        self._outage_started = None
        _LOGGER.debug("Connection to the panel restored after %.1fs", elapsed)

    def _reconnect_interval(self, now: float) -> float:
        """Every second during a short drop, slower once the outage drags on."""
        if (
                self._outage_started is not None and
                now - self._outage_started >= NATIVE_RECONNECT_FAST_WINDOW
        ):
            return max(NATIVE_RECONNECT_MIN_INTERVAL, NATIVE_RECONNECT_SLOW_INTERVAL)
        return NATIVE_RECONNECT_MIN_INTERVAL

    async def _ensure_reconnected(self) -> None:
        async with self._reconnect_lock:
            now = asyncio.get_running_loop().time()
            if (
                    self._last_reconnect_attempt is not None and
                    now - self._last_reconnect_attempt < self._reconnect_interval(now)
            ):
                return
            self._last_reconnect_attempt = now
            await self._client.reconnect()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def connect(self) -> None:
        if self._already_connected:
            self._already_connected = False
        else:
            await self._client.connect()
        await self._client.ensure_initialized()

    async def close(self) -> None:
        self._client.disconnect()

    async def read_memory(self, address: int, length: int) -> bytes:
        """Read raw panel memory (used e.g. for the module-name table)."""
        async with self._client._lock:
            return await self._client._protocol.read_memory(address, length)

    # ------------------------------------------------------------------
    # Thermostats (temperature sensors)
    # ------------------------------------------------------------------
    async def _read_labels(self, address: int, count: int) -> dict[int, str]:
        from ..const import MODULE_LABEL_SIZE
        data = await self.read_memory(address, count * MODULE_LABEL_SIZE)
        labels: dict[int, str] = {}
        for i in range(count):
            raw = data[i * MODULE_LABEL_SIZE:(i + 1) * MODULE_LABEL_SIZE]
            name = raw.split(b"\x00", 1)[0].decode("latin-1").strip()
            if name:
                labels[i + 1] = name
        return labels

    async def _thermostat_status(self) -> bytes:
        from inim.prime.native.const import FrameOperation
        from ..const import THERMOSTAT_STATUS_OPERATION, THERMOSTAT_STATUS_LENGTH
        # operation (4 LE) + master PIN (6) + padding (2); a read-only status
        # command (the same Prime/STUDIO uses for its thermostat page).
        payload = THERMOSTAT_STATUS_OPERATION.to_bytes(4, "little") + b"\x74\x00\x00\x00\x00\x00\x00\x00"
        async with self._client._lock:
            return await self._client._protocol.exchange_payload(
                payload = payload,
                operation = FrameOperation.COMMAND,
                response_payload_length = THERMOSTAT_STATUS_LENGTH,
            )

    async def update_thermostats(self) -> dict:
        from ..const import (
            KEYPAD_LABELS_ADDRESS, KEYPAD_LABELS_COUNT,
            SENSOR_LABELS_ADDRESS, SENSOR_LABELS_COUNT,
        )
        from ..models.thermostats import (
            UnifiedThermostat, parse_temperatures, thermostat_name,
        )
        if self._thermostat_names is None:
            keypad = await self._read_labels(KEYPAD_LABELS_ADDRESS, KEYPAD_LABELS_COUNT)
            sensor = await self._read_labels(SENSOR_LABELS_ADDRESS, SENSOR_LABELS_COUNT)
            self._thermostat_names = (keypad, sensor)
        keypad, sensor = self._thermostat_names
        status = await self._thermostat_status()
        self._thermostats = {
            index: UnifiedThermostat(index, thermostat_name(index, keypad, sensor), temperature)
            for index, temperature in parse_temperatures(status).items()
        }
        return self._thermostats

    @property
    def thermostats(self) -> dict:
        return self._thermostats

    @property
    def serial_number(self) -> Optional[str]:
        info = self._client.panel_info
        return info[0] if info else None


    # ------------------------------------------------------------------
    #   Properties
    # ------------------------------------------------------------------
    @property
    def zones(self) -> MappingProxyType[int, UnifiedZone]:
        return MappingProxyType[int, UnifiedZone](self._zones)

    @property
    def partitions(self) -> MappingProxyType[int, UnifiedPartition]:
        return MappingProxyType[int, UnifiedPartition](self._partitions)

    @property
    def outputs(self) -> MappingProxyType[int, UnifiedOutput]:
        return MappingProxyType[int, UnifiedOutput](self._outputs)

    @property
    def timers(self) -> MappingProxyType[int, UnifiedTimer]:
        return MappingProxyType(self._timers)

    async def _read_memory(self, address: int, length: int) -> bytes:
        async with self._client._lock:
            return await self._client._protocol.read_memory(address, length)

    async def _read_stable(self, address: int, length: int, attempts: int = 5) -> bytes:
        """Read until two consecutive reads match.

        Right after a restart the panel answered the first read of the timer
        label table with part of the arming scenario labels ("SCENARIO 019"
        ...); a second read returned the right names.
        """
        previous = await self._read_memory(address, length)
        for _ in range(attempts):
            await _sleep(0.3)
            current = await self._read_memory(address, length)
            if current == previous:
                return current
            previous = current
        return previous

    async def _write_memory(self, address: int, data: bytes) -> None:
        """Write raw bytes, as Prime/STUDIO does (without its programming
        session, which makes the panel drop its connections for a while)."""
        header = (
            address.to_bytes(8, "little")
            + len(data).to_bytes(4, "little")
            + len(data).to_bytes(4, "little")
            + b"\x01\x00\x11"
        )
        request = header + bytes([sum(header) & 0xFF]) + data
        async with self._client._lock:
            ack = await self._client._protocol.exchange_payload(request, FrameOperation.READ, 1)
        if not ack or ack[0] != sum(data) & 0xFF:
            raise ConnectionError(f"Panel did not confirm the write at {address:#x}")

    async def set_timer_enabled(self, timer_id: int, enabled: bool) -> None:
        if not 0 <= timer_id < TIMERS_COUNT:
            raise ValueError(f"Invalid timer {timer_id}")

        async def _do() -> None:
            address = TIMERS_ENABLED_ADDRESS + timer_id // 8
            current = (await self._read_memory(address, 1))[0]
            bit = 1 << (timer_id % 8)
            new = current | bit if enabled else current & ~bit
            if new != current:
                await self._write_memory(address, bytes([new]))
        await self._call_command(_do, ("timer", timer_id, enabled))

    async def update_timers(self) -> MappingProxyType[int, UnifiedTimer]:
        async def _do() -> MappingProxyType[int, UnifiedTimer]:
            if self._timer_labels is None:
                raw = await self._read_stable(TIMER_LABELS_ADDRESS, TIMERS_COUNT * TIMER_LABEL_SIZE)
                labels = {
                    i: raw[i * TIMER_LABEL_SIZE:(i + 1) * TIMER_LABEL_SIZE]
                    .decode("latin-1").replace("\x00", "").strip()
                    for i in range(TIMERS_COUNT)
                }
                # The timer-label address differs by firmware/model. If we read
                # the wrong table (e.g. the codes or scenario labels on a layout
                # we don't know yet), the slots are "CODICE 2xx" / "SCENARIO 0xx"
                # instead of the "TIMER 0xx" defaults. Only trust the read when it
                # looks like a timer table, otherwise expose no timers rather than
                # bogus entities.
                looks_like_timers = sum(
                    bool(_DEFAULT_TIMER_SLOT.match(label)) for label in labels.values()
                )
                if looks_like_timers < 3:
                    self._timers = {}
                    return self.timers
                self._timer_labels = labels
            active = await self._read_memory(TIMERS_ACTIVE_ADDRESS, TIMERS_MASK_SIZE)
            enabled = await self._read_memory(TIMERS_ENABLED_ADDRESS, TIMERS_MASK_SIZE)
            self._timers = {
                i: UnifiedTimer(i, label, _bit(active, i), _bit(enabled, i))
                for i, label in self._timer_labels.items()
            }
            return self.timers
        return await self._call_with_reconnect(_do)

    @property
    def count_bypassed_zones(self) -> int:
        return sum(1 for zone in self._zones.values() if zone.bypass == True)

    @property
    def count_zone_alarm_memories(self) -> int:
        return sum(1 for zone in self._zones.values() if zone.alarm_memory == True)

    @property
    def count_partition_alarm_memories(self) -> int:
        return sum(1 for partition in self._partitions.values() if partition.alarm_memory == True)

    def get_zone(self, zone_id: int) -> UnifiedZone | None:
        return self._zones.get(zone_id)

    def get_partition(self, partition_id: int) -> UnifiedPartition | None:
        return self._partitions.get(partition_id)

    # State reported by a panel event (webhook), applied to the cache until the
    # next read replaces it. False when the object is not known.
    def apply_zone_state(self, zone_id: int, state: UnifiedZoneState) -> bool:
        zone = self._zones.get(zone_id)
        if zone is None:
            return False
        self._zones = {**self._zones, zone_id: replace(zone, state = state)}
        return True

    def apply_output_state(self, output_id: int, state: bool) -> bool:
        output = self._outputs.get(output_id)
        if output is None:
            return False
        self._outputs = {**self._outputs, output_id: replace(output, state = state)}
        return True

    # ------------------------------------------------------------------
    # Updaters
    # ------------------------------------------------------------------
    async def update_zones(self) -> MappingProxyType[int, UnifiedZone]:
        async def _do() -> MappingProxyType[int, UnifiedZone]:
            native_zone_terminals = await self._client.update_zone_terminals()

            result: dict[int, UnifiedZone] = {}
            for terminal_id, zone_terminal in native_zone_terminals.items():
                for zone in zone_terminal.zones:
                    result[zone.zone_id] = _zone_to_unified(zone, zone_terminal)

            self._zones = result
            return self.zones

        return await self._call_with_reconnect(_do)

    async def update_partitions(self) -> MappingProxyType[int, UnifiedPartition]:
        async def _do() -> MappingProxyType[int, UnifiedPartition]:
            native_partitions = await self._client.update_partitions()

            partitions: dict[int, UnifiedPartition] = {}
            for partition_id, partition in native_partitions.items():
                partitions[partition_id] = _partition_to_unified(partition)
            self._partitions = partitions
            return self.partitions
        return await self._call_with_reconnect(_do)

    async def update_outputs(self) -> MappingProxyType[int, UnifiedOutput]:
        async def _do() -> MappingProxyType[int, UnifiedOutput]:
            native_outputs = await self._client.update_outputs()

            outputs: dict[int, UnifiedOutput] = {}
            for output_id, output in native_outputs.items():
                outputs[output_id] = _output_to_unified(output)
            self._outputs = outputs
            return self.outputs
        return await self._call_with_reconnect(_do)


    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    ### Zones
    async def set_zone_bypass(self, zone_id: int, bypass: bool) -> None:
        await self._call_command(
            lambda: self._client.set_zone_bypass(zone_id, bypass),
            ("zone_bypass", zone_id, bypass),
        )

    async def set_all_zone_bypasses(self, bypass: bool) -> None:
        await self._call_command(
            lambda: self._client.set_all_zone_bypasses(bypass),
            ("all_zone_bypasses", bypass),
        )

    ### Partition arming status
    async def set_partition_arming_statuses(
            self,
            arming_statuses: dict[int, UnifiedArmingStatus],
    ) -> None:
        native_arming_statuses: dict[int, NativeArmingStatus] = {
            partition_id: _UNIFIED_ARMING_STATUS_MAP[arming_status]
            for partition_id, arming_status in arming_statuses.items()
        }
        await self._call_command(
            lambda: self._client.set_partition_arming_statuses(native_arming_statuses),
            ("arming_statuses", tuple(sorted(arming_statuses.items(), key = lambda item: item[0]))),
        )

    async def set_partition_arming_status(
            self,
            partition_id: int,
            arming_status: UnifiedArmingStatus,
    ) -> None:
        native_arming_status = _UNIFIED_ARMING_STATUS_MAP[arming_status]
        await self._call_command(
            lambda: self._client.set_partition_arming_status(partition_id, native_arming_status),
            ("arming_status", partition_id, arming_status),
        )

    async def set_all_partitions_arming_status(self, arming_status: UnifiedArmingStatus) -> None:
        native_arming_status = _UNIFIED_ARMING_STATUS_MAP[arming_status]
        await self._call_command(
            lambda: self._client.set_all_partitions_arming_status(native_arming_status),
            ("all_arming_status", arming_status),
        )

    ### Reset partition memory
    async def reset_partition_memories(self, partition_ids: set[int]) -> None:
        await self._call_command(
            lambda: self._client.reset_partition_memories(partition_ids),
            ("reset_memories", tuple(sorted(partition_ids))),
        )

    async def reset_partition_memory(self, partition_id: int) -> None:
        await self._call_command(
            lambda: self._client.reset_partition_memory(partition_id),
            ("reset_memory", partition_id),
        )

    async def reset_all_partition_memories(self) -> None:
        await self._call_command(
            self._client.reset_all_partition_memories, ("reset_all_memories",),
        )

    ### Outputs
    async def set_output(self, output_id: int, state: bool) -> None:
        await self._call_command(
            lambda: self._client.set_output(output_id, state),
            ("output", output_id, state),
        )