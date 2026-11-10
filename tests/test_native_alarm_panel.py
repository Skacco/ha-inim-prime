"""Unit tests for the Native (partition-based) alarm panel logic."""
from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)

from custom_components.inim_prime.alarm_control_panel import _native_arm_map
from custom_components.inim_prime.config_flow.const import OptionsKey
from custom_components.inim_prime.config_flow.models import clean_native_panel
from custom_components.inim_prime.entities.alarm_panel import PartitionAlarmPanel
from custom_components.inim_prime.models.partitions import (
    UnifiedArmingStatus,
    UnifiedPartitionState,
)


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


def _panel(parts, arm_map):
    return PartitionAlarmPanel(_FakeCoord(parts), arm_map)


def test_clean_native_panel():
    assert clean_native_panel(None) == {}
    assert clean_native_panel({}) == {}
    # drops empty lists and blanks; coerces to int
    assert clean_native_panel({
        "arm_away": ["1", "2", ""],
        "arm_home": [],
        "arm_night": ["3"],
    }) == {"arm_away": [1, 2], "arm_night": [3]}


def test_native_arm_map_filters_to_known_commands():
    options = {OptionsKey.ALARM_PANEL_NATIVE: {
        "arm_away": [1, 2],
        "arm_night": [2],
        "bogus": [9],       # not a known command -> ignored
        "arm_home": [],     # empty -> ignored
    }}
    assert _native_arm_map(options) == {
        "arm_away": frozenset({1, 2}),
        "arm_night": frozenset({2}),
    }


def test_supported_features_from_arm_map():
    panel = _panel(
        [_FakePart(1, UnifiedArmingStatus.DISARMED)],
        {"arm_away": {1}, "arm_night": {1}},
    )
    feats = panel.supported_features
    assert feats & AlarmControlPanelEntityFeature.ARM_AWAY
    assert feats & AlarmControlPanelEntityFeature.ARM_NIGHT
    assert not (feats & AlarmControlPanelEntityFeature.ARM_HOME)


def test_state_disarmed_and_armed_match():
    arm_map = {"arm_away": {1, 2}, "arm_night": {2}}

    # all disarmed
    panel = _panel(
        [_FakePart(1, UnifiedArmingStatus.DISARMED), _FakePart(2, UnifiedArmingStatus.DISARMED)],
        arm_map,
    )
    assert panel.alarm_state == AlarmControlPanelState.DISARMED

    # armed set {1,2} -> away
    panel = _panel(
        [_FakePart(1, UnifiedArmingStatus.ARM_AWAY), _FakePart(2, UnifiedArmingStatus.ARM_AWAY)],
        arm_map,
    )
    assert panel.alarm_state == AlarmControlPanelState.ARMED_AWAY

    # armed set {2} -> night
    panel = _panel(
        [_FakePart(1, UnifiedArmingStatus.DISARMED), _FakePart(2, UnifiedArmingStatus.ARM_AWAY)],
        arm_map,
    )
    assert panel.alarm_state == AlarmControlPanelState.ARMED_NIGHT


def test_state_custom_bypass_and_triggered():
    arm_map = {"arm_away": {1, 2}, "arm_night": {2}}

    # armed set {1} matches nothing -> custom bypass
    panel = _panel(
        [_FakePart(1, UnifiedArmingStatus.ARM_AWAY), _FakePart(2, UnifiedArmingStatus.DISARMED)],
        arm_map,
    )
    assert panel.alarm_state == AlarmControlPanelState.ARMED_CUSTOM_BYPASS

    # a partition in alarm -> triggered (wins over everything)
    panel = _panel(
        [_FakePart(1, UnifiedArmingStatus.ARM_AWAY, state=UnifiedPartitionState.ALARM),
         _FakePart(2, UnifiedArmingStatus.ARM_AWAY)],
        arm_map,
    )
    assert panel.alarm_state == AlarmControlPanelState.TRIGGERED
