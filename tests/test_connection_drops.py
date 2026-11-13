"""The 'connection_drops' action rebuilds the list of past drops from the
recorded history of the connectivity sensors."""
from types import SimpleNamespace

from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.inim_prime import connection_drops as cd
from custom_components.inim_prime.const import DOMAIN


def _state(started, duration):
    return SimpleNamespace(attributes = {
        "ultimo_ok": "x", "ultima_caduta": started, "durata_ultima_caduta": duration,
    })


def test_extract_drops_keeps_one_entry_per_drop():
    states = [
        _state(None, None),                                    # after a restart
        _state("2026-11-13T11:12:12.855083+00:00", 8.0),
        _state("2026-11-13T11:12:12.855083+00:00", 8.0),       # same drop, later row
        _state("2026-11-13T13:37:36.321472+00:00", 7.8),
        SimpleNamespace(attributes = {}),                      # unavailable
    ]
    drops = cd.extract_drops(states, "Native")

    assert [(d["connessione"], d["durata_secondi"], d["periodica"]) for d in drops] == [
        ("Native", 8.0, True),      # on the 71.6 minute cadence
        ("Native", 7.8, False),     # off the cadence
    ]
    assert drops[0]["inizio"].startswith("2026-11-13T")


async def test_action_lists_recorded_drops_newest_first(recorder_mock, hass):
    registry = er.async_get(hass)
    native = registry.async_get_or_create(
        "binary_sensor", DOMAIN, "SERIAL_connectivity_native",
        suggested_object_id = "centrale_inim_connessione_native",
    ).entity_id
    primelan = registry.async_get_or_create(
        "binary_sensor", DOMAIN, "SERIAL_connectivity_primelan",
        suggested_object_id = "centrale_inim_connessione_primelan",
    ).entity_id
    other = registry.async_get_or_create("binary_sensor", DOMAIN, "SERIAL_fault_low_battery").entity_id

    from homeassistant.util import dt as dt_util
    now = dt_util.utcnow()
    first = (now.replace(microsecond = 0)).isoformat()

    hass.states.async_set(native, "on", {"ultimo_ok": "a", "ultima_caduta": None, "durata_ultima_caduta": None})
    hass.states.async_set(native, "on", {"ultimo_ok": "b", "ultima_caduta": first, "durata_ultima_caduta": 8.3})
    hass.states.async_set(native, "on", {"ultimo_ok": "c", "ultima_caduta": first, "durata_ultima_caduta": 8.3})
    hass.states.async_set(primelan, "on", {"ultimo_ok": "a", "ultima_caduta": first, "durata_ultima_caduta": 12.2})
    hass.states.async_set(other, "on", {"ultima_caduta": first, "durata_ultima_caduta": 1.0})
    await async_wait_recording_done(hass)

    cd.async_register(hass)
    answer = await hass.services.async_call(
        DOMAIN, cd.SERVICE_CONNECTION_DROPS, {"hours": 1}, blocking = True, return_response = True,
    )

    assert answer["numero"] == 2
    assert sorted((d["connessione"], d["durata_secondi"]) for d in answer["cadute"]) == [
        ("Native", 8.3), ("PrimeLAN", 12.2),
    ]
    assert set(answer["cadute"][0]) == {"connessione", "inizio", "durata_secondi", "periodica"}

    # days and hours add up, and the default is the last 24 hours
    for data in ({}, {"days": 3}, {"days": 2, "hours": 5}):
        answer = await hass.services.async_call(
            DOMAIN, cd.SERVICE_CONNECTION_DROPS, data, blocking = True, return_response = True,
        )
        assert answer["numero"] == 2, data
    from datetime import datetime, timedelta
    span = datetime.fromisoformat(answer["al"]) - datetime.fromisoformat(answer["dal"])
    assert span == timedelta(days = 2, hours = 5)

    # one connection only
    for wanted, expected in (("native", ("Native", 8.3)), ("primelan", ("PrimeLAN", 12.2))):
        answer = await hass.services.async_call(
            DOMAIN, cd.SERVICE_CONNECTION_DROPS, {"hours": 1, "connection": wanted},
            blocking = True, return_response = True,
        )
        assert [(d["connessione"], d["durata_secondi"]) for d in answer["cadute"]] == [expected]
