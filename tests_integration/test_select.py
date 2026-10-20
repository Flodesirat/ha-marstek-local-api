"""Integration tests for the operating mode select."""
from __future__ import annotations

import pytest
from homeassistant.core import State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import mock_restore_cache

from tests_integration.conftest import DOMAIN, _setup_entry

# MAC from fixture: ble_mac = bc2a33600dca
_MAC = "bc2a33600dca"
_ENTITY_ID = "select.marstek_venus_a_operating_mode"


def _fake_battery_mode(mock_api, mode: str = "Auto") -> None:
    """Make ES.GetMode report whatever ES.SetMode last applied."""
    current = {"mode": mode}

    async def set_es_mode(config, **kwargs):
        current["mode"] = config["mode"]
        return True

    async def get_es_mode(**kwargs):
        return dict(current)

    mock_api.set_es_mode.side_effect = set_es_mode
    mock_api.get_es_mode.side_effect = get_es_mode


@pytest.mark.asyncio
async def test_select_entity_created(hass, config_entry, mock_api):
    """The select is registered and shows the mode the battery reports."""
    _fake_battery_mode(mock_api, "Manual")
    await _setup_entry(hass, config_entry, mock_api)

    registry = er.async_get(hass)
    assert registry.async_get_entity_id("select", DOMAIN, f"{_MAC}_operating_mode") == _ENTITY_ID

    state = hass.states.get(_ENTITY_ID)
    assert state.state == "Programmed"
    assert state.attributes["options"] == ["Auto", "AI", "Programmed", "UPS", "Drive"]
    mock_api.set_es_mode.assert_not_called()


@pytest.mark.asyncio
async def test_select_option_sends_mode(hass, config_entry, mock_api):
    """Selecting UPS sends its config once and the select shows UPS."""
    _fake_battery_mode(mock_api, "Auto")
    await _setup_entry(hass, config_entry, mock_api)

    await hass.services.async_call(
        "select", "select_option", {"entity_id": _ENTITY_ID, "option": "UPS"}, blocking=True
    )
    await hass.async_block_till_done()

    mock_api.set_es_mode.assert_awaited_once_with({"mode": "UPS", "ups_cfg": {"enable": 1}})
    assert hass.states.get(_ENTITY_ID).state == "UPS"
    assert hass.states.get("sensor.marstek_venus_a_operating_mode").state == "UPS"


@pytest.mark.asyncio
async def test_select_restored_and_reapplied(hass, config_entry, mock_api):
    """After a restart the last option is re-applied once."""
    mock_restore_cache(hass, [State(_ENTITY_ID, "Programmed")])
    _fake_battery_mode(mock_api, "Auto")

    await _setup_entry(hass, config_entry, mock_api)
    await hass.async_block_till_done(wait_background_tasks=True)

    mock_api.set_es_mode.assert_awaited_once()
    assert mock_api.set_es_mode.await_args.args[0]["mode"] == "Manual"
    assert hass.states.get(_ENTITY_ID).state == "Programmed"


@pytest.mark.asyncio
async def test_select_unknown_state_not_reapplied(hass, config_entry, mock_api):
    """An unknown restored state sends nothing."""
    mock_restore_cache(hass, [State(_ENTITY_ID, "unknown")])
    _fake_battery_mode(mock_api, "Auto")

    await _setup_entry(hass, config_entry, mock_api)
    await hass.async_block_till_done(wait_background_tasks=True)

    mock_api.set_es_mode.assert_not_called()
