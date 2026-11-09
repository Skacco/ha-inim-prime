from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from .base_coordinator import InimPrimeBaseCoordinator
from ..gateway import InimPrimeGateway

if TYPE_CHECKING:
    from ..runtime_data import InimPrimeConfigEntry


class InimPrimeThermostatsUpdateCoordinator(InimPrimeBaseCoordinator):
    """Coordinator to fetch thermostat temperatures from the panel (native)."""

    def __init__(
            self,
            hass: HomeAssistant,
            update_interval: timedelta,
            entry: InimPrimeConfigEntry,
            gateway: InimPrimeGateway,
    ):
        super().__init__(
            hass = hass,
            entry = entry,
            gateway = gateway,
            name = "INIM Prime Thermostats",
            update_interval = update_interval,
        )
        self.entry = entry

    async def _async_update_data(self) -> dict:
        try:
            return await self.gateway.update_thermostats()
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(err) from err
