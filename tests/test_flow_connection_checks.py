"""What the connection form answers: refused password and exposed port."""
import asyncio

from custom_components.inim_prime.flow import config_flow as cf
from custom_components.inim_prime.flow.models import ConfigData, NativeConfig, PrimelanConfig


def _data(**native) -> ConfigData:
    data = ConfigData()
    data.host = "192.168.1.98"
    data.native = NativeConfig(port = 6004, password = "x", use_outer_frame = False, pin = None, **native)
    return data


async def test_refused_password_gets_its_own_message(monkeypatch):
    for err in (ValueError("Padding is incorrect."), _closed := ConnectionError("closed")):
        if err is _closed:
            err.__cause__ = asyncio.IncompleteReadError(b"", 12)

        async def fail(conf, host, err = err):
            raise err
        monkeypatch.setattr(cf, "test_native_connection", fail)
        errors, placeholders = await cf._run_connection_tests(_data(), get_serial = False)
        assert errors == {"base": "native_password_rejected"}
        assert placeholders["status"] == ""


async def test_other_failures_keep_the_generic_message(monkeypatch):
    async def fail(conf, host):
        raise ConnectionRefusedError(111, "refused")
    monkeypatch.setattr(cf, "test_native_connection", fail)
    errors, placeholders = await cf._run_connection_tests(_data(), get_serial = False)
    assert errors == {"base": "connection_failed"}
    assert "Native: FAILED" in placeholders["status"]


async def test_exposed_port_stops_the_form_before_connecting(hass, aioclient_mock, monkeypatch):
    calls = []

    async def ok(conf, host):
        calls.append(host)
    monkeypatch.setattr(cf, "test_native_connection", ok)
    url = "https://ifconfig.co/port/6004"

    aioclient_mock.get(url, json = {"reachable": True})
    errors, placeholders = await cf._run_connection_tests(
        _data(check_port_exposed = True), get_serial = False, hass = hass)
    assert errors == {"base": "native_port_exposed"}
    assert placeholders["port"] == "6004"
    assert calls == []

    # closed port, or no answer from the service: the form goes on
    for kwargs in ({"json": {"reachable": False}}, {"status": 502}):
        aioclient_mock.clear_requests()
        aioclient_mock.get(url, **kwargs)
        errors, _ = await cf._run_connection_tests(
            _data(check_port_exposed = True), get_serial = False, hass = hass)
        assert errors == {}

    # checkbox off: the service is not asked at all
    aioclient_mock.clear_requests()
    errors, _ = await cf._run_connection_tests(_data(), get_serial = False, hass = hass)
    assert errors == {} and aioclient_mock.call_count == 0
