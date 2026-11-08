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
    SCAN_INTERVALS: Final = "scan_intervals"
    ALARM_PANEL: Final = "alarm_panel"

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

    class ScanIntervals:
        ZONES: Final[str] = "zones"
        PARTITIONS: Final[str] = "partitions"
        GSM: Final[str] = "gsm"
        SYSTEM_FAULTS: Final[str] = "system_faults"
        PANEL_LOG_EVENTS: Final[str] = "panel_log_events"
        OUTPUTS: Final[str] = "outputs"
        SCENARIOS: Final[str] = "scenarios"


