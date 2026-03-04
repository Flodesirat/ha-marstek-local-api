"""Tests for binary sensor value functions using Venus A FW 147 fixture data."""
import pytest


class TestBinarySensorsVenusA:
    """Verify binary sensor value_fn with real fixture data."""

    def test_charging_enabled_true(self, binary_sensor_map, venus_a_coordinator_data):
        """charg_flag=True → charging_enabled is on."""
        val = binary_sensor_map["charging_enabled"].value_fn(venus_a_coordinator_data)
        assert val is True

    def test_discharging_enabled_true(self, binary_sensor_map, venus_a_coordinator_data):
        """dischrg_flag=True → discharging_enabled is on."""
        val = binary_sensor_map["discharging_enabled"].value_fn(venus_a_coordinator_data)
        assert val is True

    def test_bluetooth_connected_true(self, binary_sensor_map, venus_a_coordinator_data):
        """BLE state='connect' → bluetooth_connected is on."""
        val = binary_sensor_map["bluetooth_connected"].value_fn(venus_a_coordinator_data)
        assert val is True

    def test_ct_connected_true(self, binary_sensor_map, venus_a_coordinator_data):
        """ct_state=1 → ct_connected is on."""
        val = binary_sensor_map["ct_connected"].value_fn(venus_a_coordinator_data)
        assert val is True


class TestBinarySensorsEdgeCases:
    """Verify binary sensor value_fn with synthetic data covering off states."""

    def test_charging_disabled(self, binary_sensor_map):
        data = {"battery": {"charg_flag": False, "dischrg_flag": True}}
        assert binary_sensor_map["charging_enabled"].value_fn(data) is False

    def test_discharging_disabled(self, binary_sensor_map):
        data = {"battery": {"charg_flag": True, "dischrg_flag": False}}
        assert binary_sensor_map["discharging_enabled"].value_fn(data) is False

    def test_bluetooth_disconnected(self, binary_sensor_map):
        data = {"ble": {"state": "disconnect", "ble_mac": "aabbccddeeff"}}
        assert binary_sensor_map["bluetooth_connected"].value_fn(data) is False

    def test_ct_disconnected(self, binary_sensor_map):
        data = {"em": {"ct_state": 0, "a_power": 0, "total_power": 0}}
        assert binary_sensor_map["ct_connected"].value_fn(data) is False

    def test_charging_absent_defaults_false(self, binary_sensor_map):
        """Missing battery key → default False."""
        assert binary_sensor_map["charging_enabled"].value_fn({}) is False

    def test_discharging_absent_defaults_false(self, binary_sensor_map):
        assert binary_sensor_map["discharging_enabled"].value_fn({}) is False

    def test_bluetooth_absent_is_false(self, binary_sensor_map):
        """Missing ble key → state is None, not 'connect' → False."""
        assert binary_sensor_map["bluetooth_connected"].value_fn({}) is False

    def test_ct_absent_is_false(self, binary_sensor_map):
        """Missing em key → ct_state is None, not 1 → False."""
        assert binary_sensor_map["ct_connected"].value_fn({}) is False

    def test_bluetooth_unknown_state(self, binary_sensor_map):
        """Unknown BLE state string → not 'connect' → False."""
        data = {"ble": {"state": "connecting"}}
        assert binary_sensor_map["bluetooth_connected"].value_fn(data) is False
