"""Shared pytest fixtures and HA module mocks for Marstek integration tests."""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
INTEGRATION_PATH = REPO_ROOT / "custom_components" / "marstek_local_api"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


# ---------------------------------------------------------------------------
# Minimal Home Assistant module stubs
# (mirrors the approach in test/test_tool.py)
# ---------------------------------------------------------------------------

def _make_module(name: str, **attrs):
    mod = type(sys)("homeassistant." + name.lstrip("homeassistant."))
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


class _SensorDeviceClass:
    BATTERY = "battery"
    TEMPERATURE = "temperature"
    ENERGY_STORAGE = "energy_storage"
    POWER = "power"
    ENERGY = "energy"
    SIGNAL_STRENGTH = "signal_strength"
    DURATION = "duration"
    VOLTAGE = "voltage"
    CURRENT = "current"


class _SensorStateClass:
    MEASUREMENT = "measurement"
    TOTAL_INCREASING = "total_increasing"


class _BinarySensorDeviceClass:
    BATTERY_CHARGING = "battery_charging"
    CONNECTIVITY = "connectivity"


@dataclass
class _SensorEntityDescription:
    key: str
    name: str | None = None
    native_unit_of_measurement: str | None = None
    device_class: str | None = None
    state_class: str | None = None


@dataclass
class _BinarySensorEntityDescription:
    key: str
    name: str | None = None
    device_class: str | None = None


class _UnitOfEnergy:
    WATT_HOUR = "Wh"
    KILO_WATT_HOUR = "kWh"


class _UnitOfPower:
    WATT = "W"


class _UnitOfTemperature:
    CELSIUS = "°C"


class _UnitOfElectricPotential:
    VOLT = "V"


class _UnitOfElectricCurrent:
    AMPERE = "A"


class _UnitOfTime:
    SECONDS = "s"


class _DataUpdateCoordinator:
    """Stub base class for coordinators."""
    def __init__(self, *a, **kw): pass


class _CoordinatorEntity:
    """Stub base class to avoid duplicate-base conflicts."""
    def __init__(self, coordinator=None, *a, **kw): pass


class _SensorEntity:
    """Stub sensor entity base."""


class _BinarySensorEntity:
    """Stub binary sensor entity base."""


def _install_ha_stubs() -> None:
    """Register minimal HA stubs so integration modules can be imported."""
    stubs = {
        "homeassistant": type(sys)("homeassistant"),
        "homeassistant.core": _make_module("core", HomeAssistant=object),
        "homeassistant.helpers": type(sys)("homeassistant.helpers"),
        "homeassistant.helpers.update_coordinator": _make_module(
            "helpers.update_coordinator",
            DataUpdateCoordinator=_DataUpdateCoordinator,
            UpdateFailed=Exception,
            CoordinatorEntity=_CoordinatorEntity,
        ),
        "homeassistant.helpers.entity": _make_module("helpers.entity", DeviceInfo=dict),
        "homeassistant.helpers.entity_platform": _make_module(
            "helpers.entity_platform", AddEntitiesCallback=object
        ),
        "homeassistant.helpers.config_validation": _make_module("helpers.config_validation"),
        "homeassistant.helpers.device_registry": _make_module("helpers.device_registry"),
        "homeassistant.config_entries": _make_module("config_entries", ConfigEntry=object),
        "homeassistant.exceptions": _make_module("exceptions", HomeAssistantError=Exception),
        "homeassistant.components": type(sys)("homeassistant.components"),
        "homeassistant.components.sensor": _make_module(
            "components.sensor",
            SensorDeviceClass=_SensorDeviceClass,
            SensorEntity=_SensorEntity,
            SensorEntityDescription=_SensorEntityDescription,
            SensorStateClass=_SensorStateClass,
        ),
        "homeassistant.components.binary_sensor": _make_module(
            "components.binary_sensor",
            BinarySensorDeviceClass=_BinarySensorDeviceClass,
            BinarySensorEntity=_BinarySensorEntity,
            BinarySensorEntityDescription=_BinarySensorEntityDescription,
        ),
        "homeassistant.const": _make_module(
            "const",
            CONF_HOST="host",
            PERCENTAGE="%",
            Platform=type("Platform", (), {"SENSOR": "sensor", "BINARY_SENSOR": "binary_sensor", "BUTTON": "button"})(),
            UnitOfEnergy=_UnitOfEnergy(),
            UnitOfPower=_UnitOfPower(),
            UnitOfTemperature=_UnitOfTemperature(),
            UnitOfElectricPotential=_UnitOfElectricPotential(),
            UnitOfElectricCurrent=_UnitOfElectricCurrent(),
            UnitOfTime=_UnitOfTime(),
        ),
        "voluptuous": _make_module("voluptuous", Schema=lambda *a, **kw: None, Required=lambda x: x, Optional=lambda x, **kw: x, All=lambda *a: a[0], Coerce=lambda t: t, Range=lambda **kw: None, In=lambda x: None),
    }
    for name, mod in stubs.items():
        if name not in sys.modules:
            sys.modules[name] = mod


# Install stubs once at import time
_install_ha_stubs()


# ---------------------------------------------------------------------------
# Load integration modules
# ---------------------------------------------------------------------------

def _load_integration_module(name: str):
    package = "custom_components.marstek_local_api"

    # Ensure package stubs exist
    if "custom_components" not in sys.modules:
        pkg = type(sys)("custom_components")
        pkg.__path__ = [str(INTEGRATION_PATH.parent)]
        sys.modules["custom_components"] = pkg
    if package not in sys.modules:
        pkg = type(sys)(package)
        pkg.__path__ = [str(INTEGRATION_PATH)]
        sys.modules[package] = pkg

    full_name = f"{package}.{name}"
    if full_name in sys.modules:
        return sys.modules[full_name]

    spec = importlib.util.spec_from_file_location(full_name, INTEGRATION_PATH / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = mod
    spec.loader.exec_module(mod)
    return mod


# Pre-load in dependency order
_const = _load_integration_module("const")
_compatibility = _load_integration_module("compatibility")
_sensor = _load_integration_module("sensor")
_binary_sensor = _load_integration_module("binary_sensor")


# ---------------------------------------------------------------------------
# Public re-exports for tests
# ---------------------------------------------------------------------------

CompatibilityMatrix = _compatibility.CompatibilityMatrix
SENSOR_TYPES = _sensor.SENSOR_TYPES
PV_SENSOR_TYPES = _sensor.PV_SENSOR_TYPES
BINARY_SENSOR_TYPES = _binary_sensor.BINARY_SENSOR_TYPES


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def venus_a_fw147_raw() -> dict:
    """Raw fixture data as captured from the Venus A FW 147 device."""
    path = FIXTURES_DIR / "Venus_A_fw147" / "all.json"
    data = json.loads(path.read_text())
    # Strip metadata key
    return {k: v for k, v in data.items() if not k.startswith("_")}


@pytest.fixture(scope="session")
def venus_a_compatibility() -> CompatibilityMatrix:
    """CompatibilityMatrix for Venus A hardware 2.0, firmware 147."""
    return CompatibilityMatrix(device_model="VenusA", firmware_version=147)


@pytest.fixture(scope="session")
def venus_a_coordinator_data(venus_a_fw147_raw, venus_a_compatibility) -> dict:
    """Simulate coordinator data after scaling is applied, matching real device output.

    Applies the same scaling the coordinator does in _async_update_data().
    ES data is absent in this fixture (ES.GetStatus not captured), so battery
    power sensors will report 0 / idle.
    """
    raw = venus_a_fw147_raw

    battery = dict(raw.get("battery", {}))
    for field_name in ("bat_temp", "bat_capacity", "bat_voltage", "bat_current"):
        if field_name in battery:
            battery[field_name] = venus_a_compatibility.scale_value(battery[field_name], field_name)

    return {
        "device": raw.get("device"),
        "wifi": raw.get("wifi"),
        "ble": raw.get("ble"),
        "battery": battery,
        "pv": raw.get("pv"),
        "mode": raw.get("mode"),
        "em": raw.get("em"),
        # es deliberately absent — mirrors a first-poll where ES.GetStatus timed out
        "_diagnostic": {"last_message_seconds": 5, "target_interval": 10, "actual_interval": 10},
        "_config": {"dod_percent": 80},
    }


@pytest.fixture
def sensor_map() -> dict:
    """Dict of sensor key → description for fast lookup."""
    return {desc.key: desc for desc in SENSOR_TYPES}


@pytest.fixture
def pv_sensor_map() -> dict:
    """Dict of PV sensor key → description."""
    return {desc.key: desc for desc in PV_SENSOR_TYPES}


@pytest.fixture
def binary_sensor_map() -> dict:
    """Dict of binary sensor key → description."""
    return {desc.key: desc for desc in BINARY_SENSOR_TYPES}
