import asyncio
import logging
import time
from datetime import timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from . import debug_services, native_handoff  # noqa: F401  (must run before native clients initialize)
from inim.prime.native.client import Client as NativeClient
from inim.prime.primelan.client import InimPrimeClient as PrimelanClient
from .adapters.native_adapter import NativeAdapter
from .adapters.primelan_adapter import PrimelanAdapter
from .const import (
    DOMAIN,
    STORAGE_KEY_OUTPUT_LABELS,
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
    SHUTTER_FIELD_OPEN,
    SHUTTER_FIELD_CLOSE,
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


_LOGGER = logging.getLogger(__name__)


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
        # Right after the setup form the client it opened is still connected
        # and initialised: use it instead of connecting a second time.
        native_client = native_handoff.take(host, native_conf["port"])
        if native_client is not None and native_client.is_initialized:
            _LOGGER.debug("Setup: reusing the Native connection opened by the setup form")
            native_adapter = NativeAdapter(native_client, connected = True)
        else:
            if native_client is not None:
                native_client.disconnect()
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
        name = "Centrale Inim",
        model = "Centrale Inim Prime",
        manufacturer = INIM_PRIME_DEVICE_MANUFACTURER,
        serial_number = data["serial_number"],
    )

    inim_gateway = _build_gateway(data)
    inim_gateway.show_hidden_outputs = bool(entry.options.get("show_hidden_outputs", False))
    inim_gateway.hide_unused_partitions = bool(entry.options.get("hide_unused_partitions", False))
    # The panel can reset the native TCP link (one client at a time) or drop
    # the PrimeLAN HTTP connection. Treat a connect failure as "not ready" so
    # Home Assistant retries with backoff instead of leaving the entry failed.
    # After an options save the entry reloads and the panel may not have
    # released the previous native connection yet: wait a moment and retry a
    # few times before reporting the error.
    # On a reload the panel can take a while to free the previous Native
    # connection (one client at a time), so retry for ~30s before giving up and
    # showing the "not ready" error.
    attempts = 10
    setup_started = time.monotonic()
    for attempt in range(1, attempts + 1):
        try:
            await inim_gateway.connect()
            _LOGGER.debug(
                "Setup: connected in %.2fs (attempt %s)",
                time.monotonic() - setup_started, attempt,
            )
            break
        except (ConnectionError, OSError, asyncio.TimeoutError, TimeoutError) as err:
            if attempt == attempts:
                raise ConfigEntryNotReady(
                    f"Cannot connect to INIM Prime panel yet: {err}"
                ) from err
            _LOGGER.debug(
                "Connect attempt %s/%s failed (%s), retrying", attempt, attempts, err
            )
            try:
                await inim_gateway.close()
            except Exception:  # noqa: BLE001 - best effort cleanup
                pass
            await asyncio.sleep(3)

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

    # Load the two backends concurrently. The Native TCP link and the PrimeLAN
    # HTTP API are independent connections, so running each backend's initial
    # refreshes as its own sequential chain -- and the two chains in parallel --
    # cuts startup time roughly to the slower of the two instead of their sum.
    # Each chain stays sequential internally: the Native protocol handles one
    # command at a time, so its coordinators must not overlap each other.
    prefer_native = bool(data.get("native"))
    # Outputs must be refreshed before platform setup, in every mode. The output
    # list is populated by the outputs refresh (update_outputs), NOT by connect()
    # -- the Native adapter's connect() only opens the link, leaving the output
    # list empty. Deferring the refresh would build the switch/light/cover
    # entities from an empty list (none created), and the empty-group cleanup
    # below would then delete the Uscite / Tapparelle devices entirely.
    defer_outputs = False
    state_coordinators = [
        c for c in (zones_coordinator, partitions_coordinator)
        if c is not None
    ]
    if outputs_coordinator is not None and not defer_outputs:
        state_coordinators.append(outputs_coordinator)
    native_chain = list(state_coordinators) if prefer_native else []
    primelan_chain = [] if prefer_native else list(state_coordinators)
    if timers_coordinator is not None:
        native_chain.append(timers_coordinator)
    # Scenarios block setup: the scenario buttons and the active-scenario sensor
    # are built from the scenario list during platform setup, so it must be loaded
    # first. Outputs, GSM, system faults and the event log are loaded in the
    # background (see below), so they don't gate startup.
    primelan_chain.extend(
        c for c in (scenarios_coordinator,)
        if c is not None
    )

    thermostats_probe: dict = {}

    async def _timed_first_refresh(coordinator) -> None:
        started = time.monotonic()
        await coordinator.async_config_entry_first_refresh()
        _LOGGER.debug(
            "Setup: first read of %s took %.2fs",
            coordinator.name, time.monotonic() - started,
        )

    async def _run_native() -> None:
        for coordinator in native_chain:
            await _timed_first_refresh(coordinator)
        # Thermostats are Native-only and optional. Probe here (inside the native
        # chain, so it overlaps the PrimeLAN loads) and keep the coordinator only
        # if the panel actually returns thermostats (the status command fails
        # cleanly on panels without any).
        if inim_gateway.supports_thermostats:
            probe = InimPrimeThermostatsUpdateCoordinator(
                hass = hass,
                update_interval = timedelta(milliseconds = THERMOSTATS_SCAN_INTERVAL_DEFAULT),
                entry = entry,
                gateway = inim_gateway,
            )
            probe_started = time.monotonic()
            await probe.async_refresh()
            _LOGGER.debug(
                "Setup: thermostat probe took %.2fs", time.monotonic() - probe_started
            )
            if probe.last_update_success and inim_gateway.thermostats:
                thermostats_probe["coordinator"] = probe

    async def _run_primelan() -> None:
        for coordinator in primelan_chain:
            await _timed_first_refresh(coordinator)
        # Output names/filter from PrimeLAN: read here, after the scenarios,
        # so it doesn't run next to another PrimeLAN request (the panel is
        # much slower when it serves two at once) nor hold up the Native reads.
        started = time.monotonic()
        try:
            await inim_gateway.load_primelan_output_labels()
        finally:
            inim_gateway.defer_output_labels = False
        _LOGGER.debug(
            "Setup: PrimeLAN output names took %.2fs", time.monotonic() - started
        )

    # The PrimeLAN output names/filter change only when the panel is
    # reprogrammed, and reading them takes 7-10s: start from the ones saved
    # last time and re-read them in the background (see below).
    labels_store = Store(
        hass, 1, f"{DOMAIN}_{data['serial_number']}_{STORAGE_KEY_OUTPUT_LABELS}"
    )
    cached_labels: dict[int, str] | None = None
    if inim_gateway.uses_primelan_output_labels:
        stored = await labels_store.async_load()
        if isinstance(stored, dict) and isinstance(stored.get("labels"), dict):
            cached_labels = {int(k): v for k, v in stored["labels"].items()}
            inim_gateway.set_primelan_output_labels(cached_labels)
            _LOGGER.debug("Setup: using %d saved PrimeLAN output names", len(cached_labels))

    inim_gateway.defer_output_labels = True

    reads_started = time.monotonic()
    await asyncio.gather(_run_native(), _run_primelan())
    _LOGGER.debug(
        "Setup: all first reads done in %.2fs", time.monotonic() - reads_started
    )
    thermostats_coordinator = thermostats_probe.get("coordinator")

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


    platforms_started = time.monotonic()
    await hass.config_entries.async_forward_entry_setups(
        entry = entry,
        platforms = PLATFORMS,
    )
    _LOGGER.debug(
        "Setup: entities created in %.2fs; ready %.2fs after start",
        time.monotonic() - platforms_started, time.monotonic() - setup_started,
    )

    _link_child_devices(hass, entry)
    _drop_old_default_names(hass, entry)
    debug_services.async_register(hass)
    _setup_realtime_webhook(hass, entry)
    _remove_stale_output_entities(hass, entry, inim_gateway)
    _remove_stale_timer_entities(hass, entry, inim_gateway)
    _remove_hidden_partition_entities(hass, entry, inim_gateway)
    _remove_hidden_zone_entities(hass, entry, inim_gateway)
    _remove_renamed_state_entities(hass, entry)
    _remove_old_connectivity_entities(hass, entry)
    _remove_stale_number_entities(hass, entry)
    _remove_old_grouped_devices(hass, entry)
    _rename_shutter_entities(hass, entry)
    _remove_unmapped_shutter_covers(hass, entry, inim_gateway)
    # After the stale-entity cleanups above, drop any optional grouping device
    # (Timer, Uscite, Tapparelle, Termostati) now left without entities.
    _remove_empty_group_devices(hass, entry)

    # PrimeLAN diagnostics are slow and don't need to gate startup: load system
    # faults and GSM, then the event log (the slowest single fetch), in one
    # background task after setup. Their entities are already created and show
    # "unavailable" until this fills them in, so setup finishes as soon as the
    # fast Native chain (and the blocking scenarios refresh) are done.
    # Outputs first (native status read: switches/lights/covers go from
    # "unavailable" to their real state as soon as it lands), then the PrimeLAN
    # diagnostics, then the event log (slowest).
    deferred = [
        c for c in (
            outputs_coordinator if defer_outputs else None,
            system_faults_coordinator,
            gsm_coordinator,
        )
        if c is not None
    ]
    refresh_labels = inim_gateway.uses_primelan_output_labels

    async def _refresh_output_labels() -> None:
        """Re-read the PrimeLAN output names; reload once if they changed."""
        started = time.monotonic()
        try:
            fresh = await inim_gateway.fetch_primelan_output_labels()
        except Exception:  # noqa: BLE001 - keep what we have, retry next start
            _LOGGER.debug("PrimeLAN output names: background read failed", exc_info = True)
            return
        _LOGGER.debug(
            "PrimeLAN output names: background read took %.2fs", time.monotonic() - started
        )
        if not fresh or fresh == inim_gateway.primelan_output_labels:
            return
        await labels_store.async_save({"labels": {str(k): v for k, v in fresh.items()}})
        if cached_labels is not None:
            # They were built from the saved names: rebuild with the new ones.
            _LOGGER.info("PrimeLAN outputs changed on the panel: reloading")
            hass.config_entries.async_schedule_reload(entry.entry_id)

    if refresh_labels and cached_labels is None and inim_gateway.primelan_output_labels:
        # First start: the names were just read (blocking). Save them.
        await labels_store.async_save(
            {"labels": {str(k): v for k, v in inim_gateway.primelan_output_labels.items()}}
        )
        refresh_labels = False

    # Native present -> shutter module names can be read ahead (see below).
    warm_module_names = inim_gateway.supports_timers

    async def _warm_module_names() -> None:
        """Read the names of all shutter modules once, so the Options shutters
        form finds them ready instead of reading them (slowly, behind the
        polling) when it is opened. The cover platform only reads the mapped
        ones, which may be fewer."""
        started = time.monotonic()
        shutters = discover_shutters(dict(inim_gateway.all_outputs or {}))
        if not shutters:
            return
        await inim_gateway.module_labels(max(shutters))
        _LOGGER.debug(
            "Shutter module names read ahead in %.2fs", time.monotonic() - started
        )

    if (
            deferred or panel_log_events_coordinator is not None
            or refresh_labels or warm_module_names
    ):
        async def _load_deferred() -> None:
            if warm_module_names:
                try:
                    await _warm_module_names()
                except Exception:  # noqa: BLE001 - names are cosmetic
                    _LOGGER.debug("Shutter module names: read ahead failed", exc_info = True)
            for coordinator in deferred:
                await coordinator.async_refresh()
            if refresh_labels:
                await _refresh_output_labels()
            if panel_log_events_coordinator is not None:
                await panel_log_events_coordinator.async_startup()
                await panel_log_events_coordinator.async_refresh()

        entry.async_create_background_task(
            hass, _load_deferred(), "inim_prime_deferred"
        )

    return True


def _remove_unmapped_shutter_covers(hass: HomeAssistant, entry: InimPrimeConfigEntry, gateway) -> None:
    """Drop cover entities for shutters that aren't mapped (no open/close command).

    An unmapped shutter can't be driven, so the cover platform no longer creates
    it; remove any left over from before it was unmapped so it doesn't linger as
    an unavailable entity."""
    from .cover import discover_shutters, _shutter_commands

    mapping = _shutter_commands(entry)
    mapped = {
        module for module in discover_shutters(dict(gateway.all_outputs or {}))
        if mapping.get(module, {}).get(SHUTTER_FIELD_OPEN) is not None
        and mapping.get(module, {}).get(SHUTTER_FIELD_CLOSE) is not None
    }
    prefix = f"{entry.data['serial_number']}_shutter_"
    entity_registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if entity.domain != "cover" or not entity.unique_id.startswith(prefix):
            continue
        module = entity.unique_id[len(prefix):]
        if module.isdigit() and int(module) not in mapped:
            entity_registry.async_remove(entity.entity_id)


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


def _remove_empty_group_devices(hass: HomeAssistant, entry: InimPrimeConfigEntry) -> None:
    """Drop optional grouping devices left without any entity.

    Timer, Uscite, Tapparelle and Termostati create their entities only when the
    panel actually has those items; on a panel without them (or after they go
    away) the grouping device would otherwise linger empty. Run after the
    platforms are set up, so the entity registry reflects what was created."""
    serial_number = entry.data["serial_number"]
    optional_ids = {
        f"{serial_number}_{kind}"
        for kind in ("timers", "outputs", "shutters", "thermostats")
    }
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        identifiers = {ident for domain, ident in device.identifiers if domain == DOMAIN}
        if not identifiers & optional_ids:
            continue
        entities = er.async_entries_for_device(
            entity_registry, device.id, include_disabled_entities = True
        )
        if not entities:
            device_registry.async_remove_device(device.id)


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
                    device_registry.async_remove_device(device.id)
                    break


def _remove_old_connectivity_entities(hass: HomeAssistant, entry: InimPrimeConfigEntry) -> None:
    """Remove the "Connessione … ultimo aggiornamento" timestamp sensors, in
    both generations of their unique_id. They duplicated the 'ultimo_ok'
    attribute of the connectivity binary sensors and wrote a new state to the
    recorder at every poll."""
    serial_number = entry.data["serial_number"]
    old_ids = {
        f"{serial_number}_last_update_native",
        f"{serial_number}_last_update_primelan",
        f"{serial_number}_native_last_update",
        f"{serial_number}_primelan_last_update",
    }
    entity_registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if entity.unique_id in old_ids:
            entity_registry.async_remove(entity.entity_id)


def _remove_renamed_state_entities(hass: HomeAssistant, entry: InimPrimeConfigEntry) -> None:
    """Remove the old zone/partition State sensors and zone Bypass switch after
    their unique_id was renamed (_state -> _stato, _bypass -> _esclusione).

    HA keeps an entity's first display name (original_name) and even restores it
    for 30 days if the same unique_id comes back, so the old English/"Bypass"
    names were stuck. Giving those entities a new unique_id makes HA create them
    fresh as "Stato"/"Esclusione"; drop the now-orphaned old ones here."""
    serial_number = entry.data["serial_number"]
    zone_prefix = f"{serial_number}_zone_"
    partition_prefix = f"{serial_number}_partition_"
    entity_registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        uid = entity.unique_id
        if uid.startswith(zone_prefix) and (uid.endswith("_state") or uid.endswith("_bypass")):
            entity_registry.async_remove(entity.entity_id)
        elif uid.startswith(partition_prefix) and uid.endswith("_state"):
            entity_registry.async_remove(entity.entity_id)


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
    # Motor relays of shutter covers are no longer offered as switches. Discover
    # from the full set so the Mod.dom terminals are caught even when shown.
    shutter_terminals = {
        oid
        for terminals in discover_shutters(dict(gateway.all_outputs or {})).values()
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


# Names earlier versions gave by default. Home Assistant keeps them as custom
# names (even across a delete + reinstall, from its deleted-entries store), so
# they would hide the current ones.
_OLD_DEVICE_NAMES = ("Partizioni", "Partizione")
_OLD_ENTITY_NAME_SUFFIXES = (" Arming Status", " Reset Memory", " Alarm Memory", " State")


def _drop_old_default_names(hass: HomeAssistant, entry: InimPrimeConfigEntry) -> None:
    """Remove custom names that are just an old default of this integration.

    Any other custom name is the user's choice and is left alone.
    """
    device_registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        if device.name_by_user in _OLD_DEVICE_NAMES:
            device_registry.async_update_device(device.id, name_by_user = None)
    entity_registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if entity.name and entity.name.endswith(_OLD_ENTITY_NAME_SUFFIXES):
            entity_registry.async_update_entity(entity.entity_id, name = None)


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
            model = f"Inim Prime {name}",
        )
        if device.via_device_id != panel.id:
            device = device_registry.async_update_device(device.id, via_device_id = panel.id)
        return device

    zones_group = group("zones", "Zone")
    partitions_group = group("partitions", "Aree")
    group_ids = {zones_group.id, partitions_group.id}

    # The Shutters group exists only when the panel has shutters (its cover
    # entities create it), so link it without creating an empty one.
    # async_get_device(identifiers=...) is deprecated from HA 2026.x in favour of
    # async_get_device_by_identifier(identifier, config_entry_id=...). Use the new
    # API when available, and fall back to the old one on any signature mismatch
    # or on older cores -- never let this lookup break setup.
    shutters_identifier = (DOMAIN, f"{serial_number}_shutters")
    try:
        shutters_group = device_registry.async_get_device_by_identifier(
            shutters_identifier, config_entry_id = entry.entry_id
        )
    except (AttributeError, TypeError):
        shutters_group = device_registry.async_get_device(identifiers = {shutters_identifier})
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


def _setup_realtime_webhook(hass: HomeAssistant, entry: InimPrimeConfigEntry) -> None:
    """Register the optional realtime-push webhook.

    Off unless the user enabled "realtime push" in the options. The webhook URL
    stays visible in the Options screen (General parameters); a one-time
    notification with it is shown by the options flow on first activation."""
    from .webhook import async_register_webhook

    async_register_webhook(hass, entry)


async def async_unload_entry(hass: HomeAssistant, entry: InimPrimeConfigEntry) -> bool:
    from .webhook import async_unregister_webhook
    async_unregister_webhook(hass, entry)
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
                new_unique_id = entity_entry.unique_id.removesuffix("_exclusion") + "_esclusione"
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
