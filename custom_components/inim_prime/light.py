from .entities.output import OutputLight
from .runtime_data import InimPrimeConfigEntry


async def async_setup_entry(hass, entry: InimPrimeConfigEntry, async_add_entities):
    outputs_coordinator = entry.runtime_data.coordinators.outputs

    entities = []

    if outputs_coordinator is not None:
        for output in (outputs_coordinator.gateway.outputs or {}).values():
            if output.dimmable:
                entities.append(OutputLight(outputs_coordinator, output))

    async_add_entities(entities)
