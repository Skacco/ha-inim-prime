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
        # Flat fields on their own page (no accordion).
        fields = result["data_schema"].schema
        keys = {str(k): k.default() for k in fields}
        assert keys["outputs"] == 30000 and keys["scenarios"] == 10000
        assert keys["partitions"] == 10000  # entry's stored value is kept

        new = {**keys, "outputs": 2000, "scenarios": 4000}
        result = await opts.async_configure(result["flow_id"], new)           # -> back to menu
        # Single save from the main menu.
        await opts.async_configure(result["flow_id"], {"next_step_id": "save_exit"})
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
        # Accumulate several commands in one session, then "Save and exit".
        # PrimeLAN-only -> each command goes straight to the scenario form.
        result = await opts.async_init(entry.entry_id)
        # Main menu carries the single save entry.
        assert "save_exit" in result["menu_options"]
        # PrimeLAN -> the alarm panel is one form: a scenario dropdown per command.
        result = await opts.async_configure(result["flow_id"], {"next_step_id": "alarm"})
        assert result["type"] == "form" and result["step_id"] == "alarm_form_primelan"
        fields = {str(k): v for k, v in result["data_schema"].schema.items()}
        assert set(fields) == {"disarm", "arm_away", "arm_home", "arm_night", "arm_vacation"}
        labels = {o["label"] for o in fields["disarm"].config["options"]}
        assert {"Spento", "Totale", "Notte"} <= labels
        # PrimeLAN-only: no "custom partitions" choice.
        assert "partizioni" not in {o["value"] for o in fields["disarm"].config["options"]}

        # Submitting returns to the main menu; nothing is saved until "Save and exit".
        result = await opts.async_configure(
            result["flow_id"], {"disarm": "0", "arm_away": "2"},
        )
        assert result["type"] == "menu" and result["step_id"] == "init"
        assert "alarm_panel" not in entry.options
        await opts.async_configure(result["flow_id"], {"next_step_id": "save_exit"})
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


async def test_alarm_form_mixed_scenario_and_partitions():
    """Both integration modes: scenarios from the dropdown, and "custom
    partitions" opens that command's partitions form before returning."""
    from custom_components.inim_prime.config_flow.options_flow import (
        InimPrimeOptionsFlowHandler,
    )

    class Flow(InimPrimeOptionsFlowHandler):
        _has_native = True
        _has_primelan = True

        def __init__(self):
            self._pending = {"alarm_panel": {"disarm": 0}, "alarm_panel_native": {}}
            self._loaded = True

        def _scenario_choices(self):
            return [(0, "Spento"), (2, "Notte")]

        def _partition_choices(self):
            return [(1, "Casa"), (2, "Esterni")]

        def _shutter_context(self):
            return [], []

        def async_show_form(self, **kw):
            return {"type": "form", **kw}

        def async_show_menu(self, **kw):
            return {"type": "menu", **kw}

    flow = Flow()
    form = await flow.async_step_alarm()
    assert form["step_id"] == "alarm_form"
    sel = next(v for k, v in form["data_schema"].schema.items() if str(k) == "arm_vacation")
    assert "partizioni" in {o["value"] for o in sel.config["options"]}

    assert "arm_vacation_partitions" in {str(k) for k in form["data_schema"].schema}

    # Night by scenario, Vacation by custom partitions (its section, same
    # screen), Disarm cleared. One submit, straight back to the main menu.
    result = await flow.async_step_alarm_form({
        "arm_night": "2",
        "arm_vacation": "partizioni",
        "arm_vacation_partitions": {"Casa": "total", "Esterni": "instant"},
        # a filled section is ignored when the dropdown is a scenario
        "arm_night_partitions": {"Casa": "total"},
    })
    assert result["type"] == "menu" and result["step_id"] == "init"
    assert flow._pending["alarm_panel"] == {"arm_night": 2}
    assert set(flow._pending["alarm_panel_native"]) == {"arm_vacation"}


async def test_module_labels_cached():
    """Module names are read from the panel once, then served from memory."""
    from custom_components.inim_prime.gateway import InimPrimeGateway
    from custom_components.inim_prime.const import MODULE_LABEL_SIZE

    class Native:
        reads = 0

        async def read_memory(self, address, length):
            Native.reads += 1
            names = [b"TENDA SALA", b"TENDA NICOLE"]
            return b"".join(n.ljust(MODULE_LABEL_SIZE, b"\x00") for n in names)[:length]

    gw = object.__new__(InimPrimeGateway)
    gw._native = Native()
    assert await gw.module_labels(2) == {1: "TENDA SALA", 2: "TENDA NICOLE"}
    assert await gw.module_labels(2) == {1: "TENDA SALA", 2: "TENDA NICOLE"}
    assert await gw.module_labels(1) == {1: "TENDA SALA"}
    assert Native.reads == 1


async def test_webhook_routes_event_to_coordinator():
    """A panel webhook event triggers a refresh of the matching coordinator."""
    from unittest.mock import AsyncMock, MagicMock
    from custom_components.inim_prime.webhook import _process

    zones = MagicMock()
    zones.async_request_refresh = AsyncMock()
    partitions = MagicMock()
    partitions.async_request_refresh = AsyncMock()

    entry = MagicMock()
    entry.runtime_data.coordinators.zones = zones
    entry.runtime_data.coordinators.partitions = partitions

    class Req:
        method = "GET"
        query = {"ev": "zone_open", "id": "5"}
        async def read(self):  # pragma: no cover - not used on GET
            return b""
        async def post(self):  # pragma: no cover - not used on GET
            return {}

    await _process(entry, Req())
    zones.async_request_refresh.assert_awaited_once()
    partitions.async_request_refresh.assert_not_called()

    # An unknown event refreshes nothing.
    zones.async_request_refresh.reset_mock()
    class Req2(Req):
        query = {"ev": "nonsense"}
    await _process(entry, Req2())
    zones.async_request_refresh.assert_not_called()


def test_connection_only_setup_seeds_scan_intervals():
    """Connection-only first setup must produce options the integration can
    load: scan_intervals present for zones/partitions (regression: it used to
    assert-fail because the defaults were never seeded)."""
    from custom_components.inim_prime.config_flow.config_flow import InimPrimeConfigFlow
    from custom_components.inim_prime.config_flow.models import ConfigData

    captured = {}

    for native, primelan in ((True, False), (True, True), (False, True)):
        flow = InimPrimeConfigFlow()
        flow._data = ConfigData(serial_number="SN123", host="1.2.3.4", use_native=native, use_primelan=primelan)
        flow.async_create_entry = lambda **kw: captured.update(kw) or kw  # type: ignore

        flow._create_entry_with_defaults()
        opts = captured["options"]
        assert "scan_intervals" in opts
        assert opts["scan_intervals"]["zones"] > 0
        assert opts["scan_intervals"]["partitions"] > 0
        if primelan:
            assert opts["scan_intervals"]["system_faults"] > 0
            assert opts["scan_intervals"]["gsm"] > 0
            assert opts["scan_intervals"]["panel_log_events"] > 0


def test_webhook_url_line_shown_in_options():
    """The push URL line is empty when off and present (fallback form) when on."""
    from custom_components.inim_prime.config_flow.options_flow import (
        InimPrimeOptionsFlowHandler,
    )
    from custom_components.inim_prime.config_flow.const import OptionsKey

    flow = InimPrimeOptionsFlowHandler()
    flow.hass = None  # forces the fallback URL branch (no real hass)

    flow._pending = {}
    assert flow._webhook_url_line() == ""

    flow._pending = {OptionsKey.REALTIME_PUSH: True, OptionsKey.WEBHOOK_ID: "secret123"}
    line = flow._webhook_url_line()
    assert "secret123" in line and "/api/webhook/" in line


async def test_pin_panel_requires_code_to_disarm():
    """The PIN panel rejects a wrong code on disarm and forwards the right one;
    arming stays code-free (code_arm_required False), and the unique_id differs."""
    import pytest
    from homeassistant.exceptions import ServiceValidationError
    from custom_components.inim_prime.entities.alarm_panel import _PinDisarmMixin

    disarmed: list = []

    class Base:
        async def async_alarm_disarm(self, code=None):
            disarmed.append(code)

    class Panel(_PinDisarmMixin, Base):
        def __init__(self):
            self._attr_unique_id = "SN_alarm_panel"
            self._make_pin_panel("4321")

    p = Panel()
    assert p._attr_unique_id == "SN_alarm_panel_pin"
    assert p._attr_code_arm_required is False
    with pytest.raises(ServiceValidationError):
        await p.async_alarm_disarm("0000")
    assert disarmed == []
    await p.async_alarm_disarm("4321")
    assert disarmed == ["4321"]


async def test_alarm_panel_no_flash_while_scenario_and_partitions_disagree(hass: HomeAssistant):
    """Scenario and partitions are read separately: no wrong state in between."""
    import time as _time

    panel = FakePanel()
    panel.scenarios[2]["st"] = 0
    panel.scenarios[0]["st"] = 1
    entry = await _setup(hass, panel, {"disarm": 0, "arm_vacation": 1})
    eid = _eid(hass, "alarm_control_panel", f"{SERIAL}_alarm_panel")
    coords = entry.runtime_data.coordinators
    ent = hass.data["alarm_control_panel"].get_entity(eid)
    assert hass.states.get(eid).state == "disarmed"

    with patch("inim.prime.primelan.client.InimPrimeClient._request", side_effect=panel.request):
        # --- arming: the scenario is active, the partitions not re-read yet
        ent._set_direction("arm_vacation")
        panel.scenarios[0]["st"] = 0
        panel.scenarios[1]["st"] = 1
        await coords.scenarios.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(eid).state == "arming"  # not "disarmed"

        # the partitions follow -> armed, and the arm window is over
        for part in panel.partitions.values():
            part["am"] = 1
        await coords.partitions.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(eid).state == "armed_vacation"
        assert ent._arming_until == 0.0

        # --- disarming: the scenario flips first, the partitions lag
        ent._pending = ("disarm", _time.monotonic() + 20)
        ent._set_direction("disarm")
        panel.scenarios[1]["st"] = 0
        panel.scenarios[0]["st"] = 1
        await coords.scenarios.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(eid).state == "disarming"  # not arming / custom bypass

        for part in panel.partitions.values():
            part["am"] = 4
        await coords.partitions.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(eid).state == "disarmed"
        assert ent._pending is None

        # --- disarming: the partitions go off first, the scenario lags
        for part in panel.partitions.values():
            part["am"] = 1
        panel.scenarios[0]["st"] = 0
        panel.scenarios[1]["st"] = 1
        await entry_refresh(hass)
        assert hass.states.get(eid).state == "armed_vacation"
        ent._arming_until = _time.monotonic() + 45  # a recent arm, still in its window
        ent._pending = ("disarm", _time.monotonic() + 20)
        ent._set_direction("disarm")
        for part in panel.partitions.values():
            part["am"] = 4
        await coords.partitions.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(eid).state == "disarmed"  # not "arming"


async def test_alarm_panel_new_command_not_clobbered_by_previous_one(hass: HomeAssistant):
    """Disarm pressed while the previous arm is still doing its last read."""
    import asyncio

    panel = FakePanel()
    panel.scenarios[2]["st"] = 0
    panel.scenarios[0]["st"] = 1
    await _setup(hass, panel, {"disarm": 0, "arm_vacation": 1})
    eid = _eid(hass, "alarm_control_panel", f"{SERIAL}_alarm_panel")
    original = panel.request
    hold = {"next_partition_read": False, "waiting": 0}
    release = asyncio.Event()

    async def slow(cmd, **params):
        if cmd == "get_partitions_status" and hold["next_partition_read"]:
            hold["next_partition_read"] = False  # only the arm's closing read
            hold["waiting"] += 1
            await release.wait()
        return await original(cmd, **params)

    async def spin(until):
        for _ in range(200):
            if until():
                return
            await asyncio.sleep(0.01)
        raise AssertionError("condition not reached")

    def call(service):
        return hass.async_create_task(hass.services.async_call(
            "alarm_control_panel", service, {"entity_id": eid}, blocking=True))

    ent = hass.data["alarm_control_panel"].get_entity(eid)
    with patch("inim.prime.primelan.client.InimPrimeClient._request", side_effect=slow):
        # arm: the scenario is confirmed, the closing partition read hangs
        hold["next_partition_read"] = True
        arm = call("alarm_arm_vacation")
        await spin(lambda: hold["waiting"] == 1)
        assert hass.states.get(eid).state == "arming"

        # a newer command starts while the arm is still finishing
        import time as _time
        newer = ("disarm", _time.monotonic() + 20)
        ent._pending = newer
        ent._set_direction("disarm")
        ent._command_seq += 1
        ent._last_command = {"command": "disarm"}

        # the old arm completes: it must not touch the newer command's state
        release.set()
        await arm
        assert ent._pending == newer
        assert ent._last_command == {"command": "disarm"}
        ent._pending = None
        await hass.async_block_till_done()
