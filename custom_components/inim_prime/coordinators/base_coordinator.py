from __future__ import annotations

import functools
import time
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, _LOGGER
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

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
    # True while failed reads are being covered by the last data
    _riding_through: bool = False
    # monotonic/wall time at which the current run of failed reads began
    _drop_started: float | None = None
    _drop_started_at = None
    # last completed drop: when it began and how long it lasted (seconds)
    last_drop_at = None
    last_drop_duration: float | None = None

    def _note_read_failed(self) -> None:
        if self._drop_started is None:
            self._drop_started = time.monotonic()
            self._drop_started_at = dt_util.utcnow()

    def _note_read_ok(self) -> None:
        if self._drop_started is None:
            return
        self.last_drop_at = self._drop_started_at
        self.last_drop_duration = round(time.monotonic() - self._drop_started, 1)
        self._drop_started = None
        self._drop_started_at = None

    @property
    def backend_reachable(self) -> bool:
        """False as soon as a read fails, even while the last data is still
        being served (used for the "last successful poll" diagnostics)."""
        return bool(self.last_update_success) and not self._riding_through

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
            except UpdateFailed as err:
                self._note_read_failed()
                if (
                    self.data is not None
                    and self._last_success is not None
                    and time.monotonic() - self._last_success <= CONNECTION_GRACE_PERIOD
                ):
                    if not self._riding_through:
                        self._riding_through = True
                        _LOGGER.debug(
                            "%s: read failed (%r), keeping the last data",
                            self.name, err.__cause__ or err,
                        )
                    return self.data
                self._riding_through = False
                raise
            if self._riding_through:
                self._riding_through = False
                _LOGGER.debug("%s: reads working again", self.name)
            self._note_read_ok()
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