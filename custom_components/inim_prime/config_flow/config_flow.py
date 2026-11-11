from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback

from .connection_test import test_native_connection, test_native_connection_and_get_serial, test_primelan_connection
from .const import (
    DataKey,
    OptionsKey,
)
from .models import NativeConfig, PrimelanConfig, ConfigData, ConfigOptions, clean_alarm_panel, extract_hybrid_sections
from .options_flow import InimPrimeOptionsFlowHandler
from .schemas import build_connection_schema, build_options_schema
from ..const import (
    DOMAIN,
    ZONES_SCAN_INTERVAL_NATIVE_DEFAULT,
    ZONES_SCAN_INTERVAL_PRIMELAN_DEFAULT,
    PARTITIONS_SCAN_INTERVAL_NATIVE_DEFAULT,
    PARTITIONS_SCAN_INTERVAL_PRIMELAN_DEFAULT,
    GSM_SCAN_INTERVAL_PRIMELAN_DEFAULT,
    SYSTEM_FAULTS_SCAN_INTERVAL_PRIMELAN_DEFAULT,
    PANEL_LOG_EVENTS_SCAN_INTERVAL_PRIMELAN_DEFAULT,
    SCENARIOS_SCAN_INTERVAL_PRIMELAN_DEFAULT,
    PANEL_LOG_EVENTS_FETCH_LIMIT_DEFAULT,
    PRIMELAN_SCAN_INTERVAL_BOTH_DEFAULT,
)
from ..entry_data import get_entry_data, get_entry_options

_LOGGER = logging.getLogger(__name__)


def _describe_error(err: BaseException) -> str:
    """Short, user-readable reason for a failed connection test."""
    if isinstance(err, (asyncio.TimeoutError, TimeoutError)):
        return "timeout (panel not reachable on that address/port, or wrong protocol settings)"
    if isinstance(err, ConnectionRefusedError):
        return "connection refused (port closed)"
    text = str(err).strip()
    return f"{type(err).__name__}: {text}" if text else type(err).__name__


async def _run_connection_tests(flow_data: ConfigData, *, get_serial: bool) -> tuple[dict[str, str], dict[str, str]]:
    """Test each configured backend separately.

    Returns (errors, placeholders) so the form can say which part failed
    and why.
    """
    errors: dict[str, str] = {}
    placeholders: dict[str, str] = {"native_error": "", "primelan_error": "", "status": ""}

    if flow_data.native is not None:
        started = time.monotonic()
        try:
            if get_serial:
                flow_data.serial_number = await test_native_connection_and_get_serial(flow_data.native, flow_data.host)
            else:
                await test_native_connection(flow_data.native, flow_data.host)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("INIM Prime: Native connection test to %s:%s failed", flow_data.host, flow_data.native.port, exc_info = True)
            placeholders["native_error"] = _describe_error(err)
        _LOGGER.debug("Connection test: Native took %.2fs", time.monotonic() - started)

    if flow_data.primelan is not None:
        started = time.monotonic()
        try:
            await test_primelan_connection(flow_data.primelan, flow_data.host)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("INIM Prime: PrimeLAN connection test to %s failed", flow_data.host, exc_info = True)
            placeholders["primelan_error"] = _describe_error(err)
        _LOGGER.debug("Connection test: PrimeLAN took %.2fs", time.monotonic() - started)

    if placeholders["native_error"] or placeholders["primelan_error"]:
        parts = []
        if flow_data.native is not None:
            parts.append("Native: " + (f"FAILED ({placeholders['native_error']})" if placeholders["native_error"] else "OK"))
        if flow_data.primelan is not None:
            parts.append("PrimeLAN: " + (f"FAILED ({placeholders['primelan_error']})" if placeholders["primelan_error"] else "OK"))
        placeholders["status"] = " | ".join(parts)
        errors["base"] = "connection_failed"
    return errors, placeholders


@config_entries.HANDLERS.register(DOMAIN)
class InimPrimeConfigFlow(config_entries.ConfigFlow, domain = DOMAIN):
    """Handle a config flow for INIM Prime integration.

    Step order:
      user (menu: native_only / primelan_only / both)
        -> [primelan_only] serial_number (manual entry)
        -> connection (shared host + per-backend options; tests the
           connection(s); Native path retrieves the serial number
           automatically here instead of asking for it)
        -> options (scan intervals, defaults adjusted for whether
           Native is active)
        -> create_entry

    self._data accumulates everything gathered so far and is used both
    to build the final entry and to pre-fill forms if the user is sent
    back to a previous step (the HA frontend keeps step history and
    lets the user navigate back; keeping every step's defaults sourced
    from self._data means resubmitting or coming back doesn't lose data).
    """
    VERSION = 2
    MINOR_VERSION = 0

    def __init__(self):
        self._options: ConfigOptions = ConfigOptions()
        self._data: ConfigData = ConfigData()
        self._migrating_from_native: bool | None = None
        self._migrating_from_primelan: bool | None = None

    def _update_migration_flags(self):
        if self._data.use_native:
            self._migrating_from_native = True
        elif self._data.use_primelan:
            self._migrating_from_primelan = True

    # ------------------------------------------------------------------
    # Step 1: backend choice
    # ------------------------------------------------------------------
    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        return self.async_show_menu(
            step_id = "user",
            menu_options = ["native_only", "primelan_only", "both"],
        )

    async def async_step_native_only(self, user_input: dict[str, Any] | None = None):
        self._data.use_native = True
        return await self.async_step_connection()

    async def async_step_primelan_only(self, user_input: dict[str, Any] | None = None):
        self._data.use_primelan = True
        return await self.async_step_serial_number()

    async def async_step_both(self, user_input: dict[str, Any] | None = None):
        self._data.use_native = True
        self._data.use_primelan = True
        return await self.async_step_connection()

    # ------------------------------------------------------------------
    # Step 2 (PrimeLAN-only path): manual serial number
    # ------------------------------------------------------------------
    async def async_step_serial_number(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            self._data.serial_number = user_input[DataKey.SERIAL_NUMBER].strip()
            return await self.async_step_connection()

        schema = vol.Schema(
            {
                vol.Required(DataKey.SERIAL_NUMBER, default = self._data.serial_number): str,
            }
        )
        return self.async_show_form(step_id = "serial_number", data_schema = schema)

    # ------------------------------------------------------------------
    # Step 3: connection details (shared host + per-backend sections)
    # ------------------------------------------------------------------
    async def async_step_connection(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {"native_error": "", "primelan_error": "", "status": ""}

        if user_input is not None:
            self._data.host = user_input[DataKey.HOST].strip()
            assert self._data.host is not None

            if self._data.use_native:
                native_conf = user_input[DataKey.NATIVE]
                self._data.native = NativeConfig(
                    port = native_conf[DataKey.Native.PORT],
                    password = native_conf[DataKey.Native.PASSWORD],
                    # With PrimeLAN configured the panel has a PrimeLAN card -> outer frame.
                    use_outer_frame = True if self._data.use_primelan else native_conf[DataKey.Native.USE_OUTER_FRAME],
                    pin = native_conf[DataKey.Native.PIN] if native_conf[DataKey.Native.USE_CUSTOM_PIN] else None,
                )

            if self._data.use_primelan:
                primelan_conf = user_input[DataKey.PRIMELAN]
                self._data.primelan = PrimelanConfig(
                    api_key = primelan_conf[DataKey.Primelan.API_KEY],
                    use_https = primelan_conf[DataKey.Primelan.USE_HTTPS],
                )

            # Native present -> serial number is retrieved here, never asked.
            errors, placeholders = await _run_connection_tests(self._data, get_serial = True)
            if not errors:
                await self.async_set_unique_id(self._data.serial_number)
                self._abort_if_unique_id_configured()
                return self._create_entry_with_defaults()

        schema = vol.Schema(
            build_connection_schema(
                flow_data = self._data,
                prefill_secrets = True,
            )
        )

        return self.async_show_form(
            step_id = "connection",
            data_schema = schema,
            errors = errors,
            description_placeholders = placeholders,
        )

    # ------------------------------------------------------------------
    # Finish setup: create the entry with default options
    # ------------------------------------------------------------------
    # The first setup only collects the connection. Everything else (scan
    # intervals, alarm panel, roller shutters, the toggles) has sensible
    # defaults and is tuned afterwards from the integration's Options --
    # where the panel's scenario/partition names and shutter modules are
    # already loaded, so the forms there are far more useful than at setup.
    def _create_entry_with_defaults(self):
        # Keep the previous form default of hiding unused areas.
        self._options.hide_unused_partitions = True
        # Connection-only setup skips the scan-interval form, but the integration
        # needs those intervals present to build its coordinators (zones and
        # partitions always; the PrimeLAN-only pollers when PrimeLAN is used).
        # Seed sensible defaults here; the user tunes them later in Options.
        native = self._data.use_native
        primelan = self._data.use_primelan
        self._options.zones_scan_interval = (
            ZONES_SCAN_INTERVAL_NATIVE_DEFAULT if native
            else ZONES_SCAN_INTERVAL_PRIMELAN_DEFAULT
        )
        self._options.partitions_scan_interval = (
            PARTITIONS_SCAN_INTERVAL_NATIVE_DEFAULT if native
            else PARTITIONS_SCAN_INTERVAL_PRIMELAN_DEFAULT
        )
        if primelan:
            # With both backends the live data comes from Native, so the
            # PrimeLAN-only diagnostics poll slower by default.
            diag_default = (
                PRIMELAN_SCAN_INTERVAL_BOTH_DEFAULT if native
                else SYSTEM_FAULTS_SCAN_INTERVAL_PRIMELAN_DEFAULT
            )
            self._options.gsm_scan_interval = (
                PRIMELAN_SCAN_INTERVAL_BOTH_DEFAULT if native
                else GSM_SCAN_INTERVAL_PRIMELAN_DEFAULT
            )
            self._options.system_faults_scan_interval = diag_default
            self._options.panel_log_events_scan_interval = (
                PRIMELAN_SCAN_INTERVAL_BOTH_DEFAULT if native
                else PANEL_LOG_EVENTS_SCAN_INTERVAL_PRIMELAN_DEFAULT
            )
            self._options.scenarios_scan_interval = SCENARIOS_SCAN_INTERVAL_PRIMELAN_DEFAULT
            self._options.panel_log_events_fetch_limit = PANEL_LOG_EVENTS_FETCH_LIMIT_DEFAULT
        assert self._data.serial_number is not None
        return self.async_create_entry(
            title = f"INIM Prime ({self._data.serial_number})",
            data = self._data.to_entry_data(),
            options = self._options.to_options_dict(),
        )

    @staticmethod
    @callback
    def async_get_options_flow(
            config_entry: ConfigEntry,
    ) -> InimPrimeOptionsFlowHandler:
        return InimPrimeOptionsFlowHandler()



    # ==================================================================
    # Reconfigure flow
    # ==================================================================
    # Entry point invoked by HA when the user picks "Reconfigure" on an
    # existing entry. Mirrors the initial-setup flow above, minus the
    # serial_number step: the panel is already identified, so its serial
    # number is carried over from the existing entry and never re-asked
    # or re-fetched.
    # ------------------------------------------------------------------

    def _init_data_from_entry(self, entry: ConfigEntry) -> None:
        """Pre-fill self._data with the entry's current configuration."""
        data = get_entry_data(entry)
        self._data.serial_number = data["serial_number"]
        self._data.host = data["host"]

        native_conf = data.get("native")
        if native_conf:
            self._data.use_native = True
            self._data.native = NativeConfig(
                port = native_conf["port"],
                password = native_conf["password"],
                use_outer_frame = native_conf["use_outer_frame"],
                pin = native_conf["pin"],
            )

        primelan_conf = data.get("primelan")
        if primelan_conf:
            self._data.use_primelan = True
            self._data.primelan = PrimelanConfig(
                api_key = primelan_conf["api_key"],
                use_https = primelan_conf["use_https"],
            )

    def _init_options_from_entry(self, entry: ConfigEntry) -> None:
        """Pre-fill self._options with the entry's current options."""
        options = get_entry_options(entry)
        scan_intervals = options["scan_intervals"]

        self._options.zones_scan_interval = scan_intervals.get("zones")
        self._options.partitions_scan_interval = scan_intervals.get("partitions")
        self._options.gsm_scan_interval = scan_intervals.get("gsm")
        self._options.system_faults_scan_interval = scan_intervals.get("system_faults")
        self._options.panel_log_events_scan_interval = scan_intervals.get("panel_log_events")
        self._options.outputs_scan_interval = scan_intervals.get("outputs")
        self._options.scenarios_scan_interval = scan_intervals.get("scenarios")
        self._options.panel_log_events_fetch_limit = options.get("panel_log_events_fetch_limit")
        self._options.alarm_panel = options.get("alarm_panel")
        self._options.show_hidden_outputs = options.get("show_hidden_outputs")
        self._options.hide_unused_partitions = options.get("hide_unused_partitions")

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None):
        # Populate self._data/self._options from the existing entry only
        # once (first time this flow instance reaches this step).
        if self._data.serial_number is None:
            reconfigure_entry = self._get_reconfigure_entry()
            self._init_data_from_entry(reconfigure_entry)
            self._init_options_from_entry(reconfigure_entry)
            self._update_migration_flags()
        return self.async_show_menu(
            step_id = "reconfigure",
            menu_options = ["reconfigure_native_only", "reconfigure_primelan_only", "reconfigure_both"],
        )

    async def async_step_reconfigure_native_only(self, user_input: dict[str, Any] | None = None):
        self._data.use_native = True
        self._data.use_primelan = False
        self._data.primelan = None
        return await self.async_step_reconfigure_connection()

    async def async_step_reconfigure_primelan_only(self, user_input: dict[str, Any] | None = None):
        self._data.use_native = False
        self._data.use_primelan = True
        self._data.native = None
        return await self.async_step_reconfigure_connection()

    async def async_step_reconfigure_both(self, user_input: dict[str, Any] | None = None):
        self._data.use_native = True
        self._data.use_primelan = True
        return await self.async_step_reconfigure_connection()

    async def async_step_reconfigure_connection(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {"native_error": "", "primelan_error": "", "status": ""}

        if user_input is not None:
            self._data.host = user_input[DataKey.HOST].strip()
            assert self._data.host is not None

            if self._data.use_native:

                native_conf = user_input[DataKey.NATIVE]
                new_password = native_conf[DataKey.Native.PASSWORD] if native_conf.get(DataKey.Native.PASSWORD) is not None else self._data.native.password
                new_pin = None
                if native_conf.get(DataKey.Native.USE_CUSTOM_PIN):
                    new_pin = native_conf[DataKey.Native.PIN] if native_conf.get(DataKey.Native.PIN) is not None else self._data.native.pin

                self._data.native = NativeConfig(
                    port = native_conf[DataKey.Native.PORT],
                    password = new_password,
                    # With PrimeLAN configured the panel has a PrimeLAN card -> outer frame.
                    use_outer_frame = True if self._data.use_primelan else native_conf[DataKey.Native.USE_OUTER_FRAME],
                    pin = new_pin,
                )

            if self._data.use_primelan:
                primelan_conf = user_input[DataKey.PRIMELAN]
                self._data.primelan = PrimelanConfig(
                    api_key = primelan_conf[DataKey.Primelan.API_KEY] if primelan_conf.get(DataKey.Primelan.API_KEY) is not None else self._data.primelan.api_key,
                    use_https = primelan_conf[DataKey.Primelan.USE_HTTPS],
                )

            # Reconfigure never touches the serial number, only the
            # connection itself is verified here.
            errors, placeholders = await _run_connection_tests(self._data, get_serial = False)
            if not errors:
                return self._finish_reconfigure()

        schema = vol.Schema(
            build_connection_schema(
                flow_data = self._data,
            )
        )

        return self.async_show_form(
            step_id = "reconfigure_connection",
            data_schema = schema,
            errors = errors,
            description_placeholders = placeholders,
        )

    def _finish_reconfigure(self):
        """Save the new connection and reload; every other option is kept.

        Reconfigure is about the connection and the integration mode only (the
        rest is tuned from Options), so the stored options are left as they are
        and just adjusted to the mode: defaults for what a newly enabled backend
        needs, and no leftovers from a backend that is no longer used.
        """
        entry = self._get_reconfigure_entry()
        native = self._data.use_native
        primelan = self._data.use_primelan
        options = dict(entry.options)
        intervals = dict(options.get("scan_intervals") or {})
        intervals.setdefault("zones", (
            ZONES_SCAN_INTERVAL_NATIVE_DEFAULT if native
            else ZONES_SCAN_INTERVAL_PRIMELAN_DEFAULT
        ))
        intervals.setdefault("partitions", (
            PARTITIONS_SCAN_INTERVAL_NATIVE_DEFAULT if native
            else PARTITIONS_SCAN_INTERVAL_PRIMELAN_DEFAULT
        ))
        if primelan:
            both = PRIMELAN_SCAN_INTERVAL_BOTH_DEFAULT
            intervals.setdefault("gsm", both if native else GSM_SCAN_INTERVAL_PRIMELAN_DEFAULT)
            intervals.setdefault(
                "system_faults", both if native else SYSTEM_FAULTS_SCAN_INTERVAL_PRIMELAN_DEFAULT
            )
            intervals.setdefault(
                "panel_log_events",
                both if native else PANEL_LOG_EVENTS_SCAN_INTERVAL_PRIMELAN_DEFAULT,
            )
            intervals.setdefault("scenarios", SCENARIOS_SCAN_INTERVAL_PRIMELAN_DEFAULT)
            options.setdefault("panel_log_events_fetch_limit", PANEL_LOG_EVENTS_FETCH_LIMIT_DEFAULT)
        else:
            for key in ("gsm", "system_faults", "panel_log_events", "scenarios"):
                intervals.pop(key, None)
            for key in ("panel_log_events_fetch_limit", "alarm_panel", "realtime_push",
                        "webhook_allow_remote"):
                options.pop(key, None)
        if not native:
            options.pop("alarm_panel_native", None)
        options["scan_intervals"] = intervals

        assert self._data.serial_number is not None
        return self.async_update_reload_and_abort(
            entry,
            title = f"INIM Prime ({self._data.serial_number})",
            data = self._data.to_entry_data(),
            options = options,
        )

