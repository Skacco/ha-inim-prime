"""Gateway that unifies the native and PrimeLAN backends behind a single API.

Coordinators and entities talk only to this gateway; they never import from
inim.prime.native or inim.prime.primelan directly. When both backends are
configured, native is preferred for everything it supports; PrimeLAN remains
the only source for GSM, system faults, log events, and output dimming until
native grows those features. Outputs come from native when it is configured
(faster), otherwise from PrimeLAN (which also reports dimming levels).
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import replace
from types import MappingProxyType

from .adapters.native_adapter import NativeAdapter
from .models.timers import UnifiedTimer
from .adapters.primelan_adapter import PrimelanAdapter

from .models.gsm import UnifiedGSM
from .models.log_events import UnifiedLogEvent
from .models.outputs import UnifiedOutput
from .models.scenarios import UnifiedScenario
from .models.partitions import UnifiedPartition, UnifiedArmingStatus
from .models.system_faults import UnifiedSystemFaults
from .models.zones import UnifiedZoneState, UnifiedZone

from .exceptions import InimPrimeFeatureNotSupportedError

# "Mod.dom 01 T01", "ModDomot 09 T02", ... -- a domotic module's internal
# terminals, reported as zones by the panel but not real alarm zones.
_MODULE_ZONE_LABEL = re.compile(r"(?i)^mod\.?dom(?:ot)?\s*\d+\s*t\d")


_DEFAULT_PARTITION_LABEL = re.compile(r"^AREA\s*\d+$", re.IGNORECASE)


_DEFAULT_TIMER_LABEL = re.compile(r"^TIMER\s*\d+$")


def _is_default_timer_label(label: str | None) -> bool:
    cleaned = (label or "").replace("\x00", "").strip()
    return not cleaned or bool(_DEFAULT_TIMER_LABEL.match(cleaned))


def _is_default_partition_label(label: str | None) -> bool:
    # The panel pads names with spaces and NUL bytes ("AREA       013 \x00").
    cleaned = (label or "").replace("\x00", "").strip()
    return bool(cleaned) and bool(_DEFAULT_PARTITION_LABEL.match(cleaned))


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
        # During setup the labels are loaded by load_primelan_output_labels()
        # at a chosen moment; update_outputs() must not fetch them meanwhile.
        self.defer_output_labels: bool = False
        # While an alarm command waits for its confirmation, routine polls
        # (zones, outputs, timers, thermostats) answer from their cache: the
        # native link serves one request at a time and would make it queue.
        self._poll_released = asyncio.Event()
        self._poll_released.set()
        self._poll_hold_timer: asyncio.TimerHandle | None = None
        # False (default) = show only the outputs PrimeLAN publishes to the
        # linked user (matched on terminal id), like the panel apps. True = also
        # show the panel's internal/mainboard outputs that aren't on PrimeLAN.
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
    def supports_timers(self) -> bool:
        return self._native is not None

    @property
    def timers(self) -> MappingProxyType[int, UnifiedTimer] | None:
        """Timers with a custom name (the default "TIMER 0xx" ones are unused)."""
        if self._native is None:
            return None
        return MappingProxyType({
            tid: t for tid, t in self._native.timers.items() if not _is_default_timer_label(t.label)
        })

    def hold_polling(self, seconds: float) -> None:
        """Pause routine polls until release_polling(), at most `seconds`."""
        if self._poll_hold_timer is not None:
            self._poll_hold_timer.cancel()
        self._poll_released.clear()
        self._poll_hold_timer = asyncio.get_running_loop().call_later(
            seconds, self.release_polling
        )

    def release_polling(self) -> None:
        if self._poll_hold_timer is not None:
            self._poll_hold_timer.cancel()
            self._poll_hold_timer = None
        self._poll_released.set()

    def _polling_held(self) -> bool:
        return not self._poll_released.is_set()

    async def _wait_polling_released(self) -> None:
        """Routine PrimeLAN polls wait here while a command is in progress.

        The panel answers PrimeLAN requests slowly when several run at once
        (measured: a command takes ~1s alone, 4-6s next to other requests).
        """
        await self._poll_released.wait()

    async def update_timers(self) -> MappingProxyType[int, UnifiedTimer]:
        if self._native is None:
            raise InimPrimeFeatureNotSupportedError("Timers require the native protocol")
        if self._polling_held() and self.timers:
            return self.timers
        await self._native.update_timers()
        return self.timers

    async def set_timer_enabled(self, timer_id: int, enabled: bool) -> None:
        if self._native is None:
            raise InimPrimeFeatureNotSupportedError("Timers require the native protocol")
        await self._native.set_timer_enabled(timer_id, enabled)

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
        # The Native TCP link and the PrimeLAN HTTPS session are independent, so
        # open them concurrently -- the slow PrimeLAN TLS handshake no longer
        # waits for (or adds to) the Native connect.
        tasks = []
        if self._native is not None:
            tasks.append(self._native.connect())
        if self._primelan is not None:
            tasks.append(self._primelan.connect())
        if tasks:
            await asyncio.gather(*tasks)

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
        zones = self._preferred.zones
        # The domotic modules' internal T01/T02 terminals show up as "zones"
        # (e.g. "Mod.dom 01 T01") but aren't real alarm zones -- hide them.
        filtered = {
            zid: z for zid, z in zones.items()
            if not _MODULE_ZONE_LABEL.match((z.label or "").replace("\x00", "").strip())
        }
        if len(filtered) == len(zones):
            return zones
        return MappingProxyType(filtered)

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
    def supports_thermostats(self) -> bool:
        return self._native is not None

    @property
    def thermostats(self) -> dict:
        return self._native.thermostats if self._native is not None else {}

    async def update_thermostats(self) -> dict:
        if self._native is None:
            return {}
        if self._polling_held() and self.thermostats:
            return self.thermostats
        return await self._native.update_thermostats()

    async def module_labels(self, count: int) -> dict[int, str]:
        """Domotic-module names from native memory ({module_number: name}).

        Used to auto-name shutters (module 1 -> 'TENDA SALA', ...). Returns {}
        when native isn't configured or the read fails (names are cosmetic).
        """
        if self._native is None:
            return {}
        # Names are cached after the first successful read: the read goes
        # through the single Native connection and would otherwise queue behind
        # the polling (slow options screen). A rename on the panel shows up
        # after the integration is reloaded.
        cached = getattr(self, "_module_labels_cache", None)
        if cached is not None and cached[0] >= count:
            return {k: v for k, v in cached[1].items() if k <= count}
        from .const import MODULE_LABELS_ADDRESS, MODULE_LABEL_SIZE
        try:
            data = await self._native.read_memory(
                MODULE_LABELS_ADDRESS, count * MODULE_LABEL_SIZE
            )
        except Exception:  # noqa: BLE001 - cosmetic; fall back to default names
            return {}
        labels: dict[int, str] = {}
        for index in range(count):
            raw = data[index * MODULE_LABEL_SIZE:(index + 1) * MODULE_LABEL_SIZE]
            name = raw.split(b"\x00", 1)[0].decode("latin-1").strip()
            if name:
                labels[index + 1] = name
        self._module_labels_cache = (count, labels)
        return labels

    @property
    def all_outputs(self) -> MappingProxyType[int, UnifiedOutput] | None:
        """Every output the backend exposes, named but NOT filtered by PrimeLAN
        membership. Shutter discovery needs this: the Mod.dom motor terminals
        (T03/T04) that identify a shutter module aren't published on PrimeLAN,
        so they'd be missing from `outputs` and no shutter would be found."""
        if self._native is not None:
            outputs = dict(self._native.outputs)
            labels = self._primelan_output_labels or {}
            # Name native outputs from PrimeLAN where the native label is empty
            # (matched on terminal id).
            if labels:
                outputs = {
                    oid: (replace(o, label = labels[oid]) if (not o.label and oid in labels) else o)
                    for oid, o in outputs.items()
                }
            return MappingProxyType(outputs)
        return self._primelan.outputs if self._primelan is not None else None

    @property
    def outputs(self) -> MappingProxyType[int, UnifiedOutput] | None:
        # Native is preferred for outputs (much faster polling); PrimeLAN is
        # used only when native isn't configured. Native has no dimming, so
        # dimmers are on/off when native is active.
        outputs = self.all_outputs
        if outputs is None:
            return None
        labels = self._primelan_output_labels or {}
        # Show only the outputs PrimeLAN publishes to the linked user (matched on
        # terminal id, which the two backends share). This drops the panel's
        # internal/mainboard outputs (SIRENA, STATO IMPIANTO, mainboard
        # garage/aux, ...) that aren't on PrimeLAN.
        if self._native is not None and labels and not self.show_hidden_outputs:
            outputs = MappingProxyType({oid: o for oid, o in outputs.items() if oid in labels})
        return outputs

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

    # State reported by a panel event (webhook): applied to the cache the
    # entities read, until the next poll replaces it.
    def apply_zone_state(self, zone_id: int, state: UnifiedZoneState) -> bool:
        return self._preferred.apply_zone_state(zone_id, state)

    def apply_partition_changes(self, partition_id: int, **changes) -> bool:
        return self._preferred.apply_partition_changes(partition_id, **changes)

    def apply_output_state(self, output_id: int, state: bool) -> bool:
        backend = self._native if self._native is not None else self._primelan
        return backend is not None and backend.apply_output_state(output_id, state)

    # ------------------------------------------------------------------
    # Refreshes -- do I/O, overwrite the cache, return it. Coordinators call
    # these on their own poll schedule via _async_update_data().
    # ------------------------------------------------------------------
    async def update_zones(self) -> MappingProxyType[int, UnifiedZone]:
        if self._polling_held() and self.zones:
            return self.zones
        return await self._preferred.update_zones()

    async def update_partitions(self) -> MappingProxyType[int, UnifiedPartition]:
        return await self._preferred.update_partitions()

    @property
    def uses_primelan_output_labels(self) -> bool:
        """Native outputs filtered/named from PrimeLAN (both backends)."""
        return self._native is not None and self._primelan is not None

    @property
    def primelan_output_labels(self) -> dict[int, str] | None:
        return self._primelan_output_labels

    def set_primelan_output_labels(self, labels: dict[int, str]) -> None:
        """Use labels read earlier (see the cache in async_setup_entry)."""
        self._primelan_output_labels = dict(labels)

    async def fetch_primelan_output_labels(self) -> dict[int, str]:
        """Read from PrimeLAN the outputs it publishes ({terminal id: name}).

        This single request takes several seconds on a real panel.
        """
        await self._primelan.update_outputs()
        # Keyed by terminal id (same key native uses), so the two backends
        # line up: PrimeLAN numbers outputs by its own id, native by terminal
        # id, and only the terminal matches.
        return dict(self._primelan.output_terminal_labels)

    async def load_primelan_output_labels(self) -> None:
        """Read once from PrimeLAN the outputs it publishes (filter + names)."""
        if not self.uses_primelan_output_labels or self._primelan_output_labels is not None:
            return
        try:
            self._primelan_output_labels = await self.fetch_primelan_output_labels()
        except Exception:  # noqa: BLE001 - labels are cosmetic, retry next time
            pass

    async def update_outputs(self) -> MappingProxyType[int, UnifiedOutput]:
        if self._polling_held() and self.outputs:
            return self.outputs
        if self._native is not None:
            await self._native.update_outputs()
            # PrimeLAN is read once: for the list of outputs enabled for its
            # user (filter) and as a fallback for names.
            if not self.defer_output_labels:
                await self.load_primelan_output_labels()
            return self.outputs
        if self._primelan is not None:
            return await self._primelan.update_outputs()
        raise InimPrimeFeatureNotSupportedError("Outputs require Native or PrimeLAN")

    async def update_scenarios(self, force: bool = False) -> MappingProxyType[int, UnifiedScenario]:
        """`force` is for the command in progress itself (it must not wait)."""
        if self._primelan is None:
            raise InimPrimeFeatureNotSupportedError("Scenarios require PrimeLAN")
        if not force:
            await self._wait_polling_released()
        return await self._primelan.update_scenarios()

    async def update_gsm(self) -> UnifiedGSM:
        if self._primelan is None:
            raise InimPrimeFeatureNotSupportedError("GSM status requires PrimeLAN")
        await self._wait_polling_released()
        return await self._primelan.update_gsm()

    async def update_system_faults(self) -> UnifiedSystemFaults:
        if self._primelan is None:
            raise InimPrimeFeatureNotSupportedError("System faults require PrimeLAN")
        await self._wait_polling_released()
        return await self._primelan.update_system_faults()


    async def get_log_events(self, limit: int) -> list[UnifiedLogEvent]:
        if self._primelan is None:
            raise InimPrimeFeatureNotSupportedError("Log events require PrimeLAN")
        await self._wait_polling_released()
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
