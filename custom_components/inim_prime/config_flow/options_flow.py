from __future__ import annotations
import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import OptionsFlowWithReload

from .const import OptionsKey
from ..const import SHUTTER_COMMAND_FIELDS
from .models import ConfigOptions, extract_command_partitions
from ..entry_data import get_entry_options, get_entry_data
from ..cover import discover_shutters, _SHUTTER_LABEL
from .schemas import (
    build_options_schema,
    build_alarm_method_schema,
    build_alarm_scenario_schema,
    build_alarm_partitions_schema,
    shutter_section_key,
)

# A shutter section key looks like "01 · TENDA SALA": the leading number is the
# module, used to store the mapping (so renaming the module can't lose it).
_SECTION_KEY = re.compile(r"^(\d{1,3})\s*·")

# HA arm commands offered in the alarm menu, with their display label (used as a
# step-title placeholder) and the menu order.
_CMD_LABELS = {
    OptionsKey.AlarmPanel.DISARM: "Disinserimento",
    OptionsKey.AlarmPanel.ARM_AWAY: "Fuori casa",
    OptionsKey.AlarmPanel.ARM_HOME: "In casa",
    OptionsKey.AlarmPanel.ARM_NIGHT: "Notte",
    OptionsKey.AlarmPanel.ARM_VACATION: "Vacanza",
}
_CMD_STEPS = {
    "cmd_disarm": OptionsKey.AlarmPanel.DISARM,
    "cmd_arm_away": OptionsKey.AlarmPanel.ARM_AWAY,
    "cmd_arm_home": OptionsKey.AlarmPanel.ARM_HOME,
    "cmd_arm_night": OptionsKey.AlarmPanel.ARM_NIGHT,
    "cmd_arm_vacation": OptionsKey.AlarmPanel.ARM_VACATION,
}
_METHOD_FIELD = OptionsKey.AlarmPanel.SCENARIO_FIELD + "_method"


class InimPrimeOptionsFlowHandler(OptionsFlowWithReload):
    """Menu-driven options flow.

    A main menu (General / Alarm / Save) keeps settings and the alarm panel apart.
    The alarm panel is a submenu with one entry per command; each command first
    asks the method (scenario vs partitions, only with both backends) and then
    shows ONLY the relevant fields, so a scenario command never displays the
    partition rows and vice-versa. Edits accumulate in ``self._working`` and are
    written when the user picks Save.
    """

    def __init__(self) -> None:
        self._working: dict[str, Any] | None = None
        self._cmd: str | None = None
        self._method: str | None = None

    # ------------------------------------------------------------------
    @property
    def _has_native(self) -> bool:
        return bool(get_entry_data(self.config_entry).get("native"))

    @property
    def _has_primelan(self) -> bool:
        return bool(get_entry_data(self.config_entry).get("primelan"))

    def _ensure_working(self) -> None:
        if self._working is None:
            self._working = dict(get_entry_options(self.config_entry))

    # ------------------------------------------------------------------
    # Main menu
    # ------------------------------------------------------------------
    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        self._ensure_working()
        options = ["general"]
        if self._has_native or self._has_primelan:
            options.append("alarm")
        options.append("save")
        return self.async_show_menu(step_id="init", menu_options=options)

    async def async_step_save(self, user_input: dict[str, Any] | None = None):
        self._ensure_working()
        working = dict(self._working)
        # Drop empty alarm maps so they don't linger as empty dicts.
        for key in (OptionsKey.ALARM_PANEL, OptionsKey.ALARM_PANEL_NATIVE):
            if key in working and not working[key]:
                working.pop(key)
        return self.async_create_entry(title="", data=working)

    # ------------------------------------------------------------------
    # General settings (everything except the alarm panel)
    # ------------------------------------------------------------------
    async def async_step_general(self, user_input: dict[str, Any] | None = None):
        self._ensure_working()
        if user_input is not None:
            gi = dict(user_input)
            had_shutters = any(_SECTION_KEY.match(k) for k in gi)
            shutter_commands = _extract_shutter_commands(gi)  # pops the sections
            if "scan_intervals" in gi:
                self._working["scan_intervals"] = gi["scan_intervals"]
            if OptionsKey.HIDE_UNUSED_PARTITIONS in gi:
                self._working[OptionsKey.HIDE_UNUSED_PARTITIONS] = gi[OptionsKey.HIDE_UNUSED_PARTITIONS]
            if OptionsKey.SHOW_HIDDEN_OUTPUTS in gi:
                self._working[OptionsKey.SHOW_HIDDEN_OUTPUTS] = gi[OptionsKey.SHOW_HIDDEN_OUTPUTS]
            if OptionsKey.PANEL_LOG_EVENTS_FETCH_LIMIT in gi:
                self._working[OptionsKey.PANEL_LOG_EVENTS_FETCH_LIMIT] = gi[OptionsKey.PANEL_LOG_EVENTS_FETCH_LIMIT]
            if had_shutters:
                if shutter_commands:
                    self._working[OptionsKey.SHUTTER_COMMANDS] = shutter_commands
                else:
                    self._working.pop(OptionsKey.SHUTTER_COMMANDS, None)
            return await self.async_step_init()

        shutters, output_choices = self._shutter_context()
        module_names: dict[int, str] = {}
        if shutters:
            gateway = self.config_entry.runtime_data.gateway
            try:
                module_names = await gateway.module_labels(max(shutters))
            except Exception:  # noqa: BLE001 - names are cosmetic
                module_names = {}

        schema = vol.Schema(
            build_options_schema(
                has_native=self._has_native,
                has_primelan=self._has_primelan,
                current_options=self._config_options(),
                shutters=shutters,
                output_choices=output_choices,
                module_names=module_names,
                partitions=self._partition_choices(),
                include_alarm=False,
            )
        )
        return self.async_show_form(
            step_id="general",
            data_schema=schema,
            description_placeholders={
                "state_backend": "Native" if self._has_native else "PrimeLAN",
            },
        )

    # ------------------------------------------------------------------
    # Alarm submenu
    # ------------------------------------------------------------------
    async def async_step_alarm(self, user_input: dict[str, Any] | None = None):
        self._ensure_working()
        # Native-only can't arm per-command via scenarios but can still set a
        # partition set / outputs for each; PrimeLAN-only uses scenarios. Offer
        # every command; 'init' goes back to the main menu.
        options = list(_CMD_STEPS.keys()) + ["init"]
        return self.async_show_menu(step_id="alarm", menu_options=options)

    async def async_step_cmd_disarm(self, user_input=None):
        return await self._enter_command(OptionsKey.AlarmPanel.DISARM)

    async def async_step_cmd_arm_away(self, user_input=None):
        return await self._enter_command(OptionsKey.AlarmPanel.ARM_AWAY)

    async def async_step_cmd_arm_home(self, user_input=None):
        return await self._enter_command(OptionsKey.AlarmPanel.ARM_HOME)

    async def async_step_cmd_arm_night(self, user_input=None):
        return await self._enter_command(OptionsKey.AlarmPanel.ARM_NIGHT)

    async def async_step_cmd_arm_vacation(self, user_input=None):
        return await self._enter_command(OptionsKey.AlarmPanel.ARM_VACATION)

    async def _enter_command(self, command: str):
        self._cmd = command
        if self._has_native and self._has_primelan:
            # Ask which method; the detail step then shows only those fields.
            return await self.async_step_method()
        # Single backend: method is forced.
        self._method = (
            OptionsKey.PanelType.PARTIZIONI if self._has_native
            else OptionsKey.PanelType.SCENARI
        )
        return await self.async_step_cmd_detail()

    async def async_step_method(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            self._method = user_input[_METHOD_FIELD]
            return await self.async_step_cmd_detail()

        scenarios = self._working.get(OptionsKey.ALARM_PANEL) or {}
        natives = self._working.get(OptionsKey.ALARM_PANEL_NATIVE) or {}
        if self._cmd in scenarios:
            default = OptionsKey.PanelType.SCENARI
        elif self._cmd in natives:
            default = OptionsKey.PanelType.PARTIZIONI
        else:
            default = OptionsKey.PanelType.SCENARI
        return self.async_show_form(
            step_id="method",
            data_schema=vol.Schema(build_alarm_method_schema(default)),
            description_placeholders={"command": _CMD_LABELS.get(self._cmd, self._cmd)},
        )

    async def async_step_cmd_detail(self, user_input: dict[str, Any] | None = None):
        scenarios = dict(self._working.get(OptionsKey.ALARM_PANEL) or {})
        natives = dict(self._working.get(OptionsKey.ALARM_PANEL_NATIVE) or {})

        if user_input is not None:
            if self._method == OptionsKey.PanelType.PARTIZIONI:
                index = {label: pid for pid, label in self._partition_choices()}
                entry = extract_command_partitions(dict(user_input), index)
                if entry:
                    natives[self._cmd] = entry
                else:
                    natives.pop(self._cmd, None)
                scenarios.pop(self._cmd, None)
            else:
                value = user_input.get(OptionsKey.AlarmPanel.SCENARIO_FIELD)
                if value not in (None, ""):
                    scenarios[self._cmd] = int(value)
                else:
                    scenarios.pop(self._cmd, None)
                natives.pop(self._cmd, None)
            self._working[OptionsKey.ALARM_PANEL] = scenarios
            self._working[OptionsKey.ALARM_PANEL_NATIVE] = natives
            return await self.async_step_alarm()

        if self._method == OptionsKey.PanelType.PARTIZIONI:
            _, output_choices = self._shutter_context()
            schema = build_alarm_partitions_schema(
                self._cmd, natives.get(self._cmd),
                self._partition_choices(), output_choices,
            )
        else:
            schema = build_alarm_scenario_schema(scenarios.get(self._cmd))
        return self.async_show_form(
            step_id="cmd_detail",
            data_schema=vol.Schema(schema),
            description_placeholders={"command": _CMD_LABELS.get(self._cmd, self._cmd)},
        )

    # ------------------------------------------------------------------
    def _config_options(self) -> ConfigOptions:
        w = self._working or {}
        scan = w.get("scan_intervals") or {}
        return ConfigOptions(
            zones_scan_interval=scan.get("zones"),
            partitions_scan_interval=scan.get("partitions"),
            gsm_scan_interval=scan.get("gsm"),
            system_faults_scan_interval=scan.get("system_faults"),
            panel_log_events_scan_interval=scan.get("panel_log_events"),
            outputs_scan_interval=scan.get("outputs"),
            scenarios_scan_interval=scan.get("scenarios"),
            panel_log_events_fetch_limit=w.get("panel_log_events_fetch_limit"),
            alarm_panel=w.get("alarm_panel"),
            alarm_panel_type=w.get("alarm_panel_type"),
            alarm_panel_native=w.get("alarm_panel_native"),
            show_hidden_outputs=w.get("show_hidden_outputs"),
            hide_unused_partitions=w.get("hide_unused_partitions"),
            shutter_commands=w.get("shutter_commands"),
        )

    def _partition_choices(self) -> list[tuple[int, str]]:
        """Partitions to offer in the Native alarm-panel mapping (id -> label)."""
        runtime = getattr(self.config_entry, "runtime_data", None)
        gateway = getattr(runtime, "gateway", None) if runtime else None
        partitions = dict(getattr(gateway, "partitions", None) or {}) if gateway else {}
        return sorted(
            ((pid, (p.label or f"Partizione {pid}")) for pid, p in partitions.items()),
            key=lambda item: item[0],
        )

    def _shutter_context(self) -> tuple[list[int], list[tuple[int, str]]]:
        """Discovered shutter modules and the current outputs to choose from."""
        runtime = getattr(self.config_entry, "runtime_data", None)
        gateway = getattr(runtime, "gateway", None) if runtime else None
        if gateway is None:
            return [], []
        all_outputs = dict(getattr(gateway, "all_outputs", None) or {})
        outputs = dict(getattr(gateway, "outputs", None) or {})
        if not all_outputs:
            return [], []
        shutters = sorted(discover_shutters(all_outputs))
        choices = sorted(
            (
                (oid, (o.label or f"Output {oid}"))
                for oid, o in outputs.items()
                if not _SHUTTER_LABEL.match((o.label or "").replace("\x00", "").strip())
            ),
            key=lambda item: item[1].lower(),
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
