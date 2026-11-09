from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry

from .coordinators import (
    InimPrimeZonesUpdateCoordinator,
    InimPrimePartitionsUpdateCoordinator,
    InimPrimeGSMUpdateCoordinator,
    InimPrimeSystemFaultsUpdateCoordinator,
    InimPrimePanelLogEventsCoordinator,
    InimPrimeOutputsUpdateCoordinator,
    InimPrimeScenariosUpdateCoordinator,
    InimPrimeTimersUpdateCoordinator,
)

from .gateway import InimPrimeGateway


@dataclass
class InimPrimeCoordinators:
    """Coordinators for this entry, typed per-feature instead of string-keyed.

    zones / partitions always exist. The PrimeLAN-only ones are None on a
    native-only entry -- platforms check for None instead of doing
    dict.get(SOME_STRING_KEY).
    """
    zones: InimPrimeZonesUpdateCoordinator | None = None
    partitions: InimPrimePartitionsUpdateCoordinator | None = None
    gsm: InimPrimeGSMUpdateCoordinator | None = None
    system_faults: InimPrimeSystemFaultsUpdateCoordinator | None = None
    panel_log_events: InimPrimePanelLogEventsCoordinator | None = None
    outputs: InimPrimeOutputsUpdateCoordinator | None = None
    scenarios: InimPrimeScenariosUpdateCoordinator | None = None
    timers: InimPrimeTimersUpdateCoordinator | None = None

    def all(self) -> list:
        """All coordinators that exist, for shutdown/iteration."""
        return [
            c for c in (
                self.zones, self.partitions, self.gsm,
                self.system_faults, self.panel_log_events,
                self.outputs, self.scenarios, self.timers,
            ) if c is not None
        ]

@dataclass
class InimPrimeRuntimeData:
    gateway: InimPrimeGateway
    coordinators: InimPrimeCoordinators
    # Per-shutter travel times in seconds: {module: {"open": s, "close": s}}.
    # Shared, mutable: the cover reads it, the number entities edit it live.
    shutter_times: dict = field(default_factory=dict)

type InimPrimeConfigEntry = ConfigEntry[InimPrimeRuntimeData]