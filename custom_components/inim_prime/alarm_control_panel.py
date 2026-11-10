from .config_flow.const import OptionsKey
from .entities.alarm_panel import ScenarioAlarmPanel, PartitionAlarmPanel
from .runtime_data import InimPrimeConfigEntry


def _native_arm_map(options) -> dict[str, frozenset[int]]:
    """Read the Native panel's per-command partition sets from the options."""
    raw = options.get(OptionsKey.ALARM_PANEL_NATIVE) or {}
    result: dict[str, frozenset[int]] = {}
    for command in OptionsKey.AlarmPanel.NATIVE_ARMS:
        pids = raw.get(command) or []
        ids = frozenset(
            int(pid) for pid in pids if str(pid).strip() != ""
        )
        if ids:
            result[command] = ids
    return result


async def async_setup_entry(hass, entry: InimPrimeConfigEntry, async_add_entities):
    coordinators = entry.runtime_data.coordinators
    options = entry.options
    has_native = bool(entry.data.get("native"))
    has_primelan = bool(entry.data.get("primelan"))

    # Which panel to build:
    #   - both backends: the user chooses (default: scenarios);
    #   - Native only: partitions (scenarios aren't available);
    #   - PrimeLAN only: scenarios.
    if has_native and has_primelan:
        panel_type = options.get(OptionsKey.ALARM_PANEL_TYPE, OptionsKey.PanelType.SCENARI)
    elif has_native:
        panel_type = OptionsKey.PanelType.PARTIZIONI
    else:
        panel_type = OptionsKey.PanelType.SCENARI

    if panel_type == OptionsKey.PanelType.PARTIZIONI:
        # Native panel: needs the partitions. Arm commands are offered only for
        # the states with a partition set; disarm and state always work.
        if coordinators.partitions is None:
            return
        async_add_entities([
            PartitionAlarmPanel(coordinators.partitions, _native_arm_map(options))
        ])
        return

    # Scenario panel (PrimeLAN): needs scenarios + at least a disarm scenario.
    commands = dict(options.get(OptionsKey.ALARM_PANEL) or {})
    if coordinators.scenarios is None or coordinators.partitions is None or "disarm" not in commands:
        return

    async_add_entities([
        ScenarioAlarmPanel(coordinators.scenarios, coordinators.partitions, commands)
    ])
