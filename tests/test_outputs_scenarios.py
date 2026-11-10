"""Outputs (light) and scenarios (button/sensor) over a simulated PrimeLAN Web API."""
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.inim_prime.const import DOMAIN

SERIAL = "PRIME123"


class FakePanel:
    """Answers PrimeLAN api.cgi commands like the panel does."""

    def __init__(self):
        # t: 0 = normal/hybrid, 1 = dimming. st: 0/1, or 0..100 for dimmers
        self.outputs = {
            31: {"lb": "Luce Portico   ", "tl": 31, "st": 0, "t": 0},
            570: {"lb": "Faretti Soggiorno", "tl": 570, "st": 40, "t": 1, "v": "230,1", "p": "12,5", "c": "0,98"},
        }
        self.scenarios = {0: {"lb": "Spento", "st": 0}, 1: {"lb": "Totale", "st": 0}, 2: {"lb": "Notte", "st": 1}}
        # am: 1 away, 2 stay, 3 instant, 4 disarmed; st: 0 alarm, 1 ok
        self.partitions = {1: {"lb": "Casa", "st": 1, "am": 4}, 2: {"lb": "Esterni", "st": 1, "am": 4}}
        # what each scenario arms
        self.scenario_arms = {0: {}, 1: {1: 1, 2: 1}, 2: {2: 3}}
        self.calls = []

    async def request(self, cmd, timeout=None, retries=None, retry_delay=None, **params):
        self.calls.append((cmd, params))
        if cmd == "ping":
            return {}
        if cmd == "get_zones_status":
            return {"zone": [{"id": "1", "lb": "Ingresso", "tl": "1", "st": "1", "mm": "0", "by": "1"}]}
        if cmd == "get_partitions_status":
            return {"part": [{"id": str(i), "lb": p["lb"], "st": str(p["st"]), "am": str(p["am"]), "mm": "0"}
                             for i, p in self.partitions.items()]}
        if cmd == "get_outputs_status":
            return {"cmd": [{"id": str(i), **{k: str(v) for k, v in o.items()}} for i, o in self.outputs.items()]}
        if cmd == "get_scenarios_status":
            return {"sce": [{"id": str(i), "lb": s["lb"], "st": str(s["st"])} for i, s in self.scenarios.items()]}
        if cmd == "get_faults_status":
            return {"vcc": "13,7", "fau": "0"}
        if cmd == "get_gsm_status":
            return {"vcc": "13,6", "fwv": "1.0", "gop": "TIM", "gpw": "20", "cre": "--"}
        if cmd == "get_log_elements":
            return {"log": []}
        if cmd == "set_outputs_mode":
            self.outputs[int(params["p1"])]["st"] = int(params["p2"])
            return {}
        if cmd == "set_scenarios_mode":
            sid = int(params["p1"])
            for i, s in self.scenarios.items():
                s["st"] = 1 if i == sid else 0
            for pid, part in self.partitions.items():
                part["am"] = self.scenario_arms.get(sid, {}).get(pid, 4)
            return {}
        raise AssertionError(f"unexpected command {cmd} {params}")


async def _setup(hass: HomeAssistant, panel: FakePanel, alarm_panel: dict | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        minor_version=0,
        unique_id=SERIAL,
        data={"serial_number": SERIAL, "host": "192.0.2.10",
              "primelan": {"api_key": "k", "use_https": False}},
        options={"scan_intervals": {"zones": 5000, "partitions": 10000, "gsm": 30000,
                                    "system_faults": 15000, "panel_log_events": 15000},
                 "panel_log_events_fetch_limit": 40,
                 **({"alarm_panel": alarm_panel} if alarm_panel else {})},
    )
    entry.add_to_hass(hass)
    with patch("inim.prime.primelan.client.InimPrimeClient._request", side_effect=panel.request):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _eid(hass, platform, unique_id):
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)


async def test_outputs_switches_and_dimmers(hass: HomeAssistant):
    panel = FakePanel()
    await _setup(hass, panel)

    relay = _eid(hass, "switch", f"{SERIAL}_output_31")
    assert _eid(hass, "light", f"{SERIAL}_output_31") is None
    assert _eid(hass, "switch", f"{SERIAL}_output_570") is None
    dimmer = _eid(hass, "light", f"{SERIAL}_output_570")
    assert relay and dimmer

    s = hass.states.get(relay)
    assert s.state == "off"
    assert s.attributes["output_id"] == 31

    s = hass.states.get(dimmer)
    assert s.state == "on"
    assert s.attributes["supported_color_modes"] == ["brightness"]
    assert s.attributes["brightness"] == round(40 * 255 / 100)

    with patch("inim.prime.primelan.client.InimPrimeClient._request", side_effect=panel.request):
        await hass.services.async_call("switch", "turn_on", {"entity_id": relay}, blocking=True)
        await hass.async_block_till_done()
        assert ("set_outputs_mode", {"p1": 31, "p2": 1}) in panel.calls
        assert hass.states.get(relay).state == "on"

        await hass.services.async_call("light", "turn_on", {"entity_id": dimmer, "brightness": 255}, blocking=True)
        await hass.async_block_till_done()
        assert ("set_outputs_mode", {"p1": 570, "p2": 100}) in panel.calls
        assert hass.states.get(dimmer).attributes["brightness"] == 255

        await hass.services.async_call("light", "turn_off", {"entity_id": dimmer}, blocking=True)
        await hass.async_block_till_done()
        assert ("set_outputs_mode", {"p1": 570, "p2": 0}) in panel.calls
        assert hass.states.get(dimmer).state == "off"

        # Dimmer back on without brightness -> full level (last level was 0)
        await hass.services.async_call("light", "turn_on", {"entity_id": dimmer}, blocking=True)
        await hass.async_block_till_done()
        assert panel.calls[-2] == ("set_outputs_mode", {"p1": 570, "p2": 100}) or \
            ("set_outputs_mode", {"p1": 570, "p2": 100}) in panel.calls


async def test_scenarios(hass: HomeAssistant):
    panel = FakePanel()
    await _setup(hass, panel)

    btn = _eid(hass, "button", f"{SERIAL}_scenario_1_activate")
    active = _eid(hass, "sensor", f"{SERIAL}_active_scenario")
    assert btn and _eid(hass, "button", f"{SERIAL}_scenario_2_activate")
    assert hass.states.get(active).state == "Notte"
    assert hass.states.get(btn).attributes["scenario_id"] == 1

    with patch("inim.prime.primelan.client.InimPrimeClient._request", side_effect=panel.request):
        await hass.services.async_call("button", "press", {"entity_id": btn}, blocking=True)
        await hass.async_block_till_done()
    assert ("set_scenarios_mode", {"p1": 1}) in panel.calls
    assert hass.states.get(active).state == "Totale"
    # no alarm_panel options -> no alarm panel entity
    assert _eid(hass, "alarm_control_panel", f"{SERIAL}_alarm_panel") is None


async def test_unload(hass: HomeAssistant):
    panel = FakePanel()
    entry = await _setup(hass, panel)
    with patch("inim.prime.primelan.client.InimPrimeClient._request", side_effect=panel.request):
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_options_flow_has_outputs_and_scenarios(hass: HomeAssistant):
    panel = FakePanel()
    entry = await _setup(hass, panel)
    coords = entry.runtime_data.coordinators
    # Entry created before these options existed -> PrimeLAN defaults (Sdomotica-like)
    assert coords.outputs.update_interval.total_seconds() == 30
    assert coords.scenarios.update_interval.total_seconds() == 10

    opts = hass.config_entries.options
    with patch("inim.prime.primelan.client.InimPrimeClient._request", side_effect=panel.request):
        result = await opts.async_init(entry.entry_id)                       # main menu
        result = await opts.async_configure(result["flow_id"], {"next_step_id": "scan"})
        fields = result["data_schema"].schema["scan_intervals"].schema.schema
        keys = {str(k): k.default() for k in fields}
        assert keys["outputs"] == 30000 and keys["scenarios"] == 10000
        assert keys["partitions"] == 10000  # entry's stored value is kept

        new = {**keys, "outputs": 2000, "scenarios": 4000}
        result = await opts.async_configure(                                  # submit -> save & close
            result["flow_id"],
            {"scan_intervals": new},
        )
        await hass.async_block_till_done()

    assert entry.options["scan_intervals"]["outputs"] == 2000
    coords = entry.runtime_data.coordinators
    assert coords.outputs.update_interval.total_seconds() == 2
    assert coords.scenarios.update_interval.total_seconds() == 4


async def test_new_primelan_defaults():
    from custom_components.inim_prime.config_flow.schemas import build_options_schema
    schema = build_options_schema(has_native=False, has_primelan=True)
    fields = list(schema.values())[0].schema.schema
    d = {str(k): k.default() for k in fields}
    assert (d["zones"], d["partitions"], d["outputs"], d["scenarios"]) == (5000, 5000, 30000, 10000)
    schema = build_options_schema(has_native=True, has_primelan=True)
    d = {str(k): k.default() for k in list(schema.values())[0].schema.schema}
    assert (d["zones"], d["partitions"], d["outputs"]) == (500, 500, 500)



def test_shutters_split_from_general():
    """Shutter sections live in their own schema, not the general options form."""
    import re
    from custom_components.inim_prime.config_flow.schemas import (
        build_options_schema, build_shutters_schema,
    )
    section_key = re.compile(r"^\d{1,3}\s*·")

    # General form (options flow) excludes the per-shutter sections.
    general = build_options_schema(
        has_native=True, has_primelan=True,
        shutters=[1, 2], output_choices=[(5, "Apri"), (6, "Chiudi")],
        include_shutters=False,
    )
    assert not any(section_key.match(str(k)) for k in general)

    # Dedicated shutters schema has one section per module.
    shut = build_shutters_schema(
        shutters=[1, 2], output_choices=[(5, "Apri"), (6, "Chiudi")],
    )
    assert sum(1 for k in shut if section_key.match(str(k))) == 2

    # No modules -> empty (so the menu entry is hidden).
    assert build_shutters_schema(shutters=[], output_choices=[(5, "Apri")]) == {}

    # Back-compat: the single config-flow form still includes shutters.
    full = build_options_schema(
        has_native=True, has_primelan=True,
        shutters=[1], output_choices=[(5, "Apri"), (6, "Chiudi")],
        include_shutters=True,
    )
    assert any(section_key.match(str(k)) for k in full)


async def test_alarm_panel_uses_scenarios(hass: HomeAssistant):
    panel = FakePanel()
    panel.scenarios[2]["st"] = 0
    panel.scenarios[0]["st"] = 1
    await _setup(hass, panel, {"disarm": 0, "arm_vacation": 1, "arm_night": 2})

    eid = _eid(hass, "alarm_control_panel", f"{SERIAL}_alarm_panel")
    assert eid
    st = hass.states.get(eid)
    assert st.state == "disarmed"
    assert st.attributes["code_arm_required"] is False
    # only configured commands are offered: night + vacation
    from homeassistant.components.alarm_control_panel import AlarmControlPanelEntityFeature as F
    assert st.attributes["supported_features"] == F.ARM_NIGHT | F.ARM_VACATION

    with patch("inim.prime.primelan.client.InimPrimeClient._request", side_effect=panel.request):
        await hass.services.async_call("alarm_control_panel", "alarm_arm_vacation", {"entity_id": eid}, blocking=True)
        await hass.async_block_till_done()
        assert ("set_scenarios_mode", {"p1": 1}) in panel.calls
        st = hass.states.get(eid)
        assert st.state == "armed_vacation"
        assert st.attributes["active_scenario"] == "Totale"
        assert st.attributes["armed_partitions"] == ["Casa", "Esterni"]

        await hass.services.async_call("alarm_control_panel", "alarm_arm_night", {"entity_id": eid}, blocking=True)
        await hass.async_block_till_done()
        assert hass.states.get(eid).state == "armed_night"

        # alarm on a partition -> triggered
        panel.partitions[2]["st"] = 0
        await entry_refresh(hass)
        assert hass.states.get(eid).state == "triggered"
        panel.partitions[2]["st"] = 1

        await hass.services.async_call("alarm_control_panel", "alarm_disarm", {"entity_id": eid}, blocking=True)
        await hass.async_block_till_done()
        assert ("set_scenarios_mode", {"p1": 0}) in panel.calls
        assert hass.states.get(eid).state == "disarmed"

        # armed from the keypad without a mapped scenario -> arming for a few
        # seconds (scenario being re-read), then custom bypass
        panel.partitions[1]["am"] = 1
        await entry_refresh(hass)
        assert hass.states.get(eid).state == "arming"
        import time as _t
        with patch("custom_components.inim_prime.entities.alarm_panel.time.monotonic", return_value=_t.monotonic() + 60):
            await entry_refresh(hass)
            assert hass.states.get(eid).state == "armed_custom_bypass"


async def entry_refresh(hass):
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    await entry.runtime_data.coordinators.partitions.async_refresh()
    await entry.runtime_data.coordinators.scenarios.async_refresh()
    await hass.async_block_till_done()


async def test_options_flow_alarm_panel(hass: HomeAssistant):
    panel = FakePanel()
    entry = await _setup(hass, panel)
    opts = hass.config_entries.options
    with patch("inim.prime.primelan.client.InimPrimeClient._request", side_effect=panel.request):
        # Save-on-submit: one command per flow session. PrimeLAN-only -> each
        # command goes straight to the scenario form (no Method step).
        result = await opts.async_init(entry.entry_id)
        result = await opts.async_configure(result["flow_id"], {"next_step_id": "alarm"})
        result = await opts.async_configure(result["flow_id"], {"next_step_id": "cmd_disarm"})
        await opts.async_configure(result["flow_id"], {"scenario": 0})   # save & close
        await hass.async_block_till_done()

        result = await opts.async_init(entry.entry_id)
        result = await opts.async_configure(result["flow_id"], {"next_step_id": "alarm"})
        result = await opts.async_configure(result["flow_id"], {"next_step_id": "cmd_arm_away"})
        await opts.async_configure(result["flow_id"], {"scenario": 2.0})  # save & close (merges)
        await hass.async_block_till_done()
    assert entry.options["alarm_panel"] == {"disarm": 0, "arm_away": 2}
    assert _eid(hass, "alarm_control_panel", f"{SERIAL}_alarm_panel")


async def test_zone_devices_linked_to_panel(hass: HomeAssistant):
    from homeassistant.helpers import device_registry as dr
    panel = FakePanel()
    entry = await _setup(hass, panel)
    reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    panel_dev = reg.async_get_device(identifiers={(DOMAIN, SERIAL)})
    zones = reg.async_get_device(identifiers={(DOMAIN, f"{SERIAL}_zones")})
    parts = reg.async_get_device(identifiers={(DOMAIN, f"{SERIAL}_partitions")})
    # The grouping devices hang off the panel...
    assert zones.via_device_id == panel_dev.id and parts.via_device_id == panel_dev.id
    # ...and there are no per-item zone/partition devices any more.
    assert reg.async_get_device(identifiers={(DOMAIN, f"{SERIAL}_zone_1")}) is None
    assert reg.async_get_device(identifiers={(DOMAIN, f"{SERIAL}_partition_1")}) is None
    # Each zone/partition entity lives on the grouping device.
    zone_entity = ent_reg.async_get_entity_id("binary_sensor", DOMAIN, f"{SERIAL}_zone_1_triggered")
    part_entity = ent_reg.async_get_entity_id("sensor", DOMAIN, f"{SERIAL}_partition_1_stato")
    assert ent_reg.async_get(zone_entity).device_id == zones.id
    assert ent_reg.async_get(part_entity).device_id == parts.id


async def test_alarm_panel_no_custom_bypass_while_scenario_lags(hass: HomeAssistant):
    """Partitions switch first, the active scenario a few reads later."""
    panel = FakePanel()
    panel.scenarios[2]["st"] = 0
    panel.scenarios[0]["st"] = 1
    await _setup(hass, panel, {"disarm": 0, "arm_vacation": 1, "arm_night": 2})
    eid = _eid(hass, "alarm_control_panel", f"{SERIAL}_alarm_panel")

    original = panel.request
    lag = {"left": 0, "target": None}
    seen = []

    async def lagging(cmd, **params):
        if cmd == "set_scenarios_mode":
            sid = int(params["p1"])
            for pid, part in panel.partitions.items():
                part["am"] = panel.scenario_arms.get(sid, {}).get(pid, 4)
            panel.calls.append((cmd, params))
            lag.update(left=3, target=sid)
            return {}
        if cmd == "get_scenarios_status" and lag["target"] is not None:
            lag["left"] -= 1
            if lag["left"] <= 0:
                for i, s in panel.scenarios.items():
                    s["st"] = 1 if i == lag["target"] else 0
                lag["target"] = None
        res = await original(cmd, **params)
        st = hass.states.get(eid)
        if st:
            seen.append(st.state)
        return res

    async def no_sleep(*_):
        return None

    with patch("inim.prime.primelan.client.InimPrimeClient._request", side_effect=lagging), \
         patch("custom_components.inim_prime.entities.alarm_panel.asyncio.sleep", no_sleep):
        await hass.services.async_call("alarm_control_panel", "alarm_arm_vacation", {"entity_id": eid}, blocking=True)
        await hass.async_block_till_done()

    assert "armed_custom_bypass" not in seen
    assert "arming" in seen
    assert hass.states.get(eid).state == "armed_vacation"



async def test_alarm_panel_reports_refused_command(hass: HomeAssistant):
    """Scenario never activates (e.g. open zones) -> event with open zones."""
    panel = FakePanel()
    panel.scenarios[2]["st"] = 0
    panel.scenarios[0]["st"] = 1
    await _setup(hass, panel, {"disarm": 0, "arm_vacation": 1})
    eid = _eid(hass, "alarm_control_panel", f"{SERIAL}_alarm_panel")
    events = []
    hass.bus.async_listen("inim_prime_alarm_command_result", lambda e: events.append(e.data))

    original = panel.request

    async def refusing(cmd, **params):
        if cmd == "set_scenarios_mode":
            panel.calls.append((cmd, params))
            return {}  # panel accepts the request but doesn't arm
        if cmd == "get_zones_status":
            return {"zone": [{"id": "1", "lb": "Ingresso", "tl": "1", "st": "2", "mm": "0", "by": "1"}]}
        return await original(cmd, **params)

    async def no_sleep(*_):
        return None

    with patch("inim.prime.primelan.client.InimPrimeClient._request", side_effect=refusing), \
         patch("custom_components.inim_prime.entities.alarm_panel.asyncio.sleep", no_sleep):
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        await entry.runtime_data.coordinators.zones.async_refresh()
        await hass.services.async_call("alarm_control_panel", "alarm_arm_vacation", {"entity_id": eid}, blocking=True)
        await hass.async_block_till_done()

    st = hass.states.get(eid)
    assert st.state == "disarmed"
    assert st.attributes["last_command"]["success"] is False
    assert events and events[0]["success"] is False and events[0]["command"] == "arm_vacation"
    assert events[0]["open_zones"] == ["Ingresso"]


async def test_stale_output_entities_removed(hass: HomeAssistant):
    panel = FakePanel()
    entry = await _setup(hass, panel)
    reg = er.async_get(hass)
    assert _eid(hass, "switch", f"{SERIAL}_output_31")
    # output 31 disappears (e.g. hidden) -> its entity is removed on reload
    del panel.outputs[31]
    with patch("inim.prime.primelan.client.InimPrimeClient._request", side_effect=panel.request):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert _eid(hass, "switch", f"{SERIAL}_output_31") is None
    assert _eid(hass, "light", f"{SERIAL}_output_570")  # still there


async def test_log_events_history_attribute(hass: HomeAssistant):
    from datetime import datetime
    from custom_components.inim_prime.models.log_events import UnifiedLogEvent
    panel = FakePanel()
    entry = await _setup(hass, panel)
    coord = entry.runtime_data.coordinators.panel_log_events
    coord.last_panel_log_events = [
        UnifiedLogEvent(i, datetime(2026, 11, 8, 23, i), f"Evento {i}", "Utente", f"Zona {i}", None)
        for i in range(50)
    ]
    eid = _eid(hass, "event", f"{SERIAL}_panel_log_events")
    coord.async_update_listeners()
    await hass.async_block_till_done()
    events = hass.states.get(eid).attributes["events"]
    assert len(events) == 40                       # = panel_log_events_fetch_limit
    assert events[0]["type"] == "Evento 49"        # newest first
    assert events[-1]["location"] == "Zona 10"



async def test_hide_unused_partitions(hass: HomeAssistant):
    panel = FakePanel()
    panel.partitions[3] = {"lb": "AREA       013 \x00", "st": 1, "am": 4}
    entry = await _setup(hass, panel)
    reg = er.async_get(hass)
    # Partitions share one device now, so hiding removes the entities.
    assert reg.async_get_entity_id("sensor", DOMAIN, f"{SERIAL}_partition_3_stato")

    with patch("inim.prime.primelan.client.InimPrimeClient._request", side_effect=panel.request):
        hass.config_entries.async_update_entry(entry, options={**entry.options, "hide_unused_partitions": True})
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    assert reg.async_get_entity_id("sensor", DOMAIN, f"{SERIAL}_partition_3_stato") is None
    assert reg.async_get_entity_id("sensor", DOMAIN, f"{SERIAL}_partition_1_stato")
    assert set(entry.runtime_data.gateway.partitions) == {1, 2}


async def test_alarm_panel_keypad_arming_settles(hass: HomeAssistant):
    """Armed from the keypad: partitions change before the scenario is read."""
    panel = FakePanel()
    panel.scenarios[2]["st"] = 0
    panel.scenarios[0]["st"] = 1
    entry = await _setup(hass, panel, {"disarm": 0, "arm_vacation": 1})
    eid = _eid(hass, "alarm_control_panel", f"{SERIAL}_alarm_panel")
    coords = entry.runtime_data.coordinators

    # partitions armed, scenario not yet re-read (still "Spento")
    for part in panel.partitions.values():
        part["am"] = 1
    original = panel.request

    async def stale_scenarios(cmd, **params):
        if cmd == "get_scenarios_status":
            return {"sce": [{"id": str(i), "lb": s["lb"], "st": str(s["st"])} for i, s in panel.scenarios.items()]}
        return await original(cmd, **params)

    with patch("inim.prime.primelan.client.InimPrimeClient._request", side_effect=stale_scenarios):
        await coords.partitions.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(eid).state == "arming"          # not custom bypass

        # scenario now reported -> final state
        panel.scenarios[0]["st"] = 0
        panel.scenarios[1]["st"] = 1
        await coords.scenarios.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(eid).state == "armed_vacation"
