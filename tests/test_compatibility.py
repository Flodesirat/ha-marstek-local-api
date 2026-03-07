"""Tests for the CompatibilityMatrix scaling logic using Venus A FW 147 fixture data."""
import pytest

from conftest import CompatibilityMatrix


# ---------------------------------------------------------------------------
# Venus A FW 147 — expected divisors from SCALING_MATRIX
# For VenusA / HW 2.0:
#   bat_temp, bat_capacity, bat_power, total_grid_*: divisor 1.0 at FW 0+
#   bat_voltage, bat_current: divisor 100.0 (all FW)
# ---------------------------------------------------------------------------

class TestVenusAFirmware147Scaling:
    """Verify scaling factors applied by CompatibilityMatrix for Venus A FW 147."""

    @pytest.fixture(autouse=True)
    def matrix(self, venus_a_compatibility):
        self.m = venus_a_compatibility

    # --- bat_temp ---

    def test_bat_temp_identity(self):
        """VenusA FW 0+: divisor 1.0 → value unchanged."""
        assert self.m.scale_value(29.0, "bat_temp") == pytest.approx(29.0)

    def test_bat_temp_integer(self):
        assert self.m.scale_value(25, "bat_temp") == pytest.approx(25.0)

    def test_bat_temp_none(self):
        assert self.m.scale_value(None, "bat_temp") is None

    # --- bat_capacity ---

    def test_bat_capacity_fixture_value(self):
        """869.0 Wh raw → 869.0 Wh after scaling (divisor 1.0)."""
        assert self.m.scale_value(869.0, "bat_capacity") == pytest.approx(869.0)

    def test_bat_capacity_zero(self):
        assert self.m.scale_value(0, "bat_capacity") == pytest.approx(0.0)

    # --- bat_power ---

    def test_bat_power_positive(self):
        """Charging: positive power, divisor 1.0 for VenusA."""
        assert self.m.scale_value(500, "bat_power") == pytest.approx(500.0)

    def test_bat_power_negative(self):
        """Discharging: negative power preserved."""
        assert self.m.scale_value(-800, "bat_power") == pytest.approx(-800.0)

    # --- bat_voltage ---

    def test_bat_voltage_scaled(self):
        """VenusA all FW: raw in centi-V → divide by 100."""
        assert self.m.scale_value(5000, "bat_voltage") == pytest.approx(50.0)

    def test_bat_voltage_none(self):
        assert self.m.scale_value(None, "bat_voltage") is None

    # --- bat_current ---

    def test_bat_current_scaled(self):
        """VenusA all FW: raw in centi-A → divide by 100."""
        assert self.m.scale_value(200, "bat_current") == pytest.approx(2.0)

    def test_bat_current_negative(self):
        """Negative current (discharge) scaled correctly."""
        assert self.m.scale_value(-150, "bat_current") == pytest.approx(-1.5)

    # --- energy totals ---

    def test_total_grid_input_energy(self):
        """VenusA FW 0+: divisor 1.0 → raw value in Wh unchanged."""
        assert self.m.scale_value(12345, "total_grid_input_energy") == pytest.approx(12345.0)

    def test_total_grid_output_energy(self):
        assert self.m.scale_value(6789, "total_grid_output_energy") == pytest.approx(6789.0)

    def test_total_load_energy(self):
        assert self.m.scale_value(9999, "total_load_energy") == pytest.approx(9999.0)

    # --- unknown field ---

    def test_unknown_field_returns_raw(self):
        """Fields not in the matrix are returned unchanged."""
        assert self.m.scale_value(42, "nonexistent_field") == 42

    def test_unknown_field_none(self):
        assert self.m.scale_value(None, "nonexistent_field") is None


class TestCompatibilityMatrixFirmwareBoundary:
    """Verify that the firmware version boundary lookup selects the correct entry."""

    def test_venus_c_below_154_bat_power(self):
        """VenusC FW 100 < 154 → divisor 10.0."""
        m = CompatibilityMatrix(device_model="VenusC", firmware_version=100)
        assert m.scale_value(500, "bat_power") == pytest.approx(50.0)

    def test_venus_c_at_154_bat_power(self):
        """VenusC FW 154 → divisor 1.0."""
        m = CompatibilityMatrix(device_model="VenusC", firmware_version=154)
        assert m.scale_value(500, "bat_power") == pytest.approx(500.0)

    def test_venus_c_above_154_bat_power(self):
        """VenusC FW 200 > 154 → still uses FW 154 entry (divisor 1.0)."""
        m = CompatibilityMatrix(device_model="VenusC", firmware_version=200)
        assert m.scale_value(500, "bat_power") == pytest.approx(500.0)

    def test_venus_e_hw3_fw139_bat_capacity(self):
        """VenusE 3.0 FW 139 → divisor 0.1 → value × 10."""
        m = CompatibilityMatrix(device_model="VenusE 3.0", firmware_version=139)
        assert m.scale_value(100, "bat_capacity") == pytest.approx(1000.0)

    def test_venus_e_hw3_below_139_bat_capacity(self):
        """VenusE 3.0 FW 100 < 139 → divisor 1.0."""
        m = CompatibilityMatrix(device_model="VenusE 3.0", firmware_version=100)
        assert m.scale_value(100, "bat_capacity") == pytest.approx(100.0)

    def test_venus_d_below_154_bat_temp(self):
        """VenusD FW 100: divisor 1.0 (value already in °C)."""
        m = CompatibilityMatrix(device_model="VenusD", firmware_version=100)
        assert m.scale_value(290, "bat_temp") == pytest.approx(290.0)

    def test_venus_d_at_154_bat_temp(self):
        """VenusD FW 154: divisor 0.1 → value × 10."""
        m = CompatibilityMatrix(device_model="VenusD", firmware_version=154)
        assert m.scale_value(290, "bat_temp") == pytest.approx(2900.0)


class TestPVPowerScaling:
    """Verify pv_power scaling: Venus A ÷10, all other models unchanged."""

    def test_venus_a_pv_power_scaled_by_10(self):
        """VenusA FW 0+: raw in deca-W → divide by 10."""
        m = CompatibilityMatrix(device_model="VenusA", firmware_version=147)
        assert m.scale_value(1000, "pv_power") == pytest.approx(100.0)

    def test_venus_a_pv_power_zero(self):
        m = CompatibilityMatrix(device_model="VenusA", firmware_version=147)
        assert m.scale_value(0, "pv_power") == pytest.approx(0.0)

    def test_venus_a_pv_power_none(self):
        m = CompatibilityMatrix(device_model="VenusA", firmware_version=147)
        assert m.scale_value(None, "pv_power") is None

    def test_venus_a_pv_power_high_fw_still_scaled(self):
        """VenusA FW 999 (future): still uses FW 0 entry → ÷10."""
        m = CompatibilityMatrix(device_model="VenusA", firmware_version=999)
        assert m.scale_value(500, "pv_power") == pytest.approx(50.0)

    def test_venus_d_pv_power_not_in_matrix_returns_raw(self):
        """VenusD has no pv_power entry → raw value returned unchanged."""
        m = CompatibilityMatrix(device_model="VenusD", firmware_version=154)
        assert m.scale_value(1000, "pv_power") == pytest.approx(1000.0)

    def test_venus_e_pv_power_not_in_matrix_returns_raw(self):
        """VenusE has no pv_power entry → raw value returned unchanged."""
        m = CompatibilityMatrix(device_model="VenusE", firmware_version=200)
        assert m.scale_value(1000, "pv_power") == pytest.approx(1000.0)

    def test_venus_c_pv_power_not_in_matrix_returns_raw(self):
        """VenusC has no pv_power entry → raw value returned unchanged."""
        m = CompatibilityMatrix(device_model="VenusC", firmware_version=154)
        assert m.scale_value(1000, "pv_power") == pytest.approx(1000.0)

    def test_venus_e_hw3_pv_power_not_in_matrix_returns_raw(self):
        """VenusE 3.0 has no pv_power entry → raw value returned unchanged."""
        m = CompatibilityMatrix(device_model="VenusE 3.0", firmware_version=139)
        assert m.scale_value(1000, "pv_power") == pytest.approx(1000.0)


class TestCompatibilityMatrixHardwareVersionParsing:
    """Verify hardware version extraction from model strings."""

    def test_venus_e_default_hw(self):
        m = CompatibilityMatrix(device_model="VenusE", firmware_version=154)
        assert m.hardware_version == "2.0"
        assert m.base_model == "VenusE"

    def test_venus_e_30_hw(self):
        m = CompatibilityMatrix(device_model="VenusE 3.0", firmware_version=139)
        assert m.hardware_version == "3.0"
        assert m.base_model == "VenusE"

    def test_venus_a_default_hw(self):
        m = CompatibilityMatrix(device_model="VenusA", firmware_version=147)
        assert m.hardware_version == "2.0"
        assert m.base_model == "VenusA"

    def test_empty_model_defaults_to_hw2(self):
        m = CompatibilityMatrix(device_model="", firmware_version=100)
        assert m.hardware_version == "2.0"

    def test_model_without_version_defaults_to_hw2(self):
        m = CompatibilityMatrix(device_model="VenusD", firmware_version=200)
        assert m.hardware_version == "2.0"
