"""Local webhook receiver for realtime panel events (fully local, opt-in).

The Inim Prime panel's own outbound HTTP client ("Invio pagine web" in
Prime/STUDIO) can fire an HTTP request on each event. When the user enables
"realtime push" in the options, we register a Home Assistant local webhook and
point those panel events at it. On each event we trigger an immediate refresh
of the coordinator that owns the affected entities, so they update right away
instead of waiting for the next poll.

Design notes:
- Opt-in and off by default: nothing is registered unless the user enabled it
  and a webhook id was generated.
- The webhook id is the shared secret (bearer token) embedded in the URL; HA
  routes unknown ids to a 200 with no handler, so ids can't be enumerated.
- The handler never errors back to the panel: it always answers 200, so a
  malformed request can't make the panel retry or log noise.
- We refresh the relevant coordinator rather than optimistically patching the
  cached state: the Native link serializes commands under a lock and the poll
  is already fast, so a targeted refresh is simple and always consistent.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from aiohttp import web
from homeassistant.components import webhook
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .config_flow.const import OptionsKey
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
        webhook.async_register(
            hass,
            DOMAIN,
            f"INIM Prime {entry.title}",
            webhook_id,
            _make_handler(entry),
            allowed_methods=["POST", "GET"],
            local_only=True,
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


async def _process(entry: InimPrimeConfigEntry, request: web.Request) -> None:
    ev = (await _read_params(request)).get("ev")
    names = _EVENT_COORDINATORS.get(ev or "")
    if not names:
        _LOGGER.debug("INIM webhook: ignoring unknown event %r", ev)
        return
    coordinators = entry.runtime_data.coordinators
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
