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
    build_scan_intervals_schema,
    build_general_params_schema,
    build_shutters_schema,
    build_alarm_method_schema,
    build_alarm_scenario_schema,
    build_alarm_partitions_schema,
)

# A shutter section key looks like "01 · TENDA SALA": the leading number is the
# module, used to store the mapping (so renaming the module can't lose it).
_SECTION_KEY = re.compile(r"^(\d{1,3})\s*·")

_CMD_LABELS = {
    OptionsKey.AlarmPanel.DISARM: "Disinserimento",
    OptionsKey.AlarmPanel.ARM_AWAY: "Fuori casa",
    OptionsKey.AlarmPanel.ARM_HOME: "In casa",
    OptionsKey.AlarmPanel.ARM_NIGHT: "Notte",
    OptionsKey.AlarmPanel.ARM_VACATION: "Vacanza",
}
_CMD_STEPS = (
    "cmd_disarm", "cmd_arm_away", "cmd_arm_home", "cmd_arm_night", "cmd_arm_vacation",
)
_STEP_TO_CMD = {
    "cmd_disarm": OptionsKey.AlarmPanel.DISARM,
    "cmd_arm_away": OptionsKey.AlarmPanel.ARM_AWAY,
    "cmd_arm_home": OptionsKey.AlarmPanel.ARM_HOME,
    "cmd_arm_night": OptionsKey.AlarmPanel.ARM_NIGHT,
    "cmd_arm_vacation": OptionsKey.AlarmPanel.ARM_VACATION,
}
_METHOD_FIELD = OptionsKey.AlarmPanel.SCENARIO_FIELD + "_method"
_SCAN_INTERVAL_KEYS = frozenset(
    v for k, v in vars(OptionsKey.ScanIntervals).items()
    if not k.startswith("_") and isinstance(v, str)
)


class InimPrimeOptionsFlowHandler(OptionsFlowWithReload):
    """Menu-driven options flow with a single final save.

    The main menu lists general parameters, scan intervals, the alarm panel and
    (when present) shutters, plus "Save and exit". Every section edits an
    in-memory copy of the options and returns to this menu; nothing is written to
    the entry until "Save and exit". Going back (or closing with the X) therefore
    never persists a half-made change -- there is one clear save and one clear
    cancel.
    """

    _cmd: str | None = None
    _method: str | None = None
    # In-memory working copy of the options; written to the entry only on
    # "Save and exit". Loaded from the stored options on first use.
    _loaded: bool = False
    _pending: dict | None = None

    @property
    def _has_native(self) -> bool:
        return bool(get_entry_data(self.config_entry).get("native"))

    @property
    def _has_primelan(self) -> bool:
        return bool(get_entry_data(self.config_entry).get("primelan"))

    # ------------------------------------------------------------------
    def _load(self) -> None:
        if self._loaded:
            return
        opts = dict(get_entry_options(self.config_entry))
        # Normalise the alarm maps to plain mutable dicts for in-place edits.
        opts[OptionsKey.ALARM_PANEL] = dict(opts.get(OptionsKey.ALARM_PANEL) or {})
        opts[OptionsKey.ALARM_PANEL_NATIVE] = dict(opts.get(OptionsKey.ALARM_PANEL_NATIVE) or {})
        self._pending = opts
        self._loaded = True

    @property
    def _scenarios(self) -> dict:
        self._load()
        return self._pending[OptionsKey.ALARM_PANEL]

    @property
    def _natives(self) -> dict:
        self._load()
        return self._pending[OptionsKey.ALARM_PANEL_NATIVE]

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        self._load()
        # General parameters first, scan intervals second.
        options = ["params", "scan"]
        if self._has_native or self._has_primelan:
            options.append("alarm")
        # "Tapparelle" only when the panel actually has shutter modules.
        shutters, _ = self._shutter_context()
        if shutters:
            options.append("shutters")
        options.append("save_exit")
        return self.async_show_menu(step_id="init", menu_options=options)

    async def async_step_save_exit(self, user_input: dict[str, Any] | None = None):
        # The single write: persist the working copy, dropping empty alarm maps.
        self._load()
        opts = dict(self._pending)
        if not opts.get(OptionsKey.ALARM_PANEL):
            opts.pop(OptionsKey.ALARM_PANEL, None)
        if not opts.get(OptionsKey.ALARM_PANEL_NATIVE):
            opts.pop(OptionsKey.ALARM_PANEL_NATIVE, None)
        return self.async_create_entry(title="", data=opts)

    def _alarm_label(self, cmd: str) -> str:
        """Menu label showing the command's current (pending) configuration."""
        name = _CMD_LABELS.get(cmd, cmd)
        if cmd in self._scenarios:
            sid = self._scenarios[cmd]
            scen = dict(self._scenario_choices()).get(sid, f"Scenario {sid}")
            return f"{name} — Scenario «{scen}»"
        if self._natives.get(cmd):
            return f"{name} — Partizioni"
        return f"{name} — —"

    async def async_step_alarm(self, user_input: dict[str, Any] | None = None):
        # Dynamic labels (dict menu) so each command shows its current config.
        self._load()
        menu: dict[str, str] = {
            step: self._alarm_label(_STEP_TO_CMD[step]) for step in _CMD_STEPS
        }
        menu["init"] = "‹ Torna al menù"
        return self.async_show_menu(step_id="alarm", menu_options=menu)

    # --- General parameters (no intervals, no alarm, no shutters) -------
    async def async_step_params(self, user_input: dict[str, Any] | None = None):
        self._load()
        if user_input is not None:
            if OptionsKey.HIDE_UNUSED_PARTITIONS in user_input:
                self._pending[OptionsKey.HIDE_UNUSED_PARTITIONS] = user_input[OptionsKey.HIDE_UNUSED_PARTITIONS]
            if OptionsKey.SHOW_HIDDEN_OUTPUTS in user_input:
                self._pending[OptionsKey.SHOW_HIDDEN_OUTPUTS] = user_input[OptionsKey.SHOW_HIDDEN_OUTPUTS]
            if OptionsKey.PANEL_LOG_EVENTS_FETCH_LIMIT in user_input:
                self._pending[OptionsKey.PANEL_LOG_EVENTS_FETCH_LIMIT] = user_input[OptionsKey.PANEL_LOG_EVENTS_FETCH_LIMIT]
            return await self.async_step_init()

        schema = vol.Schema(
            build_general_params_schema(
                has_native=self._has_native,
                has_primelan=self._has_primelan,
                current_options=self._config_options(),
            )
        )
        # Not the last step: submitting returns to the menu (the single save is
        # there), so HA shows "Avanti" and a back arrow back to the menu.
        return self.async_show_form(step_id="params", data_schema=schema, last_step=False)

    # --- Scan intervals (flat: its own page, no accordion) --------------
    async def async_step_scan(self, user_input: dict[str, Any] | None = None):
        self._load()
        if user_input is not None:
            # Fields are flat on this page; collect them back into scan_intervals.
            intervals = {
                key: value for key, value in user_input.items()
                if key in _SCAN_INTERVAL_KEYS
            }
            if intervals:
                self._pending["scan_intervals"] = {
                    **(self._pending.get("scan_intervals") or {}),
                    **intervals,
                }
            return await self.async_step_init()

        schema = vol.Schema(
            build_scan_intervals_schema(
                has_native=self._has_native,
                has_primelan=self._has_primelan,
                current_options=self._config_options(),
                as_section=False,
            )
        )
        return self.async_show_form(step_id="scan", data_schema=schema, last_step=False)

    # --- Shutters (only shown when the panel has shutter modules) -------
    async def async_step_shutters(self, user_input: dict[str, Any] | None = None):
        self._load()
        shutters, output_choices = self._shutter_context()
        if user_input is not None:
            shutter_commands = _extract_shutter_commands(dict(user_input))
            if shutter_commands:
                self._pending[OptionsKey.SHUTTER_COMMANDS] = shutter_commands
            else:
                self._pending.pop(OptionsKey.SHUTTER_COMMANDS, None)
            return await self.async_step_init()

        module_names: dict[int, str] = {}
        if shutters:
            gateway = self.config_entry.runtime_data.gateway
            try:
                module_names = await gateway.module_labels(max(shutters))
            except Exception:  # noqa: BLE001 - names are cosmetic
                module_names = {}

        schema = vol.Schema(
            build_shutters_schema(
                shutters=shutters,
                output_choices=output_choices,
                module_names=module_names,
                current_options=self._config_options(),
            )
        )
        return self.async_show_form(step_id="shutters", data_schema=schema, last_step=False)

    # --- Per-command routers -------------------------------------------
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
        self._load()
        self._cmd = command
        if self._has_native and self._has_primelan:
            return await self.async_step_method()
        self._method = (
            OptionsKey.PanelType.PARTIZIONI if self._has_native
            else OptionsKey.PanelType.SCENARI
        )
        return await self.async_step_cmd_detail()

    async def async_step_method(self, user_input: dict[str, Any] | None = None):
        # A menu (not a form) so it carries a "‹ Indietro" entry, and the labels
        # mark which method is currently configured for this command.
        self._load()
        cmd = self._cmd
        if cmd in self._scenarios:
            sid = self._scenarios[cmd]
            name = dict(self._scenario_choices()).get(sid, f"Scenario {sid}")
            scen_label = f"● Scenario — attuale: «{name}»"
        else:
            scen_label = "Scenario"
        part_label = (
            "● Partizioni (Native) — attuale"
            if self._natives.get(cmd) else "Partizioni (Native)"
        )
        return self.async_show_menu(
            step_id="method",
            menu_options={
                "method_scenario": scen_label,
                "method_partizioni": part_label,
                "alarm": "‹ Indietro",
            },
            description_placeholders={"command": _CMD_LABELS.get(cmd, cmd)},
        )

    async def async_step_method_scenario(self, user_input=None):
        self._method = OptionsKey.PanelType.SCENARI
        return await self.async_step_cmd_detail()

    async def async_step_method_partizioni(self, user_input=None):
        self._method = OptionsKey.PanelType.PARTIZIONI
        return await self.async_step_cmd_detail()

    async def async_step_cmd_detail(self, user_input: dict[str, Any] | None = None):
        # Reads and writes the in-memory maps; "Save and exit" persists them.
        self._load()

        if user_input is not None:
            if self._method == OptionsKey.PanelType.PARTIZIONI:
                index = {label: pid for pid, label in self._partition_choices()}
                entry = extract_command_partitions(dict(user_input), index)
                if entry:
                    self._natives[self._cmd] = entry
                else:
                    self._natives.pop(self._cmd, None)
                self._scenarios.pop(self._cmd, None)
            else:
                value = user_input.get(OptionsKey.AlarmPanel.SCENARIO_FIELD)
                if value not in (None, ""):
                    self._scenarios[self._cmd] = int(value)
                else:
                    self._scenarios.pop(self._cmd, None)
                self._natives.pop(self._cmd, None)
            # Back to the submenu (not closing) so more commands can be edited.
            return await self.async_step_alarm()

        if self._method == OptionsKey.PanelType.PARTIZIONI:
            _, output_choices = self._shutter_context()
            schema = build_alarm_partitions_schema(
                self._cmd, self._natives.get(self._cmd),
                self._partition_choices(), output_choices,
            )
        else:
            schema = build_alarm_scenario_schema(
                self._scenarios.get(self._cmd), self._scenario_choices(),
            )
        return self.async_show_form(
            step_id="cmd_detail",
            data_schema=vol.Schema(schema),
            description_placeholders={"command": _CMD_LABELS.get(self._cmd, self._cmd)},
            # Not the last step: submitting returns to the command list, where the
            # real save ("Salva ed esci") lives. So HA labels the button "Avanti"
            # (Next) instead of the misleading "Invia", and tends to show a back
            # arrow. HA has no way to label a form button "Salva".
            last_step=False,
        )

    # ------------------------------------------------------------------
    def _config_options(self) -> ConfigOptions:
        # Read from the in-memory working copy so forms reflect in-session edits.
        o = self._pending if self._loaded else get_entry_options(self.config_entry)
        scan = o.get("scan_intervals") or {}
        return ConfigOptions(
            zones_scan_interval=scan.get("zones"),
            partitions_scan_interval=scan.get("partitions"),
            gsm_scan_interval=scan.get("gsm"),
            system_faults_scan_interval=scan.get("system_faults"),
            panel_log_events_scan_interval=scan.get("panel_log_events"),
            outputs_scan_interval=scan.get("outputs"),
            scenarios_scan_interval=scan.get("scenarios"),
            panel_log_events_fetch_limit=o.get("panel_log_events_fetch_limit"),
            alarm_panel=o.get("alarm_panel"),
            alarm_panel_type=o.get("alarm_panel_type"),
            alarm_panel_native=o.get("alarm_panel_native"),
            show_hidden_outputs=o.get("show_hidden_outputs"),
            hide_unused_partitions=o.get("hide_unused_partitions"),
            shutter_commands=o.get("shutter_commands"),
        )

    def _scenario_choices(self) -> list[tuple[int, str]]:
        runtime = getattr(self.config_entry, "runtime_data", None)
        gateway = getattr(runtime, "gateway", None) if runtime else None
        scenarios = dict(getattr(gateway, "scenarios", None) or {}) if gateway else {}
        return sorted(
            ((sid, (s.label or f"Scenario {sid}")) for sid, s in scenarios.items()),
            key=lambda item: item[0],
        )

    def _partition_choices(self) -> list[tuple[int, str]]:
        runtime = getattr(self.config_entry, "runtime_data", None)
        gateway = getattr(runtime, "gateway", None) if runtime else None
        partitions = dict(getattr(gateway, "partitions", None) or {}) if gateway else {}
        return sorted(
            ((pid, (p.label or f"Partizione {pid}")) for pid, p in partitions.items()),
            key=lambda item: item[0],
        )

    def _shutter_context(self) -> tuple[list[int], list[tuple[int, str]]]:
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
