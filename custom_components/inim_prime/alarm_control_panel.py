from .config_flow.const import OptionsKey
from .entities.alarm_panel import ScenarioAlarmPanel, PartitionAlarmPanel
from .models.partitions import UnifiedArmingStatus
from .runtime_data import InimPrimeConfigEntry

# Native-panel mode key -> the partition arming status it applies.
_MODE_STATUS = {
    "total": UnifiedArmingStatus.ARM_AWAY,
    "stay": UnifiedArmingStatus.ARM_STAY,
    "instant": UnifiedArmingStatus.ARM_INSTANT,
}


def _native_arm_map(options) -> dict[str, dict[int, UnifiedArmingStatus]]:
    """Build each Native command's target: {partition_id: arming mode}.

    Options shape: {command: {mode: [partition_ids]}}. A later mode wins if a
    partition is listed under more than one mode for the same command.
    """
    raw = options.get(OptionsKey.ALARM_PANEL_NATIVE) or {}
    result: dict[str, dict[int, UnifiedArmingStatus]] = {}
    for command in OptionsKey.AlarmPanel.NATIVE_ARMS:
        per_mode = raw.get(command) or {}
        target: dict[int, UnifiedArmingStatus] = {}
        for mode in OptionsKey.AlarmPanel.NATIVE_MODES:
            status = _MODE_STATUS[mode]
            for pid in per_mode.get(mode) or []:
                if str(pid).strip() != "":
                    target[int(pid)] = status
        if target:
            result[command] = target
    return result


def _native_output_map(options) -> dict[str, list[int]]:
    """Outputs each Native command (disarm included) also switches on."""
    raw = options.get(OptionsKey.ALARM_PANEL_NATIVE) or {}
    result: dict[str, list[int]] = {}
    for command in OptionsKey.AlarmPanel.NATIVE_OUTPUT_COMMANDS:
        outputs = (raw.get(command) or {}).get(OptionsKey.AlarmPanel.NATIVE_OUTPUT) or []
        ids = [int(o) for o in outputs if str(o).strip() != ""]
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
            PartitionAlarmPanel(
                coordinators.partitions,
                _native_arm_map(options),
                _native_output_map(options),
            )
        ])
        return

    # Scenario panel (PrimeLAN): needs scenarios + at least a disarm scenario.
    commands = dict(options.get(OptionsKey.ALARM_PANEL) or {})
    if coordinators.scenarios is None or coordinators.partitions is None or "disarm" not in commands:
        return

    async_add_entities([
        ScenarioAlarmPanel(coordinators.scenarios, coordinators.partitions, commands)
    ])
