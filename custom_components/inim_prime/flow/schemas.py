from __future__ import annotations

import voluptuous as vol
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    TextSelector, TextSelectorType, TextSelectorConfig,
    NumberSelector, NumberSelectorConfig, NumberSelectorMode,
    SelectSelector, SelectSelectorConfig, SelectSelectorMode, SelectOptionDict,
)

from .const import (
    DataKey,
    OptionsKey,
)
from .models import ConfigData, ScanIntervalsDefault, ConfigOptions
from ..const import (
    NATIVE_CYCLE_PAUSE_DEFAULT,
    NATIVE_CYCLE_PAUSE_BEFORE_DEFAULT,
    NATIVE_CYCLE_PAUSE_AFTER_DEFAULT,
    NATIVE_CYCLE_PAUSE_MIN,
    NATIVE_CYCLE_PAUSE_MAX,
    ZONES_SCAN_INTERVAL_PRIMELAN_DEFAULT,
    PARTITIONS_SCAN_INTERVAL_PRIMELAN_DEFAULT,
    ZONES_SCAN_INTERVAL_NATIVE_DEFAULT,
    PARTITIONS_SCAN_INTERVAL_NATIVE_DEFAULT,
    GSM_SCAN_INTERVAL_PRIMELAN_DEFAULT,
    SYSTEM_FAULTS_SCAN_INTERVAL_PRIMELAN_DEFAULT,
    PANEL_LOG_EVENTS_SCAN_INTERVAL_PRIMELAN_DEFAULT,
    OUTPUTS_SCAN_INTERVAL_NATIVE_DEFAULT,
    OUTPUTS_SCAN_INTERVAL_PRIMELAN_DEFAULT,
    SCENARIOS_SCAN_INTERVAL_PRIMELAN_DEFAULT,
    PRIMELAN_SCAN_INTERVAL_BOTH_DEFAULT,
    NATIVE_PASSWORD_DEFAULT,
    NATIVE_CHECK_PORT_EXPOSED_DEFAULT,
    NATIVE_PORT_CHECK_SERVER_DEFAULT,
    PRIMELAN_USE_HTTPS_DEFAULT,
    NATIVE_USE_CUSTOM_PIN_DEFAULT,
    NATIVE_PORT_DEFAULT,
    NATIVE_USE_OUTER_FRAME_DEFAULT,
    SHUTTER_COMMAND_FIELDS,
    SCAN_INTERVAL_MIN,
    SCAN_INTERVAL_MAX,
    PANEL_LOG_EVENTS_FETCH_LIMIT_DEFAULT,
    PANEL_LOG_EVENTS_FETCH_LIMIT_MIN,
    PANEL_LOG_EVENTS_FETCH_LIMIT_MAX,
)


# ----------------------------------------------------------------------
# Per-backend connection schemas
# ----------------------------------------------------------------------

def build_native_schema(
        *,
        current_port: int | None = None,
        current_use_outer_frame: bool | None = None,
        current_use_custom_pin: bool | None = None,
        require_password: bool = True,
        ask_outer_frame: bool = True,
        current_password: str | None = None,
        current_check_port_exposed: bool | None = None,
        current_port_check_server: str | None = None,
) -> dict:
    """Build the native-protocol connection schema.

    ask_outer_frame=False hides the "Use Outer Frame" field: when PrimeLAN is
    also configured the panel necessarily has a PrimeLAN card, so the outer
    frame is always used.

    include_host=False is used when the host is asked once at the top
    level and shared with the PrimeLAN section (see build_connection_schema).
    """
    schema: dict = {}

    # Port
    schema[vol.Required(
        DataKey.Native.PORT,
        default = current_port if current_port is not None else NATIVE_PORT_DEFAULT,
    )] = int

    # Use outer frame
    if ask_outer_frame:
        schema[vol.Required(
            DataKey.Native.USE_OUTER_FRAME,
            default = current_use_outer_frame if current_use_outer_frame is not None else NATIVE_USE_OUTER_FRAME_DEFAULT,
        )] = bool

    # Password
    if require_password:
        schema[vol.Required(
            DataKey.Native.PASSWORD,
            default = current_password or NATIVE_PASSWORD_DEFAULT,
        )] = TextSelector(TextSelectorConfig(type = TextSelectorType.PASSWORD))
    else:
        schema[vol.Optional(
            DataKey.Native.PASSWORD,
        )] = TextSelector(TextSelectorConfig(type = TextSelectorType.PASSWORD))

    # Use custom pin
    schema[vol.Required(
        DataKey.Native.USE_CUSTOM_PIN,
        default = current_use_custom_pin if current_use_custom_pin is not None else NATIVE_USE_CUSTOM_PIN_DEFAULT,
    )] = bool

    # Pin
    schema[vol.Optional(
        DataKey.Native.PIN,
    )] = TextSelector(TextSelectorConfig(type = TextSelectorType.PASSWORD))

    # Check, when the form is submitted, that the port is not reachable from the Internet
    schema[vol.Required(
        DataKey.Native.CHECK_PORT_EXPOSED,
        default = (
            current_check_port_exposed if current_check_port_exposed is not None
            else NATIVE_CHECK_PORT_EXPOSED_DEFAULT
        ),
    )] = bool
    schema[vol.Optional(
        DataKey.Native.PORT_CHECK_SERVER,
        default = current_port_check_server or NATIVE_PORT_CHECK_SERVER_DEFAULT,
    )] = TextSelector(TextSelectorConfig(type = TextSelectorType.URL))

    return schema


def build_primelan_schema(
        *,
        current_use_https: bool | None = None,
        require_api_key: bool = True,
        current_api_key: str | None = None,
) -> dict:
    """Build the PrimeLAN connection schema."""
    schema: dict = {}


    # Use https
    schema[vol.Required(
        DataKey.Primelan.USE_HTTPS,
        default = current_use_https if current_use_https is not None else PRIMELAN_USE_HTTPS_DEFAULT,
    )] = bool

    # Api key
    if require_api_key and current_api_key:
        schema[vol.Required(DataKey.Primelan.API_KEY, default = current_api_key)] = TextSelector(
            TextSelectorConfig(type = TextSelectorType.PASSWORD)
        )
    elif require_api_key:
        schema[vol.Required(DataKey.Primelan.API_KEY)] = TextSelector(
            TextSelectorConfig(type = TextSelectorType.PASSWORD)
        )
    else:
        schema[vol.Optional(DataKey.Primelan.API_KEY)] = TextSelector(
            TextSelectorConfig(type = TextSelectorType.PASSWORD)
        )

    return schema


def build_connection_schema(
        *,
        flow_data: ConfigData,
        prefill_secrets: bool = False,
) -> dict:
    """Build the combined connection-step schema for the chosen backend(s).

    The host is asked once, at top level, and shared by whichever backend
    section(s) are active -- Native and PrimeLAN always talk to the same
    panel/gateway address.
    """

    schema: dict = {
        vol.Required(DataKey.HOST, default = flow_data.host): str,
    }

    if flow_data.use_native:
        if flow_data.native is None:
            schema[vol.Required(DataKey.NATIVE)] = section(
                vol.Schema(
                    build_native_schema(
                        require_password = True,
                        ask_outer_frame = not flow_data.use_primelan,
                    )
                )
            )
        else:
            schema[vol.Required(DataKey.NATIVE)] = section(
                vol.Schema(
                    build_native_schema(
                        current_port = flow_data.native.port,
                        current_use_outer_frame = flow_data.native.use_outer_frame,
                        current_use_custom_pin = flow_data.native.pin is not None,
                        current_check_port_exposed = flow_data.native.check_port_exposed,
                        current_port_check_server = flow_data.native.port_check_server,
                        # First setup, form shown again after an error: keep
                        # what was typed. Reconfigure: blank keeps the stored one.
                        require_password = prefill_secrets,
                        current_password = flow_data.native.password if prefill_secrets else None,
                        ask_outer_frame = not flow_data.use_primelan,
                    )
                )
            )

    if flow_data.use_primelan:
        if flow_data.primelan is None:
            schema[vol.Required(DataKey.PRIMELAN)] = section(
                vol.Schema(
                    build_primelan_schema(
                        require_api_key = True,
                    )
                )
            )
        else:
            schema[vol.Required(DataKey.PRIMELAN)] = section(
                vol.Schema(
                    build_primelan_schema(
                        current_use_https = flow_data.primelan.use_https,
                        require_api_key = prefill_secrets,
                        current_api_key = flow_data.primelan.api_key if prefill_secrets else None,
                    )
                )
            )

    return schema


# ----------------------------------------------------------------------
# Integration options schema (scan intervals, log fetch limit)
# ----------------------------------------------------------------------

def build_scan_intervals_schema(
        *,
        has_native: bool,
        has_primelan: bool,
        current_options: ConfigOptions | None = None,
        migrating_from_native: bool | None = None,
        migrating_from_primelan: bool | None = None,
        as_section: bool = True,
) -> dict:
    """The scan-interval fields (one poller per field).

    ``as_section=True`` wraps them in a collapsible "scan_intervals" section
    (used by the all-in-one config-flow form, where it sits among other
    sections). ``as_section=False`` returns the fields flat -- the options flow
    shows them on a page of their own, so the accordion would just be noise.
    """
    both_backends = has_native and has_primelan
    gsm_default = PRIMELAN_SCAN_INTERVAL_BOTH_DEFAULT if both_backends else GSM_SCAN_INTERVAL_PRIMELAN_DEFAULT
    faults_default = PRIMELAN_SCAN_INTERVAL_BOTH_DEFAULT if both_backends else SYSTEM_FAULTS_SCAN_INTERVAL_PRIMELAN_DEFAULT
    log_default = PRIMELAN_SCAN_INTERVAL_BOTH_DEFAULT if both_backends else PANEL_LOG_EVENTS_SCAN_INTERVAL_PRIMELAN_DEFAULT
    scan_intervals_defaults = ScanIntervalsDefault(
        zones = ZONES_SCAN_INTERVAL_NATIVE_DEFAULT if has_native else ZONES_SCAN_INTERVAL_PRIMELAN_DEFAULT,
        partitions = PARTITIONS_SCAN_INTERVAL_NATIVE_DEFAULT if has_native else PARTITIONS_SCAN_INTERVAL_PRIMELAN_DEFAULT,
        gsm = gsm_default,
        system_faults = faults_default,
        panel_log_events = log_default,
        outputs = OUTPUTS_SCAN_INTERVAL_NATIVE_DEFAULT if has_native else OUTPUTS_SCAN_INTERVAL_PRIMELAN_DEFAULT,
        scenarios = SCENARIOS_SCAN_INTERVAL_PRIMELAN_DEFAULT,
    )

    if current_options is not None:
        # In case the user is migrating from native or primelan does not use the existing intervals for zones and partitions
        # This is to help the user who does not know that a different backend has different timings
        if not (
                (migrating_from_native and has_primelan and not has_native) or # If was using native, but is changing to only prime, reset defaults
                (migrating_from_primelan and not migrating_from_native and has_native) # If I was using primelan only, but I'm migrating to something which also uses native, reset defaults
        ):
            scan_intervals_defaults["zones"] = current_options.zones_scan_interval if current_options.zones_scan_interval is not None else scan_intervals_defaults["zones"]
            scan_intervals_defaults["partitions"] = current_options.partitions_scan_interval if current_options.partitions_scan_interval is not None else scan_intervals_defaults["partitions"]
            scan_intervals_defaults["outputs"] = current_options.outputs_scan_interval if current_options.outputs_scan_interval is not None else scan_intervals_defaults["outputs"]
        scan_intervals_defaults["scenarios"] = current_options.scenarios_scan_interval if current_options.scenarios_scan_interval is not None else scan_intervals_defaults["scenarios"]
        scan_intervals_defaults["gsm"] = current_options.gsm_scan_interval if current_options.gsm_scan_interval is not None else scan_intervals_defaults["gsm"]
        scan_intervals_defaults["system_faults"] = current_options.system_faults_scan_interval if current_options.system_faults_scan_interval is not None else scan_intervals_defaults["system_faults"]
        scan_intervals_defaults["panel_log_events"] = current_options.panel_log_events_scan_interval if current_options.panel_log_events_scan_interval is not None else scan_intervals_defaults["panel_log_events"]

    scan_interval_fields: dict = {
        vol.Required(
            OptionsKey.ScanIntervals.ZONES,
            default = scan_intervals_defaults["zones"],
        ): NumberSelector(NumberSelectorConfig(
            min = SCAN_INTERVAL_MIN,
            max = SCAN_INTERVAL_MAX,
            mode = NumberSelectorMode.BOX,
            unit_of_measurement = "ms",
        )),
        vol.Required(
            OptionsKey.ScanIntervals.PARTITIONS,
            default = scan_intervals_defaults["partitions"],
        ): NumberSelector(NumberSelectorConfig(
            min = SCAN_INTERVAL_MIN,
            max = SCAN_INTERVAL_MAX,
            mode = NumberSelectorMode.BOX,
            unit_of_measurement = "ms",
        )),
        vol.Required(
            OptionsKey.ScanIntervals.OUTPUTS,
            default = scan_intervals_defaults["outputs"],
        ): NumberSelector(NumberSelectorConfig(
            min = SCAN_INTERVAL_MIN,
            max = SCAN_INTERVAL_MAX,
            mode = NumberSelectorMode.BOX,
            unit_of_measurement = "ms",
        )),
    }

    if has_primelan:
        scan_interval_fields[vol.Required(
            OptionsKey.ScanIntervals.SCENARIOS,
            default = scan_intervals_defaults["scenarios"],
        )] = NumberSelector(NumberSelectorConfig(
            min = SCAN_INTERVAL_MIN,
            max = SCAN_INTERVAL_MAX,
            mode = NumberSelectorMode.BOX,
            unit_of_measurement = "ms",
        ))

        scan_interval_fields[vol.Required(
            OptionsKey.ScanIntervals.GSM,
            default = scan_intervals_defaults["gsm"],
        )] = NumberSelector(NumberSelectorConfig(
            min = SCAN_INTERVAL_MIN,
            max = SCAN_INTERVAL_MAX,
            mode = NumberSelectorMode.BOX,
            unit_of_measurement = "ms",
        ))

        scan_interval_fields[vol.Required(
            OptionsKey.ScanIntervals.SYSTEM_FAULTS,
            default = scan_intervals_defaults["system_faults"],
        )] = NumberSelector(NumberSelectorConfig(
            min = SCAN_INTERVAL_MIN,
            max = SCAN_INTERVAL_MAX,
            mode = NumberSelectorMode.BOX,
            unit_of_measurement = "ms",
        ))

        scan_interval_fields[vol.Required(
            OptionsKey.ScanIntervals.PANEL_LOG_EVENTS,
            default = scan_intervals_defaults["panel_log_events"],
        )] = NumberSelector(NumberSelectorConfig(
            min = SCAN_INTERVAL_MIN,
            max = SCAN_INTERVAL_MAX,
            mode = NumberSelectorMode.BOX,
            unit_of_measurement = "ms",
        ))

    if not as_section:
        return scan_interval_fields
    return {
        vol.Required("scan_intervals"): section(vol.Schema(scan_interval_fields)),
    }


def build_general_params_schema(
        *,
        has_native: bool,
        has_primelan: bool,
        current_options: ConfigOptions | None = None,
) -> dict:
    """The non-interval general parameters: hide unused areas, show hidden
    outputs (both backends) and the log fetch limit (PrimeLAN)."""
    schema: dict = {}
    schema[vol.Required(
        OptionsKey.HIDE_UNUSED_PARTITIONS,
        default = bool(current_options.hide_unused_partitions) if current_options else True,
    )] = bool
    # Optional extra alarm panel protected by a PIN (code required to disarm).
    schema[vol.Required(
        OptionsKey.ALARM_PIN_PANEL,
        default = bool(current_options.alarm_pin_panel) if current_options else False,
    )] = bool
    schema[vol.Optional(
        OptionsKey.ALARM_PIN,
        description = {
            "suggested_value": current_options.alarm_pin if current_options else None
        },
    )] = TextSelector(TextSelectorConfig(type = TextSelectorType.PASSWORD))
    if has_primelan:
        # Realtime push is driven by the panel's outbound HTTP ("Gestione Api
        # Http in uscita"), which is a PrimeLAN Web API feature -- so only offer
        # it when PrimeLAN is configured.
        schema[vol.Required(
            OptionsKey.REALTIME_PUSH,
            default = bool(current_options.realtime_push) if current_options else False,
        )] = bool
        schema[vol.Required(
            OptionsKey.WEBHOOK_ALLOW_REMOTE,
            default = bool(current_options.webhook_allow_remote) if current_options else False,
        )] = bool
    if has_native and has_primelan:
        schema[vol.Required(
            OptionsKey.SHOW_HIDDEN_OUTPUTS,
            default = bool(current_options.show_hidden_outputs) if current_options else False,
        )] = bool
    if has_primelan:
        fetch = (
            current_options.panel_log_events_fetch_limit
            if current_options and current_options.panel_log_events_fetch_limit is not None
            else PANEL_LOG_EVENTS_FETCH_LIMIT_DEFAULT
        )
        schema[vol.Required(
            OptionsKey.PANEL_LOG_EVENTS_FETCH_LIMIT,
            default = fetch,
        )] = NumberSelector(NumberSelectorConfig(
            min = PANEL_LOG_EVENTS_FETCH_LIMIT_MIN,
            max = PANEL_LOG_EVENTS_FETCH_LIMIT_MAX,
            mode = NumberSelectorMode.BOX,
        ))
    if has_native:
        # Experimental, last in the form: Native closed across the panel's cycle.
        schema[vol.Required(
            OptionsKey.NATIVE_CYCLE_PAUSE,
            default = (
                bool(current_options.native_cycle_pause) if current_options
                else NATIVE_CYCLE_PAUSE_DEFAULT
            ),
        )] = bool
        for key, current, default in (
                (OptionsKey.NATIVE_CYCLE_PAUSE_BEFORE,
                 current_options.native_cycle_pause_before if current_options else None,
                 NATIVE_CYCLE_PAUSE_BEFORE_DEFAULT),
                (OptionsKey.NATIVE_CYCLE_PAUSE_AFTER,
                 current_options.native_cycle_pause_after if current_options else None,
                 NATIVE_CYCLE_PAUSE_AFTER_DEFAULT),
        ):
            schema[vol.Required(
                key, default = current if current is not None else default,
            )] = NumberSelector(NumberSelectorConfig(
                min = NATIVE_CYCLE_PAUSE_MIN,
                max = NATIVE_CYCLE_PAUSE_MAX,
                step = 0.5,
                unit_of_measurement = "s",
                mode = NumberSelectorMode.BOX,
            ))
    return schema


def build_options_schema(
        *,
        has_native: bool,
        has_primelan: bool,
        current_options: ConfigOptions | None = None,
        migrating_from_native: bool | None = None,
        migrating_from_primelan: bool | None = None,
        shutters: list[int] | None = None,
        output_choices: list[tuple[int, str]] | None = None,
        module_names: dict[int, str] | None = None,
        partitions: list[tuple[int, str]] | None = None,
        include_alarm: bool = True,
        include_shutters: bool = True,
) -> dict:
    """Build the options schema, only including fields the active backend(s) support.

    ``include_alarm=False`` leaves out the alarm-panel fields (scenario map and
    the per-command blocks): the options flow configures those through its own
    menu/step sub-flow instead of this single form.

    ``include_shutters=False`` leaves out the per-shutter sections: the options
    flow moves those to a dedicated "Tapparelle" step, shown only when the panel
    actually has shutter modules.

    GSM / system faults / log events (and their fetch limit) are
    PrimeLAN-exclusive today, so they're omitted entirely when PrimeLAN
    isn't configured for this entry.
    """
    schema: dict = dict(build_scan_intervals_schema(
        has_native = has_native,
        has_primelan = has_primelan,
        current_options = current_options,
        migrating_from_native = migrating_from_native,
        migrating_from_primelan = migrating_from_primelan,
    ))

    panel_log_events_fetch_limit = (
        current_options.panel_log_events_fetch_limit
        if current_options and current_options.panel_log_events_fetch_limit is not None
        else PANEL_LOG_EVENTS_FETCH_LIMIT_DEFAULT
    )

    # Areas never configured keep the panel's default name ("AREA 013"...).
    schema[vol.Required(
        OptionsKey.HIDE_UNUSED_PARTITIONS,
        default = bool(current_options.hide_unused_partitions) if current_options else True,
    )] = bool

    if has_native and has_primelan:
        # Native sees every output of the panel; PrimeLAN only those enabled
        # for its user. Off = show only the PrimeLAN ones (like the panel apps).
        schema[vol.Required(
            OptionsKey.SHOW_HIDDEN_OUTPUTS,
            default = bool(current_options.show_hidden_outputs) if current_options else False,
        )] = bool

    current_alarm = (current_options.alarm_panel if current_options else None) or {}

    if include_alarm and has_primelan and not has_native:
        # PrimeLAN-only alarm panel: one scenario id per command, like the
        # Sdomotica panel. Empty fields = command not offered.
        alarm_fields: dict = {}
        for key in OptionsKey.AlarmPanel.ALL:
            marker = vol.Optional(key, description = {"suggested_value": current_alarm.get(key)})
            alarm_fields[marker] = NumberSelector(NumberSelectorConfig(
                min = 0,
                max = 49,
                mode = NumberSelectorMode.BOX,
            ))
        schema[vol.Optional(OptionsKey.ALARM_PANEL)] = section(
            vol.Schema(alarm_fields), {"collapsed": not current_alarm},
        )

    # Per-command blocks. One block per command; shown whenever Native is
    # available. With both backends each block also offers a Scenario field:
    # if set, the command runs that scenario (keeps the panel's checks);
    # if empty, it arms the partitions below (forced). Disarm = all off.
    if include_alarm and has_native:
        current_native = (current_options.alarm_panel_native if current_options else None) or {}
        native_output_options = [
            SelectOptionDict(value = str(oid), label = label)
            for oid, label in (output_choices or [])
        ]
        # One single-select per partition: a partition can only have ONE mode per
        # command, so a double state is impossible by construction. Localised via
        # translation_key (selector.partition_mode.*).
        def _mode_select():
            return SelectSelector(SelectSelectorConfig(
                options = [
                    OptionsKey.AlarmPanel.NATIVE_MODE_OFF,
                    *OptionsKey.AlarmPanel.NATIVE_MODES,  # total / stay / instant
                ],
                translation_key = "partition_mode",
                mode = SelectSelectorMode.DROPDOWN,
            ))

        def _output_select():
            return SelectSelector(SelectSelectorConfig(
                options = native_output_options,
                multiple = True,
                mode = SelectSelectorMode.DROPDOWN,
            ))

        def _method_field(fields: dict, command: str) -> None:
            # Only with both backends: declare whether this command uses the
            # scenario field or the partition rows. The extractor honours it, so
            # the fields of the other method are simply ignored (HA can't hide
            # them in a single form).
            if not has_primelan:
                return
            if current_alarm.get(command) is not None:
                default = OptionsKey.PanelType.SCENARI
            elif (current_native.get(command) or {}):
                default = OptionsKey.PanelType.AREE
            else:
                default = OptionsKey.PanelType.SCENARI
            fields[vol.Required(
                OptionsKey.AlarmPanel.SCENARIO_FIELD + "_method",
                default = default,
            )] = SelectSelector(SelectSelectorConfig(
                options = [OptionsKey.PanelType.SCENARI, OptionsKey.PanelType.AREE],
                translation_key = "alarm_method",
                mode = SelectSelectorMode.LIST,
            ))

        def _scenario_field(fields: dict, command: str) -> None:
            # Only with both backends: a scenario id for this command (hybrid).
            if not has_primelan:
                return
            marker = vol.Optional(
                OptionsKey.AlarmPanel.SCENARIO_FIELD,
                description = {"suggested_value": current_alarm.get(command)},
            )
            fields[marker] = NumberSelector(NumberSelectorConfig(
                min = 0, max = 49, mode = NumberSelectorMode.BOX,
            ))

        def _current_mode(command_modes: dict, pid: int) -> str:
            for mode in OptionsKey.AlarmPanel.NATIVE_MODES:
                if pid in (command_modes.get(mode) or []):
                    return mode
            return OptionsKey.AlarmPanel.NATIVE_MODE_OFF

        partition_list = list(partitions or [])

        for command in OptionsKey.AlarmPanel.NATIVE_ARMS:
            command_modes = current_native.get(command, {}) or {}
            has_scenario = has_primelan and current_alarm.get(command) is not None
            fields = {}
            _method_field(fields, command)
            _scenario_field(fields, command)
            # A row per partition, keyed by its label (shown as the field name);
            # default = its current mode, or "disarmed".
            for pid, label in partition_list:
                marker = vol.Optional(label, default = _current_mode(command_modes, pid))
                fields[marker] = _mode_select()
            out_marker = vol.Optional(
                OptionsKey.AlarmPanel.NATIVE_OUTPUT,
                description = {"suggested_value": [
                    str(o) for o in command_modes.get(OptionsKey.AlarmPanel.NATIVE_OUTPUT, [])
                ]},
            )
            fields[out_marker] = _output_select()
            schema[vol.Optional(command)] = section(
                vol.Schema(fields), {"collapsed": not (command_modes or has_scenario)},
            )

        # Disarm has no arming modes: a scenario (both backends) + outputs only.
        disarm_current = current_native.get(OptionsKey.AlarmPanel.DISARM, {}) or {}
        disarm_has_scenario = has_primelan and current_alarm.get(OptionsKey.AlarmPanel.DISARM) is not None
        disarm_fields = {}
        _method_field(disarm_fields, OptionsKey.AlarmPanel.DISARM)
        _scenario_field(disarm_fields, OptionsKey.AlarmPanel.DISARM)
        disarm_marker = vol.Optional(
            OptionsKey.AlarmPanel.NATIVE_OUTPUT,
            description = {"suggested_value": [
                str(o) for o in disarm_current.get(OptionsKey.AlarmPanel.NATIVE_OUTPUT, [])
            ]},
        )
        disarm_fields[disarm_marker] = _output_select()
        schema[vol.Optional(OptionsKey.AlarmPanel.DISARM)] = section(
            vol.Schema(disarm_fields),
            {"collapsed": not (disarm_current or disarm_has_scenario)},
        )

    if has_primelan:
        schema[vol.Required(
            OptionsKey.PANEL_LOG_EVENTS_FETCH_LIMIT,
            default = panel_log_events_fetch_limit,
        )] = NumberSelector(NumberSelectorConfig(
            min = PANEL_LOG_EVENTS_FETCH_LIMIT_MIN,
            max = PANEL_LOG_EVENTS_FETCH_LIMIT_MAX,
            mode = NumberSelectorMode.BOX,
        ))

    # Roller shutters: one section per shutter (see build_shutters_schema).
    if include_shutters:
        schema.update(build_shutters_schema(
            shutters = shutters,
            output_choices = output_choices,
            module_names = module_names,
            current_options = current_options,
        ))
    return schema


def build_shutters_schema(
        *,
        shutters: list[int] | None = None,
        output_choices: list[tuple[int, str]] | None = None,
        module_names: dict[int, str] | None = None,
        current_options: ConfigOptions | None = None,
) -> dict:
    """One section per shutter, titled with the shutter's name (read from the
    panel), each holding Open / Close (and optional 25/50/75 %) dropdowns that
    list the panel's command outputs. The Smart module runs the full travel from
    a single trigger, so HA just pulses the chosen command output; the module has
    no stop, so none is offered.

    Returns an empty schema when the panel has no shutter modules.
    """
    schema: dict = {}
    if not (shutters and output_choices):
        return schema
    option_items = [
        SelectOptionDict(value = str(oid), label = name) for oid, name in output_choices
    ]
    current_map = (current_options.shutter_commands if current_options else None) or {}
    names = module_names or {}
    for module in shutters:
        cur = current_map.get(str(module)) or current_map.get(module) or {}
        section_key = shutter_section_key(module, names.get(module))
        fields: dict = {}
        for kind in SHUTTER_COMMAND_FIELDS:
            value = cur.get(kind)
            fields[vol.Optional(
                kind,
                description = {"suggested_value": str(value)} if value not in (None, "") else {},
            )] = SelectSelector(SelectSelectorConfig(
                options = option_items,
                mode = SelectSelectorMode.DROPDOWN,
            ))
        schema[vol.Optional(section_key)] = section(
            vol.Schema(fields), {"collapsed": True},
        )
    return schema


def shutter_section_key(module: int, name: str | None) -> str:
    """Options-form section key for a shutter: shown as the section title.

    HA renders the raw key when there's no translation, so using the panel's
    module name makes each shutter's block self-explanatory. The module number
    is kept as a prefix so the mapping can be stored by module and two shutters
    can never collide on the same name.
    """
    label = (name or "").strip() or f"Tapparella {module:02d}"
    return f"{module:02d} · {label}"


# ----------------------------------------------------------------------
# Alarm-panel per-command sub-forms (menu/step options flow)
# ----------------------------------------------------------------------

def _mode_select() -> SelectSelector:
    """Single-choice mode for one partition (localised via selector.partition_mode)."""
    return SelectSelector(SelectSelectorConfig(
        options = [
            OptionsKey.AlarmPanel.NATIVE_MODE_OFF,
            *OptionsKey.AlarmPanel.NATIVE_MODES,
        ],
        translation_key = "partition_mode",
        mode = SelectSelectorMode.DROPDOWN,
    ))


def build_alarm_method_schema(default_method: str) -> dict:
    """One Required 'method' selector: scenario vs partitions (both backends)."""
    return {
        vol.Required(
            OptionsKey.AlarmPanel.SCENARIO_FIELD + "_method",  # distinct key
            default = default_method,
        ): SelectSelector(SelectSelectorConfig(
            options = [OptionsKey.PanelType.SCENARI, OptionsKey.PanelType.AREE],
            translation_key = "alarm_method",
            mode = SelectSelectorMode.LIST,
        ))
    }


def build_alarm_scenario_schema(
        current_scenario: int | None,
        scenarios: list[tuple[int, str]] | None = None,
) -> dict:
    """One scenario field for a command.

    When the panel's scenario list is available it's a name dropdown (value =
    scenario id, label = the panel's scenario name); otherwise it falls back to
    a plain id box (0-49).
    """
    if scenarios:
        options = [
            SelectOptionDict(value = str(sid), label = (label or f"Scenario {sid}"))
            for sid, label in scenarios
        ]
        return {
            vol.Optional(
                OptionsKey.AlarmPanel.SCENARIO_FIELD,
                description = {"suggested_value": str(current_scenario) if current_scenario is not None else None},
            ): SelectSelector(SelectSelectorConfig(
                options = options, mode = SelectSelectorMode.DROPDOWN,
            ))
        }
    return {
        vol.Optional(
            OptionsKey.AlarmPanel.SCENARIO_FIELD,
            description = {"suggested_value": current_scenario},
        ): NumberSelector(NumberSelectorConfig(min = 0, max = 49, mode = NumberSelectorMode.BOX))
    }


def build_alarm_partitions_schema(
        command: str,
        current_cmd: dict | None,
        partitions: list[tuple[int, str]] | None,
        output_choices: list[tuple[int, str]] | None,
) -> dict:
    """Per-partition mode selects (arm commands) + outputs for one command.

    Disarm has no arming modes, only the outputs it may switch on.
    """
    current_cmd = current_cmd or {}
    fields: dict = {}

    def _current_mode(pid: int) -> str:
        for mode in OptionsKey.AlarmPanel.NATIVE_MODES:
            if pid in (current_cmd.get(mode) or []):
                return mode
        return OptionsKey.AlarmPanel.NATIVE_MODE_OFF

    if command != OptionsKey.AlarmPanel.DISARM:
        for pid, label in (partitions or []):
            fields[vol.Optional(label, default = _current_mode(pid))] = _mode_select()

    output_options = [
        SelectOptionDict(value = str(oid), label = label)
        for oid, label in (output_choices or [])
    ]
    fields[vol.Optional(
        OptionsKey.AlarmPanel.NATIVE_OUTPUT,
        description = {"suggested_value": [
            str(o) for o in current_cmd.get(OptionsKey.AlarmPanel.NATIVE_OUTPUT, [])
        ]},
    )] = SelectSelector(SelectSelectorConfig(
        options = output_options, multiple = True, mode = SelectSelectorMode.DROPDOWN,
    ))
    return fields