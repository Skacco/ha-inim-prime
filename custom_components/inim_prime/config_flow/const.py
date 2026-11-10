from typing import Final


class DataKey:
    SERIAL_NUMBER: Final = "serial_number"
    HOST: Final = "host"
    NATIVE: Final = "native"
    PRIMELAN: Final = "primelan"

    class Native:
        PORT: Final = "port"
        USE_OUTER_FRAME: Final = "use_outer_frame"
        PASSWORD: Final = "password"
        USE_CUSTOM_PIN: Final = "use_custom_pin"
        PIN: Final = "pin"

    class Primelan:
        API_KEY: Final = "api_key"
        USE_HTTPS: Final = "use_https"

class OptionsKey:
    PANEL_LOG_EVENTS_FETCH_LIMIT: Final = "panel_log_events_fetch_limit"
    SHOW_HIDDEN_OUTPUTS: Final = "show_hidden_outputs"
    HIDE_UNUSED_PARTITIONS: Final = "hide_unused_partitions"
    SCAN_INTERVALS: Final = "scan_intervals"
    ALARM_PANEL: Final = "alarm_panel"        # scenario mapping (PrimeLAN)
    ALARM_PANEL_TYPE: Final = "alarm_panel_type"    # "scenari" | "partizioni"
    ALARM_PANEL_NATIVE: Final = "alarm_panel_native"  # per-arm partition sets
    SHUTTERS: Final = "shutters"              # the options-form section
    SHUTTER_COMMANDS: Final = "shutter_commands"  # stored mapping

    class AlarmPanel:
        """Scenario id activated by each alarm panel command (PrimeLAN)."""
        DISARM: Final[str] = "disarm"
        ARM_AWAY: Final[str] = "arm_away"
        ARM_HOME: Final[str] = "arm_home"
        ARM_NIGHT: Final[str] = "arm_night"
        ARM_VACATION: Final[str] = "arm_vacation"
        ARM_CUSTOM_BYPASS: Final[str] = "arm_custom_bypass"

        ALL: Final[tuple[str, ...]] = (
            DISARM, ARM_AWAY, ARM_HOME, ARM_NIGHT, ARM_VACATION, ARM_CUSTOM_BYPASS,
        )
        # Arm commands offered by the Native (partition-based) panel: each maps
        # to per-mode sets of partitions (disarm = all partitions off).
        NATIVE_ARMS: Final[tuple[str, ...]] = (
            ARM_AWAY, ARM_HOME, ARM_NIGHT, ARM_VACATION,
        )
        # Arming modes a partition can be set to for a command. Not listed in any
        # mode -> that partition stays disarmed for that command.
        NATIVE_MODES: Final[tuple[str, ...]] = ("total", "stay", "instant")
        # Pseudo-mode holding the outputs a command also activates (the panel
        # self-releases monostable outputs). Disarm can trigger outputs too.
        NATIVE_OUTPUT: Final[str] = "outputs"
        NATIVE_OUTPUT_COMMANDS: Final[tuple[str, ...]] = (
            DISARM, ARM_AWAY, ARM_HOME, ARM_NIGHT, ARM_VACATION,
        )
        # Per-command field (hybrid, both backends): a scenario id. When set, the
        # command runs that scenario; when empty, it arms its partitions.
        SCENARIO_FIELD: Final[str] = "scenario"
        # Per-partition single-select value meaning "leave this partition off".
        NATIVE_MODE_OFF: Final[str] = "disarmed"

    class PanelType:
        SCENARI: Final[str] = "scenari"
        PARTIZIONI: Final[str] = "partizioni"

    class ScanIntervals:
        ZONES: Final[str] = "zones"
        PARTITIONS: Final[str] = "partitions"
        GSM: Final[str] = "gsm"
        SYSTEM_FAULTS: Final[str] = "system_faults"
        PANEL_LOG_EVENTS: Final[str] = "panel_log_events"
        OUTPUTS: Final[str] = "outputs"
        SCENARIOS: Final[str] = "scenarios"


