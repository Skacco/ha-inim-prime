from __future__ import annotations
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import OptionsFlowWithReload

from .models import ConfigOptions, clean_alarm_panel
from ..entry_data import get_entry_options, get_entry_data
from .schemas import build_options_schema

class InimPrimeOptionsFlowHandler(OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            data = dict(user_input)
            if "alarm_panel" in data:
                alarm_panel = clean_alarm_panel(data.pop("alarm_panel"))
                if alarm_panel:
                    data["alarm_panel"] = alarm_panel
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
        )


        has_native = bool(data.get("native"))
        has_primelan = bool(data.get("primelan"))

        schema = vol.Schema(
            build_options_schema(
                has_native = has_native,
                has_primelan = has_primelan,
                current_options = config_options,
            )
        )
        return self.async_show_form(step_id = "init", data_schema = schema)