from __future__ import annotations
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import OptionsFlowWithReload

from .const import OptionsKey
from .models import ConfigOptions, clean_alarm_panel
from ..entry_data import get_entry_options, get_entry_data
from ..cover import discover_shutters
from .schemas import build_options_schema


def _parse_shutter_section(section: dict | None) -> dict:
    """Turn the form section {"01_open": "998", "01_close": "997", ...} into
    {"1": {"open": 998, "close": 997}} (only the filled commands)."""
    result: dict[str, dict] = {}
    for key, value in (section or {}).items():
        if value in (None, "") or "_" not in key:
            continue
        module_str, _, kind = key.partition("_")
        try:
            module = str(int(module_str))
        except (TypeError, ValueError):
            continue
        if kind == "name":
            result.setdefault(module, {})["name"] = str(value).strip()
        elif kind in ("open", "close", "stop"):
            try:
                result.setdefault(module, {})[kind] = int(value)
            except (TypeError, ValueError):
                continue
    return result


class InimPrimeOptionsFlowHandler(OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            data = dict(user_input)
            if "alarm_panel" in data:
                alarm_panel = clean_alarm_panel(data.pop("alarm_panel"))
                if alarm_panel:
                    data["alarm_panel"] = alarm_panel
            shutter_commands = _parse_shutter_section(data.pop(OptionsKey.SHUTTERS, None))
            if shutter_commands:
                data[OptionsKey.SHUTTER_COMMANDS] = shutter_commands
            return self.async_create_entry(title = "", data = data)

        data = get_entry_data(self.config_entry)
        options = get_entry_options(self.config_entry)
        config_options = ConfigOptions(
            zones_scan_interval = options["scan_intervals"].get("zones"),
            partitions_scan_interval = options["scan_intervals"].get("partitions"),
            gsm_scan_interval = options["scan_intervals"].get("gsm"),
            system_faults_scan_interval = options["scan_intervals"].get("system_faults"),
            panel_log_events_scan_interval = options["scan_intervals"].get("panel_log_events"),
            outputs_scan_interval = options["scan_intervals"].get("outputs"),
            scenarios_scan_interval = options["scan_intervals"].get("scenarios"),
            panel_log_events_fetch_limit = options.get("panel_log_events_fetch_limit"),
            alarm_panel = options.get("alarm_panel"),
            show_hidden_outputs = options.get("show_hidden_outputs"),
            hide_unused_partitions = options.get("hide_unused_partitions"),
            shutter_commands = options.get("shutter_commands"),
        )

        has_native = bool(data.get("native"))
        has_primelan = bool(data.get("primelan"))

        shutters, output_choices = self._shutter_context()

        schema = vol.Schema(
            build_options_schema(
                has_native = has_native,
                has_primelan = has_primelan,
                current_options = config_options,
                shutters = shutters,
                output_choices = output_choices,
            )
        )
        return self.async_show_form(step_id = "init", data_schema = schema)

    def _shutter_context(self) -> tuple[list[int], list[tuple[int, str]]]:
        """Discovered shutter modules and the current outputs to choose from."""
        runtime = getattr(self.config_entry, "runtime_data", None)
        gateway = getattr(runtime, "gateway", None) if runtime else None
        outputs = dict(getattr(gateway, "outputs", None) or {}) if gateway else {}
        if not outputs:
            return [], []
        shutters = sorted(discover_shutters(outputs))
        choices = sorted(
            ((oid, (o.label or f"Output {oid}")) for oid, o in outputs.items()),
            key = lambda item: item[1].lower(),
        )
        return shutters, choices
