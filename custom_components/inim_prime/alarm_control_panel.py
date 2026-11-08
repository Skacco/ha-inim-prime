from .entities.alarm_panel import ScenarioAlarmPanel
from .runtime_data import InimPrimeConfigEntry


async def async_setup_entry(hass, entry: InimPrimeConfigEntry, async_add_entities):
    coordinators = entry.runtime_data.coordinators
    commands = dict(entry.options.get("alarm_panel") or {})

    # Needs scenarios (PrimeLAN) and at least a disarm scenario.
    if coordinators.scenarios is None or coordinators.partitions is None or "disarm" not in commands:
        return

    async_add_entities([
        ScenarioAlarmPanel(coordinators.scenarios, coordinators.partitions, commands)
    ])
