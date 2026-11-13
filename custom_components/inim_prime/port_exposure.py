"""Optional check that the Native port cannot be reached from the Internet.

Whoever reaches that port can send commands to the panel knowing only the
connection password (the default one is the same on every panel), and can make
it drop its network connections even without knowing it.

The integration runs inside the local network and cannot see the port "from
outside" by itself: when the user enables the check, it asks an external
service to try the port on the public address the request comes from. The
service is any server running "echoip" (https://github.com/mpolden/echoip):
GET <server>/port/<port> answers {"ip": ..., "port": ..., "reachable": bool}.
Nothing about the panel is sent, only the port number.
"""
from __future__ import annotations

import asyncio
import logging

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, NATIVE_PORT_CHECK_SERVER_DEFAULT

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20


def _issue_id(entry_id: str) -> str:
    return f"native_port_exposed_{entry_id}"


def _check_url(server: str | None, port: int) -> str:
    server = (server or NATIVE_PORT_CHECK_SERVER_DEFAULT).strip().rstrip("/")
    if "://" not in server:
        server = f"https://{server}"
    return f"{server}/port/{port}"


async def async_port_reachable(hass: HomeAssistant, server: str | None, port: int) -> bool | None:
    """True/False as answered by the service, None when it gave no answer."""
    url = _check_url(server, port)
    try:
        response = await async_get_clientsession(hass).get(
            url,
            headers = {"Accept": "application/json"},
            timeout = aiohttp.ClientTimeout(total = REQUEST_TIMEOUT),
        )
        async with response:
            response.raise_for_status()
            answer = await response.json(content_type = None)
    except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError, ValueError) as err:
        _LOGGER.debug("Port check: no answer from %s (%r)", url, err)
        return None
    reachable = answer.get("reachable") if isinstance(answer, dict) else None
    if not isinstance(reachable, bool):
        _LOGGER.debug("Port check: unexpected answer from %s: %r", url, answer)
        return None
    return reachable


async def async_check_port_exposure(hass: HomeAssistant, entry_id: str, native_conf: dict | None) -> None:
    """Run the check once (at start-up) and raise or clear the repair notice."""
    if not native_conf or not native_conf.get("check_port_exposed"):
        ir.async_delete_issue(hass, DOMAIN, _issue_id(entry_id))
        return
    port = native_conf["port"]
    reachable = await async_port_reachable(hass, native_conf.get("port_check_server"), port)
    if reachable is None:
        return      # no answer: leave a previous notice as it is
    if not reachable:
        ir.async_delete_issue(hass, DOMAIN, _issue_id(entry_id))
        return
    _LOGGER.warning(
        "The Native port %s of the panel can be reached from the Internet: "
        "remove its forwarding on the router", port,
    )
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(entry_id),
        is_fixable = False,
        severity = ir.IssueSeverity.ERROR,
        translation_key = "native_port_exposed",
        translation_placeholders = {"port": str(port)},
    )
