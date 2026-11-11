"""Hand the Native client opened by the setup form over to the integration.

The setup form connects and initialises a Native client to validate the
credentials and read the serial number (several seconds on a real panel).
Closing it and connecting again right away in async_setup_entry would repeat
all of that, and the panel (one client at a time) often refuses the second
connection for a few seconds. So the already-initialised client is parked here
for a short time and picked up by the setup that follows.
"""
from __future__ import annotations

import asyncio

from inim.prime.native.client import Client as NativeClient

# How long a parked client waits to be picked up before it is closed.
KEEP_SECONDS = 30.0

_parked: dict[tuple[str, int], tuple[NativeClient, asyncio.TimerHandle]] = {}


def keep(host: str, port: int, client: NativeClient) -> None:
    """Park an open, initialised client for the setup that follows."""
    key = (host, int(port))
    drop(host, port)
    timer = asyncio.get_running_loop().call_later(KEEP_SECONDS, drop, host, port)
    _parked[key] = (client, timer)


def take(host: str, port: int) -> NativeClient | None:
    """Return the parked client for this panel, if any (it is then ours)."""
    parked = _parked.pop((host, int(port)), None)
    if parked is None:
        return None
    client, timer = parked
    timer.cancel()
    return client


def drop(host: str, port: int) -> None:
    """Close and forget the parked client (setup did not happen)."""
    client = take(host, port)
    if client is not None:
        client.disconnect()
