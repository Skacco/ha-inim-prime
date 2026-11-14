"""Experimental: keep the Native connection closed across the panel's cycle.

The panel was seen closing its network connections at instants that fall on
a fixed cadence (PERIODIC_DROP_PHASE + k * PERIODIC_DROP_PERIOD, Unix time),
and only when a local connection is open at that moment. With this option
the Native connection is closed a few seconds before each instant and
reopened a few seconds after. Only Native: PrimeLAN is never touched.

During the pause the entities keep their last value and the requests wait
for the connection to come back; the pause is not recorded as a drop.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .adapters.native_adapter import NativeAdapter
from .const import (
    NATIVE_CYCLE_PAUSE_AFTER_DEFAULT,
    NATIVE_CYCLE_PAUSE_BEFORE_DEFAULT,
    NATIVE_CYCLE_PAUSE_MAX,
    NATIVE_CYCLE_PAUSE_MIN,
    PERIODIC_DROP_PERIOD,
    PERIODIC_DROP_PHASE,
)
from .flow.const import OptionsKey

_LOGGER = logging.getLogger(__name__)


def next_cycle_instant(wall_time: float) -> float:
    """First instant of the cadence that comes after wall_time (Unix time)."""
    cycles = math.floor((wall_time - PERIODIC_DROP_PHASE) / PERIODIC_DROP_PERIOD) + 1
    return PERIODIC_DROP_PHASE + cycles * PERIODIC_DROP_PERIOD


def _margin(value, default: float) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(seconds, NATIVE_CYCLE_PAUSE_MIN), NATIVE_CYCLE_PAUSE_MAX)


async def _sleep_until(wall_time: float) -> None:
    # In short steps on the wall clock: the instants are wall-clock times and
    # a clock adjustment during a long sleep would shift the pause.
    while (remaining := wall_time - time.time()) > 0:
        await asyncio.sleep(min(remaining, 30.0))


async def run_cycle_pause(adapter: NativeAdapter, before: float, after: float) -> None:
    """Pause the Native connection around every instant of the cadence."""
    while True:
        instant = next_cycle_instant(time.time() + before)
        await _sleep_until(instant - before)
        try:
            # a request in progress may use the time left before the instant
            paused = await adapter.pause(idle_timeout = max(before - 0.2, 0.1))
            if not paused:
                _LOGGER.debug(
                    "Cycle pause skipped: the Native connection is not open "
                    "or a request did not end in time"
                )
            else:
                _LOGGER.debug(
                    "Cycle pause: Native connection closed %.1fs before the "
                    "panel's cycle, reopening %.1fs after it",
                    instant - time.time(), after,
                )
                await _sleep_until(instant + after)
        finally:
            if adapter.paused:
                await asyncio.shield(adapter.resume())
                _LOGGER.debug("Cycle pause: Native connection reopened")
        # never the same instant twice, whatever the margins
        await _sleep_until(instant + after + 1.0)


def setup_cycle_pause(hass: HomeAssistant, entry: ConfigEntry, adapter: NativeAdapter | None) -> None:
    """Start the pause task when the option is on and Native is configured."""
    options = entry.options
    if adapter is None or not options.get(OptionsKey.NATIVE_CYCLE_PAUSE):
        return
    before = _margin(options.get(OptionsKey.NATIVE_CYCLE_PAUSE_BEFORE), NATIVE_CYCLE_PAUSE_BEFORE_DEFAULT)
    after = _margin(options.get(OptionsKey.NATIVE_CYCLE_PAUSE_AFTER), NATIVE_CYCLE_PAUSE_AFTER_DEFAULT)
    _LOGGER.debug(
        "Cycle pause enabled: Native connection closed from %.1fs before to "
        "%.1fs after each cycle of the panel", before, after,
    )
    task = entry.async_create_background_task(
        hass, run_cycle_pause(adapter, before, after), "inim_prime cycle pause",
    )
    entry.async_on_unload(task.cancel)
