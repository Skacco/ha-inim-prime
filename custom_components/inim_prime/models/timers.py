from dataclasses import dataclass


@dataclass(frozen=True)
class UnifiedTimer:
    timer_id: int
    label: str
    active: bool
    enabled: bool
