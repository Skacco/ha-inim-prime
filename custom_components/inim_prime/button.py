from .entities.panel import DisableAllZoneBypassesButton, ResetAllPartitionMemoriesButton
from .entities.partition import ResetPartitionMemoryButton
from .entities.scenario import ScenarioButton
from .runtime_data import InimPrimeConfigEntry


async def async_setup_entry(hass, entry: InimPrimeConfigEntry, async_add_entities):

    partitions_coordinator = entry.runtime_data.coordinators.partitions
    zones_coordinator = entry.runtime_data.coordinators.zones
    scenarios_coordinator = entry.runtime_data.coordinators.scenarios

    entities = []

    if partitions_coordinator is not None:
        for partition in partitions_coordinator.gateway.partitions.values():
            entities.append(ResetPartitionMemoryButton(partitions_coordinator, partition))

        entities.append(ResetAllPartitionMemoriesButton(partitions_coordinator))

    if zones_coordinator is not None:
        entities.append(DisableAllZoneBypassesButton(zones_coordinator))

    if scenarios_coordinator is not None:
        for scenario in (scenarios_coordinator.gateway.scenarios or {}).values():
            entities.append(ScenarioButton(scenarios_coordinator, scenario))

    async_add_entities(entities)  # coordinators already refreshed before setup