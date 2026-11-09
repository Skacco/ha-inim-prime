"""Thermostat (temperature sensor) model and response parsing.

The panel answers the thermostat-status command (operation 0x2b, with PIN)
with a fixed table: the temperature of thermostat index ``i`` is a uint16 LE,
in tenths of a degree, at byte offset ``TEMP_BASE + i*2``. ``0xFFFF`` marks a
slot that isn't present.

Each thermostat's name comes from one of two contiguous label tables in panel
memory: indexes 1..30 are keypad-based thermostats (named after the keypad),
31..45 are sensor-based ones (named after the temperature sensor). Found by
decrypting a Prime/STUDIO "Termostati" capture.
"""
from __future__ import annotations

from dataclasses import dataclass

TEMP_BASE = 24
MAX_INDEX = 45
KEYPAD_MAX_INDEX = 30  # 1..30 keypad thermostats, 31..45 sensor thermostats


@dataclass(frozen = True)
class UnifiedThermostat:
    index: int
    name: str
    temperature: float  # degrees Celsius


def parse_temperatures(status: bytes) -> dict[int, float]:
    """{thermostat index: temperature °C} for every present slot."""
    temps: dict[int, float] = {}
    for index in range(1, MAX_INDEX + 1):
        offset = TEMP_BASE + index * 2
        if offset + 2 > len(status):
            break
        raw = int.from_bytes(status[offset:offset + 2], "little")
        if raw in (0x0000, 0xFFFF) or raw > 1000:  # absent or out of range
            continue
        temps[index] = round(raw / 10, 1)
    return temps


def thermostat_name(
    index: int,
    keypad_labels: dict[int, str],
    sensor_labels: dict[int, str],
) -> str:
    """Name for a thermostat index: keypad name (1..30) or sensor name (31..45)."""
    if index <= KEYPAD_MAX_INDEX:
        name = keypad_labels.get(index)
    else:
        name = sensor_labels.get(index - KEYPAD_MAX_INDEX)
    return name or f"Termostato {index}"
