from dataclasses import dataclass


@dataclass(frozen=True)
class UnifiedScenario:
    scenario_id: int
    label: str
    active: bool | None

    ### Special methods
    def __hash__(self) -> int:
        return hash(self.scenario_id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, UnifiedScenario):
            return NotImplemented

        return self.scenario_id == other.scenario_id
