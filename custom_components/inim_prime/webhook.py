"""Local webhook receiver for realtime panel events (fully local, opt-in).

The Inim Prime panel's own outbound HTTP client ("Invio pagine web" in
Prime/STUDIO) can fire an HTTP request on each event. When the user enables
"realtime push" in the options, we register a Home Assistant local webhook and
point those panel events at it.

The panel sends each URL exactly as written (no variables, no wildcard), so
the user writes the object and its new state into the URL, one entry per
object and per activation/restoral. When an event names a zone or an output
we set that entity's state straight from the parameters, so even a pulse
shorter than a poll is seen; then, and for every other event, we refresh the
coordinator that owns the affected entities, which reconciles with the panel.

Design notes:
- Opt-in and off by default: nothing is registered unless the user enabled it
  and a webhook id was generated.
- The webhook id is the shared secret (bearer token) embedded in the URL; HA
  routes unknown ids to a 200 with no handler, so ids can't be enumerated.
- The handler never errors back to the panel: it always answers 200, so a
  malformed request can't make the panel retry or log noise.
- A state taken from the URL is only as right as the entry written in
  Prime/STUDIO; the refresh that follows corrects a wrong one.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from aiohttp import web
from homeassistant.components import webhook
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .config_flow.const import OptionsKey
from .models.zones import UnifiedZoneState
from .runtime_data import InimPrimeConfigEntry

_LOGGER = logging.getLogger(__name__)

# Panel event name (the `ev` query/body param) -> coordinators to refresh.
# Mirrors the URL scheme documented for the panel's outbound HTTP table, so the
# same per-event URLs work here.
_EVENT_COORDINATORS: dict[str, tuple[str, ...]] = {
    "zone_open": ("zones",),
    "zone_close": ("zones",),
    "arm": ("partitions",),
    "disarm": ("partitions",),
    "alarm": ("partitions",),
    "tamper": ("system_faults",),
    "fault": ("system_faults",),
    "fault_restore": ("system_faults",),
    "output": ("outputs",),
}

# Cap the body we read from an unauthenticated LAN request.
MAX_WEBHOOK_BODY = 1024


def _configured_webhook_id(entry: InimPrimeConfigEntry) -> str | None:
    """The webhook id stored in options, only when realtime push is enabled."""
    options = entry.options
    if not options.get(OptionsKey.REALTIME_PUSH):
        return None
    return options.get(OptionsKey.WEBHOOK_ID)


def async_register_webhook(hass: HomeAssistant, entry: InimPrimeConfigEntry) -> None:
    """Register the entry's webhook when realtime push is enabled."""
    webhook_id = _configured_webhook_id(entry)
    if not webhook_id:
        return
    try:
        # LAN-only by default (the panel calls HA's local IP directly). Some
        # setups behind a reverse proxy make HA see even LAN requests as
        # "remote"; the "accept non-local requests" option lifts the check
        # (the webhook only sets zone/output states and triggers a refresh, and
        # is guarded by the secret id in the URL).
        allow_remote = bool(entry.options.get(OptionsKey.WEBHOOK_ALLOW_REMOTE))
        webhook.async_register(
            hass,
            DOMAIN,
            f"INIM Prime {entry.title}",
            webhook_id,
            _make_handler(entry),
            allowed_methods=["POST", "GET"],
            local_only=not allow_remote,
        )
    except ValueError:
        # Already registered (e.g. a reload raced the unregister); harmless.
        _LOGGER.debug("INIM webhook already registered")


def async_unregister_webhook(hass: HomeAssistant, entry: InimPrimeConfigEntry) -> None:
    """Unregister the entry's webhook, if one is configured."""
    webhook_id = entry.options.get(OptionsKey.WEBHOOK_ID)
    if webhook_id:
        webhook.async_unregister(hass, webhook_id)


def _make_handler(
    entry: InimPrimeConfigEntry,
) -> Callable[[HomeAssistant, str, web.Request], Awaitable[web.Response]]:
    async def _handle(
        hass: HomeAssistant, webhook_id: str, request: web.Request
    ) -> web.Response:
        try:
            await _process(entry, request)
        except Exception:  # noqa: BLE001 - never error back to the panel.
            _LOGGER.debug("INIM webhook: ignoring malformed request", exc_info=True)
        return web.Response(status=200)

    return _handle


_ZONE_EVENT_STATES = {
    "zone_open": UnifiedZoneState.ALARM,
    "zone_close": UnifiedZoneState.STANDBY,
}
_OUTPUT_ON = ("1", "on", "true")
_OUTPUT_OFF = ("0", "off", "false")


def _apply_event(coordinators, ev: str, params: dict[str, str]) -> None:
    """Set the state the event carries (zones and outputs) on the entity."""
    try:
        item_id = int(params.get("id", ""))
    except ValueError:
        return
    if ev in _ZONE_EVENT_STATES:
        coordinator = coordinators.zones
        applied = coordinator is not None and coordinator.gateway.apply_zone_state(
            item_id, _ZONE_EVENT_STATES[ev]
        )
    elif ev == "output":
        state = (params.get("state") or "").strip().lower()
        if state not in _OUTPUT_ON + _OUTPUT_OFF:
            return
        coordinator = coordinators.outputs
        applied = coordinator is not None and coordinator.gateway.apply_output_state(
            item_id, state in _OUTPUT_ON
        )
    else:
        return
    if applied:
        # Entities read the gateway cache: have them write the new state now.
        coordinator.async_update_listeners()
    else:
        _LOGGER.debug("INIM webhook: %r for unknown id %s", ev, item_id)


async def _process(entry: InimPrimeConfigEntry, request: web.Request) -> None:
    params = await _read_params(request)
    ev = params.get("ev")
    names = _EVENT_COORDINATORS.get(ev or "")
    if not names:
        _LOGGER.debug("INIM webhook: ignoring unknown event %r", ev)
        return
    _LOGGER.debug("INIM webhook: event %r -> refresh %s", ev, names)
    coordinators = entry.runtime_data.coordinators
    _apply_event(coordinators, ev, params)
    for name in names:
        coordinator = getattr(coordinators, name, None)
        if coordinator is not None:
            await coordinator.async_request_refresh()


async def _read_params(request: web.Request) -> dict[str, str]:
    """Flatten query-string (and small form body) params. Query wins on conflict."""
    merged: dict[str, str] = {}
    if request.method == "POST":
        raw = await request.read()
        if len(raw) > MAX_WEBHOOK_BODY:
            _LOGGER.debug("INIM webhook: body too large (%d bytes), ignoring", len(raw))
        elif raw:
            try:
                for key, value in (await request.post()).items():
                    if isinstance(value, str):
                        merged[key] = value
            except Exception:  # noqa: BLE001
                pass
    for key, value in request.query.items():
        merged[key] = value
    return merged
