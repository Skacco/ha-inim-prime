"""Timers are read from panel memory like Prime/STUDIO's monitoring page."""
import asyncio
from types import SimpleNamespace

from custom_components.inim_prime.adapters import native_adapter as na
from custom_components.inim_prime.gateway import InimPrimeGateway


def _label(text: str) -> bytes:
    return text.encode().ljust(15, b" ") + b"\x00"


def _fake_client():
    labels = b"".join(
        _label(n) for n in ["CARRAIO", "Chiusura zip", "Astro per fari"] + [f"TIMER      {i:03d}" for i in range(4, 41)]
    )
    memory = {
        na.TIMER_LABELS_ADDRESS: labels,
        na.TIMERS_ACTIVE_ADDRESS: bytes([0b001, 0, 0, 0, 0, 0]),
        na.TIMERS_ENABLED_ADDRESS: bytes([0b101, 0, 0, 0, 0, 0]),
    }
    reads = []

    async def read_memory(address, length):
        reads.append(address)
        return memory[address][:length]

    client = SimpleNamespace(_lock = asyncio.Lock(), _protocol = SimpleNamespace(read_memory = read_memory))
    return client, reads


async def _no_sleep(_):
    return None


async def test_timers_read_and_filtered(monkeypatch):
    monkeypatch.setattr(na, "_sleep", _no_sleep)
    client, reads = _fake_client()
    adapter = na.NativeAdapter(client)
    gateway = InimPrimeGateway(native = adapter)

    timers = await gateway.update_timers()

    assert {t.label for t in timers.values()} == {"CARRAIO", "Chiusura zip", "Astro per fari"}
    assert timers[0].active and timers[0].enabled
    assert not timers[1].active and not timers[1].enabled
    assert not timers[2].active and timers[2].enabled
    assert len(adapter.timers) == 40

    # Labels are read once, the two masks every update.
    await gateway.update_timers()
    assert reads.count(na.TIMER_LABELS_ADDRESS) == 2  # read twice, until stable
    assert reads.count(na.TIMERS_ACTIVE_ADDRESS) == 2


async def test_timer_labels_reread_until_stable(monkeypatch):
    monkeypatch.setattr(na, "_sleep", _no_sleep)
    client, reads = _fake_client()
    good = client._protocol.read_memory
    stale = [b"".join(_label(f"SCENARIO   {i:03d}") for i in range(19, 59))]

    async def read_memory(address, length):
        if address == na.TIMER_LABELS_ADDRESS and stale:
            reads.append(address)
            return stale.pop()
        return await good(address, length)

    client._protocol.read_memory = read_memory
    gateway = InimPrimeGateway(native = na.NativeAdapter(client))
    timers = await gateway.update_timers()
    assert {t.label for t in timers.values()} == {"CARRAIO", "Chiusura zip", "Astro per fari"}
