"""Integration tests for the Drive mode (select option, number entity, controller)."""
from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import State
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed, mock_restore_cache

from tests_integration.conftest import DOMAIN, _setup_entry

# MAC from fixture: ble_mac = bc2a33600dca
_MAC = "bc2a33600dca"
_SELECT = "select.marstek_venus_a_operating_mode"
_NUMBER = "number.marstek_venus_a_drive_grid_power"


def _fake_battery(mock_api, mode: str = "Auto") -> dict:
    """Make ES.GetMode report whatever ES.SetMode last applied."""
    current = {"mode": mode}

    async def set_es_mode(config, **kwargs):
        current["mode"] = config["mode"]
        return True

    async def get_es_mode(**kwargs):
        return dict(current)

    mock_api.set_es_mode.side_effect = set_es_mode
    mock_api.get_es_mode.side_effect = get_es_mode
    return current


def _drive_calls(mock_api) -> list[int]:
    """Setpoints sent as Drive commands (Passive, high priority)."""
    powers = []
    for call in mock_api.set_es_mode.await_args_list:
        config = call.args[0]
        if config["mode"] == "Passive":
            assert config["passive_cfg"]["cd_time"] == 300
            assert call.kwargs == {"priority": True}
            powers.append(config["passive_cfg"]["power"])
    return powers


def _coordinator(hass, entry):
    return hass.data[DOMAIN][entry.entry_id]["coordinator"]


async def _poll_mode(hass, entry) -> None:
    """Run a coordinator refresh that includes ES.GetMode (medium tier)."""
    coordinator = _coordinator(hass, entry)
    coordinator.update_count = 1
    await coordinator.async_refresh()
    await hass.async_block_till_done()


async def _select(hass, option: str) -> None:
    await hass.services.async_call(
        "select", "select_option", {"entity_id": _SELECT, "option": option}, blocking=True
    )
    await hass.async_block_till_done()


async def _set_power(hass, value: float) -> None:
    await hass.services.async_call(
        "number", "set_value", {"entity_id": _NUMBER, "value": value}, blocking=True
    )
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_number_entity_created(hass, config_entry, mock_api):
    """The setpoint entity is registered with the Venus A range and 0 W."""
    _fake_battery(mock_api)
    await _setup_entry(hass, config_entry, mock_api)

    registry = er.async_get(hass)
    assert registry.async_get_entity_id("number", DOMAIN, f"{_MAC}_drive_grid_power") == _NUMBER
    state = hass.states.get(_NUMBER)
    assert float(state.state) == 0
    assert state.attributes["min"] == -1500
    assert state.attributes["max"] == 1500
    assert state.attributes["unit_of_measurement"] == "W"
    assert "Drive" in hass.states.get(_SELECT).attributes["options"]


@pytest.mark.asyncio
async def test_power_limits_option_overrides_model(hass, config_entry, mock_api):
    """Per-battery limits from the options set the number range."""
    hass.config_entries.async_update_entry(
        config_entry, options={"drive_power_limits": {_MAC: {"max_charge": 1000, "max_discharge": 600}}}
    )
    _fake_battery(mock_api)
    await _setup_entry(hass, config_entry, mock_api)

    state = hass.states.get(_NUMBER)
    assert (state.attributes["min"], state.attributes["max"]) == (-1000, 600)


@pytest.mark.asyncio
async def test_drive_mode_sending_rules(hass, config_entry, mock_api):
    """Entering Drive resets to 0 W; 30 W step; 60 s refresh; Passive kept up."""
    battery = _fake_battery(mock_api)
    await _setup_entry(hass, config_entry, mock_api)
    await _set_power(hass, 400)  # Ignored outside Drive, then reset on entry

    await _select(hass, "Drive")
    assert _drive_calls(mock_api) == [0]
    assert hass.states.get(_SELECT).state == "Drive"
    assert float(hass.states.get(_NUMBER).state) == 0

    # 30 W step
    await _set_power(hass, 20)
    assert _drive_calls(mock_api) == [0]
    await _set_power(hass, -30)
    assert _drive_calls(mock_api) == [0, -30]
    await _set_power(hass, -10)
    assert _drive_calls(mock_api) == [0, -30]

    # Refresh: the exact setpoint, 60 s after the last accepted command
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=59))
    await hass.async_block_till_done()
    assert _drive_calls(mock_api) == [0, -30]
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert _drive_calls(mock_api) == [0, -30, -10]

    # The battery drops back to Manual on its own: Passive is re-imposed
    battery["mode"] = "Manual"
    await _poll_mode(hass, config_entry)
    assert _drive_calls(mock_api) == [0, -30, -10, -10]
    assert battery["mode"] == "Passive"
    assert hass.states.get(_SELECT).state == "Drive"

    await _select(hass, "Auto")  # Leave Drive before the test ends


@pytest.mark.asyncio
async def test_other_option_stops_drive(hass, config_entry, mock_api):
    """Selecting another mode stops the controller: no refresh, no re-imposing."""
    battery = _fake_battery(mock_api)
    await _setup_entry(hass, config_entry, mock_api)
    await _select(hass, "Drive")

    await _select(hass, "Auto")
    assert battery["mode"] == "Auto"
    assert hass.states.get(_SELECT).state == "Auto"

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=120))
    await hass.async_block_till_done()
    await _poll_mode(hass, config_entry)
    assert _drive_calls(mock_api) == [0]
    assert battery["mode"] == "Auto"


@pytest.mark.asyncio
async def test_polled_passive_is_treated_as_drive(hass, config_entry, mock_api):
    """A battery found in Passive is driven at the current setpoint (0 W)."""
    _fake_battery(mock_api, "Passive")
    await _setup_entry(hass, config_entry, mock_api)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.states.get(_SELECT).state == "Drive"
    assert _drive_calls(mock_api) == [0]

    await _select(hass, "Auto")


@pytest.mark.asyncio
async def test_restart_in_drive_reapplies_zero(hass, config_entry, mock_api):
    """After a restart in Drive, Drive is re-applied at 0 W."""
    mock_restore_cache(hass, [State(_SELECT, "Drive"), State(_NUMBER, "800")])
    _fake_battery(mock_api, "Auto")

    await _setup_entry(hass, config_entry, mock_api)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert _drive_calls(mock_api) == [0]
    assert hass.states.get(_SELECT).state == "Drive"
    assert float(hass.states.get(_NUMBER).state) == 0

    await _select(hass, "Auto")


@pytest.mark.asyncio
async def test_unload_stops_drive(hass, config_entry, mock_api):
    """Unloading the entry stops the controller and its refresh."""
    _fake_battery(mock_api)
    await _setup_entry(hass, config_entry, mock_api)
    await _select(hass, "Drive")
    controller = _coordinator(hass, config_entry).drive_controller

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert not controller.active
    assert controller._refresh_handle is None
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=120))
    await hass.async_block_till_done()
    assert _drive_calls(mock_api) == [0]
