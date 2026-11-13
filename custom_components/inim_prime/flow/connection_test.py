from __future__ import annotations

from .. import native_handoff  # noqa: F401
from inim.prime.native.client import Client as NativeClient
from inim.prime.primelan.client import InimPrimeClient

from .models import NativeConfig, PrimelanConfig


async def _open_native_client(native_conf: NativeConfig, host: str) -> NativeClient:
    """Connect and initialise; the connection is closed again on any failure."""
    client = NativeClient(
        host = host, password = native_conf.password,
        use_outer_frame = native_conf.use_outer_frame,
        port = native_conf.port, pin = native_conf.pin,
    )
    try:
        await client.connect()
        await client.initialize()
    except BaseException:
        client.disconnect()
        raise
    return client


async def test_native_connection(native_conf: NativeConfig, host: str) -> None:
    client = await _open_native_client(native_conf, host)
    client.disconnect()


async def test_native_connection_and_get_serial(native_conf: NativeConfig, host: str) -> str:
    """First setup: validate, read the serial and keep the client for the setup
    that follows (see native_handoff) instead of connecting twice in a row."""
    client = await _open_native_client(native_conf, host)
    panel_info = client.panel_info
    serial_number = panel_info[0] if panel_info else None
    if not serial_number:
        client.disconnect()
        raise ValueError("Native panel did not report a serial number")
    native_handoff.keep(host, native_conf.port, client)
    return serial_number


async def test_primelan_connection(primelan_conf: PrimelanConfig, host: str) -> None:
    client = InimPrimeClient(host = host, api_key = primelan_conf.api_key, use_https = primelan_conf.use_https)
    await client.connect()
    await client.close()