from .entities.zone import ZoneBypassSwitch
from .entities.output import OutputSwitch
from .runtime_data import InimPrimeConfigEntry

async def async_setup_entry(hass, entry: InimPrimeConfigEntry, async_add_entities):

    zones_coordinator = entry.runtime_data.coordinators.zones
    outputs_coordinator = entry.runtime_data.coordinators.outputs

    entities = []

    if zones_coordinator is not None:
        for zone in zones_coordinator.gateway.zones.values():
            entities.append(ZoneBypassSwitch(zones_coordinator, zone))

    if outputs_coordinator is not None:
        for output in (outputs_coordinator.gateway.outputs or {}).values():
            if not output.dimmable:
                entities.append(OutputSwitch(outputs_coordinator, output))

    async_add_entities(entities, update_before_add = True)
