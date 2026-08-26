"""Tests for battery_energy.py — derived battery charge/discharge energy sensors."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from conftest import _load_integration_module

_sensor_mod = _load_integration_module("sensor")
_battery_energy_mod = _load_integration_module("battery_energy")
_const_mod = _load_integration_module("const")

MarstekSensorEntityDescription = _sensor_mod.MarstekSensorEntityDescription
DOMAIN = _const_mod.DOMAIN

es_energy_components = _battery_energy_mod.es_energy_components
aggregate_energy_components = _battery_energy_mod.aggregate_energy_components
_BatteryEnergyBalanceTracker = _battery_energy_mod._BatteryEnergyBalanceTracker
MarstekBatteryEnergyBalanceSensor = _battery_energy_mod.MarstekBatteryEnergyBalanceSensor
build_single_device_entities = _battery_energy_mod.build_single_device_entities
build_multi_device_entities = _battery_energy_mod.build_multi_device_entities
build_aggregate_entities = _battery_energy_mod.build_aggregate_entities


# ---------------------------------------------------------------------------
# es_energy_components / aggregate_energy_components
# ---------------------------------------------------------------------------

class TestEsEnergyComponents:
    """Raw (pv, grid_in, grid_out, load) extraction from ES.GetStatus, in Wh.

    All 4 fields are already scaled to Wh by the coordinator (via
    CompatibilityMatrix.scale_value) before this function ever sees them.
    """

    def test_happy_path_returns_values_unchanged(self):
        data = {"es": {
            "total_pv_energy": 100,
            "total_grid_input_energy": 50,
            "total_grid_output_energy": 5,
            "total_load_energy": 20,
        }}
        assert es_energy_components(data) == (100, 50, 5, 20)

    def test_missing_es_key_returns_none(self):
        assert es_energy_components({}) is None

    @pytest.mark.parametrize("missing", [
        "total_pv_energy", "total_grid_input_energy", "total_grid_output_energy", "total_load_energy",
    ])
    def test_any_missing_component_returns_none(self, missing):
        components = {
            "total_pv_energy": 10,
            "total_grid_input_energy": 50,
            "total_grid_output_energy": 5,
            "total_load_energy": 20,
        }
        del components[missing]
        assert es_energy_components({"es": components}) is None

    def test_zero_values_are_valid(self):
        data = {"es": {
            "total_pv_energy": 0,
            "total_grid_input_energy": 0,
            "total_grid_output_energy": 0,
            "total_load_energy": 0,
        }}
        assert es_energy_components(data) == (0, 0, 0, 0)


class TestAggregateEnergyComponents:
    """Raw (pv, grid_in, grid_out, load) extraction from multi-device aggregates, in Wh."""

    def test_happy_path_returns_values_unchanged(self):
        data = {"aggregates": {
            "total_pv_energy": 100,
            "total_grid_import": 50,
            "total_grid_export": 5,
            "total_load_energy": 20,
        }}
        assert aggregate_energy_components(data) == (100, 50, 5, 20)

    def test_missing_aggregates_key_returns_none(self):
        assert aggregate_energy_components({}) is None

    def test_partial_aggregates_returns_none(self):
        data = {"aggregates": {"total_pv_energy": 10}}
        assert aggregate_energy_components(data) is None


# ---------------------------------------------------------------------------
# _BatteryEnergyBalanceTracker
# ---------------------------------------------------------------------------

class TestBatteryEnergyBalanceTracker:
    """Unit tests for the charge/discharge accumulation logic."""

    def test_first_update_seeds_baseline_without_accumulating(self):
        tracker = _BatteryEnergyBalanceTracker("charge")
        tracker.update((1000, 500, 0, 200))
        assert tracker.total_wh == 0.0

    def test_none_components_are_ignored(self):
        tracker = _BatteryEnergyBalanceTracker("charge")
        tracker.update(None)
        assert tracker.total_wh == 0.0
        # Still no baseline set — a later real update seeds instead of computing a delta.
        tracker.update((1000, 500, 0, 200))
        assert tracker.total_wh == 0.0

    def test_charging_event_credits_charge_only(self):
        """net = ΔPV + ΔGridIn - ΔLoad - ΔGridOut = 300+50-100-0 = 250 -> charge."""
        charge = _BatteryEnergyBalanceTracker("charge")
        discharge = _BatteryEnergyBalanceTracker("discharge")
        for t in (charge, discharge):
            t.update((1000, 500, 0, 200))
            t.update((1300, 550, 0, 300))
        assert charge.total_wh == pytest.approx(250)
        assert discharge.total_wh == 0.0

    def test_discharging_event_credits_discharge_only(self):
        """net = 0+0-100-400 = -500 -> discharge."""
        charge = _BatteryEnergyBalanceTracker("charge")
        discharge = _BatteryEnergyBalanceTracker("discharge")
        for t in (charge, discharge):
            t.update((1300, 550, 0, 300))
            t.update((1300, 550, 400, 400))
        assert charge.total_wh == 0.0
        assert discharge.total_wh == pytest.approx(500)

    def test_zero_net_credits_neither(self):
        """net = 100+0-100-0 = 0 -> neither side moves."""
        charge = _BatteryEnergyBalanceTracker("charge")
        discharge = _BatteryEnergyBalanceTracker("discharge")
        for t in (charge, discharge):
            t.update((1000, 500, 0, 200))
            t.update((1100, 500, 0, 300))
        assert charge.total_wh == 0.0
        assert discharge.total_wh == 0.0

    def test_counter_reset_resynchronizes_without_corrupting_total(self):
        """A firmware counter going backwards (device reboot) must not produce a bogus delta."""
        tracker = _BatteryEnergyBalanceTracker("charge")
        tracker.update((1000, 500, 0, 200))
        tracker.update((1300, 550, 0, 300))  # net=250 -> total=250
        assert tracker.total_wh == pytest.approx(250)

        tracker.update((10, 5, 0, 2))  # reset: all components dropped
        assert tracker.total_wh == pytest.approx(250)  # unchanged, baseline resynced

        # Next poll after the reset accumulates normally again from the new baseline.
        tracker.update((40, 15, 0, 12))  # net = 30+10-10-0 = 30
        assert tracker.total_wh == pytest.approx(280)

    def test_partial_counter_drop_also_resynchronizes(self):
        """Even if only one of the 4 counters drops, treat it as a reset (not a partial credit)."""
        tracker = _BatteryEnergyBalanceTracker("charge")
        tracker.update((1000, 500, 0, 200))
        tracker.update((900, 550, 0, 300))  # pv dropped, others rose
        assert tracker.total_wh == 0.0

    def test_seed_sets_initial_total(self):
        tracker = _BatteryEnergyBalanceTracker("discharge")
        tracker.seed(1234.5)
        assert tracker.total_wh == 1234.5

    def test_seed_none_is_noop(self):
        tracker = _BatteryEnergyBalanceTracker("discharge")
        tracker.total_wh = 42.0
        tracker.seed(None)
        assert tracker.total_wh == 42.0

    def test_seed_then_accumulates_on_top(self):
        tracker = _BatteryEnergyBalanceTracker("charge")
        tracker.seed(1000.0)
        tracker.update((1000, 500, 0, 200))  # baseline
        tracker.update((1300, 550, 0, 300))  # net=250
        assert tracker.total_wh == pytest.approx(1250.0)

    def test_charge_and_discharge_are_symmetric_and_never_both_move(self):
        """For any given delta, exactly one side accumulates (or neither, if net==0)."""
        charge = _BatteryEnergyBalanceTracker("charge")
        discharge = _BatteryEnergyBalanceTracker("discharge")
        sequence = [
            (1000, 500, 0, 200),
            (1300, 550, 0, 300),   # net > 0
            (1300, 550, 400, 400),  # net < 0
            (1400, 550, 400, 500),  # net = 0
        ]
        for components in sequence:
            charge.update(components)
            discharge.update(components)
        # Across this mixed sequence, both directions were exercised at least once.
        assert charge.total_wh > 0
        assert discharge.total_wh > 0


# ---------------------------------------------------------------------------
# MarstekBatteryEnergyBalanceSensor
# ---------------------------------------------------------------------------

def _make_description(direction="charge", components_fn=es_energy_components):
    return MarstekSensorEntityDescription(
        key=f"total_battery_{direction}_energy",
        name=f"Energy Total Battery {direction.capitalize()}",
        category="es",
        direction=direction,
        components_fn=components_fn,
    )


def _make_entity(desc=None, data=None, freshness_fn=None):
    desc = desc or _make_description()
    coordinator = MagicMock()
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    coordinator.data = data if data is not None else {}
    entity = MarstekBatteryEnergyBalanceSensor(
        coordinator=coordinator,
        entity_description=desc,
        unique_id="mac123_total_battery_charge_energy",
        device_info={"identifiers": {(DOMAIN, "mac123")}},
        get_data_fn=lambda: coordinator.data,
        freshness_fn=freshness_fn,
    )
    entity.hass = MagicMock()
    entity.async_write_ha_state = MagicMock()
    return entity, coordinator


class TestMarstekBatteryEnergyBalanceSensor:

    def test_init_sets_unique_id_and_device_info(self):
        entity, _ = _make_entity()
        assert entity._attr_unique_id == "mac123_total_battery_charge_energy"
        assert entity._attr_device_info == {"identifiers": {(DOMAIN, "mac123")}}
        assert entity._attr_has_entity_name is True

    def test_native_value_starts_at_zero(self):
        entity, _ = _make_entity()
        assert entity.native_value == 0.0

    def test_handle_coordinator_update_first_poll_seeds_baseline(self):
        entity, coordinator = _make_entity(
            data={"es": {
                "total_pv_energy": 10, "total_grid_input_energy": 50,
                "total_grid_output_energy": 0, "total_load_energy": 20,
            }}
        )
        entity._handle_coordinator_update()
        assert entity.native_value == 0.0
        assert entity.async_write_ha_state.called

    def test_handle_coordinator_update_accumulates_on_second_poll(self):
        entity, coordinator = _make_entity(
            data={"es": {
                "total_pv_energy": 10, "total_grid_input_energy": 50,
                "total_grid_output_energy": 0, "total_load_energy": 20,
            }}
        )
        entity._handle_coordinator_update()  # baseline
        coordinator.data = {"es": {
            "total_pv_energy": 310,  # +300 Wh
            "total_grid_input_energy": 100,  # +50 Wh
            "total_grid_output_energy": 0,
            "total_load_energy": 20,  # unchanged
        }}
        entity._handle_coordinator_update()
        # net = 300 + 50 - 0 - 0 = 350 Wh = 0.35 kWh
        assert entity.native_value == pytest.approx(0.35)

    def test_discharge_direction_only_accumulates_on_discharge(self):
        desc = _make_description(direction="discharge")
        entity, coordinator = _make_entity(
            desc=desc,
            data={"es": {
                "total_pv_energy": 10, "total_grid_input_energy": 50,
                "total_grid_output_energy": 0, "total_load_energy": 20,
            }},
        )
        entity._handle_coordinator_update()  # baseline
        coordinator.data = {"es": {
            "total_pv_energy": 40, "total_grid_input_energy": 100,
            "total_grid_output_energy": 0, "total_load_energy": 20,
        }}
        entity._handle_coordinator_update()  # this was a charge event, not discharge
        assert entity.native_value == 0.0

    def test_missing_components_leaves_total_unchanged(self):
        entity, coordinator = _make_entity(data={})
        entity._handle_coordinator_update()
        assert entity.native_value == 0.0
        assert entity.async_write_ha_state.called

    def test_available_true_with_data(self):
        entity, _ = _make_entity(data={"es": {"total_pv_energy": 1}})
        assert entity.available is True

    def test_available_false_with_no_data(self):
        entity, coordinator = _make_entity()
        coordinator.data = None
        assert entity.available is False

    def test_available_false_with_empty_data(self):
        entity, coordinator = _make_entity()
        coordinator.data = {}
        assert entity.available is False

    def test_available_false_when_stale(self):
        entity, _ = _make_entity(
            data={"es": {"total_pv_energy": 1}},
            freshness_fn=lambda: False,
        )
        assert entity.available is False

    def test_available_true_when_fresh(self):
        entity, _ = _make_entity(
            data={"es": {"total_pv_energy": 1}},
            freshness_fn=lambda: True,
        )
        assert entity.available is True

    async def test_async_added_to_hass_restores_previous_total(self):
        entity, _ = _make_entity()
        last_data = MagicMock()
        last_data.native_value = 12.5  # kWh
        entity.async_get_last_sensor_data = AsyncMock(return_value=last_data)

        await entity.async_added_to_hass()

        assert entity.native_value == pytest.approx(12.5)

    async def test_async_added_to_hass_no_previous_state_stays_at_zero(self):
        entity, _ = _make_entity()
        entity.async_get_last_sensor_data = AsyncMock(return_value=None)

        await entity.async_added_to_hass()

        assert entity.native_value == 0.0

    async def test_async_added_to_hass_then_accumulates_on_top_of_restored_total(self):
        entity, coordinator = _make_entity(
            data={"es": {
                "total_pv_energy": 10, "total_grid_input_energy": 50,
                "total_grid_output_energy": 0, "total_load_energy": 20,
            }}
        )
        last_data = MagicMock()
        last_data.native_value = 1.0  # kWh -> seeded as 1000 Wh
        entity.async_get_last_sensor_data = AsyncMock(return_value=last_data)
        await entity.async_added_to_hass()

        entity._handle_coordinator_update()  # baseline (post-restore first poll)
        coordinator.data = {"es": {
            "total_pv_energy": 310, "total_grid_input_energy": 100,
            "total_grid_output_energy": 0, "total_load_energy": 20,
        }}
        entity._handle_coordinator_update()  # net = 350 Wh

        assert entity.native_value == pytest.approx(1.35)


# ---------------------------------------------------------------------------
# build_single_device_entities / build_multi_device_entities / build_aggregate_entities
# ---------------------------------------------------------------------------

class TestBuildSingleDeviceEntities:

    def test_builds_one_entity_per_description_with_ble_mac_unique_id(self):
        coordinator = MagicMock()
        coordinator.async_add_listener = MagicMock(return_value=lambda: None)
        coordinator.data = {}
        coordinator.is_category_fresh = MagicMock(return_value=True)
        entry = MagicMock()
        entry.data = {"ble_mac": "aabbccddee", "device": "VenusE", "firmware": "147"}

        descriptions = [_make_description("charge"), _make_description("discharge")]
        entities = build_single_device_entities(coordinator, entry, descriptions)

        assert len(entities) == 2
        assert entities[0]._attr_unique_id == "aabbccddee_total_battery_charge_energy"
        assert entities[1]._attr_unique_id == "aabbccddee_total_battery_discharge_energy"
        assert entities[0]._attr_device_info["name"] == "Marstek VenusE"

    def test_falls_back_to_wifi_mac(self):
        coordinator = MagicMock()
        coordinator.data = {}
        entry = MagicMock()
        entry.data = {"wifi_mac": "ffeeddccbbaa", "device": "VenusE", "firmware": "147"}

        entities = build_single_device_entities(coordinator, entry, [_make_description()])
        assert "ffeeddccbbaa" in entities[0]._attr_unique_id

    def test_freshness_fn_delegates_to_coordinator(self):
        coordinator = MagicMock()
        coordinator.data = {}
        coordinator.is_category_fresh = MagicMock(return_value=False)
        entry = MagicMock()
        entry.data = {"ble_mac": "aabbccddee", "device": "VenusE", "firmware": "147"}

        entities = build_single_device_entities(coordinator, entry, [_make_description()])
        assert entities[0].available is False
        coordinator.is_category_fresh.assert_called_with("es")


class TestBuildMultiDeviceEntities:

    def test_builds_entities_with_mac_unique_id_and_suffix_in_name(self):
        coordinator = MagicMock()
        coordinator.get_device_data = MagicMock(return_value={})
        device_coordinator = MagicMock()
        device_coordinator.is_category_fresh = MagicMock(return_value=True)
        device_data = {"device": "VenusE", "firmware": "147"}

        entities = build_multi_device_entities(
            coordinator, "aa:bb:cc:dd:ee:ff", device_coordinator, device_data,
            [_make_description("charge")],
        )

        assert len(entities) == 1
        assert entities[0]._attr_unique_id == "aa:bb:cc:dd:ee:ff_total_battery_charge_energy"
        assert "eeff" in entities[0]._attr_device_info["name"].lower()

    def test_get_data_fn_uses_correct_mac(self):
        coordinator = MagicMock()
        coordinator.get_device_data = MagicMock(side_effect=lambda mac: {"mac": mac})
        device_coordinator = MagicMock()
        device_coordinator.is_category_fresh = MagicMock(return_value=True)

        entities = build_multi_device_entities(
            coordinator, "aa:bb:cc:dd:ee:ff", device_coordinator, {"device": "VenusE"},
            [_make_description()],
        )
        assert entities[0]._get_data_fn() == {"mac": "aa:bb:cc:dd:ee:ff"}


class TestBuildAggregateEntities:

    def test_builds_entities_with_system_unique_id(self):
        coordinator = MagicMock()
        coordinator.data = {}

        entities = build_aggregate_entities(
            coordinator, "aabb_ccdd",
            [_make_description("charge", aggregate_energy_components),
             _make_description("discharge", aggregate_energy_components)],
        )

        assert len(entities) == 2
        assert entities[0]._attr_unique_id == "aabb_ccdd_total_battery_charge_energy"
        assert entities[0]._attr_device_info["name"] == "Marstek System"

    def test_no_freshness_fn_aggregate_is_always_considered_fresh(self):
        coordinator = MagicMock()
        coordinator.data = {"aggregates": {
            "total_pv_energy": 1, "total_grid_import": 1,
            "total_grid_export": 0, "total_load_energy": 1,
        }}
        entities = build_aggregate_entities(
            coordinator, "aabb_ccdd", [_make_description("charge", aggregate_energy_components)]
        )
        assert entities[0].available is True


# ---------------------------------------------------------------------------
# End-to-end: entity descriptions are wired into SENSOR_TYPES / AGGREGATE_SENSOR_TYPES
# ---------------------------------------------------------------------------

class TestSensorTypesWiring:

    def test_single_device_descriptions_present(self):
        keys = {d.key for d in _sensor_mod.SENSOR_TYPES}
        assert "total_battery_charge_energy" in keys
        assert "total_battery_discharge_energy" in keys

    def test_aggregate_descriptions_present(self):
        keys = {d.key for d in _sensor_mod.AGGREGATE_SENSOR_TYPES}
        assert "system_total_battery_charge_energy" in keys
        assert "system_total_battery_discharge_energy" in keys

    def test_battery_energy_descriptions_have_no_value_fn(self):
        """These are stateful, driven by components_fn, not the generic value_fn path."""
        for d in _sensor_mod.SENSOR_TYPES:
            if d.components_fn is not None:
                assert d.value_fn is None

    def test_setup_entry_single_device_includes_battery_energy_sensors(self):
        entry = MagicMock()
        entry.data = {"ble_mac": "aabbccddee", "device": "VenusE", "firmware": "147"}
        coordinator = MagicMock()
        coordinator.__class__ = _sensor_mod.MarstekDataUpdateCoordinator
        coordinator.data = {"battery": {"soc": 80}}
        coordinator.compatibility.base_model = "VenusE"
        coordinator.is_category_fresh = MagicMock(return_value=True)

        hass = MagicMock()
        hass.data = {DOMAIN: {entry.entry_id: {_const_mod.DATA_COORDINATOR: coordinator}}}

        added = []
        import asyncio
        asyncio.run(_sensor_mod.async_setup_entry(hass, entry, added.append))

        keys = {e.entity_description.key for e in added[0]}
        assert "total_battery_charge_energy" in keys
        assert "total_battery_discharge_energy" in keys
        battery_entities = [
            e for e in added[0]
            if e.entity_description.key in ("total_battery_charge_energy", "total_battery_discharge_energy")
        ]
        assert all(isinstance(e, MarstekBatteryEnergyBalanceSensor) for e in battery_entities)
