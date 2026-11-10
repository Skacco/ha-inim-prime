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
    extract_native_panel_sections,
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


def test_extract_native_panel_sections():
    data = {
        "arm_away": {"total": ["1", "2"], "instant": ["3"], "stay": [], "outputs": ["5", "6"]},
        "disarm": {"outputs": ["7"]},
        "arm_home": {"total": [], "outputs": []},   # empty -> dropped
        "scan_intervals": {"zones": 5000},          # unrelated -> left intact
    }
    assert extract_native_panel_sections(data) == {
        "arm_away": {"total": [1, 2], "instant": [3], "outputs": [5, 6]},
        "disarm": {"outputs": [7]},
    }
    # command sections are popped; unrelated keys stay
    assert "arm_away" not in data and "disarm" not in data and "arm_home" not in data
    assert data["scan_intervals"] == {"zones": 5000}
    assert extract_native_panel_sections(None) == {}


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


def test_extract_sections_ignores_non_dict_values():
    # a stray non-dict under a command key must not crash extraction
    data = {"arm_away": "oops", "arm_night": {"total": ["2"]}}
    assert extract_native_panel_sections(data) == {"arm_night": {"total": [2]}}


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
