import asyncio
from datetime import timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from . import debug_services, native_compat  # noqa: F401  (must run before native clients initialize)
from inim.prime.native.client import Client as NativeClient
from inim.prime.primelan.client import InimPrimeClient as PrimelanClient
from .adapters.native_adapter import NativeAdapter
from .adapters.primelan_adapter import PrimelanAdapter
from .const import (
    DOMAIN,
    INIM_PRIME_DEVICE_MANUFACTURER,
    ZONES_SCAN_INTERVAL_PRIMELAN_DEFAULT,
    PARTITIONS_SCAN_INTERVAL_PRIMELAN_DEFAULT,
    GSM_SCAN_INTERVAL_PRIMELAN_DEFAULT,
    SYSTEM_FAULTS_SCAN_INTERVAL_PRIMELAN_DEFAULT,
    PANEL_LOG_EVENTS_SCAN_INTERVAL_PRIMELAN_DEFAULT,
    PANEL_LOG_EVENTS_FETCH_LIMIT_DEFAULT,
    OUTPUTS_SCAN_INTERVAL_PRIMELAN_DEFAULT,
    OUTPUTS_SCAN_INTERVAL_NATIVE_DEFAULT,
    SCENARIOS_SCAN_INTERVAL_PRIMELAN_DEFAULT,
    TIMERS_SCAN_INTERVAL_DEFAULT,
    THERMOSTATS_SCAN_INTERVAL_DEFAULT,
)
from .coordinators import (
    InimPrimeGSMUpdateCoordinator,
    InimPrimePartitionsUpdateCoordinator,
    InimPrimeZonesUpdateCoordinator,
    InimPrimePanelLogEventsCoordinator,
    InimPrimeSystemFaultsUpdateCoordinator,
    InimPrimeOutputsUpdateCoordinator,
    InimPrimeScenariosUpdateCoordinator,
    InimPrimeTimersUpdateCoordinator,
    InimPrimeThermostatsUpdateCoordinator,
)
from .entry_data import (
    get_entry_options,
    get_entry_data,
    InimPrimeConfigData,
    PrimelanConfigData, InimPrimeOptionsData, ScanIntervalsData,
)
from .gateway import InimPrimeGateway
from .cover import discover_shutters
from .runtime_data import (
    InimPrimeConfigEntry,
    InimPrimeCoordinators,
    InimPrimeRuntimeData,
)

PLATFORMS = [
    "binary_sensor",
    "sensor",
    "switch",
    "select",
    "button",
    "event",
    "light",
    "alarm_control_panel",
    "cover",
]


def _build_gateway(data: InimPrimeConfigData) -> InimPrimeGateway:
    """Construct adapters for whichever backend(s) are configured on this entry.

    Presence of the "native" / "primelan" keys in entry.data is what decides
    whether each backend is active -- there's no separate enable flag.
    """
    native_adapter = None
    primelan_adapter = None

    host = data["host"]
    native_conf = data.get("native")
    primelan_conf = data.get("primelan")

    if native_conf:
        native_client = NativeClient(
            host = host,
            password = native_conf["password"],
            use_outer_frame = native_conf["use_outer_frame"],
            port = native_conf["port"],
            pin = native_conf["pin"],
        )
        native_adapter = NativeAdapter(native_client)


    if primelan_conf:
        primelan_client = PrimelanClient(
            host = host,
            api_key = primelan_conf["api_key"],
            use_https = primelan_conf["use_https"],
        )
        primelan_adapter = PrimelanAdapter(primelan_client)

    return InimPrimeGateway(native = native_adapter, primelan = primelan_adapter)


async def async_setup_entry(hass: HomeAssistant, entry: InimPrimeConfigEntry) -> bool:
    """Set up INIM Prime integration."""
    hass.data.setdefault(DOMAIN, {})

    # Get the data as typed dict
    data = get_entry_data(entry)
    options = get_entry_options(entry)

    # --- Register the panel device ---
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id = entry.entry_id,
        identifiers = {(DOMAIN, data["serial_number"])},
        name = "Inim Prime Panel",
        model = "Prime Panel",
        manufacturer = INIM_PRIME_DEVICE_MANUFACTURER,
        serial_number = data["serial_number"],
    )

    inim_gateway = _build_gateway(data)
    inim_gateway.show_hidden_outputs = bool(entry.options.get("show_hidden_outputs", False))
    inim_gateway.hide_unused_partitions = bool(entry.options.get("hide_unused_partitions", False))
    # The panel can reset the native TCP link (one client at a time) or drop
    # the PrimeLAN HTTP connection. Treat a connect failure as "not ready" so
    # Home Assistant retries with backoff instead of leaving the entry failed.
    try:
        await inim_gateway.connect()
    except (ConnectionError, OSError, asyncio.TimeoutError, TimeoutError) as err:
        raise ConfigEntryNotReady(
            f"Cannot connect to INIM Prime panel yet: {err}"
        ) from err

    ###
    ### Coordinators
    ###

    scan_intervals = options["scan_intervals"]

    zones_coordinator: InimPrimeZonesUpdateCoordinator | None = None
    partitions_coordinator: InimPrimePartitionsUpdateCoordinator | None = None
    system_faults_coordinator: InimPrimeSystemFaultsUpdateCoordinator | None = None
    gsm_coordinator: InimPrimeGSMUpdateCoordinator | None = None
    panel_log_events_coordinator: InimPrimePanelLogEventsCoordinator | None = None
    outputs_coordinator: InimPrimeOutputsUpdateCoordinator | None = None
    scenarios_coordinator: InimPrimeScenariosUpdateCoordinator | None = None
    timers_coordinator: InimPrimeTimersUpdateCoordinator | None = None
    thermostats_coordinator: InimPrimeThermostatsUpdateCoordinator | None = None

    if inim_gateway.supports_zones:
        zones_coordinator = InimPrimeZonesUpdateCoordinator(
            hass = hass,
            update_interval = timedelta(milliseconds = scan_intervals["zones"]),
            entry = entry,
            gateway = inim_gateway,
        )

    if inim_gateway.supports_partitions:
        partitions_coordinator = InimPrimePartitionsUpdateCoordinator(
            hass = hass,
            update_interval = timedelta(milliseconds = scan_intervals["partitions"]),
            entry = entry,
            gateway = inim_gateway,
        )

    if inim_gateway.supports_system_faults:
        system_faults_coordinator = InimPrimeSystemFaultsUpdateCoordinator(
            hass = hass,
            update_interval = timedelta(milliseconds = scan_intervals["system_faults"]),
            entry = entry,
            gateway = inim_gateway,
        )

    if inim_gateway.supports_gsm:
       gsm_coordinator = InimPrimeGSMUpdateCoordinator(
            hass = hass,
            update_interval = timedelta(milliseconds = scan_intervals["gsm"]),
            entry = entry,
            gateway = inim_gateway,
        )

    if inim_gateway.supports_log_events:
        panel_log_events_coordinator = InimPrimePanelLogEventsCoordinator(
            hass = hass,
            update_interval = timedelta(milliseconds = scan_intervals["panel_log_events"]),
            entry = entry,
            gateway = inim_gateway,
        )

    if inim_gateway.supports_outputs:
        default_outputs_interval = (
            OUTPUTS_SCAN_INTERVAL_NATIVE_DEFAULT
            if data.get("native") else OUTPUTS_SCAN_INTERVAL_PRIMELAN_DEFAULT
        )
        outputs_coordinator = InimPrimeOutputsUpdateCoordinator(
            hass = hass,
            update_interval = timedelta(milliseconds = scan_intervals.get("outputs", default_outputs_interval)),
            entry = entry,
            gateway = inim_gateway,
        )

    if inim_gateway.supports_scenarios:
        scenarios_coordinator = InimPrimeScenariosUpdateCoordinator(
            hass = hass,
            update_interval = timedelta(milliseconds = scan_intervals.get("scenarios", SCENARIOS_SCAN_INTERVAL_PRIMELAN_DEFAULT)),
            entry = entry,
            gateway = inim_gateway,
        )

    if inim_gateway.supports_timers:
        timers_coordinator = InimPrimeTimersUpdateCoordinator(
            hass = hass,
            update_interval = timedelta(milliseconds = TIMERS_SCAN_INTERVAL_DEFAULT),
            entry = entry,
            gateway = inim_gateway,
        )

    if zones_coordinator is not None:
        await zones_coordinator.async_config_entry_first_refresh()

    if partitions_coordinator is not None:
        await partitions_coordinator.async_config_entry_first_refresh()

    if system_faults_coordinator is not None:
        await system_faults_coordinator.async_config_entry_first_refresh()

    if gsm_coordinator is not None:
        await gsm_coordinator.async_config_entry_first_refresh()

    if outputs_coordinator is not None:
        await outputs_coordinator.async_config_entry_first_refresh()

    if scenarios_coordinator is not None:
        await scenarios_coordinator.async_config_entry_first_refresh()

    if timers_coordinator is not None:
        await timers_coordinator.async_config_entry_first_refresh()

    if panel_log_events_coordinator is not None:
        await panel_log_events_coordinator.async_startup()
        await panel_log_events_coordinator.async_config_entry_first_refresh()

    # Thermostats are optional and not on every panel. Probe once without
    # blocking setup: keep the coordinator only if the panel actually returns
    # thermostats (the status command fails cleanly on panels without any).
    if inim_gateway.supports_thermostats:
        _thermostats = InimPrimeThermostatsUpdateCoordinator(
            hass = hass,
            update_interval = timedelta(milliseconds = THERMOSTATS_SCAN_INTERVAL_DEFAULT),
            entry = entry,
            gateway = inim_gateway,
        )
        await _thermostats.async_refresh()
        if _thermostats.last_update_success and inim_gateway.thermostats:
            thermostats_coordinator = _thermostats

    # Typed, no hass.data[DOMAIN][entry.entry_id] bookkeeping, no manual
    # cleanup of a dict key on unload -- HA clears runtime_data for us.
    entry.runtime_data = InimPrimeRuntimeData(
        gateway = inim_gateway,
        coordinators = InimPrimeCoordinators(
            zones = zones_coordinator,
            partitions = partitions_coordinator,
            gsm = gsm_coordinator,
            system_faults = system_faults_coordinator,
            panel_log_events = panel_log_events_coordinator,
            outputs = outputs_coordinator,
            scenarios = scenarios_coordinator,
            timers = timers_coordinator,
            thermostats = thermostats_coordinator,
        ),
    )


    await hass.config_entries.async_forward_entry_setups(
        entry = entry,
        platforms = PLATFORMS,
    )

    _link_child_devices(hass, entry)
    debug_services.async_register(hass)
    _remove_stale_output_entities(hass, entry, inim_gateway)
    _remove_stale_timer_entities(hass, entry, inim_gateway)
    _remove_hidden_partition_entities(hass, entry, inim_gateway)
    _remove_hidden_zone_entities(hass, entry, inim_gateway)
    _remove_stale_number_entities(hass, entry)
    _remove_old_grouped_devices(hass, entry)
    _reset_grouped_entity_naming(hass, entry)
    _rename_shutter_entities(hass, entry)

    return True


def _rename_shutter_entities(hass: HomeAssistant, entry: InimPrimeConfigEntry) -> None:
    """Rename shutter cover entity_ids to match the shutter name.

    They used to be derived from the per-shutter device name (e.g.
    cover.tapparella_03_tapparella_03); give them a clean id from the shutter's
    own name instead (cover.tenda_sala). entity_id changes, but existing ones
    were not meaningful anyway."""
    from homeassistant.util import slugify

    prefix = f"{entry.data['serial_number']}_shutter_"
    entity_registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if entity.domain != "cover" or not entity.unique_id.startswith(prefix):
            continue
        name = entity.original_name or entity.name
        if not name:
            continue
        desired = f"cover.{slugify(name)}"
        if entity.entity_id != desired and entity_registry.async_get(desired) is None:
            entity_registry.async_update_entity(entity.entity_id, new_entity_id = desired)


def _reset_grouped_entity_naming(hass: HomeAssistant, entry: InimPrimeConfigEntry) -> None:
    """Clear the stale has_entity_name flag on zone/partition/shutter entities.

    These used has_entity_name=True (per-item devices) before they were grouped.
    Home Assistant keeps the flag in the entity registry, so after the upgrade the
    grouping device name ("Zones"/"Partitions"/"Shutters") is still prepended to
    the friendly name. Force it off here so the name is just the item label."""
    serial_number = entry.data["serial_number"]
    prefixes = (
        f"{serial_number}_zone_",
        f"{serial_number}_partition_",
        f"{serial_number}_shutter_",
    )
    entity_registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if entity.has_entity_name and entity.unique_id.startswith(prefixes):
            entity_registry.async_update_entity(entity.entity_id, has_entity_name = False)


def _remove_old_grouped_devices(hass: HomeAssistant, entry: InimPrimeConfigEntry) -> None:
    """Remove the old per-item devices now that zones, partitions, shutters and
    thermostats each hold their entities on a single grouping device.

    The entities move by unique_id to the group device ({serial}_zones,
    {serial}_partitions, {serial}_shutters, {serial}_thermostats), leaving the
    old per-item devices ({serial}_zone_<id>, _partition_<id>, _shutter_<module>,
    _thermostat_<index>) empty, so drop them from this entry. The group ids end
    in a plural with no trailing "_<number>", so they are never matched here."""
    serial_number = entry.data["serial_number"]
    prefixes = tuple(
        f"{serial_number}_{kind}_"
        for kind in ("zone", "partition", "shutter", "thermostat")
    )
    device_registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        for domain, identifier in device.identifiers:
            if domain != DOMAIN:
                continue
            for prefix in prefixes:
                if identifier.startswith(prefix) and identifier[len(prefix):].isdigit():
                    device_registry.async_update_device(
                        device.id, remove_config_entry_id = entry.entry_id
                    )
                    break


def _remove_hidden_zone_entities(hass: HomeAssistant, entry: InimPrimeConfigEntry, gateway) -> None:
    """Remove entities of zones no longer exposed (e.g. the domotic modules'
    internal T01/T02 terminals, which aren't real zones). Zones share one
    device now, so the stale items are entities, not devices."""
    shown = set(gateway.zones)
    prefix = f"{entry.data['serial_number']}_zone_"
    entity_registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if not entity.unique_id.startswith(prefix):
            continue
        zid = entity.unique_id[len(prefix):].split("_", 1)[0]
        if zid.isdigit() and int(zid) not in shown:
            entity_registry.async_remove(entity.entity_id)


def _remove_stale_number_entities(hass: HomeAssistant, entry: InimPrimeConfigEntry) -> None:
    """Remove the old per-shutter travel-time numbers (the number platform is
    gone now that shutters are command-driven)."""
    entity_registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if entity.domain == "number":
            entity_registry.async_remove(entity.entity_id)


def _remove_hidden_partition_entities(hass: HomeAssistant, entry: InimPrimeConfigEntry, gateway) -> None:
    """Remove entities of partitions hidden as unused. Partitions share one
    device now, so the stale items are entities, not devices."""
    shown = set(gateway.partitions)
    hidden = set(gateway.all_partitions) - shown
    if not hidden:
        return
    prefix = f"{entry.data['serial_number']}_partition_"
    entity_registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if not entity.unique_id.startswith(prefix):
            continue
        pid = entity.unique_id[len(prefix):].split("_", 1)[0]
        if pid.isdigit() and int(pid) in hidden:
            entity_registry.async_remove(entity.entity_id)


def _remove_stale_timer_entities(hass: HomeAssistant, entry: InimPrimeConfigEntry, gateway) -> None:
    """Delete timer entities of timers no longer exposed (unused or renamed)."""
    timers = gateway.timers if gateway.supports_timers else None
    if timers is None:
        return
    prefix = f"{entry.data['serial_number']}_timer_"
    entity_registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if entity.domain not in ("binary_sensor", "switch") or not entity.unique_id.startswith(prefix):
            continue
        suffix = entity.unique_id[len(prefix):].removesuffix("_enabled")
        if suffix.isdigit() and int(suffix) not in timers:
            entity_registry.async_remove(entity.entity_id)


def _remove_stale_output_entities(hass: HomeAssistant, entry: InimPrimeConfigEntry, gateway) -> None:
    """Delete output entities the integration no longer exposes.

    E.g. after turning off "show outputs not available via PrimeLAN": those
    entities would otherwise stay in the registry as unavailable. They are
    created again if the outputs come back.
    """
    outputs = gateway.outputs
    if outputs is None:
        return
    # Motor relays of shutter covers are no longer offered as switches.
    shutter_terminals = {
        oid
        for terminals in discover_shutters(dict(outputs)).values()
        for oid in terminals.values()
    }
    prefix = f"{entry.data['serial_number']}_output_"
    entity_registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if entity.domain not in ("switch", "light") or not entity.unique_id.startswith(prefix):
            continue
        suffix = entity.unique_id[len(prefix):]
        if suffix.isdigit() and (int(suffix) not in outputs or int(suffix) in shutter_terminals):
            entity_registry.async_remove(entity.entity_id)


def _link_child_devices(hass: HomeAssistant, entry: InimPrimeConfigEntry) -> None:
    """Hang the grouping devices off the panel: Panel > Zones / Partitions /
    Shutters / Timers / Thermostats / GSM.

    Zones, partitions and shutters each live on a single grouping device that
    holds all their entities (named per item). Home Assistant no longer accepts
    ``via_device`` in DeviceInfo, so the links are set on the device registry
    once the devices exist.
    """
    device_registry = dr.async_get(hass)
    serial_number = entry.data["serial_number"]
    devices = dr.async_entries_for_config_entry(device_registry, entry.entry_id)
    panel = next((d for d in devices if (DOMAIN, serial_number) in d.identifiers), None)
    if panel is None:
        return

    def group(key: str, name: str):
        device = device_registry.async_get_or_create(
            config_entry_id = entry.entry_id,
            identifiers = {(DOMAIN, f"{serial_number}_{key}")},
            name = name,
            manufacturer = INIM_PRIME_DEVICE_MANUFACTURER,
            model = f"Prime {name}",
        )
        if device.via_device_id != panel.id:
            device = device_registry.async_update_device(device.id, via_device_id = panel.id)
        return device

    zones_group = group("zones", "Zones")
    partitions_group = group("partitions", "Partitions")
    group_ids = {zones_group.id, partitions_group.id}

    # The Shutters group exists only when the panel has shutters (its cover
    # entities create it), so link it without creating an empty one.
    shutters_group = device_registry.async_get_device(
        identifiers = {(DOMAIN, f"{serial_number}_shutters")}
    )
    if shutters_group is not None:
        if shutters_group.via_device_id != panel.id:
            device_registry.async_update_device(shutters_group.id, via_device_id = panel.id)
        group_ids.add(shutters_group.id)

    # Zones, partitions and shutters now hold their entities directly on the
    # grouping device above, so there are no per-item child devices left to
    # re-parent; any other device (GSM, Timers, Thermostats, ...) hangs off the
    # panel.
    for device in devices:
        if device.id == panel.id or device.id in group_ids:
            continue
        if device.via_device_id != panel.id:
            device_registry.async_update_device(device.id, via_device_id = panel.id)


async def async_remove_config_entry_device(
        hass: HomeAssistant,
        config_entry: InimPrimeConfigEntry,
        device_entry: DeviceEntry,
) -> bool:
    """Allow removing sub-devices but not the panel."""
    for domain, dev_id in device_entry.identifiers:
        # Prevent deleting the panel itself
        data = get_entry_data(config_entry)
        if dev_id == data["serial_number"]:
            return False

    return True


async def async_unload_entry(hass: HomeAssistant, entry: InimPrimeConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        for coordinator in entry.runtime_data.coordinators.all():
            if hasattr(coordinator, "async_shutdown"):
                await coordinator.async_shutdown()
        await entry.runtime_data.gateway.close()

    return unload_ok

async def async_migrate_entry(hass: HomeAssistant, entry: InimPrimeConfigEntry) -> bool:
    """Migrate old (flat, PrimeLAN-only) entries to the native/primelan split schema.

    v1 entries always predate the native backend, so they're unconditionally
    PrimeLAN-only: {serial_number, host, api_key, use_https} becomes
    {serial_number, primelan: {host, api_key, use_https}}.

    Unique IDs and entity unique_ids are keyed off serial_number and
    zone/partition IDs, not the backend, so this migration doesn't touch
    entity registry entries, names, or history.
    """
    if entry.version == 1:
        new_data = InimPrimeConfigData(
            serial_number = entry.data["serial_number"],
            host = entry.data["host"],
            primelan = PrimelanConfigData(
                api_key = entry.data["api_key"],
                use_https = entry.data["use_https"],
            )
        )

        new_options = InimPrimeOptionsData(
            scan_intervals = ScanIntervalsData(
                zones = entry.options["zones_scan_interval"] * 1000 if entry.options.get("zones_scan_interval") is not None else ZONES_SCAN_INTERVAL_PRIMELAN_DEFAULT,
                partitions = entry.options["partitions_scan_interval"] * 1000 if entry.options.get("partitions_scan_interval") is not None else PARTITIONS_SCAN_INTERVAL_PRIMELAN_DEFAULT,
                gsm = entry.options["gsm_scan_interval"] * 1000 if entry.options.get("gsm_scan_interval") is not None else GSM_SCAN_INTERVAL_PRIMELAN_DEFAULT,
                system_faults = entry.options["system_faults_scan_interval"] * 1000 if entry.options.get("system_faults_scan_interval") is not None else SYSTEM_FAULTS_SCAN_INTERVAL_PRIMELAN_DEFAULT,
                panel_log_events = entry.options["panel_log_events_scan_interval"] * 1000 if entry.options.get("panel_log_events_scan_interval") is not None else PANEL_LOG_EVENTS_SCAN_INTERVAL_PRIMELAN_DEFAULT,
            ),
            panel_log_events_fetch_limit = entry.options.get("panel_log_events_fetch_limit", PANEL_LOG_EVENTS_FETCH_LIMIT_DEFAULT),
        )

        @callback
        def update_unique_id(entity_entry: er.RegistryEntry) -> dict | None:
            new_unique_id = None
            # Zones
            if (
                    entity_entry.unique_id.endswith("_exclusion") and
                    "zone" in entity_entry.unique_id
            ):
                new_unique_id = entity_entry.unique_id.removesuffix("_exclusion") + "_bypass"
            # Partitions
            elif (
                    entity_entry.unique_id.endswith("_mode") and
                    "partition" in entity_entry.unique_id
            ):
                new_unique_id = entity_entry.unique_id.removesuffix("_mode") + "_arming_status"
            elif (
                    entity_entry.unique_id.endswith("_clear_alarm_memory") and
                    "partition" in entity_entry.unique_id
            ):
                new_unique_id = entity_entry.unique_id.removesuffix("_clear_alarm_memory") + "_reset_memory"
            # Panel
            elif entity_entry.unique_id.endswith("_excluded_zones_count"):
                new_unique_id = entity_entry.unique_id.removesuffix("_excluded_zones_count") + "_count_bypassed_zones"
            elif entity_entry.unique_id.endswith("_include_all_zones"):
                new_unique_id = entity_entry.unique_id.removesuffix("_include_all_zones") + "_disable_all_zone_bypasses"
            elif entity_entry.unique_id.endswith("_clear_all_partitions_alarm_memory"):
                new_unique_id = entity_entry.unique_id.removesuffix("_clear_all_partitions_alarm_memory") + "_reset_all_partition_memories"
            elif entity_entry.unique_id.endswith("_zones_alarm_memory_count"):
                new_unique_id = entity_entry.unique_id.removesuffix("_zones_alarm_memory_count") + "_count_zone_alarm_memories"
            elif entity_entry.unique_id.endswith("_partitions_alarm_memory_count"):
                new_unique_id = entity_entry.unique_id.removesuffix("_partitions_alarm_memory_count") + "_count_partition_alarm_memories"

            if new_unique_id is not None:
                return {"new_unique_id": new_unique_id}
            else:
                return None

        await er.async_migrate_entries(hass, entry.entry_id, update_unique_id)



        # dentro async_migrate_entry, quando rilevi il cambio enum:
        ir.async_create_issue(
            hass,
            DOMAIN,
            "partition_state_enum_renamed_v2",
            is_fixable = False,
            severity = ir.IssueSeverity.WARNING,
            translation_key = "partition_state_enum_renamed",
        )

        ir.async_create_issue(
            hass,
            DOMAIN,
            "partition_arming_status_enum_renamed_v2",
            is_fixable = False,
            severity = ir.IssueSeverity.WARNING,
            translation_key = "partition_arming_status_enum_renamed",
        )

        ir.async_create_issue(
            hass,
            DOMAIN,
            "zone_state_enum_renamed_v2",
            is_fixable = False,
            severity = ir.IssueSeverity.WARNING,
            translation_key = "zone_state_enum_renamed",
        )

        hass.config_entries.async_update_entry(
            entry,
            data = new_data,
            options = new_options,
            version = 2,
            minor_version = 0,
        )

    return True