"""Diagnostics for short connection drops (beta).

When the Native connection drops, probe the panel once a second for a while
and log, per second, what answers and what does not:

- Native port: plain TCP connect
- PrimeLAN port: plain TCP connect
- PrimeLAN CGI on a brand new connection
- PrimeLAN CGI through the integration's own (kept-alive) session

This tells apart "the whole network board stops" from "only the Native
channel is closed" from "only connections that were already open are dropped".
"""
from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlsplit

import aiohttp

_LOGGER = logging.getLogger(__name__)

PROBE_SECONDS = 20
PROBE_TIMEOUT = 3.0


def describe(err: BaseException) -> str:
    if isinstance(err, ConnectionRefusedError):
        return "refused"
    if isinstance(err, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"
    if isinstance(err, ConnectionResetError):
        return "reset"
    text = str(err)
    return f"{type(err).__name__}: {text}" if text else type(err).__name__


async def _timed(coro) -> str:
    started = time.monotonic()
    try:
        await asyncio.wait_for(coro, PROBE_TIMEOUT)
    except Exception as err:  # noqa: BLE001 -- diagnostics: report anything
        return describe(err)
    return f"ok {(time.monotonic() - started) * 1000:.0f}ms"


async def tcp_connect(host: str, port: int) -> None:
    _, writer = await asyncio.open_connection(host, port)
    writer.close()


async def _http_new_connection(url: str, api_key: str, ssl_context) -> None:
    connector = aiohttp.TCPConnector(ssl = ssl_context, force_close = True)
    async with aiohttp.ClientSession(connector = connector) as session:
        async with session.get(url, params = {"apikey": api_key, "cmd": "ping"}) as response:
            await response.read()
            if response.status != 200:
                raise ConnectionError(f"HTTP {response.status}")


async def probe_outage(native_client, primelan_client) -> None:
    """Log one line per second describing what the panel answers."""
    try:
        transport = native_client._protocol._transport
        native_host, native_port = transport._host, transport._port
    except AttributeError:
        return

    primelan_url = primelan_host = primelan_port = ssl_context = None
    if primelan_client is not None:
        from inim.prime.primelan.const import API_PATH
        base = urlsplit(primelan_client._base_url)
        primelan_host = base.hostname
        primelan_port = base.port or (443 if base.scheme == "https" else 80)
        primelan_url = f"{primelan_client._base_url}{API_PATH}"
        if base.scheme == "https":
            from inim.prime.primelan.client import _create_ssl_context
            ssl_context = await asyncio.to_thread(_create_ssl_context)
        else:
            ssl_context = False

    started = time.monotonic()
    for tick in range(PROBE_SECONDS):
        probes = [_timed(tcp_connect(native_host, native_port))]
        if primelan_client is not None:
            probes += [
                _timed(tcp_connect(primelan_host, primelan_port)),
                _timed(_http_new_connection(primelan_url, primelan_client._api_key, ssl_context)),
                _timed(primelan_client.ping()),
            ]
        results = await asyncio.gather(*probes)
        line = f"Native tcp:{native_port} {results[0]}"
        if primelan_client is not None:
            line += (
                f" | PrimeLAN tcp:{primelan_port} {results[1]}"
                f" | PrimeLAN CGI new connection {results[2]}"
                f" | PrimeLAN CGI kept connection {results[3]}"
            )
        _LOGGER.warning("Outage probe +%4.1fs | %s", time.monotonic() - started, line)
        await asyncio.sleep(max(0.0, started + tick + 1 - time.monotonic()))
