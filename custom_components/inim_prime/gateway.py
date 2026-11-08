"""Gateway that unifies the native and PrimeLAN backends behind a single API.

Coordinators and entities talk only to this gateway; they never import from
inim.prime.native or inim.prime.primelan directly. When both backends are
configured, native is preferred for everything it supports; PrimeLAN remains
the only source for GSM, system faults, log events, and output dimming until
native grows those features. Outputs come from native when it is configured
(faster), otherwise from PrimeLAN (which also reports dimming levels).
"""
from __future__ import annotations

import re
from dataclasses import replace
from types import MappingProxyType

from .adapters.native_adapter import NativeAdapter
from .adapters.primelan_adapter import PrimelanAdapter

from .models.gsm import UnifiedGSM
from .models.log_events import UnifiedLogEvent
from .models.outputs import UnifiedOutput
from .models.scenarios import UnifiedScenario
from .models.partitions import UnifiedPartition, UnifiedArmingStatus
from .models.system_faults import UnifiedSystemFaults
from .models.zones import UnifiedZone

from .exceptions import InimPrimeFeatureNotSupportedError


_DEFAULT_PARTITION_LABEL = re.compile(r"^AREA\s*\d+$", re.IGNORECASE)


def _is_default_partition_label(label: str | None) -> bool:
    return bool(label) and bool(_DEFAULT_PARTITION_LABEL.match(label.strip()))


class InimPrimeGateway:

    def __init__(
            self,
            native: NativeAdapter | None = None,
            primelan: PrimelanAdapter | None = None,
    ) -> None:
        if native is None and primelan is None:
            raise ValueError("InimPrimeGateway requires at least one backend")

        self._native = native
        self._primelan = primelan
        # Output labels from PrimeLAN, used to name native outputs whose
        # label the native library can't read (see native_compat).
        self._primelan_output_labels: dict[int, str] | None = None
        # With both backends: False = only expose outputs PrimeLAN also
        # publishes (outputs enabled for the PrimeLAN user).
        self.show_hidden_outputs: bool = False
        # Hide areas still named like the panel default ("AREA 013").
        self.hide_unused_partitions: bool = False

        # Preferred backend for anything both sides support equally.

        self._preferred: NativeAdapter | PrimelanAdapter = native or primelan

    # ------------------------------------------------------------------
    # Capabilities -- used by __init__.py / platform files to decide which
    # coordinators & entities to set up at all.
    # ------------------------------------------------------------------
    @property
    def supports_zones(self) -> bool:
        return self._native is not None or self._primelan is not None

    @property
    def supports_partitions(self) -> bool:
        return self._native is not None or self._primelan is not None

    @property
    def supports_gsm(self) -> bool:
        return self._primelan is not None

    @property
    def supports_system_faults(self) -> bool:
        return self._primelan is not None

    @property
    def supports_log_events(self) -> bool:
        return self._primelan is not None

    @property
    def supports_outputs(self) -> bool:
        return self._native is not None or self._primelan is not None

    @property
    def supports_scenarios(self) -> bool:
        return self._primelan is not None

    @property
    def backends_active(self) -> list[str]:
        active = []
        if self._native is not None:
            active.append("native")
        if self._primelan is not None:
            active.append("primelan")
        return active

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def connect(self) -> None:
        if self._native is not None:
            await self._native.connect()
        if self._primelan is not None:
            await self._primelan.connect()

    async def close(self) -> None:
        if self._native is not None:
            await self._native.close()
        if self._primelan is not None:
            await self._primelan.close()

    # ------------------------------------------------------------------
    # Cached reads -- no I/O. Safe to call from anywhere, anytime.
    # ------------------------------------------------------------------
    @property
    def zones(self) -> MappingProxyType[int, UnifiedZone]:
        return self._preferred.zones

    @property
    def partitions(self) -> MappingProxyType[int, UnifiedPartition]:
        partitions = self._preferred.partitions
        if not self.hide_unused_partitions:
            return partitions
        return MappingProxyType({
            pid: p for pid, p in partitions.items() if not _is_default_partition_label(p.label)
        })

    @property
    def all_partitions(self) -> MappingProxyType[int, UnifiedPartition]:
        """Every partition, including the hidden unused ones."""
        return self._preferred.partitions

    @property
    def outputs(self) -> MappingProxyType[int, UnifiedOutput] | None:
        # Native is preferred for outputs (much faster polling); PrimeLAN is
        # used only when native isn't configured. Native has no dimming, so
        # dimmers are on/off when native is active.
        if self._native is not None:
            outputs = self._native.outputs
            labels = self._primelan_output_labels
            if labels is not None and not self.show_hidden_outputs:
                outputs = MappingProxyType({
                    oid: o for oid, o in outputs.items() if oid in labels
                })
            if labels and any(not o.label for o in outputs.values()):
                outputs = MappingProxyType({
                    oid: (replace(o, label = labels.get(oid, "")) if not o.label else o)
                    for oid, o in outputs.items()
                })
            return outputs
        return self._primelan.outputs if self._primelan is not None else None

    @property
    def scenarios(self) -> MappingProxyType[int, UnifiedScenario] | None:
        return self._primelan.scenarios if self._primelan is not None else None

    @property
    def gsm(self) -> UnifiedGSM | None:
        return self._primelan.gsm if self._primelan is not None else None

    @property
    def system_faults(self) -> UnifiedSystemFaults | None:
        return self._primelan.system_faults if self._primelan is not None else None

    @property
    def count_bypassed_zones(self) -> int:
        return self._preferred.count_bypassed_zones

    @property
    def count_zone_alarm_memories(self) -> int:
        return self._preferred.count_zone_alarm_memories

    @property
    def count_partition_alarm_memories(self) -> int:
        return self._preferred.count_partition_alarm_memories

    def get_zone(self, zone_id: int) -> UnifiedZone | None:
        return self._preferred.get_zone(zone_id)

    def get_partition(self, partition_id: int) -> UnifiedPartition | None:
            return self._preferred.get_partition(partition_id)

    # ------------------------------------------------------------------
    # Refreshes -- do I/O, overwrite the cache, return it. Coordinators call
    # these on their own poll schedule via _async_update_data().
    # ------------------------------------------------------------------
    async def update_zones(self) -> MappingProxyType[int, UnifiedZone]:
        return await self._preferred.update_zones()

    async def update_partitions(self) -> MappingProxyType[int, UnifiedPartition]:
        return await self._preferred.update_partitions()

    async def update_outputs(self) -> MappingProxyType[int, UnifiedOutput]:
        if self._native is not None:
            await self._native.update_outputs()
            # PrimeLAN is read once: for the list of outputs enabled for its
            # user (filter) and as a fallback for names.
            if self._primelan is not None and self._primelan_output_labels is None:
                try:
                    primelan_outputs = await self._primelan.update_outputs()
                    self._primelan_output_labels = {oid: o.label for oid, o in primelan_outputs.items()}
                except Exception:  # noqa: BLE001 - labels are cosmetic, retry next time
                    pass
            return self.outputs
        if self._primelan is not None:
            return await self._primelan.update_outputs()
        raise InimPrimeFeatureNotSupportedError("Outputs require Native or PrimeLAN")

    async def update_scenarios(self) -> MappingProxyType[int, UnifiedScenario]:
        if self._primelan is None:
            raise InimPrimeFeatureNotSupportedError("Scenarios require PrimeLAN")
        return await self._primelan.update_scenarios()

    async def update_gsm(self) -> UnifiedGSM:
        if self._primelan is None:
            raise InimPrimeFeatureNotSupportedError("GSM status requires PrimeLAN")
        return await self._primelan.update_gsm()

    async def update_system_faults(self) -> UnifiedSystemFaults:
        if self._primelan is None:
            raise InimPrimeFeatureNotSupportedError("System faults require PrimeLAN")
        return await self._primelan.update_system_faults()


    async def get_log_events(self, limit: int) -> list[UnifiedLogEvent]:
        if self._primelan is None:
            raise InimPrimeFeatureNotSupportedError("Log events require PrimeLAN")
        return await self._primelan.get_log_events(limit)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    async def set_zone_bypass(
            self,
            zone_id: int,
            bypass: bool
    ) -> None:
        await self._preferred.set_zone_bypass(zone_id, bypass)

    async def set_all_zone_bypasses(
            self,
            bypass: bool,
    ) -> None:
        await self._preferred.set_all_zone_bypasses(bypass)



    async def set_partition_arming_statuses(
            self,
            arming_statuses: dict[int, UnifiedArmingStatus],
    ) -> None:
        await self._preferred.set_partition_arming_statuses(arming_statuses)

    async def set_partition_arming_status(
            self,
            partition_id: int,
            arming_status: UnifiedArmingStatus
    ) -> None:
        await self._preferred.set_partition_arming_status(partition_id, arming_status)

    async def set_all_partitions_arming_status(
            self,
            arming_status: UnifiedArmingStatus,
    ) -> None:
        await self._preferred.set_all_partitions_arming_status(arming_status)



    async def reset_partition_memories(
            self,
            partition_ids: set[int],
    ) -> None:
        await self._preferred.reset_partition_memories(partition_ids)

    async def reset_partition_memory(
            self,
            partition_id: int,
    ) -> None:
        await self._preferred.reset_partition_memory(partition_id)

    async def reset_all_partition_memories(self) -> None:
        await self._preferred.reset_all_partition_memories()



    async def set_output(
            self,
            output_id: int,
            state: bool,
            level: int | None = None,
    ) -> None:
        # Output ids come from whichever backend reports them (see `outputs`),
        # so commands must go to that same backend.
        if self._native is not None:
            await self._native.set_output(output_id, state)
            return
        if self._primelan is not None:
            await self._primelan.set_output(output_id, state, level)
            return
        raise InimPrimeFeatureNotSupportedError("Output set requires Native or PrimeLAN")

    async def activate_scenario(self, scenario_id: int) -> None:
        if self._primelan is None:
            raise InimPrimeFeatureNotSupportedError("Scenarios require PrimeLAN")
        await self._primelan.activate_scenario(scenario_id)
