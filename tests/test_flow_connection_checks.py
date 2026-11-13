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

    # no answer from the service: the check was asked for, the form stops too
    aioclient_mock.clear_requests()
    aioclient_mock.get(url, status = 502)
    errors, placeholders = await cf._run_connection_tests(
        _data(check_port_exposed = True), get_serial = False, hass = hass)
    assert errors == {"base": "native_port_check_failed"}
    assert placeholders["port"] == "6004" and calls == []

    # closed port: the form goes on
    aioclient_mock.clear_requests()
    aioclient_mock.get(url, json = {"reachable": False})
    errors, _ = await cf._run_connection_tests(
        _data(check_port_exposed = True), get_serial = False, hass = hass)
    assert errors == {} and calls == ["192.168.1.98"]

    # checkbox off: the service is not asked at all
    aioclient_mock.clear_requests()
    errors, _ = await cf._run_connection_tests(_data(), get_serial = False, hass = hass)
    assert errors == {} and aioclient_mock.call_count == 0


async def test_port_check_outcome_is_reported_next_to_another_error(hass, aioclient_mock, monkeypatch):
    async def fail(conf, host):
        raise ValueError("Padding is incorrect.")
    monkeypatch.setattr(cf, "test_native_connection", fail)
    url = "https://ifconfig.co/port/6004"

    aioclient_mock.get(url, json = {"reachable": False})
    errors, placeholders = await cf._run_connection_tests(
        _data(check_port_exposed = True), get_serial = False, hass = hass)
    assert errors == {"base": "native_password_rejected"}
    assert placeholders["port_check_outcome"] == "closed"
    assert "6004" in placeholders["port_check"] and placeholders["port_check"].startswith("✅")

    # checkbox off: nothing is said about the port
    _, placeholders = await cf._run_connection_tests(_data(), get_serial = False, hass = hass)
    assert placeholders["port_check"] == ""


async def test_password_longer_than_the_key_is_refused_before_connecting(monkeypatch):
    calls = []

    async def ok(conf, host):
        calls.append(host)
    monkeypatch.setattr(cf, "test_native_connection", ok)
    data = _data()
    data.native.password = "x" * 17
    errors, placeholders = await cf._run_connection_tests(data, get_serial = False)
    assert errors == {"base": "native_password_too_long"}
    assert placeholders["max"] == "16" and calls == []

    data.native.password = "x" * 16
    errors, _ = await cf._run_connection_tests(data, get_serial = False)
    assert errors == {} and calls == ["192.168.1.98"]
