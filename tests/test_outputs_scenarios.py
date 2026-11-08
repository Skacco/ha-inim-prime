"""Outputs (light) and scenarios (button/sensor) over a simulated PrimeLAN Web API."""
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.inim_prime_skacco.const import DOMAIN

SERIAL = "PRIME123"


class FakePanel:
    """Answers PrimeLAN api.cgi commands like the panel does."""

    def __init__(self):
        # t: 0 = normal/hybrid, 1 = dimming. st: 0/1, or 0..100 for dimmers
        self.outputs = {
            31: {"lb": "Luce Portico   ", "tl": 31, "st": 0, "t": 0},
            570: {"lb": "Faretti Soggiorno", "tl": 570, "st": 40, "t": 1, "v": "230,1", "p": "12,5", "c": "0,98"},
        }
        self.scenarios = {1: {"lb": "Totale", "st": 0}, 2: {"lb": "Notte", "st": 1}}
        self.calls = []

    async def request(self, cmd, timeout=None, retries=None, retry_delay=None, **params):
        self.calls.append((cmd, params))
        if cmd == "ping":
            return {}
        if cmd == "get_zones_status":
            return {"zone": [{"id": "1", "lb": "Ingresso", "tl": "1", "st": "1", "mm": "0", "by": "1"}]}
        if cmd == "get_partitions_status":
            return {"part": [{"id": "1", "lb": "Casa", "st": "0", "am": "4", "mm": "0"}]}
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
            for i, s in self.scenarios.items():
                s["st"] = 1 if i == int(params["p1"]) else 0
            return {}
        raise AssertionError(f"unexpected command {cmd} {params}")


async def _setup(hass: HomeAssistant, panel: FakePanel) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        minor_version=0,
        unique_id=SERIAL,
        data={"serial_number": SERIAL, "host": "192.0.2.10",
              "primelan": {"api_key": "k", "use_https": False}},
        options={"scan_intervals": {"zones": 5000, "partitions": 10000, "gsm": 30000,
                                    "system_faults": 15000, "panel_log_events": 15000},
                 "panel_log_events_fetch_limit": 40},
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


async def test_unload(hass: HomeAssistant):
    panel = FakePanel()
    entry = await _setup(hass, panel)
    with patch("inim.prime.primelan.client.InimPrimeClient._request", side_effect=panel.request):
        assert await hass.config_entries.async_unload(entry.entry_id)
