from __future__ import annotations

import logging
from datetime import timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from .base_coordinator import InimPrimeBaseCoordinator
from ..gateway import InimPrimeGateway
from ..models.scenarios import UnifiedScenario

if TYPE_CHECKING:
    from ..runtime_data import InimPrimeConfigEntry

_LOGGER = logging.getLogger(__name__)


class InimPrimeScenariosUpdateCoordinator(InimPrimeBaseCoordinator):
    """Coordinator to fetch scenarios from the panel."""

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
            name = "INIM Prime Scenarios",
            update_interval = update_interval,
        )
        self.entry = entry

    async def _async_update_data(self) -> MappingProxyType[int, UnifiedScenario]:
        """Fetch data from API."""
        try:
            return await self.gateway.update_scenarios()
        except Exception as err:
            raise UpdateFailed(err) from err
