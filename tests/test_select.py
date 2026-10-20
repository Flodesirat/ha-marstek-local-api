"""Tests for select.py (operating mode select) and modes.py."""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import _load_integration_module

sys.modules.pop("custom_components.marstek_local_api.select", None)

_const_mod = _load_integration_module("const")
_coordinator_mod = _load_integration_module("coordinator")
_modes_mod = _load_integration_module("modes")
_select_mod = _load_integration_module("select")

MarstekOperatingModeSelect = _select_mod.MarstekOperatingModeSelect
async_setup_entry = _select_mod.async_setup_entry
OPTION_TO_MODE = _select_mod.OPTION_TO_MODE
MarstekMultiDeviceCoordinator = _coordinator_mod.MarstekMultiDeviceCoordinator
build_mode_config = _modes_mod.build_mode_config
mode_state_from_config = _modes_mod.mode_state_from_config

DOMAIN = _const_mod.DOMAIN
DATA_COORDINATOR = _const_mod.DATA_COORDINATOR
MAX_RETRIES = _const_mod.MAX_RETRIES

_MAC = "aabbccddeeff"

# Configs required by the spec (issue #16)
EXPECTED_CONFIGS = {
    "Auto": {"mode": "Auto", "auto_cfg": {"enable": 1}},
    "AI": {"mode": "AI", "ai_cfg": {"enable": 1}},
    "Programmed": {
        "mode": "Manual",
        "manual_cfg": {
            "time_num": 9, "start_time": "00:00", "end_time": "00:00",
            "week_set": 0, "power": 0, "enable": 0,
        },
    },
    "UPS": {"mode": "UPS", "ups_cfg": {"enable": 1}},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dev_coord(mode="Auto"):
    coord = MagicMock()
    coord.data = {"battery": {}, "mode": {"mode": mode}} if mode else {"battery": {}}
    coord.api = MagicMock()
    coord.api.set_es_mode = AsyncMock(return_value=True)
    coord.async_refresh = AsyncMock()
    coord.async_set_updated_data = MagicMock(side_effect=lambda data: setattr(coord, "data", data))
    return coord


def _make_entry(**data):
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {"ble_mac": _MAC, "wifi_mac": "112233445566", "device": "VenusA", "firmware": 147, **data}
    return entry


def _make_single(mode="Auto"):
    coord = _make_dev_coord(mode)
    entity = MarstekOperatingModeSelect(_make_entry(), coord, coord, _MAC, {})
    entity.hass = MagicMock()
    return entity, coord


def _make_multi(mode="Auto"):
    dev_coord = _make_dev_coord(mode)
    system = MagicMock()
    system.data = {"devices": {_MAC: dev_coord.data}, "aggregates": {}}
    system.async_refresh = AsyncMock()
    system.async_set_updated_data = MagicMock()
    entity = MarstekOperatingModeSelect(_make_entry(), system, dev_coord, _MAC, {})
    entity.hass = MagicMock()
    return entity, dev_coord, system


# ===========================================================================
# modes.py
# ===========================================================================

class TestModes:
    @pytest.mark.parametrize("option", list(EXPECTED_CONFIGS))
    def test_build_mode_config(self, option):
        assert build_mode_config(OPTION_TO_MODE[option]) == EXPECTED_CONFIGS[option]

    def test_build_mode_config_unknown(self):
        assert build_mode_config("Passive") == {}

    def test_manual_config_is_a_copy(self):
        build_mode_config("Manual")["manual_cfg"]["enable"] = 1
        assert build_mode_config("Manual")["manual_cfg"]["enable"] == 0

    def test_mode_state_from_config_ups(self):
        assert mode_state_from_config("UPS", {"ups_cfg": {"enable": 1}}) == {
            "mode": "UPS", "ups_cfg": {"enable": 1},
        }

    def test_ups_is_a_known_operating_mode(self):
        assert "UPS" in _const_mod.OPERATING_MODES
        assert "select" in _const_mod.PLATFORMS


# ===========================================================================
# async_setup_entry
# ===========================================================================

class TestAsyncSetupEntry:
    async def test_single_device(self):
        coord = _make_dev_coord()
        entry = _make_entry()
        hass = MagicMock()
        hass.data = {DOMAIN: {entry.entry_id: {DATA_COORDINATOR: coord}}}
        add_entities = MagicMock()

        await async_setup_entry(hass, entry, add_entities)

        (entities,) = add_entities.call_args[0]
        assert len(entities) == 1
        entity = entities[0]
        assert entity._attr_unique_id == f"{_MAC}_operating_mode"
        assert entity.device_coordinator is coord
        assert entity._attr_device_info["name"] == "Marstek VenusA"

    async def test_multi_device(self):
        macs = ["aabbccddeeff", "001122334455"]
        coord = MagicMock()
        coord.__class__ = MarstekMultiDeviceCoordinator
        coord.get_device_macs = MagicMock(return_value=macs)
        coord.devices = [{"ble_mac": m, "device": "VenusE", "firmware": 139} for m in macs]
        coord.device_coordinators = {m: _make_dev_coord() for m in macs}
        entry = _make_entry()
        hass = MagicMock()
        hass.data = {DOMAIN: {entry.entry_id: {DATA_COORDINATOR: coord}}}
        add_entities = MagicMock()

        await async_setup_entry(hass, entry, add_entities)

        (entities,) = add_entities.call_args[0]
        assert [e._attr_unique_id for e in entities] == [f"{m}_operating_mode" for m in macs]
        assert entities[1].device_coordinator is coord.device_coordinators[macs[1]]
        assert entities[1]._attr_device_info["name"] == "Marstek VenusE 4455"


# ===========================================================================
# State
# ===========================================================================

class TestState:
    def test_options(self):
        entity, _ = _make_single()
        assert entity.options == ["Auto", "AI", "Programmed", "UPS"]

    @pytest.mark.parametrize(
        ("reported", "expected"),
        [("Auto", "Auto"), ("AI", "AI"), ("Manual", "Programmed"), ("UPS", "UPS"), ("Passive", None)],
    )
    def test_current_option_follows_reported_mode(self, reported, expected):
        entity, _ = _make_single(mode=reported)
        assert entity.current_option == expected

    def test_current_option_without_mode_data(self):
        entity, _ = _make_single(mode=None)
        assert entity.current_option is None

    def test_available(self):
        entity, coord = _make_single()
        assert entity.available
        coord.data = None
        assert not entity.available


# ===========================================================================
# async_select_option
# ===========================================================================

class TestSelectOption:
    @pytest.mark.parametrize("option", list(EXPECTED_CONFIGS))
    async def test_sends_config_once_and_updates_cache(self, option):
        entity, coord = _make_single(mode="Passive")

        await entity.async_select_option(option)

        coord.api.set_es_mode.assert_awaited_once_with(EXPECTED_CONFIGS[option])
        assert entity.current_option == option
        coord.async_refresh.assert_awaited_once()

    async def test_multi_device_updates_both_caches(self):
        entity, dev_coord, system = _make_multi(mode="Auto")

        await entity.async_select_option("UPS")

        assert dev_coord.data["mode"] == {"mode": "UPS", "ups_cfg": {"enable": 1}}
        system_data = system.async_set_updated_data.call_args[0][0]
        assert system_data["devices"][_MAC]["mode"]["mode"] == "UPS"
        assert "aggregates" in system_data
        dev_coord.async_refresh.assert_awaited_once()
        system.async_refresh.assert_awaited_once()

    async def test_retries_then_succeeds(self):
        entity, coord = _make_single()
        coord.api.set_es_mode = AsyncMock(side_effect=[False, Exception("timeout"), True])

        with patch("asyncio.sleep", new=AsyncMock()) as mock_sleep:
            await entity.async_select_option("AI")

        assert coord.api.set_es_mode.await_count == 3
        assert mock_sleep.await_count == 2
        assert entity.current_option == "AI"

    async def test_raises_after_max_retries(self):
        entity, coord = _make_single()
        coord.api.set_es_mode = AsyncMock(return_value=False)

        with patch("asyncio.sleep", new=AsyncMock()):
            with pytest.raises(Exception, match="device rejected"):
                await entity.async_select_option("UPS")

        assert coord.api.set_es_mode.await_count == MAX_RETRIES
        assert entity.current_option == "Auto"
        coord.async_refresh.assert_awaited_once()

    async def test_unknown_option(self):
        entity, coord = _make_single()
        with pytest.raises(Exception, match="Unknown operating mode"):
            await entity.async_select_option("Passive")
        coord.api.set_es_mode.assert_not_called()

    async def test_refresh_failure_is_logged_only(self):
        entity, coord = _make_single()
        coord.async_refresh = AsyncMock(side_effect=Exception("boom"))

        await entity.async_select_option("AI")

        assert entity.current_option == "AI"


# ===========================================================================
# Restore on restart
# ===========================================================================

class TestRestore:
    async def test_reapplies_last_option_once(self):
        entity, coord = _make_single(mode="Auto")
        entity.async_get_last_state = AsyncMock(return_value=SimpleNamespace(state="Programmed"))

        await entity.async_added_to_hass()

        entity._entry.async_create_background_task.assert_called_once()
        _hass, coro, _name = entity._entry.async_create_background_task.call_args[0]
        await coro
        coord.api.set_es_mode.assert_awaited_once_with(EXPECTED_CONFIGS["Programmed"])
        assert entity.current_option == "Programmed"

    @pytest.mark.parametrize("last_state", [None, SimpleNamespace(state="unknown"), SimpleNamespace(state="unavailable")])
    async def test_nothing_to_restore(self, last_state):
        entity, coord = _make_single()
        entity.async_get_last_state = AsyncMock(return_value=last_state)

        await entity.async_added_to_hass()

        entity._entry.async_create_background_task.assert_not_called()
        coord.api.set_es_mode.assert_not_called()

    async def test_reapply_failure_is_logged_only(self):
        entity, coord = _make_single()
        coord.api.set_es_mode = AsyncMock(return_value=False)

        with patch("asyncio.sleep", new=AsyncMock()):
            await entity._async_reapply("UPS")

        assert coord.api.set_es_mode.await_count == MAX_RETRIES
