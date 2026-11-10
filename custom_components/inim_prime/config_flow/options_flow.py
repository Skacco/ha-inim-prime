from __future__ import annotations
import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import OptionsFlowWithReload

from .const import OptionsKey
from ..const import SHUTTER_COMMAND_FIELDS
from .models import ConfigOptions, clean_alarm_panel, clean_native_panel
from ..entry_data import get_entry_options, get_entry_data
from ..cover import discover_shutters, _SHUTTER_LABEL
from .schemas import build_options_schema, shutter_section_key

# A shutter section key looks like "01 · TENDA SALA": the leading number is the
# module, used to store the mapping (so renaming the module can't lose it).
_SECTION_KEY = re.compile(r"^(\d{1,3})\s*·")


class InimPrimeOptionsFlowHandler(OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            data = dict(user_input)
            if "alarm_panel" in data:
                alarm_panel = clean_alarm_panel(data.pop("alarm_panel"))
                if alarm_panel:
                    data["alarm_panel"] = alarm_panel
            if OptionsKey.ALARM_PANEL_NATIVE in data:
                native_panel = clean_native_panel(data.pop(OptionsKey.ALARM_PANEL_NATIVE))
                if native_panel:
                    data[OptionsKey.ALARM_PANEL_NATIVE] = native_panel
            shutter_commands = _extract_shutter_commands(data)  # pops the sections
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
            alarm_panel_type = options.get("alarm_panel_type"),
            alarm_panel_native = options.get("alarm_panel_native"),
            show_hidden_outputs = options.get("show_hidden_outputs"),
            hide_unused_partitions = options.get("hide_unused_partitions"),
            shutter_commands = options.get("shutter_commands"),
        )

        has_native = bool(data.get("native"))
        has_primelan = bool(data.get("primelan"))

        shutters, output_choices = self._shutter_context()
        partition_choices = self._partition_choices()
        module_names: dict[int, str] = {}
        if shutters:
            gateway = self.config_entry.runtime_data.gateway
            try:
                module_names = await gateway.module_labels(max(shutters))
            except Exception:  # noqa: BLE001 - names are cosmetic
                module_names = {}

        schema = vol.Schema(
            build_options_schema(
                has_native = has_native,
                has_primelan = has_primelan,
                current_options = config_options,
                shutters = shutters,
                output_choices = output_choices,
                module_names = module_names,
                partitions = partition_choices,
            )
        )
        return self.async_show_form(step_id = "init", data_schema = schema)

    def _partition_choices(self) -> list[tuple[int, str]]:
        """Partitions to offer in the Native alarm-panel mapping (id -> label)."""
        runtime = getattr(self.config_entry, "runtime_data", None)
        gateway = getattr(runtime, "gateway", None) if runtime else None
        partitions = dict(getattr(gateway, "partitions", None) or {}) if gateway else {}
        return sorted(
            ((pid, (p.label or f"Partizione {pid}")) for pid, p in partitions.items()),
            key = lambda item: item[0],
        )

    def _shutter_context(self) -> tuple[list[int], list[tuple[int, str]]]:
        """Discovered shutter modules and the current outputs to choose from."""
        runtime = getattr(self.config_entry, "runtime_data", None)
        gateway = getattr(runtime, "gateway", None) if runtime else None
        if gateway is None:
            return [], []
        # Discover modules from the full output set (the Mod.dom motor terminals
        # aren't on PrimeLAN), but offer only the normal outputs as the command
        # choices (the Apri/Chiudi virtual outputs are on PrimeLAN).
        all_outputs = dict(getattr(gateway, "all_outputs", None) or {})
        outputs = dict(getattr(gateway, "outputs", None) or {})
        if not all_outputs:
            return [], []
        shutters = sorted(discover_shutters(all_outputs))
        # Exclude the shutter modules' raw motor terminals (Mod.dom XX T03/T04)
        # from the command choices: they're only used to detect the shutters, and
        # driving them directly doesn't work (the Smart module reclaims the relay).
        # The command to pick is the virtual "Apri/Chiudi" output, not the motor.
        choices = sorted(
            (
                (oid, (o.label or f"Output {oid}"))
                for oid, o in outputs.items()
                if not _SHUTTER_LABEL.match((o.label or "").replace("\x00", "").strip())
            ),
            key = lambda item: item[1].lower(),
        )
        return shutters, choices


def _extract_shutter_commands(data: dict) -> dict:
    """Pull the per-shutter sections out of the submitted options and return
    {"<module>": {"open": id, "close": id}} (filled fields only)."""
    result: dict[str, dict[str, int]] = {}
    for key in list(data.keys()):
        match = _SECTION_KEY.match(key)
        if not match or not isinstance(data[key], dict):
            continue
        section = data.pop(key)
        module = str(int(match.group(1)))
        commands: dict[str, int] = {}
        for kind in SHUTTER_COMMAND_FIELDS:
            value = section.get(kind)
            if value in (None, ""):
                continue
            try:
                commands[kind] = int(value)
            except (TypeError, ValueError):
                continue
        if commands:
            result[module] = commands
    return result
