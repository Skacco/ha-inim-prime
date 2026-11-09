"""Adapter that exposes inim.prime.native.Client through the unified interface.

Only wraps what the native gateway currently supports: zones (status +
bypass), partitions (status + arming + alarm-memory reset), and outputs
(on/off only, no dimming). GSM, system faults, log events and scenarios are
not implemented in the native library yet, so this adapter simply has no
methods for them -- InimPrimeGateway is what decides whether those features
are available at all (gated on whether PrimelanAdapter is configured).
"""
from __future__ import annotations

import asyncio
import re
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
    NATIVE_RECONNECT_MIN_INTERVAL,
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
        "_reconnect_lock",
        "_last_reconnect_attempt",
        "_timer_labels",
        "_timers",
    )

    def __init__(self, client: NativeClient) -> None:
        self._client = client

        self._zones: dict[int, UnifiedZone] = {}
        self._partitions: dict[int, UnifiedPartition] = {}
        self._outputs: dict[int, UnifiedOutput] = {}

        self._reconnect_lock = asyncio.Lock()
        self._last_reconnect_attempt: float | None = None
        self._timer_labels: dict[int, str] | None = None
        self._timers: dict[int, UnifiedTimer] = {}

    async def _call_with_reconnect(self, coro_factory):
        try:
            return await coro_factory()
        except (ConnectionError, TimeoutError, OSError):
            await self._ensure_reconnected()
            return await coro_factory()

    async def _ensure_reconnected(self) -> None:
        async with self._reconnect_lock:
            now = asyncio.get_running_loop().time()
            if (
                    self._last_reconnect_attempt is not None and
                    now - self._last_reconnect_attempt < NATIVE_RECONNECT_MIN_INTERVAL
            ):
                return
            self._last_reconnect_attempt = now
            await self._client.reconnect()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def connect(self) -> None:
        await self._client.connect()
        await self._client.ensure_initialized()

    async def close(self) -> None:
        self._client.disconnect()

    async def read_memory(self, address: int, length: int) -> bytes:
        """Read raw panel memory (used e.g. for the module-name table)."""
        async with self._client._lock:
            return await self._client._protocol.read_memory(address, length)

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
        await self._call_with_reconnect(_do)

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
        await self._call_with_reconnect(
            lambda: self._client.set_zone_bypass(zone_id, bypass)
        )

    async def set_all_zone_bypasses(self, bypass: bool) -> None:
        await self._call_with_reconnect(
            lambda: self._client.set_all_zone_bypasses(bypass)
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
        await self._call_with_reconnect(
            lambda: self._client.set_partition_arming_statuses(native_arming_statuses)
        )

    async def set_partition_arming_status(
            self,
            partition_id: int,
            arming_status: UnifiedArmingStatus,
    ) -> None:
        native_arming_status = _UNIFIED_ARMING_STATUS_MAP[arming_status]
        await self._call_with_reconnect(
            lambda: self._client.set_partition_arming_status(partition_id, native_arming_status)
        )

    async def set_all_partitions_arming_status(self, arming_status: UnifiedArmingStatus) -> None:
        native_arming_status = _UNIFIED_ARMING_STATUS_MAP[arming_status]
        await self._call_with_reconnect(
            lambda: self._client.set_all_partitions_arming_status(native_arming_status)
        )

    ### Reset partition memory
    async def reset_partition_memories(self, partition_ids: set[int]) -> None:
        await self._call_with_reconnect(
            lambda: self._client.reset_partition_memories(partition_ids)
        )

    async def reset_partition_memory(self, partition_id: int) -> None:
        await self._call_with_reconnect(
            lambda: self._client.reset_partition_memory(partition_id)
        )

    async def reset_all_partition_memories(self) -> None:
        await self._call_with_reconnect(self._client.reset_all_partition_memories)

    ### Outputs
    async def set_output(self, output_id: int, state: bool) -> None:
        await self._call_with_reconnect(
            lambda: self._client.set_output(output_id, state)
        )