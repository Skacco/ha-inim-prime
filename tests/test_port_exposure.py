"""Optional start-up check that the Native port is not reachable from the Internet."""
from homeassistant.helpers import issue_registry as ir

from custom_components.inim_prime.const import DOMAIN
from custom_components.inim_prime.port_exposure import (
    _check_url,
    async_check_port_exposure,
)

URL = "https://ifconfig.co/port/6004"
CONF = {"port": 6004, "check_port_exposed": True}


def _issue(hass):
    return ir.async_get(hass).async_get_issue(DOMAIN, "native_port_exposed_e1")


def test_check_url():
    assert _check_url(None, 6004) == URL
    assert _check_url(" example.org/ ", 1234) == "https://example.org/port/1234"
    assert _check_url("http://10.0.0.1:8080", 6004) == "http://10.0.0.1:8080/port/6004"


async def test_disabled_makes_no_request(hass, aioclient_mock):
    await async_check_port_exposure(hass, "e1", {"port": 6004})
    assert aioclient_mock.call_count == 0
    assert _issue(hass) is None


async def test_reachable_raises_notice_then_clears(hass, aioclient_mock):
    aioclient_mock.get(URL, json = {"ip": "1.2.3.4", "port": 6004, "reachable": True})
    await async_check_port_exposure(hass, "e1", CONF)
    assert _issue(hass) is not None

    aioclient_mock.clear_requests()
    aioclient_mock.get(URL, json = {"ip": "1.2.3.4", "port": 6004, "reachable": False})
    await async_check_port_exposure(hass, "e1", CONF)
    assert _issue(hass) is None


async def test_no_answer_changes_nothing(hass, aioclient_mock):
    aioclient_mock.get(URL, status = 500)
    await async_check_port_exposure(hass, "e1", CONF)
    assert _issue(hass) is None
    aioclient_mock.clear_requests()
    aioclient_mock.get(URL, text = "not json")
    await async_check_port_exposure(hass, "e1", CONF)
    assert _issue(hass) is None


async def test_custom_server(hass, aioclient_mock):
    aioclient_mock.get("https://my.server/port/6004", json = {"reachable": True})
    await async_check_port_exposure(hass, "e1", {**CONF, "port_check_server": "my.server"})
    assert _issue(hass) is not None
