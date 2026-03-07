"""Tests for MarstekMultiDeviceCoordinator aggregate calculations.

Tests the _calculate_aggregates() logic in isolation, using coordinator data
dicts built from the Venus A FW 147 fixture as a single-device base, then
extended to multi-device scenarios.
"""
import importlib
import sys
from unittest.mock import MagicMock

import pytest

from conftest import _load_integration_module

_coordinator_mod = _load_integration_module("coordinator")
MarstekMultiDeviceCoordinator = _coordinator_mod.MarstekMultiDeviceCoordinator


# ---------------------------------------------------------------------------
# Helper to build a minimal coordinator that exposes _calculate_aggregates
# ---------------------------------------------------------------------------

def _make_multi_coordinator(device_data_list: list[dict]) -> MarstekMultiDeviceCoordinator:
    """Build a MarstekMultiDeviceCoordinator with pre-populated device coordinators."""
    hass = MagicMock()
    hass.data = {}
    coordinator = MarstekMultiDeviceCoordinator.__new__(MarstekMultiDeviceCoordinator)
    coordinator.devices = []
    coordinator.device_coordinators = {}
    coordinator.update_count = 1
    coordinator.dod_percent = 80

    for i, device_data in enumerate(device_data_list):
        mac = f"aabbccdd{i:04x}"
        device_coord = MagicMock()
        device_coord.data = device_data
        coordinator.device_coordinators[mac] = device_coord

    return coordinator


# ---------------------------------------------------------------------------
# Single device — Venus A FW 147 data
# ---------------------------------------------------------------------------

class TestAggregatesSingleDevice:
    @pytest.fixture
    def coordinator(self, venus_a_coordinator_data):
        return _make_multi_coordinator([venus_a_coordinator_data])

    def test_total_battery_power_zero(self, coordinator):
        """No ES data → bat_power defaults to 0."""
        agg = coordinator._calculate_aggregates()
        assert agg["total_battery_power"] == 0

    def test_total_power_in_zero(self, coordinator):
        agg = coordinator._calculate_aggregates()
        assert agg["total_power_in"] == 0

    def test_total_power_out_zero(self, coordinator):
        agg = coordinator._calculate_aggregates()
        assert agg["total_power_out"] == 0

    def test_total_rated_capacity(self, coordinator):
        """rated_capacity=4160 Wh from battery data."""
        agg = coordinator._calculate_aggregates()
        assert agg["total_rated_capacity"] == pytest.approx(4160.0)

    def test_total_remaining_capacity(self, coordinator):
        """bat_capacity=869.0 Wh (after scaling)."""
        agg = coordinator._calculate_aggregates()
        assert agg["total_remaining_capacity"] == pytest.approx(869.0)

    def test_average_soc(self, coordinator):
        """Single device soc=20 → weighted average = 20."""
        agg = coordinator._calculate_aggregates()
        assert agg["average_soc"] == pytest.approx(20.0)

    def test_total_available_capacity(self, coordinator):
        """(100 - 20) * 4160 / 100 = 3328.0 Wh."""
        agg = coordinator._calculate_aggregates()
        assert agg["total_available_capacity"] == pytest.approx(3328.0)

    def test_combined_state_idle(self, coordinator):
        agg = coordinator._calculate_aggregates()
        assert agg["combined_state"] == "idle"

    def test_total_grid_power_zero(self, coordinator):
        agg = coordinator._calculate_aggregates()
        assert agg["total_grid_power"] == 0

    def test_total_solar_power_zero(self, coordinator):
        agg = coordinator._calculate_aggregates()
        assert agg["total_solar_power"] == 0


# ---------------------------------------------------------------------------
# Single device — charging state
# ---------------------------------------------------------------------------

class TestAggregatesSingleDeviceCharging:
    @pytest.fixture
    def coordinator(self, venus_a_coordinator_data):
        data = {
            **venus_a_coordinator_data,
            "es": {
                "bat_power": 1200,
                "ongrid_power": 500,
                "offgrid_power": 0,
                "pv_power": 1500,
                "total_pv_energy": 10000,
                "total_grid_input_energy": 5000,
                "total_grid_output_energy": 2000,
                "total_load_energy": 8000,
            },
        }
        return _make_multi_coordinator([data])

    def test_total_battery_power(self, coordinator):
        assert coordinator._calculate_aggregates()["total_battery_power"] == 1200

    def test_total_power_in(self, coordinator):
        assert coordinator._calculate_aggregates()["total_power_in"] == 1200

    def test_total_power_out(self, coordinator):
        assert coordinator._calculate_aggregates()["total_power_out"] == 0

    def test_combined_state_charging(self, coordinator):
        assert coordinator._calculate_aggregates()["combined_state"] == "charging"

    def test_total_solar_power(self, coordinator):
        assert coordinator._calculate_aggregates()["total_solar_power"] == 1500

    def test_total_grid_power(self, coordinator):
        assert coordinator._calculate_aggregates()["total_grid_power"] == 500

    def test_total_pv_energy(self, coordinator):
        assert coordinator._calculate_aggregates()["total_pv_energy"] == 10000

    def test_total_grid_import(self, coordinator):
        assert coordinator._calculate_aggregates()["total_grid_import"] == 5000

    def test_total_grid_export(self, coordinator):
        assert coordinator._calculate_aggregates()["total_grid_export"] == 2000

    def test_total_load_energy(self, coordinator):
        assert coordinator._calculate_aggregates()["total_load_energy"] == 8000


# ---------------------------------------------------------------------------
# Single device — discharging state
# ---------------------------------------------------------------------------

class TestAggregatesSingleDeviceDischarging:
    @pytest.fixture
    def coordinator(self, venus_a_coordinator_data):
        data = {**venus_a_coordinator_data, "es": {"bat_power": -800}}
        return _make_multi_coordinator([data])

    def test_total_battery_power(self, coordinator):
        assert coordinator._calculate_aggregates()["total_battery_power"] == -800

    def test_total_power_out(self, coordinator):
        assert coordinator._calculate_aggregates()["total_power_out"] == 800

    def test_total_power_in(self, coordinator):
        assert coordinator._calculate_aggregates()["total_power_in"] == 0

    def test_combined_state_discharging(self, coordinator):
        assert coordinator._calculate_aggregates()["combined_state"] == "discharging"


# ---------------------------------------------------------------------------
# Two devices — same state
# ---------------------------------------------------------------------------

class TestAggregatesTwoDevicesBothCharging:
    @pytest.fixture
    def coordinator(self, venus_a_coordinator_data):
        dev1 = {**venus_a_coordinator_data, "es": {"bat_power": 1000}, "battery": {"soc": 30, "rated_capacity": 4000, "bat_capacity": 1200}}
        dev2 = {**venus_a_coordinator_data, "es": {"bat_power": 500}, "battery": {"soc": 60, "rated_capacity": 2000, "bat_capacity": 1200}}
        return _make_multi_coordinator([dev1, dev2])

    def test_total_battery_power(self, coordinator):
        assert coordinator._calculate_aggregates()["total_battery_power"] == 1500

    def test_total_power_in(self, coordinator):
        assert coordinator._calculate_aggregates()["total_power_in"] == 1500

    def test_combined_state_charging(self, coordinator):
        assert coordinator._calculate_aggregates()["combined_state"] == "charging"

    def test_total_rated_capacity(self, coordinator):
        assert coordinator._calculate_aggregates()["total_rated_capacity"] == pytest.approx(6000.0)

    def test_average_soc_weighted(self, coordinator):
        """Weighted average: (30*4000 + 60*2000) / (4000+2000) = 240000/6000 = 40."""
        assert coordinator._calculate_aggregates()["average_soc"] == pytest.approx(40.0)

    def test_total_available_capacity(self, coordinator):
        """(100 - 40) * 6000 / 100 = 3600 Wh."""
        assert coordinator._calculate_aggregates()["total_available_capacity"] == pytest.approx(3600.0)


# ---------------------------------------------------------------------------
# Two devices — conflicting states
# ---------------------------------------------------------------------------

class TestAggregatesTwoDevicesConflicting:
    @pytest.fixture
    def coordinator(self, venus_a_coordinator_data):
        dev1 = {**venus_a_coordinator_data, "es": {"bat_power": 800}}
        dev2 = {**venus_a_coordinator_data, "es": {"bat_power": -600}}
        return _make_multi_coordinator([dev1, dev2])

    def test_combined_state_conflicting(self, coordinator):
        assert coordinator._calculate_aggregates()["combined_state"] == "conflicting"

    def test_total_battery_power(self, coordinator):
        assert coordinator._calculate_aggregates()["total_battery_power"] == 200


# ---------------------------------------------------------------------------
# Two devices — partly charging (one idle, one charging)
# ---------------------------------------------------------------------------

class TestAggregatesTwoDevicesPartlyCharging:
    @pytest.fixture
    def coordinator(self, venus_a_coordinator_data):
        dev1 = {**venus_a_coordinator_data, "es": {"bat_power": 600}}
        dev2 = {**venus_a_coordinator_data, "es": {"bat_power": 0}}
        return _make_multi_coordinator([dev1, dev2])

    def test_combined_state_partly_charging(self, coordinator):
        assert coordinator._calculate_aggregates()["combined_state"] == "partly_charging"


# ---------------------------------------------------------------------------
# Empty coordinator
# ---------------------------------------------------------------------------

class TestAggregatesEmpty:
    def test_no_devices(self):
        coordinator = _make_multi_coordinator([])
        agg = coordinator._calculate_aggregates()
        assert agg == {}

    def test_device_with_no_data(self):
        coordinator = _make_multi_coordinator([])
        # Add a coordinator with None data
        mac = "000000000000"
        device_coord = MagicMock()
        device_coord.data = None
        coordinator.device_coordinators[mac] = device_coord
        agg = coordinator._calculate_aggregates()
        assert agg == {}
