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
_power_battery = _sensor_mod._power_battery
_filter_energy_glitch = _sensor_mod._filter_energy_glitch


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_single_coordinator(device_model: str = "VenusE", data: dict | None = None) -> MagicMock:
    c = MagicMock()
    c.data = data if data is not None else {"battery": {"soc": 80}}
    c.device_model = device_model
    c.compatibility.base_model = device_model
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
    device_coordinator.compatibility.base_model = device_model

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
# _power_battery helper
# ---------------------------------------------------------------------------

class TestPowerBattery:
    """Unit tests for _power_battery(data) = pv_power - ongrid_power - offgrid_power."""

    def test_no_es_returns_none(self):
        assert _power_battery({}) is None
        assert _power_battery({"battery": {"soc": 80}}) is None

    def test_all_zero_returns_zero(self):
        data = {"es": {"pv_power": 0, "ongrid_power": 0, "offgrid_power": 0}}
        assert _power_battery(data) == 0

    def test_pv_only_charging(self):
        """PV=500 (from pv dict), grid=0, offgrid=0 → battery power = +500 (charging)."""
        data = {"es": {"ongrid_power": 0, "offgrid_power": 0}, "pv": {"pv_power": 500}}
        assert _power_battery(data) == 500

    def test_grid_export_discharging(self):
        """PV=0, ongrid=800, offgrid=0 → battery power = -800 (discharging)."""
        data = {"es": {"ongrid_power": 800, "offgrid_power": 0}}
        assert _power_battery(data) == -800

    def test_pv_minus_grid_minus_offgrid(self):
        """PV=1500, ongrid=500, offgrid=200 → 1500 - 500 - 200 = 800."""
        data = {"es": {"ongrid_power": 500, "offgrid_power": 200}, "pv": {"pv_power": 1500}}
        assert _power_battery(data) == 800

    def test_missing_fields_default_zero(self):
        """Missing pv/grid/offgrid keys default to 0."""
        assert _power_battery({"es": {}}) == 0
        assert _power_battery({"es": {}, "pv": {"pv_power": 300}}) == 300
        assert _power_battery({"es": {"ongrid_power": 100}}) == -100

    def test_none_field_values_treated_as_zero(self):
        """None values for fields are treated as 0."""
        data = {"es": {"ongrid_power": None, "offgrid_power": None}, "pv": {"pv_power": None}}
        assert _power_battery(data) == 0


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

    async def test_pv_channel_keys_present_for_venus_d(self):
        """VenusD — all pv1..pv4 channel sensor keys are present."""
        hass = MagicMock()
        entry = _make_entry(device="VenusD")
        coordinator = _make_single_coordinator(device_model=DEVICE_MODEL_VENUS_D)
        hass.data = {DOMAIN: {entry.entry_id: {DATA_COORDINATOR: coordinator}}}

        added = []
        await async_setup_entry(hass, entry, added.append)

        keys = {e.entity_description.key for e in added[0]}
        for ch in range(1, 5):
            for field in ("power", "voltage", "current", "state"):
                assert f"pv{ch}_{field}" in keys, f"pv{ch}_{field} missing for VenusD"

    async def test_pv_channel_keys_present_for_venus_a(self):
        """VenusA — all pv1..pv4 channel sensor keys are present."""
        hass = MagicMock()
        entry = _make_entry(device="VenusA")
        coordinator = _make_single_coordinator(device_model=DEVICE_MODEL_VENUS_A)
        hass.data = {DOMAIN: {entry.entry_id: {DATA_COORDINATOR: coordinator}}}

        added = []
        await async_setup_entry(hass, entry, added.append)

        keys = {e.entity_description.key for e in added[0]}
        for ch in range(1, 5):
            for field in ("power", "voltage", "current", "state"):
                assert f"pv{ch}_{field}" in keys, f"pv{ch}_{field} missing for VenusA"

    async def test_pv_channel_keys_present_for_venus_a_with_space(self):
        """'Venus A' (with space, as reported by device) — pv channel sensors created."""
        hass = MagicMock()
        entry = _make_entry(device="Venus A")
        coordinator = _make_single_coordinator(device_model="Venus A")
        coordinator.compatibility.base_model = DEVICE_MODEL_VENUS_A  # normalized
        hass.data = {DOMAIN: {entry.entry_id: {DATA_COORDINATOR: coordinator}}}

        added = []
        await async_setup_entry(hass, entry, added.append)

        keys = {e.entity_description.key for e in added[0]}
        for ch in range(1, 5):
            for field in ("power", "voltage", "current", "state"):
                assert f"pv{ch}_{field}" in keys, f"pv{ch}_{field} missing for 'Venus A'"

    async def test_pv_channel_keys_absent_for_venus_e(self):
        """VenusE — no pv channel sensors created."""
        hass = MagicMock()
        entry = _make_entry(device="VenusE")
        coordinator = _make_single_coordinator(device_model="VenusE")
        hass.data = {DOMAIN: {entry.entry_id: {DATA_COORDINATOR: coordinator}}}

        added = []
        await async_setup_entry(hass, entry, added.append)

        keys = {e.entity_description.key for e in added[0]}
        for ch in range(1, 5):
            for field in ("power", "voltage", "current", "state"):
                assert f"pv{ch}_{field}" not in keys, f"pv{ch}_{field} should not exist for VenusE"


# ---------------------------------------------------------------------------
# _filter_energy_glitch — firmware glitch filter for energy counters
# ---------------------------------------------------------------------------

def _energy_desc():
    """Return a MarstekSensorEntityDescription for an energy counter sensor."""
    from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
    from homeassistant.const import UnitOfEnergy
    return MarstekSensorEntityDescription(
        key="total_grid_export",
        name="Energy Total Grid Export",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda data: data.get("es", {}).get("total_grid_output_energy"),
        category="es",
    )


def _non_energy_desc():
    """Return a description that is NOT an energy TOTAL_INCREASING sensor."""
    return MarstekSensorEntityDescription(
        key="battery_soc",
        name="Battery SOC",
        value_fn=lambda data: data.get("battery", {}).get("soc"),
    )


class TestFilterEnergyGlitch:
    """Tests for _filter_energy_glitch — reproduces the Venus A firmware bug."""

    def test_first_value_accepted_and_stored(self):
        state = {"last_valid": None, "drop_count": 0}
        desc = _energy_desc()
        assert _filter_energy_glitch(desc, 53.549, state) == pytest.approx(53.549)
        assert state["last_valid"] == pytest.approx(53.549)
        assert state["drop_count"] == 0

    def test_increasing_value_accepted(self):
        state = {"last_valid": 53.549, "drop_count": 0}
        desc = _energy_desc()
        assert _filter_energy_glitch(desc, 53.957, state) == pytest.approx(53.957)

    def test_single_glitch_rejected(self):
        """Reproduces: 53.957 → 0.199 (firmware bug) → 53.961."""
        state = {"last_valid": 53.957, "drop_count": 0}
        desc = _energy_desc()
        assert _filter_energy_glitch(desc, 0.199, state) is None
        assert state["last_valid"] == pytest.approx(53.957)
        assert state["drop_count"] == 1

    def test_value_recovers_after_single_glitch(self):
        """After one rejected glitch, the correct value is accepted."""
        state = {"last_valid": 53.957, "drop_count": 1}
        desc = _energy_desc()
        assert _filter_energy_glitch(desc, 53.961, state) == pytest.approx(53.961)
        assert state["drop_count"] == 0

    def test_two_consecutive_glitches_rejected(self):
        """Two consecutive drops are still rejected."""
        state = {"last_valid": 56.260, "drop_count": 0}
        desc = _energy_desc()
        _filter_energy_glitch(desc, 0.196, state)  # drop_count → 1
        result = _filter_energy_glitch(desc, 0.201, state)  # drop_count → 2
        assert result is None
        assert state["drop_count"] == 2

    def test_three_consecutive_drops_accepted_as_real_reset(self):
        """Three consecutive lower readings → genuine counter reset → accept."""
        state = {"last_valid": 56.260, "drop_count": 0}
        desc = _energy_desc()
        _filter_energy_glitch(desc, 0.196, state)   # drop_count → 1
        _filter_energy_glitch(desc, 0.201, state)   # drop_count → 2
        result = _filter_energy_glitch(desc, 0.205, state)  # drop_count → 3 → reset
        assert result == pytest.approx(0.205)
        assert state["last_valid"] == pytest.approx(0.205)
        assert state["drop_count"] == 0

    def test_none_value_returned_as_none(self):
        state = {"last_valid": 53.0, "drop_count": 0}
        desc = _energy_desc()
        assert _filter_energy_glitch(desc, None, state) is None
        assert state["last_valid"] == pytest.approx(53.0)

    def test_non_energy_sensor_not_filtered(self):
        """Non-energy sensors pass through unchanged even if the value drops."""
        state = {"last_valid": 80.0, "drop_count": 0}
        desc = _non_energy_desc()
        assert _filter_energy_glitch(desc, 20.0, state) == pytest.approx(20.0)


class TestTotalGridExportGlitchOnSensor:
    """End-to-end test: MarstekSensor.native_value for total_grid_export."""

    def _make_sensor(self, es_value):
        coord = _make_single_coordinator(data={"es": {"total_grid_output_energy": es_value}})
        entry = _make_entry()
        desc = next(d for d in SENSOR_TYPES if d.key == "total_grid_export")
        sensor = MarstekSensor(coordinator=coord, entity_description=desc, entry=entry)
        sensor.coordinator = coord
        return sensor, coord

    def test_normal_sequence_passes_through(self):
        sensor, coord = self._make_sensor(53549)
        assert sensor.native_value == pytest.approx(53.549)
        coord.data = {"es": {"total_grid_output_energy": 53957}}
        assert sensor.native_value == pytest.approx(53.957)

    def test_single_glitch_returns_none(self):
        """Firmware bug: value drops from ~53.957 kWh to 0.199 kWh for one poll."""
        sensor, coord = self._make_sensor(53957)
        _ = sensor.native_value  # seed last_valid
        coord.data = {"es": {"total_grid_output_energy": 199}}
        assert sensor.native_value is None

    def test_value_recovers_after_glitch(self):
        """After the glitch, the next correct reading is accepted."""
        sensor, coord = self._make_sensor(53957)
        _ = sensor.native_value  # seed
        coord.data = {"es": {"total_grid_output_energy": 199}}
        _ = sensor.native_value  # glitch → None
        coord.data = {"es": {"total_grid_output_energy": 53961}}
        assert sensor.native_value == pytest.approx(53.961)
