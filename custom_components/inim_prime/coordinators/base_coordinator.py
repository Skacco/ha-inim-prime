from __future__ import annotations

import functools
import time
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, _LOGGER
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

if TYPE_CHECKING:
    # only imported by the type checker -- never executes at runtime,
    # so this can't participate in a circular import
    from ..runtime_data import InimPrimeConfigEntry

from ..gateway import InimPrimeGateway
from ..entry_data import InimPrimeConfigData, InimPrimeOptionsData, get_entry_data, get_entry_options
from ..const import CONNECTION_GRACE_PERIOD


class InimPrimeBaseCoordinator(DataUpdateCoordinator):
    """Common init for all INIM Prime coordinators: stores the gateway and
    typed, cached views of entry.data / entry.options.

    Caching (rather than calling get_entry_data/get_entry_options on every
    access) relies on both entry.data and entry.options changes always
    triggering a full entry reload -- which recreates coordinators from
    scratch -- rather than mutating them in place on a live entry.
    """
    entry_data: InimPrimeConfigData
    entry_options: InimPrimeOptionsData

    # monotonic time of the last successful read (None until the first one)
    _last_success: float | None = None

    def __init_subclass__(cls, **kwargs) -> None:
        """Make every coordinator ride through short connection drops.

        The panel can stop answering for a few seconds (it was seen closing
        the local connection periodically). Instead of flipping every entity
        to "unavailable" at the first failed read, a failed read within
        CONNECTION_GRACE_PERIOD of the last good one returns the last data.
        Past the grace period the failure is reported as usual.
        """
        super().__init_subclass__(**kwargs)
        fetch = cls.__dict__.get("_async_update_data")
        if fetch is None:
            return

        @functools.wraps(fetch)
        async def _async_update_data(self):
            try:
                data = await fetch(self)
            except UpdateFailed:
                if (
                    self.data is not None
                    and self._last_success is not None
                    and time.monotonic() - self._last_success <= CONNECTION_GRACE_PERIOD
                ):
                    return self.data
                raise
            self._last_success = time.monotonic()
            return data

        cls._async_update_data = _async_update_data

    def __init__(
            self,
            hass: HomeAssistant,
            update_interval: timedelta,
            entry: InimPrimeConfigEntry,
            gateway: InimPrimeGateway,
            name: str,
    ):
        super().__init__(
            hass = hass,
            config_entry = entry,
            logger = _LOGGER,
            name = name,
            update_interval = update_interval,
        )
        self.gateway = gateway
        self.entry_data = get_entry_data(entry)
        self.entry_options = get_entry_options(entry)

    @property
    def serial_number(self) -> str:
        return self.entry_data["serial_number"]