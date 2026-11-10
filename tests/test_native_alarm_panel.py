"""Unit tests for the Native (partition-based) alarm panel logic."""
from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)

from custom_components.inim_prime.alarm_control_panel import (
    _native_arm_map,
    _native_output_map,
)
from custom_components.inim_prime.config_flow.const import OptionsKey
from custom_components.inim_prime.config_flow.models import (
    clean_native_panel,
    extract_hybrid_sections,
    extract_command_partitions,
)
from custom_components.inim_prime.entities.alarm_panel import PartitionAlarmPanel
from custom_components.inim_prime.models.partitions import (
    UnifiedArmingStatus,
    UnifiedPartitionState,
)

AWAY = UnifiedArmingStatus.ARM_AWAY
INSTANT = UnifiedArmingStatus.ARM_INSTANT
STAY = UnifiedArmingStatus.ARM_STAY
OFF = UnifiedArmingStatus.DISARMED


class _FakePart:
    def __init__(self, pid, arming, state=UnifiedPartitionState.OK, label=""):
        self.partition_id = pid
        self.arming_status = arming
        self.partition_state = state
        self.label = label or f"P{pid}"


class _FakeGateway:
    def __init__(self, parts):
        self.partitions = {p.partition_id: p for p in parts}


class _FakeCoord:
    def __init__(self, parts):
        self.gateway = _FakeGateway(parts)
        self.serial_number = "SER"
        self.last_update_success = True


def _panel(parts, targets):
    return PartitionAlarmPanel(_FakeCoord(parts), targets)


# Partition label -> id, as the flow builds it from the gateway.
PIDX = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P5": 5, "P6": 6, "P8": 8}


def test_extract_hybrid_sections_partition_only():
    data = {
        "arm_away": {"P0": "total", "P1": "total", "P2": "instant", "P3": "disarmed", "outputs": ["5", "6"]},
        "disarm": {"outputs": ["7"]},
        "arm_home": {"P0": "disarmed", "outputs": []},   # nothing armed -> dropped
        "scan_intervals": {"zones": 5000},               # unrelated -> left intact
    }
    scenario_map, native_map = extract_hybrid_sections(data, PIDX)
    assert scenario_map == {}
    assert native_map == {
        "arm_away": {"total": [0, 1], "instant": [2], "outputs": [5, 6]},
        "disarm": {"outputs": [7]},
    }
    # command sections are popped; unrelated keys stay
    assert "arm_away" not in data and "disarm" not in data and "arm_home" not in data
    assert data["scan_intervals"] == {"zones": 5000}
    assert extract_hybrid_sections(None) == ({}, {})


def test_extract_hybrid_sections_mixed():
    # scenario set -> scenario-driven (partition fields ignored); empty scenario
    # -> partition-driven.
    data = {
        "arm_away": {"scenario": "2", "P0": "total"},   # scenario wins
        "arm_night": {"scenario": "3"},
        "arm_vacation": {"scenario": "", "P0": "total", "P1": "total", "P2": "instant"},
        "disarm": {"scenario": "1"},
    }
    scenario_map, native_map = extract_hybrid_sections(data, PIDX)
    assert scenario_map == {"arm_away": 2, "arm_night": 3, "disarm": 1}
    assert native_map == {"arm_vacation": {"total": [0, 1], "instant": [2]}}


def test_clean_native_panel():
    assert clean_native_panel(None) == {}
    assert clean_native_panel({
        "arm_away": {"total": ["1"], "stay": []},
        "arm_home": {},
    }) == {"arm_away": {"total": [1]}}


def test_native_arm_map_builds_per_partition_modes():
    options = {OptionsKey.ALARM_PANEL_NATIVE: {
        "arm_away": {"total": [1, 2], "instant": [3]},
        "arm_night": {"instant": [2]},
        "arm_home": {},          # empty -> ignored
        "bogus": {"total": [9]},  # unknown command -> ignored
    }}
    m = _native_arm_map(options)
    assert m["arm_away"] == {1: AWAY, 2: AWAY, 3: INSTANT}
    assert m["arm_night"] == {2: INSTANT}
    assert "arm_home" not in m and "bogus" not in m


def test_native_output_map_builds_per_command_outputs():
    options = {OptionsKey.ALARM_PANEL_NATIVE: {
        "arm_away": {"total": [1], "outputs": [5, 6]},
        "disarm": {"outputs": [7]},
        "arm_home": {"outputs": []},      # empty -> ignored
        "bogus": {"outputs": [9]},        # unknown command -> ignored
    }}
    m = _native_output_map(options)
    assert m["arm_away"] == [5, 6]
    assert m["disarm"] == [7]
    assert "arm_home" not in m and "bogus" not in m
    assert _native_output_map({}) == {}


def test_extract_command_partitions():
    idx = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    # one command's detail form: a mode per partition + outputs
    section = {"P0": "total", "P1": "total", "P2": "instant", "P3": "disarmed", "outputs": ["31"]}
    assert extract_command_partitions(section, idx) == {
        "total": [0, 1], "instant": [2], "outputs": [31],
    }
    # disarm detail: outputs only
    assert extract_command_partitions({"outputs": ["7"]}, idx) == {"outputs": [7]}
    # nothing armed -> empty
    assert extract_command_partitions({"P0": "disarmed"}, idx) == {}
    assert extract_command_partitions(None, idx) == {}


def test_extract_sections_ignores_non_dict_values():
    # a stray non-dict under a command key must not crash extraction
    data = {"arm_away": "oops", "arm_night": {"P2": "total"}}
    scenario_map, native_map = extract_hybrid_sections(data, {"P2": 2})
    assert scenario_map == {}
    assert native_map == {"arm_night": {"total": [2]}}


def test_supported_features_from_targets():
    panel = _panel([_FakePart(1, OFF)], {"arm_away": {1: AWAY}, "arm_night": {1: INSTANT}})
    feats = panel.supported_features
    assert feats & AlarmControlPanelEntityFeature.ARM_AWAY
    assert feats & AlarmControlPanelEntityFeature.ARM_NIGHT
    assert not (feats & AlarmControlPanelEntityFeature.ARM_HOME)


def test_state_matches_exact_mode_mix():
    # away mixes modes: P1 total, P2 instant
    targets = {"arm_away": {1: AWAY, 2: INSTANT}, "arm_night": {2: INSTANT}}

    assert _panel([_FakePart(1, OFF), _FakePart(2, OFF)], targets).alarm_state == \
        AlarmControlPanelState.DISARMED

    assert _panel([_FakePart(1, AWAY), _FakePart(2, INSTANT)], targets).alarm_state == \
        AlarmControlPanelState.ARMED_AWAY

    assert _panel([_FakePart(1, OFF), _FakePart(2, INSTANT)], targets).alarm_state == \
        AlarmControlPanelState.ARMED_NIGHT

    # same partitions armed but wrong mode (P2 total, not instant) -> no match
    assert _panel([_FakePart(1, AWAY), _FakePart(2, AWAY)], targets).alarm_state == \
        AlarmControlPanelState.ARMED_CUSTOM_BYPASS


def test_triggered_wins():
    targets = {"arm_away": {1: AWAY}}
    panel = _panel(
        [_FakePart(1, AWAY, state=UnifiedPartitionState.ALARM)],
        targets,
    )
    assert panel.alarm_state == AlarmControlPanelState.TRIGGERED


# ---------------------------------------------------------------------------
# Hybrid panel (both backends): per-command scenario-or-partitions.
# ---------------------------------------------------------------------------
from custom_components.inim_prime.entities.alarm_panel import HybridAlarmPanel


class _FakeScenario:
    def __init__(self, sid, active):
        self.scenario_id = sid
        self.active = active
        self.label = f"S{sid}"


class _FakeScenGateway:
    def __init__(self, scenarios):
        self.scenarios = {s.scenario_id: s for s in scenarios}


class _FakeScenCoord:
    def __init__(self, scenario_ids, active):
        self.gateway = _FakeScenGateway(
            [_FakeScenario(sid, sid == active) for sid in scenario_ids]
        )
        self.serial_number = "SER"
        self.last_update_success = True


def _hybrid(parts, scenario_ids, active, scenario_map, targets, outputs=None):
    return HybridAlarmPanel(
        _FakeScenCoord(scenario_ids, active),
        _FakeCoord(parts),
        scenario_map,
        targets,
        outputs,
    )


def test_hybrid_scenario_command_not_in_partition_targets():
    # A command with a scenario is scenario-driven: it must NOT be a partition
    # target, even if a target was also passed in.
    panel = _hybrid(
        [_FakePart(1, OFF)],
        scenario_ids=[2], active=None,
        scenario_map={"arm_away": 2},
        targets={"arm_away": {1: AWAY}, "arm_vacation": {1: INSTANT}},
    )
    assert "arm_away" not in panel._targets
    assert "arm_vacation" in panel._targets
    feats = panel.supported_features
    assert feats & AlarmControlPanelEntityFeature.ARM_AWAY      # from scenario
    assert feats & AlarmControlPanelEntityFeature.ARM_VACATION  # from partitions


def test_hybrid_state_from_active_scenario():
    panel = _hybrid(
        [_FakePart(1, AWAY)],
        scenario_ids=[2, 3], active=2,
        scenario_map={"arm_away": 2, "arm_night": 3},
        targets={},
    )
    assert panel.alarm_state == AlarmControlPanelState.ARMED_AWAY


def test_hybrid_state_from_partition_mix():
    # vacation is partition-driven with a mixed set; no scenario active.
    panel = _hybrid(
        [_FakePart(0, AWAY), _FakePart(2, INSTANT)],
        scenario_ids=[2], active=None,
        scenario_map={"arm_away": 2},
        targets={"arm_vacation": {0: AWAY, 2: INSTANT}},
    )
    assert panel.alarm_state == AlarmControlPanelState.ARMED_VACATION


def test_hybrid_disarmed_and_triggered():
    panel = _hybrid(
        [_FakePart(0, OFF), _FakePart(2, OFF)],
        scenario_ids=[2], active=None,
        scenario_map={"arm_away": 2}, targets={"arm_vacation": {0: AWAY}},
    )
    assert panel.alarm_state == AlarmControlPanelState.DISARMED

    trig = _hybrid(
        [_FakePart(0, AWAY, state=UnifiedPartitionState.ALARM)],
        scenario_ids=[2], active=2,
        scenario_map={"arm_away": 2}, targets={},
    )
    assert trig.alarm_state == AlarmControlPanelState.TRIGGERED
