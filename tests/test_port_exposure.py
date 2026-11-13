"""Optional check that the Native port is not reachable from the Internet."""
from custom_components.inim_prime.port_exposure import _check_url, async_port_reachable

URL = "https://ifconfig.co/port/6004"


def test_check_url():
    assert _check_url(None, 6004) == URL
    assert _check_url(" example.org/ ", 1234) == "https://example.org/port/1234"
    assert _check_url("http://10.0.0.1:8080", 6004) == "http://10.0.0.1:8080/port/6004"


async def test_answers(hass, aioclient_mock):
    aioclient_mock.get(URL, json = {"ip": "1.2.3.4", "port": 6004, "reachable": True})
    assert await async_port_reachable(hass, None, 6004) is True
    aioclient_mock.clear_requests()
    aioclient_mock.get(URL, json = {"reachable": False})
    assert await async_port_reachable(hass, None, 6004) is False
    for kwargs in ({"status": 500}, {"text": "not json"}, {"json": {"reachable": "yes"}}):
        aioclient_mock.clear_requests()
        aioclient_mock.get(URL, **kwargs)
        assert await async_port_reachable(hass, None, 6004) is None


async def test_custom_server(hass, aioclient_mock):
    aioclient_mock.get("https://my.server/port/6004", json = {"reachable": True})
    assert await async_port_reachable(hass, "my.server", 6004) is True
