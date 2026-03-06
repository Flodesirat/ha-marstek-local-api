"""Integration tests for entity states after coordinator data update."""
from __future__ import annotations

import time

import pytest
from homeassistant.const import STATE_UNKNOWN
from homeassistant.helpers import entity_registry as er

from tests_integration.conftest import DOMAIN

_MAC = "bc2a33600dca"


def _entity_id(hass, platform: str, key: str) -> str:
    """Resolve unique_id → entity_id via the entity registry."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(platform, DOMAIN, f"{_MAC}_{key}")
    assert entity_id is not None, f"Entity not in registry: {platform}.{_MAC}_{key}"
    return entity_id


# ---------------------------------------------------------------------------
# Sensor state tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_battery_soc_state(hass, setup_integration):
    """Battery SOC reads fixture value: 20 %."""
    entity_id = _entity_id(hass, "sensor", "battery_soc")
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "20"


@pytest.mark.asyncio
async def test_ct_total_power_state(hass, setup_integration):
    """CT total power reads fixture value: 3688 W."""
    entity_id = _entity_id(hass, "sensor", "ct_total_power")
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "3688"


# ---------------------------------------------------------------------------
# Binary sensor state tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_charging_enabled_state(hass, setup_integration):
    """Charging enabled is 'on' (charg_flag=true in fixture)."""
    entity_id = _entity_id(hass, "binary_sensor", "charging_enabled")
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "on"


@pytest.mark.asyncio
async def test_discharging_enabled_state(hass, setup_integration):
    """Discharging enabled is 'on' (dischrg_flag=true in fixture)."""
    entity_id = _entity_id(hass, "binary_sensor", "discharging_enabled")
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "on"


@pytest.mark.asyncio
async def test_ct_connected_state(hass, setup_integration):
    """CT connected is 'on' (ct_state=1 in fixture)."""
    entity_id = _entity_id(hass, "binary_sensor", "ct_connected")
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "on"


# ---------------------------------------------------------------------------
# Stale-data test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stale_data_goes_unknown(hass, setup_integration):
    """Battery sensors return STATE_UNKNOWN after STALE_DATA_THRESHOLD (300 s) without updates."""
    entry = setup_integration
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entity_id = _entity_id(hass, "sensor", "battery_soc")

    # Verify initial good state
    assert hass.states.get(entity_id).state == "20"

    # Simulate battery data being 301 s old
    coordinator.category_last_updated["battery"] = time.time() - 301

    # Push a no-op data update so all listeners re-evaluate native_value
    coordinator.async_set_updated_data(coordinator.data)
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == STATE_UNKNOWN


@pytest.mark.asyncio
async def test_custom_stale_threshold_respected(hass, setup_integration_custom_stale):
    """stale_data_threshold from entry options (120 s) overrides the default (300 s)."""
    entry = setup_integration_custom_stale
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    assert coordinator.stale_data_threshold == 120

    entity_id = _entity_id(hass, "sensor", "battery_soc")
    assert hass.states.get(entity_id).state == "20"

    # 121 s old — above the custom 120 s threshold but below the default 300 s
    coordinator.category_last_updated["battery"] = time.time() - 121
    coordinator.async_set_updated_data(coordinator.data)
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == STATE_UNKNOWN
