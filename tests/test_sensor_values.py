"""Tests for sensor value functions using Venus A FW 147 fixture data.

These tests verify that SENSOR_TYPES value_fn lambdas produce the correct
output for real device data, after the coordinator has applied scaling.
"""
import pytest


# ---------------------------------------------------------------------------
# Battery sensors
# ---------------------------------------------------------------------------

class TestBatterySensors:
    """Sensors sourced from Bat.GetStatus (after scaling)."""

    def test_soc(self, sensor_map, venus_a_coordinator_data):
        val = sensor_map["battery_soc"].value_fn(venus_a_coordinator_data)
        assert val == 20

    def test_temperature_after_scaling(self, sensor_map, venus_a_coordinator_data):
        """Venus A FW 147: bat_temp divisor=1.0 → 29.0°C unchanged."""
        val = sensor_map["battery_temperature"].value_fn(venus_a_coordinator_data)
        assert val == pytest.approx(29.0)

    def test_remaining_capacity_kwh(self, sensor_map, venus_a_coordinator_data):
        """bat_capacity=869.0 Wh → 0.869 kWh after _wh_to_kwh conversion."""
        val = sensor_map["battery_capacity"].value_fn(venus_a_coordinator_data)
        assert val == pytest.approx(0.869)

    def test_rated_capacity_kwh(self, sensor_map, venus_a_coordinator_data):
        """rated_capacity=4160.0 Wh → 4.16 kWh."""
        val = sensor_map["battery_rated_capacity"].value_fn(venus_a_coordinator_data)
        assert val == pytest.approx(4.16)

    def test_available_capacity(self, sensor_map, venus_a_coordinator_data):
        """available = (100 - soc) * rated_capacity / 100 = (100-20)*4160/100 = 3328 Wh."""
        val = sensor_map["battery_available_capacity"].value_fn(venus_a_coordinator_data)
        assert val == pytest.approx(3328.0)

    def test_charging_flag(self, sensor_map, venus_a_coordinator_data):
        """discharge_flag sensor reflects dischrg_flag=True."""
        val = sensor_map["battery_discharge_flag"].value_fn(venus_a_coordinator_data)
        assert val is True

    def test_voltage_absent_returns_none(self, sensor_map, venus_a_coordinator_data):
        """bat_voltage is not in the fixture → sensor returns None."""
        val = sensor_map["battery_voltage"].value_fn(venus_a_coordinator_data)
        assert val is None

    def test_current_absent_returns_none(self, sensor_map, venus_a_coordinator_data):
        val = sensor_map["battery_current"].value_fn(venus_a_coordinator_data)
        assert val is None

    def test_error_code_absent_returns_none(self, sensor_map, venus_a_coordinator_data):
        val = sensor_map["battery_error_code"].value_fn(venus_a_coordinator_data)
        assert val is None


# ---------------------------------------------------------------------------
# Energy System sensors (ES data absent → defaults / idle)
# ---------------------------------------------------------------------------

class TestESSensorsAbsent:
    """When ES.GetStatus was not captured, power sensors return 0/None/idle."""

    def test_battery_power_none(self, sensor_map, venus_a_coordinator_data):
        val = sensor_map["battery_power"].value_fn(venus_a_coordinator_data)
        assert val is None

    def test_battery_power_in_zero(self, sensor_map, venus_a_coordinator_data):
        """value_fn uses default 0 when es absent → max(0, 0) = 0."""
        val = sensor_map["battery_power_in"].value_fn(venus_a_coordinator_data)
        assert val == 0

    def test_battery_power_out_zero(self, sensor_map, venus_a_coordinator_data):
        val = sensor_map["battery_power_out"].value_fn(venus_a_coordinator_data)
        assert val == 0

    def test_battery_state_idle(self, sensor_map, venus_a_coordinator_data):
        val = sensor_map["battery_state"].value_fn(venus_a_coordinator_data)
        assert val == "idle"

    def test_grid_power_none(self, sensor_map, venus_a_coordinator_data):
        val = sensor_map["grid_power"].value_fn(venus_a_coordinator_data)
        assert val is None

    def test_total_pv_energy_none(self, sensor_map, venus_a_coordinator_data):
        val = sensor_map["total_pv_energy"].value_fn(venus_a_coordinator_data)
        assert val is None


class TestESSensorsWithData:
    """Verify ES power/energy sensors with a synthetic ES payload."""

    @pytest.fixture
    def data_charging(self, venus_a_coordinator_data):
        return {**venus_a_coordinator_data, "es": {"bat_power": 1200, "ongrid_power": -300, "offgrid_power": 0, "pv_power": 0, "total_pv_energy": 50000, "total_grid_input_energy": 20000, "total_grid_output_energy": 10000, "total_load_energy": 30000}}

    @pytest.fixture
    def data_discharging(self, venus_a_coordinator_data):
        return {**venus_a_coordinator_data, "es": {"bat_power": -800}}

    def test_battery_state_charging(self, sensor_map, data_charging):
        assert sensor_map["battery_state"].value_fn(data_charging) == "charging"

    def test_battery_state_discharging(self, sensor_map, data_discharging):
        assert sensor_map["battery_state"].value_fn(data_discharging) == "discharging"

    def test_battery_power_in_charging(self, sensor_map, data_charging):
        assert sensor_map["battery_power_in"].value_fn(data_charging) == 1200

    def test_battery_power_out_charging(self, sensor_map, data_charging):
        """While charging, power_out = max(0, -1200) = 0."""
        assert sensor_map["battery_power_out"].value_fn(data_charging) == 0

    def test_battery_power_out_discharging(self, sensor_map, data_discharging):
        """While discharging, power_out = max(0, 800) = 800."""
        assert sensor_map["battery_power_out"].value_fn(data_discharging) == 800

    def test_battery_power_in_discharging(self, sensor_map, data_discharging):
        assert sensor_map["battery_power_in"].value_fn(data_discharging) == 0

    def test_total_grid_import_kwh(self, sensor_map, data_charging):
        """20000 Wh → 20.0 kWh."""
        assert sensor_map["total_grid_import"].value_fn(data_charging) == pytest.approx(20.0)

    def test_total_grid_export_kwh(self, sensor_map, data_charging):
        assert sensor_map["total_grid_export"].value_fn(data_charging) == pytest.approx(10.0)

    def test_total_load_energy_kwh(self, sensor_map, data_charging):
        assert sensor_map["total_load_energy"].value_fn(data_charging) == pytest.approx(30.0)

    def test_total_pv_energy_kwh(self, sensor_map, data_charging):
        assert sensor_map["total_pv_energy"].value_fn(data_charging) == pytest.approx(50.0)

    def test_battery_power_raw(self, sensor_map, data_charging):
        assert sensor_map["battery_power"].value_fn(data_charging) == 1200

    def test_grid_power(self, sensor_map, data_charging):
        assert sensor_map["grid_power"].value_fn(data_charging) == -300


# ---------------------------------------------------------------------------
# Energy Meter / CT sensors
# ---------------------------------------------------------------------------

class TestEMSensors:
    """Sensors sourced from EM.GetStatus."""

    def test_ct_phase_a_power(self, sensor_map, venus_a_coordinator_data):
        val = sensor_map["ct_phase_a_power"].value_fn(venus_a_coordinator_data)
        assert val == 3688

    def test_ct_phase_b_power(self, sensor_map, venus_a_coordinator_data):
        val = sensor_map["ct_phase_b_power"].value_fn(venus_a_coordinator_data)
        assert val == 0

    def test_ct_phase_c_power(self, sensor_map, venus_a_coordinator_data):
        val = sensor_map["ct_phase_c_power"].value_fn(venus_a_coordinator_data)
        assert val == 0

    def test_ct_total_power(self, sensor_map, venus_a_coordinator_data):
        val = sensor_map["ct_total_power"].value_fn(venus_a_coordinator_data)
        assert val == 3688

    def test_ct_parse_state_absent(self, sensor_map, venus_a_coordinator_data):
        """parse_state not in fixture → None."""
        val = sensor_map["ct_parse_state"].value_fn(venus_a_coordinator_data)
        assert val is None


# ---------------------------------------------------------------------------
# WiFi sensors
# ---------------------------------------------------------------------------

class TestWiFiSensors:
    def test_rssi(self, sensor_map, venus_a_coordinator_data):
        assert sensor_map["wifi_rssi"].value_fn(venus_a_coordinator_data) == -27

    def test_ssid(self, sensor_map, venus_a_coordinator_data):
        assert sensor_map["wifi_ssid"].value_fn(venus_a_coordinator_data) == "Jack4GHotspot"

    def test_ip(self, sensor_map, venus_a_coordinator_data):
        assert sensor_map["wifi_ip"].value_fn(venus_a_coordinator_data) == "192.168.0.104"

    def test_gateway(self, sensor_map, venus_a_coordinator_data):
        assert sensor_map["wifi_gateway"].value_fn(venus_a_coordinator_data) == "192.168.0.1"

    def test_subnet(self, sensor_map, venus_a_coordinator_data):
        assert sensor_map["wifi_subnet"].value_fn(venus_a_coordinator_data) == "255.255.255.0"

    def test_dns(self, sensor_map, venus_a_coordinator_data):
        assert sensor_map["wifi_dns"].value_fn(venus_a_coordinator_data) == "192.168.0.1"


# ---------------------------------------------------------------------------
# Device info sensors
# ---------------------------------------------------------------------------

class TestDeviceSensors:
    def test_model(self, sensor_map, venus_a_coordinator_data):
        assert sensor_map["device_model"].value_fn(venus_a_coordinator_data) == "Venus A"

    def test_firmware_version(self, sensor_map, venus_a_coordinator_data):
        assert sensor_map["firmware_version"].value_fn(venus_a_coordinator_data) == 147

    def test_ble_mac(self, sensor_map, venus_a_coordinator_data):
        assert sensor_map["ble_mac"].value_fn(venus_a_coordinator_data) == "bc2a33600dca"

    def test_wifi_mac(self, sensor_map, venus_a_coordinator_data):
        assert sensor_map["wifi_mac"].value_fn(venus_a_coordinator_data) == "b4b024a2887a"

    def test_device_ip(self, sensor_map, venus_a_coordinator_data):
        assert sensor_map["device_ip"].value_fn(venus_a_coordinator_data) == "192.168.0.104"


# ---------------------------------------------------------------------------
# Operating mode sensor
# ---------------------------------------------------------------------------

class TestOperatingModeSensor:
    def test_mode_auto(self, sensor_map, venus_a_coordinator_data):
        assert sensor_map["operating_mode"].value_fn(venus_a_coordinator_data) == "Auto"

    def test_mode_manual(self, sensor_map, venus_a_coordinator_data):
        data = {**venus_a_coordinator_data, "mode": {"mode": "Manual"}}
        assert sensor_map["operating_mode"].value_fn(data) == "Manual"

    def test_mode_absent(self, sensor_map):
        assert sensor_map["operating_mode"].value_fn({}) is None


# ---------------------------------------------------------------------------
# Diagnostic sensor
# ---------------------------------------------------------------------------

class TestDiagnosticSensor:
    def test_last_message_seconds(self, sensor_map, venus_a_coordinator_data):
        val = sensor_map["last_message_received"].value_fn(venus_a_coordinator_data)
        assert val == 5

    def test_last_message_seconds_absent(self, sensor_map):
        assert sensor_map["last_message_received"].value_fn({}) is None


# ---------------------------------------------------------------------------
# PV sensors (Venus A has pv1..pv4, mapped via pv.pv_power field)
# ---------------------------------------------------------------------------

class TestPVSensors:
    """Venus A has multi-channel PV, but PV_SENSOR_TYPES reads pv.pv_power etc.
    The fixture has pv1_power/pv2_power but not pv_power — those are absent."""

    def test_pv_power_absent(self, pv_sensor_map, venus_a_coordinator_data):
        val = pv_sensor_map["pv_power"].value_fn(venus_a_coordinator_data)
        assert val is None

    def test_pv_voltage_absent(self, pv_sensor_map, venus_a_coordinator_data):
        val = pv_sensor_map["pv_voltage"].value_fn(venus_a_coordinator_data)
        assert val is None

    def test_pv_current_absent(self, pv_sensor_map, venus_a_coordinator_data):
        val = pv_sensor_map["pv_current"].value_fn(venus_a_coordinator_data)
        assert val is None

    def test_pv_power_with_data(self, pv_sensor_map, venus_a_coordinator_data):
        data = {**venus_a_coordinator_data, "pv": {"pv_power": 1500, "pv_voltage": 380, "pv_current": 3.9}}
        assert pv_sensor_map["pv_power"].value_fn(data) == 1500
        assert pv_sensor_map["pv_voltage"].value_fn(data) == 380
        assert pv_sensor_map["pv_current"].value_fn(data) == pytest.approx(3.9)


# ---------------------------------------------------------------------------
# Available capacity edge cases
# ---------------------------------------------------------------------------

class TestAvailableCapacityEdgeCases:
    def test_full_battery(self, sensor_map):
        data = {"battery": {"soc": 100, "rated_capacity": 4160.0}}
        val = sensor_map["battery_available_capacity"].value_fn(data)
        assert val == pytest.approx(0.0)

    def test_empty_battery(self, sensor_map):
        data = {"battery": {"soc": 0, "rated_capacity": 4160.0}}
        val = sensor_map["battery_available_capacity"].value_fn(data)
        assert val == pytest.approx(4160.0)

    def test_missing_soc(self, sensor_map):
        data = {"battery": {"rated_capacity": 4160.0}}
        assert sensor_map["battery_available_capacity"].value_fn(data) is None

    def test_missing_rated_capacity(self, sensor_map):
        data = {"battery": {"soc": 20}}
        assert sensor_map["battery_available_capacity"].value_fn(data) is None

    def test_empty_battery_data(self, sensor_map):
        assert sensor_map["battery_available_capacity"].value_fn({}) is None
