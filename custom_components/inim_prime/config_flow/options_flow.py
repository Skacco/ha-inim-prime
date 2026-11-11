from __future__ import annotations
import logging
import time
import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import OptionsFlowWithReload
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    SelectOptionDict, SelectSelector, SelectSelectorConfig, SelectSelectorMode,
)

from .const import OptionsKey
from ..const import SHUTTER_COMMAND_FIELDS
from .models import ConfigOptions, extract_command_partitions
from ..entry_data import get_entry_options, get_entry_data
from ..cover import discover_shutters, _SHUTTER_LABEL
from .schemas import (
    build_scan_intervals_schema,
    build_general_params_schema,
    build_shutters_schema,
    build_alarm_partitions_schema,
)

# A shutter section key looks like "01 · TENDA SALA": the leading number is the
# module, used to store the mapping (so renaming the module can't lose it).
_SECTION_KEY = re.compile(r"^(\d{1,3})\s*·")

# Same wording and order as Home Assistant's alarm card. These labels are
# built in code (menus with dynamic labels and dropdown options can't use the
# translation files), so they are picked by the Home Assistant language here.
_CMD_LABELS_IT = {
    OptionsKey.AlarmPanel.ARM_HOME: "In casa",
    OptionsKey.AlarmPanel.ARM_AWAY: "Fuori casa",
    OptionsKey.AlarmPanel.ARM_NIGHT: "Notte",
    OptionsKey.AlarmPanel.ARM_VACATION: "Vacanza",
    OptionsKey.AlarmPanel.DISARM: "Disattivato",
}
_CMD_LABELS_EN = {
    OptionsKey.AlarmPanel.ARM_HOME: "Home",
    OptionsKey.AlarmPanel.ARM_AWAY: "Away",
    OptionsKey.AlarmPanel.ARM_NIGHT: "Night",
    OptionsKey.AlarmPanel.ARM_VACATION: "Vacation",
    OptionsKey.AlarmPanel.DISARM: "Disarmed",
}
_LOGGER = logging.getLogger(__name__)

_TEXTS = {
    "it": {
        "custom_partitions": "➜ Aree personalizzate…",
        "all_disarmed": "➜ Tutte le aree disinserite",
        "partitions": "Aree",
        "back_to_menu": "‹ Torna al menù",
    },
    "en": {
        "custom_partitions": "➜ Custom partitions…",
        "all_disarmed": "➜ All partitions disarmed",
        "partitions": "Partitions",
        "back_to_menu": "‹ Back to menu",
    },
}
_CMD_STEPS = (
    "cmd_arm_home", "cmd_arm_away", "cmd_arm_night", "cmd_arm_vacation", "cmd_disarm",
)
_STEP_TO_CMD = {
    "cmd_arm_home": OptionsKey.AlarmPanel.ARM_HOME,
    "cmd_arm_away": OptionsKey.AlarmPanel.ARM_AWAY,
    "cmd_arm_night": OptionsKey.AlarmPanel.ARM_NIGHT,
    "cmd_arm_vacation": OptionsKey.AlarmPanel.ARM_VACATION,
    "cmd_disarm": OptionsKey.AlarmPanel.DISARM,
}
# Dropdown value meaning "arm via custom partitions" (scenario ids are digits).
_PARTITIONS_CHOICE = "aree"


def _section_key(cmd: str) -> str:
    """Key of a command's collapsed partitions section in the alarm form."""
    return f"{cmd}_partitions"
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
        if getattr(self, "_notify_webhook_url", False) and opts.get(OptionsKey.WEBHOOK_ID):
            self._notify_first_webhook_url(opts[OptionsKey.WEBHOOK_ID])
        return self.async_create_entry(title="", data=opts)

    def _notify_first_webhook_url(self, webhook_id: str) -> None:
        """One-time notification with the push URL, shown on first activation.
        The URL also stays visible in Options (General parameters)."""
        from homeassistant.components import persistent_notification
        from homeassistant.components import webhook as hass_webhook
        try:
            url = hass_webhook.async_generate_url(
                self.hass, webhook_id, allow_external=False, allow_ip=True
            )
        except Exception:  # noqa: BLE001 - URL is informational only
            url = f"http://<IP-HA>:8123/api/webhook/{webhook_id}"
        persistent_notification.async_create(
            self.hass,
            (
                "Push in tempo reale attivato. L'URL è anche nelle Opzioni "
                "(Parametri generali). In Prime/STUDIO → Configurazione PrimeLAN "
                "→ Web API → «Gestione Api Http in uscita» punta gli eventi a "
                f"questo URL locale, uno per evento:\n\n`{url}`\n\n"
                "Esempi: `?ev=zone_open&id=<zona>`, `?ev=arm&area=<area>`, "
                "`?ev=alarm&area=<area>`, `?ev=output`."
            ),
            title="INIM Prime - push in tempo reale",
            notification_id=f"inim_prime_webhook_{self.config_entry.entry_id}",
        )

    @property
    def _italian(self) -> bool:
        hass = getattr(self, "hass", None)
        if hass is None:
            return True
        return str(hass.config.language or "").lower().startswith("it")

    def _text(self, key: str) -> str:
        return _TEXTS["it" if self._italian else "en"][key]

    def _cmd_label(self, cmd: str) -> str:
        labels = _CMD_LABELS_IT if self._italian else _CMD_LABELS_EN
        return labels.get(cmd, cmd)

    def _alarm_label(self, cmd: str) -> str:
        """Menu label showing the command's current (pending) configuration."""
        name = self._cmd_label(cmd)
        if self._natives.get(cmd):
            return f"{name} — {self._text('partitions')}"
        return f"{name} — —"

    async def async_step_alarm(self, user_input: dict[str, Any] | None = None):
        self._load()
        # With scenarios available (PrimeLAN) the whole panel is one form: a
        # dropdown per command. Native-only has no scenarios, so it keeps the
        # per-command list leading to the partitions form.
        if self._has_primelan:
            return await self.async_step_alarm_form()
        menu: dict[str, str] = {
            step: self._alarm_label(_STEP_TO_CMD[step]) for step in _CMD_STEPS
        }
        menu["init"] = self._text("back_to_menu")
        return self.async_show_menu(step_id="alarm", menu_options=menu)

    async def async_step_alarm_form(self, user_input: dict[str, Any] | None = None):
        """The whole alarm panel on one screen, one group per command.

        Each group is a dropdown (unset, a scenario by name or, with Native,
        "custom partitions") followed by a collapsed section holding that
        command's partition modes and output activation. The section is used
        only when the dropdown says "custom partitions".
        """
        self._load()
        commands = list(_STEP_TO_CMD.values())
        if user_input is not None:
            index = {label: pid for pid, label in self._partition_choices()}
            for cmd in commands:
                value = user_input.get(cmd)
                if value == _PARTITIONS_CHOICE:
                    entry = extract_command_partitions(
                        dict(user_input.get(_section_key(cmd)) or {}), index,
                    )
                    if entry:
                        self._natives[cmd] = entry
                    else:
                        self._natives.pop(cmd, None)
                    self._scenarios.pop(cmd, None)
                elif value in (None, ""):
                    self._scenarios.pop(cmd, None)
                    self._natives.pop(cmd, None)
                else:
                    self._scenarios[cmd] = int(value)
                    self._natives.pop(cmd, None)
            return await self.async_step_init()

        options = [
            SelectOptionDict(value=str(sid), label=name)
            for sid, name in self._scenario_choices()
        ]
        if self._has_native:
            # Disarming has no per-partition choice: it turns them all off.
            disarm_options = options + [SelectOptionDict(
                value=_PARTITIONS_CHOICE, label=self._text("all_disarmed"),
            )]
            options.append(SelectOptionDict(
                value=_PARTITIONS_CHOICE, label=self._text("custom_partitions"),
            ))
        else:
            disarm_options = options
        _, output_choices = self._shutter_context()
        partitions = self._partition_choices()
        schema: dict = {}
        for cmd in commands:
            if cmd in self._scenarios:
                current = str(self._scenarios[cmd])
            elif self._natives.get(cmd) or (
                # With Native, disarm always works through the partitions.
                self._has_native and cmd == OptionsKey.AlarmPanel.DISARM
            ):
                current = _PARTITIONS_CHOICE
            else:
                current = None
            schema[vol.Optional(
                cmd, description={"suggested_value": current},
            )] = SelectSelector(SelectSelectorConfig(
                options=(
                    disarm_options if cmd == OptionsKey.AlarmPanel.DISARM else options
                ),
                mode=SelectSelectorMode.DROPDOWN,
            ))
            if self._has_native:
                schema[vol.Optional(_section_key(cmd))] = section(
                    vol.Schema(build_alarm_partitions_schema(
                        cmd, self._natives.get(cmd), partitions, output_choices,
                    )),
                    {"collapsed": True},
                )
        # Two step ids only to get two translated descriptions: with Native
        # the text explains the custom partitions, PrimeLAN-only does not.
        return self.async_show_form(
            step_id="alarm_form" if self._has_native else "alarm_form_primelan",
            data_schema=vol.Schema(schema), last_step=False,
        )

    async def async_step_alarm_form_primelan(
        self, user_input: dict[str, Any] | None = None,
    ):
        """PrimeLAN-only variant of the alarm form (same logic, own texts)."""
        return await self.async_step_alarm_form(user_input)

    # --- General parameters (no intervals, no alarm, no shutters) -------
    async def async_step_params(self, user_input: dict[str, Any] | None = None):
        self._load()
        if user_input is not None:
            if OptionsKey.HIDE_UNUSED_PARTITIONS in user_input:
                self._pending[OptionsKey.HIDE_UNUSED_PARTITIONS] = user_input[OptionsKey.HIDE_UNUSED_PARTITIONS]
            if OptionsKey.SHOW_HIDDEN_OUTPUTS in user_input:
                self._pending[OptionsKey.SHOW_HIDDEN_OUTPUTS] = user_input[OptionsKey.SHOW_HIDDEN_OUTPUTS]
            if OptionsKey.REALTIME_PUSH in user_input:
                enabled = bool(user_input[OptionsKey.REALTIME_PUSH])
                self._pending[OptionsKey.REALTIME_PUSH] = enabled
                # Generate the webhook id (the URL secret) the first time push is
                # turned on, and keep it afterwards so the URL stays stable.
                if enabled and not self._pending.get(OptionsKey.WEBHOOK_ID):
                    from homeassistant.components import webhook
                    self._pending[OptionsKey.WEBHOOK_ID] = webhook.async_generate_id()
                    # First activation: also surface the URL once in a notification.
                    self._notify_webhook_url = True
            if OptionsKey.WEBHOOK_ALLOW_REMOTE in user_input:
                self._pending[OptionsKey.WEBHOOK_ALLOW_REMOTE] = user_input[OptionsKey.WEBHOOK_ALLOW_REMOTE]
            if OptionsKey.PANEL_LOG_EVENTS_FETCH_LIMIT in user_input:
                self._pending[OptionsKey.PANEL_LOG_EVENTS_FETCH_LIMIT] = user_input[OptionsKey.PANEL_LOG_EVENTS_FETCH_LIMIT]
            if OptionsKey.ALARM_PIN_PANEL in user_input:
                self._pending[OptionsKey.ALARM_PIN_PANEL] = user_input[OptionsKey.ALARM_PIN_PANEL]
            # The PIN field is optional; an empty submit clears it.
            self._pending[OptionsKey.ALARM_PIN] = (user_input.get(OptionsKey.ALARM_PIN) or "").strip()
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
        return self.async_show_form(
            step_id="params",
            data_schema=schema,
            last_step=False,
            description_placeholders={"webhook_url": self._webhook_url_line()},
        )

    def _webhook_url_line(self) -> str:
        """Markdown line with the realtime-push webhook URL, or empty.

        Shown in the General parameters form so the URL is always re-viewable
        (no notification). Empty when push is off or not configured yet."""
        if not (self._pending.get(OptionsKey.REALTIME_PUSH)
                and self._pending.get(OptionsKey.WEBHOOK_ID)):
            return ""
        from homeassistant.components import webhook as hass_webhook
        wid = self._pending[OptionsKey.WEBHOOK_ID]
        try:
            # LAN URL: the panel is local and the webhook is local_only.
            url = hass_webhook.async_generate_url(
                self.hass, wid, allow_external=False, allow_ip=True
            )
        except Exception:  # noqa: BLE001 - URL is informational only
            url = f"http://<IP-HA>:8123/api/webhook/{wid}"
        return (
            "\n\n**URL push in tempo reale** (Prime/STUDIO → Configurazione "
            f"PrimeLAN → Web API → «Gestione Api Http in uscita»):\n\n`{url}`"
        )

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
            started = time.monotonic()
            try:
                module_names = await gateway.module_labels(max(shutters))
            except Exception:  # noqa: BLE001 - names are cosmetic
                module_names = {}
            _LOGGER.debug(
                "Shutters form: module names ready in %.2fs", time.monotonic() - started
            )

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
        # Native-only path (from the command list).
        self._load()
        self._cmd = command
        return await self.async_step_cmd_detail()

    async def async_step_cmd_detail(self, user_input: dict[str, Any] | None = None):
        """Partitions form of one command (mode per partition + outputs).

        Reads and writes the in-memory maps; "Save and exit" persists them.
        """
        self._load()

        if user_input is not None:
            index = {label: pid for pid, label in self._partition_choices()}
            entry = extract_command_partitions(dict(user_input), index)
            if entry:
                self._natives[self._cmd] = entry
            else:
                self._natives.pop(self._cmd, None)
            self._scenarios.pop(self._cmd, None)
            return await self.async_step_alarm()

        _, output_choices = self._shutter_context()
        schema = build_alarm_partitions_schema(
            self._cmd, self._natives.get(self._cmd),
            self._partition_choices(), output_choices,
        )
        return self.async_show_form(
            step_id="cmd_detail",
            data_schema=vol.Schema(schema),
            description_placeholders={"command": self._cmd_label(self._cmd)},
            # Not the last step (the single save is on the main menu): HA shows
            # "Avanti" instead of "Invia". HA can't label a form button "Salva".
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
            realtime_push=o.get("realtime_push"),
            webhook_allow_remote=o.get("webhook_allow_remote"),
            alarm_pin_panel=o.get("alarm_pin_panel"),
            alarm_pin=o.get("alarm_pin"),
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
            ((pid, (p.label or f"Area {pid}")) for pid, p in partitions.items()),
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
