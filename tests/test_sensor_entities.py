"""Tests for sensor.py — entity classes, setup entry, and helper exception branches."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from conftest import _load_integration_module, PV_SENSOR_TYPES, SENSOR_TYPES

# ---------------------------------------------------------------------------
# Load modules
# ---------------------------------------------------------------------------
_sensor_mod = _load_integration_module("sensor")
_coordinator_mod = _load_integration_module("coordinator")
_const_mod = _load_integration_module("const")

MarstekSensor = _sensor_mod.MarstekSensor
MarstekMultiDeviceSensor = _sensor_mod.MarstekMultiDeviceSensor
MarstekAggregateSensor = _sensor_mod.MarstekAggregateSensor
MarstekSensorEntityDescription = _sensor_mod.MarstekSensorEntityDescription
AGGREGATE_SENSOR_TYPES = _sensor_mod.AGGREGATE_SENSOR_TYPES
async_setup_entry = _sensor_mod.async_setup_entry

MarstekMultiDeviceCoordinator = _coordinator_mod.MarstekMultiDeviceCoordinator

DOMAIN = _const_mod.DOMAIN
DATA_COORDINATOR = _const_mod.DATA_COORDINATOR
DEVICE_MODEL_VENUS_D = _const_mod.DEVICE_MODEL_VENUS_D
DEVICE_MODEL_VENUS_A = _const_mod.DEVICE_MODEL_VENUS_A

# Helper functions (private but testable)
_wh_to_kwh = _sensor_mod._wh_to_kwh
_available_capacity_kwh = _sensor_mod._available_capacity_kwh
_usable_capacity = _sensor_mod._usable_capacity
_available_until_dod = _sensor_mod._available_until_dod
_time_to_full = _sensor_mod._time_to_full
_time_to_dod = _sensor_mod._time_to_dod
_usable_soc = _sensor_mod._usable_soc


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_single_coordinator(device_model: str = "VenusE", data: dict | None = None) -> MagicMock:
    c = MagicMock()
    c.data = data if data is not None else {"battery": {"soc": 80}}
    c.device_model = device_model
    c.is_category_fresh = MagicMock(return_value=True)
    return c


def _make_entry(mac: str = "aabbccddee", device: str = "VenusA", firmware: str = "147") -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {"ble_mac": mac, "device": device, "firmware": firmware}
    return entry


def _make_multi_coordinator(
    macs: list[str] | None = None,
    device_model: str = "VenusE",
) -> MagicMock:
    macs = macs or ["aa:bb:cc:dd:ee:ff"]
    device_coordinator = MagicMock()
    device_coordinator.is_category_fresh = MagicMock(return_value=True)
    device_coordinator.device_model = device_model

    coordinator = MagicMock()
    coordinator.__class__ = MarstekMultiDeviceCoordinator  # makes isinstance() return True
    coordinator.get_device_macs.return_value = macs
    coordinator.device_coordinators = {mac: device_coordinator for mac in macs}
    coordinator.devices = [{"ble_mac": mac, "device": device_model} for mac in macs]
    coordinator.data = {"aggregates": {"total_power_in": 0}}
    coordinator.get_device_data = MagicMock(return_value={"battery": {"soc": 80}})
    return coordinator


# ---------------------------------------------------------------------------
# Exception branches in helper functions
# ---------------------------------------------------------------------------

class TestHelperExceptionBranches:
    """Cover the except clauses that are not reached by normal happy-path tests."""

    def test_wh_to_kwh_non_numeric_returns_none(self):
        assert _wh_to_kwh("not_a_number") is None

    def test_available_capacity_kwh_invalid_soc_returns_none(self):
        data = {"battery": {"soc": "bad", "rated_capacity": 4160}}
        assert _available_capacity_kwh(data) is None

    def test_usable_capacity_invalid_rated_returns_none(self):
        data = {"battery": {"rated_capacity": "bad"}}
        assert _usable_capacity(data) is None

    def test_available_until_dod_invalid_rated_returns_none(self):
        data = {"battery": {"rated_capacity": "bad", "bat_capacity": 100}}
        assert _available_until_dod(data) is None

    def test_time_to_full_invalid_rated_returns_none(self):
        data = {"battery": {"rated_capacity": "bad", "bat_capacity": 100}, "es": {"bat_power": 100}}
        assert _time_to_full(data) is None

    def test_time_to_dod_invalid_rated_returns_none(self):
        data = {"battery": {"rated_capacity": "bad", "bat_capacity": 100}, "es": {"bat_power": -100}}
        assert _time_to_dod(data) is None

    def test_usable_soc_invalid_soc_returns_none(self):
        data = {"battery": {"soc": "bad"}, "_config": {"dod_percent": 80}}
        assert _usable_soc(data) is None


# ---------------------------------------------------------------------------
# MarstekSensor entity
# ---------------------------------------------------------------------------

class TestMarstekSensor:

    def _make(self, desc, data=None, category_fresh=True):
        coord = _make_single_coordinator(data=data or {"battery": {"soc": 80}})
        coord.is_category_fresh = MagicMock(return_value=category_fresh)
        entry = _make_entry()
        sensor = MarstekSensor(coordinator=coord, entity_description=desc, entry=entry)
        sensor.coordinator = coord
        return sensor

    def test_init_unique_id_uses_ble_mac(self):
        sensor = self._make(SENSOR_TYPES[0])
        assert sensor._attr_unique_id == f"aabbccddee_{SENSOR_TYPES[0].key}"

    def test_init_falls_back_to_wifi_mac(self):
        coord = _make_single_coordinator()
        entry = MagicMock()
        entry.data = {"wifi_mac": "ffeeddccbbaa", "device": "VenusA", "firmware": "147"}
        sensor = MarstekSensor(coordinator=coord, entity_description=SENSOR_TYPES[0], entry=entry)
        assert "ffeeddccbbaa" in sensor._attr_unique_id

    def test_native_value_no_value_fn_returns_none(self):
        desc = MarstekSensorEntityDescription(key="no_fn", name="No fn")
        sensor = self._make(desc)
        assert sensor.native_value is None

    def test_native_value_stale_category_returns_none(self):
        desc = next(d for d in SENSOR_TYPES if d.category)
        sensor = self._make(desc, category_fresh=False)
        assert sensor.native_value is None

    def test_native_value_fresh_returns_value(self):
        soc_desc = next(d for d in SENSOR_TYPES if d.key == "battery_soc")
        sensor = self._make(soc_desc, data={"battery": {"soc": 75}})
        assert sensor.native_value == 75

    def test_available_with_data_is_true(self):
        sensor = self._make(SENSOR_TYPES[0])
        assert sensor.available is True

    def test_available_with_no_data_is_false(self):
        sensor = self._make(SENSOR_TYPES[0], data={})
        sensor.coordinator.data = None
        assert sensor.available is False

    def test_available_uses_custom_fn(self):
        desc = MarstekSensorEntityDescription(
            key="custom", name="Custom", available_fn=lambda data: False
        )
        sensor = self._make(desc)
        assert sensor.available is False


# ---------------------------------------------------------------------------
# MarstekMultiDeviceSensor entity
# ---------------------------------------------------------------------------

class TestMarstekMultiDeviceSensor:

    def _make(self, desc, category_fresh=True, device_data=None):
        device_coord = MagicMock()
        device_coord.is_category_fresh = MagicMock(return_value=category_fresh)

        multi_coord = MagicMock()
        multi_coord.get_device_data = MagicMock(return_value=device_data or {"battery": {"soc": 80}})

        sensor = MarstekMultiDeviceSensor(
            coordinator=multi_coord,
            device_coordinator=device_coord,
            entity_description=desc,
            device_mac="aa:bb:cc:dd:ee:ff",
            device_data={"device": "VenusA", "firmware": "147"},
        )
        sensor.coordinator = multi_coord
        return sensor

    def test_init_unique_id(self):
        sensor = self._make(SENSOR_TYPES[0])
        assert sensor._attr_unique_id == f"aa:bb:cc:dd:ee:ff_{SENSOR_TYPES[0].key}"

    def test_init_mac_suffix_in_device_name(self):
        sensor = self._make(SENSOR_TYPES[0])
        assert "eeff" in sensor._attr_device_info["name"].lower()

    def test_native_value_no_value_fn_returns_none(self):
        desc = MarstekSensorEntityDescription(key="no_fn", name="No fn")
        sensor = self._make(desc)
        assert sensor.native_value is None

    def test_native_value_stale_returns_none(self):
        desc = next(d for d in SENSOR_TYPES if d.category)
        sensor = self._make(desc, category_fresh=False)
        assert sensor.native_value is None

    def test_native_value_fresh_returns_value(self):
        soc_desc = next(d for d in SENSOR_TYPES if d.key == "battery_soc")
        sensor = self._make(soc_desc, device_data={"battery": {"soc": 60}})
        assert sensor.native_value == 60

    def test_available_with_data_is_true(self):
        sensor = self._make(SENSOR_TYPES[0])
        assert sensor.available is True

    def test_available_with_no_data_is_false(self):
        sensor = self._make(SENSOR_TYPES[0], device_data={})
        sensor.coordinator.get_device_data.return_value = None
        assert sensor.available is False

    def test_available_uses_custom_fn(self):
        desc = MarstekSensorEntityDescription(
            key="custom", name="Custom", available_fn=lambda data: False
        )
        sensor = self._make(desc)
        assert sensor.available is False


# ---------------------------------------------------------------------------
# MarstekAggregateSensor entity
# ---------------------------------------------------------------------------

class TestMarstekAggregateSensor:

    def _make(self, desc, aggregates=None):
        coord = MagicMock()
        coord.data = {"aggregates": aggregates if aggregates is not None else {"total_power_in": 100}}
        sensor = MarstekAggregateSensor(
            coordinator=coord,
            entity_description=desc,
            system_unique_id="aabb_ccdd",
            device_count=2,
        )
        sensor.coordinator = coord
        return sensor

    def test_init_unique_id(self):
        sensor = self._make(AGGREGATE_SENSOR_TYPES[0])
        assert sensor._attr_unique_id == f"aabb_ccdd_{AGGREGATE_SENSOR_TYPES[0].key}"

    def test_native_value_returns_value(self):
        desc = next(d for d in AGGREGATE_SENSOR_TYPES if d.key == "system_total_power_in")
        sensor = self._make(desc, aggregates={"total_power_in": 500})
        assert sensor.native_value == 500

    def test_native_value_no_value_fn_returns_none(self):
        desc = MarstekSensorEntityDescription(key="no_fn", name="No fn")
        sensor = self._make(desc)
        assert sensor.native_value is None

    def test_available_with_aggregates_is_true(self):
        sensor = self._make(AGGREGATE_SENSOR_TYPES[0])
        assert sensor.available is True

    def test_available_empty_aggregates_is_false(self):
        sensor = self._make(AGGREGATE_SENSOR_TYPES[0], aggregates={})
        assert sensor.available is False

    def test_available_uses_custom_fn(self):
        desc = MarstekSensorEntityDescription(
            key="custom", name="Custom", available_fn=lambda data: False
        )
        sensor = self._make(desc)
        assert sensor.available is False


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------

class TestAsyncSetupEntry:

    async def test_single_device_non_pv_model(self):
        """Single device (VenusE) — standard sensors only, no PV sensors."""
        hass = MagicMock()
        entry = _make_entry(device="VenusE")
        coordinator = _make_single_coordinator(device_model="VenusE")
        hass.data = {DOMAIN: {entry.entry_id: {DATA_COORDINATOR: coordinator}}}

        added = []
        await async_setup_entry(hass, entry, added.append)

        assert len(added[0]) == len(SENSOR_TYPES)

    async def test_single_device_venus_d_adds_pv_sensors(self):
        """Single device (VenusD) — standard + PV sensors."""
        hass = MagicMock()
        entry = _make_entry(device="VenusD")
        coordinator = _make_single_coordinator(device_model=DEVICE_MODEL_VENUS_D)
        hass.data = {DOMAIN: {entry.entry_id: {DATA_COORDINATOR: coordinator}}}

        added = []
        await async_setup_entry(hass, entry, added.append)

        assert len(added[0]) == len(SENSOR_TYPES) + len(PV_SENSOR_TYPES)

    async def test_single_device_venus_a_adds_pv_sensors(self):
        """Single device (VenusA) — standard + PV sensors."""
        hass = MagicMock()
        entry = _make_entry(device="VenusA")
        coordinator = _make_single_coordinator(device_model=DEVICE_MODEL_VENUS_A)
        hass.data = {DOMAIN: {entry.entry_id: {DATA_COORDINATOR: coordinator}}}

        added = []
        await async_setup_entry(hass, entry, added.append)

        assert len(added[0]) == len(SENSOR_TYPES) + len(PV_SENSOR_TYPES)

    async def test_multi_device_standard_model(self):
        """Multi-device (VenusE) — per-device standard sensors + aggregates."""
        hass = MagicMock()
        entry = _make_entry()
        macs = ["aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66"]
        coordinator = _make_multi_coordinator(macs=macs, device_model="VenusE")
        hass.data = {DOMAIN: {entry.entry_id: {DATA_COORDINATOR: coordinator}}}

        added = []
        await async_setup_entry(hass, entry, added.append)

        expected = len(macs) * len(SENSOR_TYPES) + len(AGGREGATE_SENSOR_TYPES)
        assert len(added[0]) == expected

    async def test_multi_device_venus_d_adds_pv_sensors(self):
        """Multi-device (VenusD) — per-device standard + PV sensors + aggregates."""
        hass = MagicMock()
        entry = _make_entry()
        macs = ["aa:bb:cc:dd:ee:ff"]
        coordinator = _make_multi_coordinator(macs=macs, device_model=DEVICE_MODEL_VENUS_D)
        hass.data = {DOMAIN: {entry.entry_id: {DATA_COORDINATOR: coordinator}}}

        added = []
        await async_setup_entry(hass, entry, added.append)

        expected = len(SENSOR_TYPES) + len(PV_SENSOR_TYPES) + len(AGGREGATE_SENSOR_TYPES)
        assert len(added[0]) == expected
