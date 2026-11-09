from .runtime_data import InimPrimeConfigEntry
from .models.system_faults import UNIFIED_EXPOSED_SYSTEM_FAULTS

from .entities.panel import SystemFaultBinarySensor, BackendConnectivityBinarySensor
from .entities.partition import PartitionAlarmMemoryBinarySensor
from .entities.timer import TimerActiveBinarySensor
from .entities.zone import ZoneStateBinarySensor, ZoneAlarmMemoryBinarySensor


def _first(*coordinators):
    """First non-None coordinator (a representative poller of a backend)."""
    return next((c for c in coordinators if c is not None), None)


async def async_setup_entry(hass, entry: InimPrimeConfigEntry, async_add_entities) -> None:
    """Set up INIM Prime binary sensors from a config entry."""
    zones_coordinator = entry.runtime_data.coordinators.zones
    partitions_coordinator = entry.runtime_data.coordinators.partitions
    system_faults_coordinator = entry.runtime_data.coordinators.system_faults

    entities = []

    if zones_coordinator is not None:
        for zone in zones_coordinator.gateway.zones.values():
            entities.append(ZoneStateBinarySensor(zones_coordinator, zone))
            entities.append(ZoneAlarmMemoryBinarySensor(zones_coordinator, zone))

    if partitions_coordinator is not None:
        for partition in partitions_coordinator.gateway.partitions.values():
            entities.append(PartitionAlarmMemoryBinarySensor(partitions_coordinator, partition))

    if system_faults_coordinator is not None:
        for exposedSystemFault in UNIFIED_EXPOSED_SYSTEM_FAULTS:
            entities.append(
                SystemFaultBinarySensor(system_faults_coordinator, exposedSystemFault)
            )

    timers_coordinator = entry.runtime_data.coordinators.timers
    if timers_coordinator is not None and timers_coordinator.gateway.timers:
        for timer in timers_coordinator.gateway.timers.values():
            entities.append(TimerActiveBinarySensor(timers_coordinator, timer))

    # One connectivity sensor per configured backend: two when both are set up,
    # otherwise just the one in use. Each tracks a representative coordinator of
    # that backend (zones/outputs/... are Native when Native is configured;
    # faults/scenarios/GSM are PrimeLAN-only).
    serial_number = entry.data["serial_number"]
    coordinators = entry.runtime_data.coordinators
    if entry.data.get("native"):
        native_coordinator = _first(
            coordinators.zones, coordinators.partitions, coordinators.outputs,
            coordinators.timers, coordinators.thermostats,
        )
        if native_coordinator is not None:
            entities.append(BackendConnectivityBinarySensor(
                native_coordinator, serial_number, "Connessione Native", "connectivity_native",
            ))
    if entry.data.get("primelan"):
        # Scenarios block setup (fresh at startup); faults/GSM are deferred, so
        # prefer scenarios as the PrimeLAN health signal.
        primelan_coordinator = _first(
            coordinators.scenarios, coordinators.system_faults,
            coordinators.gsm, coordinators.panel_log_events,
        )
        if primelan_coordinator is not None:
            entities.append(BackendConnectivityBinarySensor(
                primelan_coordinator, serial_number, "Connessione PrimeLAN", "connectivity_primelan",
            ))

    async_add_entities(entities)  # coordinators already refreshed before setup
