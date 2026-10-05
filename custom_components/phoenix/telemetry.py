"""Fixed, read-only local robot metrics with strict unavailable semantics."""

import math
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntityDescription
from homeassistant.components.sensor import SensorDeviceClass, SensorEntityDescription, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfElectricPotential, UnitOfSoundPressure, UnitOfTemperature

TELEMETRY_SECONDS = 30
NUMERIC_FIELDS = {
    "battery_percent": (0, 100),
    "battery_temperature_c": (-50, 150),
    "cpu_temperature_c": (-50, 150),
    "fan_percent": (0, 100),
    "main_board_temperature_c": (-50, 150),
    "microphone_rms_db": (-200, 100),
    "speaker_volume_percent": (0, 100),
    "system_voltage_v": (0, 100),
}
ENUM_FIELDS = {
    "camera": ["idle", "active", "disabled"],
    "charging_state": ["charging", "discharging", "full", "not_charging", "not_plugged_in"],
}
BOOLEAN_FIELDS = ("hatch_open", "head_touch", "plugged_in", "sleeping")
TELEMETRY_KEYS = frozenset((*NUMERIC_FIELDS, *ENUM_FIELDS, *BOOLEAN_FIELDS))


def validate_values(values: Any) -> dict[str, Any]:
    """Missing/null/malformed readings are unavailable, never inferred states."""
    if not isinstance(values, dict) or set(values).difference(TELEMETRY_KEYS):
        raise ValueError("Invalid telemetry fields")
    checked = {}
    for key, (lower, upper) in NUMERIC_FIELDS.items():
        value = values.get(key)
        checked[key] = (
            value
            if isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and lower <= value <= upper
            else None
        )
    for key, options in ENUM_FIELDS.items():
        value = values.get(key)
        checked[key] = value if isinstance(value, str) and value in options else None
    for key in BOOLEAN_FIELDS:
        value = values.get(key)
        checked[key] = value if isinstance(value, bool) else None
    return checked


SENSOR_DESCRIPTIONS = (
    SensorEntityDescription(
        key="battery_percent",
        translation_key="battery_percent",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    *(
        SensorEntityDescription(
            key=key,
            translation_key=key,
            device_class=SensorDeviceClass.TEMPERATURE,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=1,
        )
        for key in ("battery_temperature_c", "cpu_temperature_c", "main_board_temperature_c")
    ),
    *(
        SensorEntityDescription(key=key, translation_key=key, device_class=SensorDeviceClass.ENUM, options=options)
        for key, options in ENUM_FIELDS.items()
    ),
    *(
        SensorEntityDescription(
            key=key,
            translation_key=key,
            native_unit_of_measurement=PERCENTAGE,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=0,
        )
        for key in ("fan_percent", "speaker_volume_percent")
    ),
    SensorEntityDescription(
        key="microphone_rms_db",
        translation_key="microphone_rms_db",
        native_unit_of_measurement=UnitOfSoundPressure.DECIBEL,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="system_voltage_v",
        translation_key="system_voltage_v",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
)
BINARY_DESCRIPTIONS = (
    BinarySensorEntityDescription(
        key="hatch_open", translation_key="hatch_open", device_class=BinarySensorDeviceClass.OPENING
    ),
    BinarySensorEntityDescription(key="head_touch", translation_key="head_touch"),
    BinarySensorEntityDescription(
        key="plugged_in", translation_key="plugged_in", device_class=BinarySensorDeviceClass.PLUG
    ),
    BinarySensorEntityDescription(key="sleeping", translation_key="sleeping"),
)
