from __future__ import annotations
from dataclasses import dataclass
from typing import TypedDict

from .const import OptionsKey

from ..entry_data import (
    InimPrimeConfigData,
    NativeConfigData,
    PrimelanConfigData,
    InimPrimeOptionsData,
    ScanIntervalsData,
)


@dataclass
class NativeConfig:
    port: int
    password: str
    use_outer_frame: bool
    pin: str | None

    def to_dict(self) -> NativeConfigData:
        return {
            "port": self.port,
            "password": self.password,
            "use_outer_frame": self.use_outer_frame,
            "pin": self.pin,
        }


@dataclass
class PrimelanConfig:
    api_key: str
    use_https: bool

    def to_dict(self) -> PrimelanConfigData:
        return {"api_key": self.api_key, "use_https": self.use_https}


@dataclass
class ConfigData:
    serial_number: str | None = None
    host: str | None = None
    use_native: bool = False
    use_primelan: bool = False
    native: NativeConfig | None = None
    primelan: PrimelanConfig | None = None

    def to_entry_data(self) -> InimPrimeConfigData:
        assert self.serial_number is not None
        assert self.host is not None
        data: InimPrimeConfigData = {
            "serial_number": self.serial_number,
            "host": self.host,
        }
        if self.native is not None:
            data["native"] = self.native.to_dict()
        if self.primelan is not None:
            data["primelan"] = self.primelan.to_dict()
        return data


@dataclass
class ConfigOptions:
    zones_scan_interval: int | None = None
    partitions_scan_interval: int | None = None
    gsm_scan_interval: int | None = None
    system_faults_scan_interval: int | None = None
    panel_log_events_scan_interval: int | None = None
    outputs_scan_interval: int | None = None
    scenarios_scan_interval: int | None = None
    panel_log_events_fetch_limit: int | None = None
    alarm_panel: dict[str, int] | None = None
    alarm_panel_type: str | None = None
    alarm_panel_native: dict | None = None
    show_hidden_outputs: bool | None = None
    hide_unused_partitions: bool | None = None
    shutter_commands: dict | None = None

    def to_options_dict(self) -> InimPrimeOptionsData:
        assert self.zones_scan_interval is not None
        assert self.partitions_scan_interval is not None

        scan_intervals: ScanIntervalsData = {
            "zones": self.zones_scan_interval,
            "partitions": self.partitions_scan_interval,
        }
        if self.gsm_scan_interval is not None:
            scan_intervals["gsm"] = self.gsm_scan_interval
        if self.system_faults_scan_interval is not None:
            scan_intervals["system_faults"] = self.system_faults_scan_interval
        if self.panel_log_events_scan_interval is not None:
            scan_intervals["panel_log_events"] = self.panel_log_events_scan_interval
        if self.outputs_scan_interval is not None:
            scan_intervals["outputs"] = self.outputs_scan_interval
        if self.scenarios_scan_interval is not None:
            scan_intervals["scenarios"] = self.scenarios_scan_interval

        options: InimPrimeOptionsData = {"scan_intervals": scan_intervals}
        if self.panel_log_events_fetch_limit is not None:
            options["panel_log_events_fetch_limit"] = self.panel_log_events_fetch_limit
        if self.alarm_panel:
            options["alarm_panel"] = self.alarm_panel
        if self.alarm_panel_type:
            options["alarm_panel_type"] = self.alarm_panel_type
        if self.alarm_panel_native:
            options["alarm_panel_native"] = self.alarm_panel_native
        if self.show_hidden_outputs is not None:
            options["show_hidden_outputs"] = self.show_hidden_outputs
        if self.hide_unused_partitions is not None:
            options["hide_unused_partitions"] = self.hide_unused_partitions
        if self.shutter_commands:
            options["shutter_commands"] = self.shutter_commands
        return options

class ScanIntervalsDefault(TypedDict):
    zones: int
    partitions: int
    gsm: int
    system_faults: int
    panel_log_events: int
    outputs: int
    scenarios: int

def clean_alarm_panel(raw: dict | None) -> dict[str, int]:
    """Keep only the commands that have a scenario id set."""
    if not raw:
        return {}
    return {key: int(value) for key, value in raw.items() if value is not None and value != ""}

def extract_hybrid_sections(
    data: dict | None,
) -> tuple[dict[str, int], dict[str, dict[str, list[int]]]]:
    """Split the per-command form sections into (scenario_map, native_map).

    Each command is its own form section (keyed by the command, e.g. 'arm_away').
    With both backends a section also has a 'scenario' field: when it is set the
    command is scenario-driven and its partition fields are ignored; otherwise it
    is partition-driven. Returns ({command: scenario_id}, {command: {mode|outputs:
    [ids]}}). The matching keys are popped from ``data`` so they are not stored as
    stray top-level options."""
    if not data:
        return {}, {}
    scenario_map: dict[str, int] = {}
    raw_native: dict[str, dict] = {}
    for command in OptionsKey.AlarmPanel.NATIVE_OUTPUT_COMMANDS:
        if command not in data:
            continue
        section = data.pop(command)
        if not isinstance(section, dict):
            continue
        scenario = section.pop(OptionsKey.AlarmPanel.SCENARIO_FIELD, None)
        if scenario is not None and str(scenario).strip() != "":
            # Scenario-driven: ignore any partition fields for this command.
            scenario_map[command] = int(scenario)
        else:
            raw_native[command] = section
    return scenario_map, clean_native_panel(raw_native)

def clean_native_panel(raw: dict | None) -> dict[str, dict[str, list[int]]]:
    """Normalise the Native-panel mapping {command: {mode: [partition_ids]}}.

    Drops empty modes and empty commands, coerces ids to int."""
    if not raw:
        return {}
    result: dict[str, dict[str, list[int]]] = {}
    for command, per_mode in raw.items():
        if not isinstance(per_mode, dict):
            continue
        modes: dict[str, list[int]] = {}
        for mode, value in per_mode.items():
            ids = [int(v) for v in (value or []) if str(v).strip() != ""]
            if ids:
                modes[mode] = ids
        if modes:
            result[command] = modes
    return result
