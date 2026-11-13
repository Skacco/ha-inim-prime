"""A connection password the panel does not accept must stop the reconnects.

There is no "wrong password" answer: the password is the encryption key, and
the panel closes a connection it cannot decrypt at the first request, without
a byte of answer (seen on a Prime120 4.07). Each such attempt makes the panel
drop every network connection for about ten seconds, so retrying every few
seconds keeps the whole network card down.
"""
import asyncio
from datetime import timedelta

import pytest
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.inim_prime.adapters import native_adapter as na
from custom_components.inim_prime.const import DOMAIN
from custom_components.inim_prime.coordinators import base_coordinator as bc


def _closed_without_answer() -> ConnectionError:
    try:
        raise asyncio.IncompleteReadError(b"", 12)
    except asyncio.IncompleteReadError as exc:
        err = ConnectionError("Panel closed the connection mid-response.")
        err.__cause__ = exc
        return err


class _Client:
    """A panel whose reads follow a script: an exception is raised, anything
    else is returned. Reconnections follow their own script."""

    def __init__(self, reads: list, reconnects: list | None = None):
        self.reads = reads
        self.reconnect_script = reconnects or []
        self.is_connected = True
        self.read_calls = 0
        self.reconnect_calls = 0
        self.connect_calls = 0

    async def connect(self):
        self.connect_calls += 1
        self.is_connected = True

    async def reconnect(self):
        self.reconnect_calls += 1
        if self.reconnect_script:
            step = self.reconnect_script.pop(0)
            if isinstance(step, Exception):
                self.is_connected = False
                raise step
        self.is_connected = True

    async def ensure_initialized(self):
        await self.update_zones()

    async def update_zones(self):
        self.read_calls += 1
        step = self.reads.pop(0)
        if isinstance(step, Exception):
            self.is_connected = False
            raise step
        return step


async def _read(adapter):
    return await adapter._call_with_reconnect(adapter._client.update_zones)


@pytest.fixture(autouse = True)
def _fast(monkeypatch):
    monkeypatch.setattr(na, "NATIVE_RECONNECT_MIN_INTERVAL", 0)


async def test_password_changed_on_the_panel_stops_after_one_refused_attempt():
    # the running session breaks, the reconnection is accepted and its first
    # request is closed without an answer
    client = _Client(reads = [_closed_without_answer(), _closed_without_answer(), "never read"])
    adapter = na.NativeAdapter(client)

    with pytest.raises(na.NativePasswordRejected):
        await _read(adapter)
    assert adapter.password_rejected
    assert (client.read_calls, client.reconnect_calls) == (2, 1)

    # from now on nothing reaches the panel
    for _ in range(5):
        with pytest.raises(na.NativePasswordRejected):
            await _read(adapter)
    with pytest.raises(na.NativePasswordRejected):
        await adapter._ensure_reconnected()
    with pytest.raises(na.NativePasswordRejected):
        await adapter._call_command(client.update_zones, key = "x")
    with pytest.raises(na.NativePasswordRejected):
        await adapter.connect()
    assert (client.read_calls, client.reconnect_calls, client.connect_calls) == (2, 1, 0)


async def test_wrong_password_at_startup_is_recognised_at_the_first_request():
    client = _Client(reads = [_closed_without_answer()])
    adapter = na.NativeAdapter(client)

    with pytest.raises(na.NativePasswordRejected):
        await adapter.connect()
    assert (client.connect_calls, client.read_calls) == (1, 1)


async def test_the_panel_restarting_its_network_is_not_a_refused_password():
    """During its own short restarts the port refuses the connection: the
    running session is closed, and reconnecting fails before any request."""
    client = _Client(
        reads = [_closed_without_answer(), "zones"],
        reconnects = [ConnectionRefusedError(111, "refused")],
    )
    adapter = na.NativeAdapter(client)

    with pytest.raises(ConnectionRefusedError):
        await _read(adapter)
    assert not adapter.password_rejected

    assert await _read(adapter) == "zones"      # back once the port answers again
    assert not adapter.password_rejected


async def test_a_session_cut_after_working_reads_is_not_a_refused_password():
    client = _Client(reads = ["zones", _closed_without_answer(), "zones again"])
    adapter = na.NativeAdapter(client)

    assert await _read(adapter) == "zones"
    assert await _read(adapter) == "zones again"   # cut, reconnected, read again
    assert not adapter.password_rejected
    assert client.reconnect_calls == 1


async def test_a_timeout_on_a_new_connection_is_not_a_refused_password():
    client = _Client(reads = [_closed_without_answer(), TimeoutError()])
    adapter = na.NativeAdapter(client)

    with pytest.raises(TimeoutError):
        await _read(adapter)
    assert not adapter.password_rejected


async def test_coordinator_reports_the_refused_password_and_does_not_ride_through(hass):
    results = [{"zone": "ok"}, na.NativePasswordRejected(na.PASSWORD_REJECTED_MESSAGE)]

    class _Coordinator(bc.InimPrimeBaseCoordinator):
        def __init__(self):
            bc.DataUpdateCoordinator.__init__(
                self, hass, bc._LOGGER, name = "test", config_entry = None,
                update_interval = timedelta(seconds = 1),
            )

        async def _async_update_data(self):
            result = results.pop(0)
            if isinstance(result, Exception):
                raise UpdateFailed(result) from result
            return result

    class _Entry:
        entry_id = "ENTRY"

    coordinator = _Coordinator()
    coordinator.config_entry = _Entry()
    await coordinator.async_refresh()
    assert coordinator.last_update_success

    await coordinator.async_refresh()
    assert not coordinator.last_update_success, "no grace period for a refused password"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "native_password_rejected_ENTRY")
    assert issue is not None and issue.translation_key == "native_password_rejected"

    from custom_components.inim_prime.repairs_helpers import clear_password_rejected
    clear_password_rejected(hass, "ENTRY")
    assert ir.async_get(hass).async_get_issue(DOMAIN, "native_password_rejected_ENTRY") is None


# --- the other face of a wrong password: an answer that cannot be decrypted ---
# Some wrong passwords get an immediate answer instead of a closed connection
# (seen on a Prime120 4.07: "Padding is incorrect.", nothing drops).

def _undecryptable() -> ValueError:
    return ValueError("Padding is incorrect.")


async def test_undecryptable_answer_at_startup_is_a_refused_password():
    client = _Client(reads = [_undecryptable()])
    adapter = na.NativeAdapter(client)

    with pytest.raises(na.NativePasswordRejected):
        await adapter.connect()
    assert adapter.password_rejected
    assert (client.connect_calls, client.read_calls) == (1, 1)


async def test_undecryptable_answer_after_a_reconnection_stops_the_attempts():
    client = _Client(reads = [_closed_without_answer(), _undecryptable(), "never read"])
    adapter = na.NativeAdapter(client)

    with pytest.raises(na.NativePasswordRejected):
        await _read(adapter)
    for _ in range(3):
        with pytest.raises(na.NativePasswordRejected):
            await _read(adapter)
    assert (client.read_calls, client.reconnect_calls) == (2, 1)


async def test_undecryptable_answer_on_a_working_session_is_not_a_refused_password():
    client = _Client(reads = ["zones", _undecryptable(), "zones again"])
    adapter = na.NativeAdapter(client)

    assert await _read(adapter) == "zones"
    with pytest.raises(ValueError):
        await _read(adapter)
    assert not adapter.password_rejected
    assert await _read(adapter) == "zones again"


def test_other_value_errors_are_not_a_refused_password():
    assert not na.password_refused(ValueError("Native panel did not report a serial number"))
    assert na.password_refused(_undecryptable())
    assert na.password_refused(_closed_without_answer())
