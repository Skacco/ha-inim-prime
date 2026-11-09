from .entities.gsm import GSMSupplyVoltageSensor, GSMOperatorSensor, GSMSignalStrengthSensor, GSMCreditSensor
from .entities.panel import PanelSupplyVoltageSensor, BypassedZonesCountSensor, ZonesAlarmMemoryCountSensor, \
    PartitionsAlarmMemoryCountSensor, BackendLastUpdateSensor
from .entities.partition import PartitionStateSensor
from .entities.zone import ZoneStateSensor
from .entities.scenario import ActiveScenarioSensor
from .entities.thermostat import ThermostatTemperatureSensor
from .runtime_data import InimPrimeConfigEntry


def _first(*coordinators):
    """First non-None coordinator (a representative poller of a backend)."""
    return next((c for c in coordinators if c is not None), None)


async def async_setup_entry(hass, entry: InimPrimeConfigEntry, async_add_entities):
    zones_coordinator = entry.runtime_data.coordinators.zones
    partitions_coordinator = entry.runtime_data.coordinators.partitions
    gsm_coordinator = entry.runtime_data.coordinators.gsm
    system_faults_coordinator = entry.runtime_data.coordinators.system_faults
    scenarios_coordinator = entry.runtime_data.coordinators.scenarios

    entities = []

    if zones_coordinator is not None:
        for zone in zones_coordinator.gateway.zones.values():
            entities.append(ZoneStateSensor(zones_coordinator, zone))

        entities.append(BypassedZonesCountSensor(zones_coordinator))
        entities.append(ZonesAlarmMemoryCountSensor(zones_coordinator))

    if partitions_coordinator is not None:
        for partition in partitions_coordinator.gateway.partitions.values():
            entities.append(PartitionStateSensor(partitions_coordinator, partition))

        entities.append(PartitionsAlarmMemoryCountSensor(partitions_coordinator))

    if system_faults_coordinator is not None:
        entities.append(PanelSupplyVoltageSensor(system_faults_coordinator))

    if gsm_coordinator is not None:
        entities.append(GSMSupplyVoltageSensor(gsm_coordinator))
        entities.append(GSMOperatorSensor(gsm_coordinator))
        entities.append(GSMSignalStrengthSensor(gsm_coordinator))
        entities.append(GSMCreditSensor(gsm_coordinator))

    if scenarios_coordinator is not None:
        entities.append(ActiveScenarioSensor(scenarios_coordinator))

    thermostats_coordinator = entry.runtime_data.coordinators.thermostats
    if thermostats_coordinator is not None:
        for thermostat in thermostats_coordinator.gateway.thermostats.values():
            entities.append(ThermostatTemperatureSensor(
                thermostats_coordinator, thermostat.index, thermostat.name,
            ))

    # "Last update" timestamp per configured backend (companion of the
    # connectivity binary sensors): two when both backends are set up, one
    # otherwise.
    serial_number = entry.data["serial_number"]
    coordinators = entry.runtime_data.coordinators
    if entry.data.get("native"):
        native_coordinator = _first(
            coordinators.zones, coordinators.partitions, coordinators.outputs,
            coordinators.timers, coordinators.thermostats,
        )
        if native_coordinator is not None:
            entities.append(BackendLastUpdateSensor(
                native_coordinator, serial_number, "Ultimo aggiornamento Native", "last_update_native",
            ))
    if entry.data.get("primelan"):
        primelan_coordinator = _first(
            coordinators.system_faults, coordinators.scenarios,
            coordinators.gsm, coordinators.panel_log_events,
        )
        if primelan_coordinator is not None:
            entities.append(BackendLastUpdateSensor(
                primelan_coordinator, serial_number, "Ultimo aggiornamento PrimeLAN", "last_update_primelan",
            ))

    async_add_entities(entities)  # coordinators already refreshed before setup
