from typing import Final

import inim.prime.native.const

DOMAIN: Final[str] = "inim_prime"


PANEL_LOG_EVENTS_FETCH_LIMIT_DEFAULT: Final[int] = 30
PANEL_LOG_EVENTS_FETCH_LIMIT_TRIGGER: Final[int] = 3
PANEL_LOG_EVENTS_FETCH_LIMIT_MIN: Final[int] = 10
PANEL_LOG_EVENTS_FETCH_LIMIT_MAX: Final[int] = 100

NATIVE_RECONNECT_MIN_INTERVAL = 5.0 # seconds

# Native defaults
NATIVE_PORT_DEFAULT: Final[int] = inim.prime.native.const.Panel.DEFAULT_PORT
NATIVE_USE_OUTER_FRAME_DEFAULT: Final[bool] = False
NATIVE_PASSWORD_DEFAULT: Final[str] = inim.prime.native.const.Panel.DEFAULT_PASSWORD
NATIVE_USE_CUSTOM_PIN_DEFAULT: Final[bool] = False
NATIVE_PIN_MAX_CHARACTERS: Final[int] = inim.prime.native.const.Panel.MAX_PIN_CHARACTERS

# Primelan defaults
PRIMELAN_USE_HTTPS_DEFAULT: Final[bool] = True

# --- Defaults (custom per coordinator) ---
ZONES_SCAN_INTERVAL_NATIVE_DEFAULT: Final[int] = 500
PARTITIONS_SCAN_INTERVAL_NATIVE_DEFAULT: Final[int] = 500

ZONES_SCAN_INTERVAL_PRIMELAN_DEFAULT: Final[int] = 5000
PARTITIONS_SCAN_INTERVAL_PRIMELAN_DEFAULT: Final[int] = 5000
GSM_SCAN_INTERVAL_PRIMELAN_DEFAULT: Final[int] = 30000
SYSTEM_FAULTS_SCAN_INTERVAL_PRIMELAN_DEFAULT: Final[int] = 15000
PANEL_LOG_EVENTS_SCAN_INTERVAL_PRIMELAN_DEFAULT: Final[int] = 15000

SCAN_INTERVAL_MIN: Final[int] = 1
SCAN_INTERVAL_MAX: Final[int] = 300000

STORAGE_KEY_LAST_PANEL_EVENT_LOGS = "last_panel_event_logs"

INIM_PRIME_DEVICE_MANUFACTURER = "Inim"
INIM_PRIME_MODEL_ZONE = "Prime Zone"

# Outputs / scenarios
OUTPUTS_SCAN_INTERVAL_PRIMELAN_DEFAULT: Final[int] = 30000
OUTPUTS_SCAN_INTERVAL_NATIVE_DEFAULT: Final[int] = 500
SCENARIOS_SCAN_INTERVAL_PRIMELAN_DEFAULT: Final[int] = 10000
# When BOTH backends are configured, the live data comes from Native, so the
# PrimeLAN-only pollers (GSM, system faults, event log) can run much slower by
# default to cut PrimeLAN traffic. Scenarios stay faster (they drive the alarm
# panel). The user can still override any of these in the options.
PRIMELAN_SCAN_INTERVAL_BOTH_DEFAULT: Final[int] = 60000
TIMERS_SCAN_INTERVAL_DEFAULT: Final[int] = 10000

# Roller shutters ("tapparelle"): Smart modules driven via their motor relays
# (terminal T04 = open, T03 = close). Full-travel times are the panel's own
# programmed values; HA estimates position from them. The extra margin is held
# at the end of a full open/close to recalibrate to 0 % / 100 %.
# Length of the trigger pulse sent to a shutter command output; the Smart
# module runs the full travel on its own from that pulse.
SHUTTER_PULSE_MS: Final[int] = 500

# Domotic-module description table (native memory): one 16-byte label per
# module, module 1 first. Used to auto-name the shutters ("TENDA SALA", ...).
# Found by decrypting a Prime/STUDIO "Moduli domotici" read.
MODULE_LABELS_ADDRESS: Final[int] = 335799424
MODULE_LABEL_SIZE: Final[int] = 16
MODULE_LABELS_COUNT: Final[int] = 30

# Thermostats (temperature sensors). The status command returns the live
# temperatures; the names come from two contiguous label tables (keypads then
# sensors). Addresses/command found by decrypting a Prime/STUDIO capture.
THERMOSTAT_STATUS_OPERATION: Final[int] = 0x2B
THERMOSTAT_STATUS_LENGTH: Final[int] = 122
KEYPAD_LABELS_ADDRESS: Final[int] = 335801888
KEYPAD_LABELS_COUNT: Final[int] = 30
SENSOR_LABELS_ADDRESS: Final[int] = 335802368
SENSOR_LABELS_COUNT: Final[int] = 15
THERMOSTATS_SCAN_INTERVAL_DEFAULT: Final[int] = 30000

# Per-shutter option field labels (shown as the field names in the options form,
# and stored as the keys of the shutter command mapping).
SHUTTER_FIELD_OPEN = "Aperto"
SHUTTER_FIELD_CLOSE = "Chiuso"
# Partial-open preset field label -> position percent.
SHUTTER_PRESET_FIELDS = {"1/4": 25, "1/2": 50, "3/4": 75}
SHUTTER_COMMAND_FIELDS = (SHUTTER_FIELD_OPEN, SHUTTER_FIELD_CLOSE, *SHUTTER_PRESET_FIELDS)
