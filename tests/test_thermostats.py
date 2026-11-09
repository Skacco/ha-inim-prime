"""Thermostat status parsing and name mapping."""
from custom_components.inim_prime.models.thermostats import (
    parse_temperatures,
    thermostat_name,
)

from custom_components.inim_prime.models.thermostats import TEMP_BASE


def _status_with(temps_x10: dict[int, int]) -> bytes:
    """Build a 122-byte status buffer (0xFFFF everywhere) with the given
    temperatures (x10) placed at TEMP_BASE + index*2, like the real response."""
    buf = bytearray(b"\xff" * 122)
    for index, value in temps_x10.items():
        off = TEMP_BASE + index * 2
        buf[off:off + 2] = value.to_bytes(2, "little")
    return bytes(buf)


# Mirrors the real capture: CAMERA(2)=19.8, BAGNO GRANDE(4)=22.1, etc.
_STATUS = _status_with({2: 198, 4: 221, 32: 182, 33: 187, 35: 201})

# keypad names (1..30) and sensor names (1..15), as read from panel memory.
_KEYPAD = {1: "CUCINA", 2: "CAMERA", 3: "INGRESSO", 4: "BAGNO GRANDE"}
_SENSOR = {1: "BAGNO GRANDE", 2: "CAMERETTA", 3: "STUDIO", 4: "BAGNO PICCOLO", 5: "CUCINA"}


def test_parse_temperatures():
    assert parse_temperatures(_STATUS) == {2: 19.8, 4: 22.1, 32: 18.2, 33: 18.7, 35: 20.1}


def test_name_mapping_keypad_and_sensor():
    # 1..30 -> keypad name, 31..45 -> sensor name (index - 30).
    assert thermostat_name(2, _KEYPAD, _SENSOR) == "CAMERA"
    assert thermostat_name(4, _KEYPAD, _SENSOR) == "BAGNO GRANDE"
    assert thermostat_name(32, _KEYPAD, _SENSOR) == "CAMERETTA"
    assert thermostat_name(33, _KEYPAD, _SENSOR) == "STUDIO"
    assert thermostat_name(35, _KEYPAD, _SENSOR) == "CUCINA"


def test_name_fallback_when_missing():
    assert thermostat_name(9, {}, {}) == "Termostato 9"
