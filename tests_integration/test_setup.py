"""Integration tests for entry setup / teardown using a real HA instance."""
from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er

from tests_integration.conftest import DOMAIN

# MAC from fixture: ble_mac = bc2a33600dca
_MAC = "bc2a33600dca"


@pytest.mark.asyncio
async def test_async_setup_entry_success(hass, setup_integration):
    """Config entry reaches LOADED state after setup."""
    entry = setup_integration
    assert entry.state == ConfigEntryState.LOADED


@pytest.mark.asyncio
async def test_async_unload_entry(hass, setup_integration):
    """Entry can be cleanly unloaded."""
    entry = setup_integration
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state == ConfigEntryState.NOT_LOADED


@pytest.mark.asyncio
async def test_entity_registry_sensor_battery_soc(hass, setup_integration):
    """Entity registry contains the battery SOC sensor."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{_MAC}_battery_soc")
    assert entity_id is not None


@pytest.mark.asyncio
async def test_entity_registry_sensor_ct_total_power(hass, setup_integration):
    """Entity registry contains the CT total power sensor."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{_MAC}_ct_total_power")
    assert entity_id is not None


@pytest.mark.asyncio
async def test_entity_registry_binary_sensors(hass, setup_integration):
    """Entity registry contains all four binary sensors."""
    registry = er.async_get(hass)
    for key in ("charging_enabled", "discharging_enabled", "bluetooth_connected", "ct_connected"):
        entity_id = registry.async_get_entity_id("binary_sensor", DOMAIN, f"{_MAC}_{key}")
        assert entity_id is not None, f"Missing binary sensor: {key}"
