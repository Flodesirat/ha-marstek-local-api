"""Tests for button.py — 100% line coverage."""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import _load_integration_module

# ---------------------------------------------------------------------------
# Module loading — ensure button.py uses our stubs, not real HA modules
# ---------------------------------------------------------------------------
sys.modules.pop("custom_components.marstek_local_api.button", None)

_api_mod = _load_integration_module("api")
_coordinator_mod = _load_integration_module("coordinator")
_button_mod = _load_integration_module("button")

# ---------------------------------------------------------------------------
# Public symbols
# ---------------------------------------------------------------------------
_mode_state_from_config = _button_mod._mode_state_from_config
async_setup_entry = _button_mod.async_setup_entry
MarstekModeButton = _button_mod.MarstekModeButton
MarstekAutoModeButton = _button_mod.MarstekAutoModeButton
MarstekAIModeButton = _button_mod.MarstekAIModeButton
MarstekManualModeButton = _button_mod.MarstekManualModeButton
MarstekMultiDeviceModeButton = _button_mod.MarstekMultiDeviceModeButton
MarstekMultiDeviceAutoModeButton = _button_mod.MarstekMultiDeviceAutoModeButton
MarstekMultiDeviceAIModeButton = _button_mod.MarstekMultiDeviceAIModeButton
MarstekMultiDeviceManualModeButton = _button_mod.MarstekMultiDeviceManualModeButton
DEFAULT_MANUAL_MODE_CFG = _button_mod.DEFAULT_MANUAL_MODE_CFG

MarstekMultiDeviceCoordinator = _coordinator_mod.MarstekMultiDeviceCoordinator

MODE_AUTO = _button_mod.MODE_AUTO
MODE_AI = _button_mod.MODE_AI
MODE_MANUAL = _button_mod.MODE_MANUAL
MAX_RETRIES = _button_mod.MAX_RETRIES
DOMAIN = _button_mod.DOMAIN
DATA_COORDINATOR = _button_mod.DATA_COORDINATOR

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_DEVICE_DATA = {
    "ble_mac": "aabbccddeeff",
    "wifi_mac": "112233445566",
    "device": "VenusA",
    "firmware": "147",
}


def _make_entry(**overrides):
    entry = MagicMock()
    entry.data = {**_DEVICE_DATA, **overrides}
    entry.entry_id = "test_entry"
    return entry


def _make_coord(data=None):
    """Plain MagicMock — NOT an instance of MarstekMultiDeviceCoordinator."""
    coord = MagicMock()
    coord.data = data if data is not None else {"battery": {}}
    coord.api = MagicMock()
    coord.api.set_es_mode = AsyncMock(return_value=True)
    coord.async_refresh = AsyncMock()
    coord.async_set_updated_data = MagicMock()
    return coord


def _make_multi_coord(macs=None):
    """MagicMock whose __class__ is MarstekMultiDeviceCoordinator → isinstance True."""
    macs = macs or ["aabbccddeeff"]
    coord = MagicMock()
    coord.__class__ = MarstekMultiDeviceCoordinator  # makes isinstance() return True
    coord.data = {"devices": {}}
    coord.async_refresh = AsyncMock()
    coord.async_set_updated_data = MagicMock()
    coord.get_device_macs = MagicMock(return_value=macs)
    coord.devices = [
        {"ble_mac": mac, "wifi_mac": None, "device": "VenusA", "firmware": "147"}
        for mac in macs
    ]
    coord.device_coordinators = {mac: _make_dev_coord() for mac in macs}
    coord.get_device_data = MagicMock(return_value={"battery": {}})
    return coord


def _make_dev_coord(data=None):
    """Device coordinator mock (plain MagicMock)."""
    coord = MagicMock()
    coord.data = data if data is not None else {"battery": {}}
    coord.api = MagicMock()
    coord.api.set_es_mode = AsyncMock(return_value=True)
    coord.async_refresh = AsyncMock()
    coord.async_set_updated_data = MagicMock()
    return coord


def _make_single_btn(mode=None, coord=None, data=None):
    """Create a MarstekModeButton bypassing __init__ (for behavioral tests)."""
    if coord is None:
        coord = _make_coord(data=data)
    btn = MarstekModeButton.__new__(MarstekModeButton)
    btn.coordinator = coord
    btn._mode = mode or MODE_AUTO
    return btn


def _make_multi_btn(mode=None, coord=None, dev_coord=None, mac="aabbccddeeff"):
    """Create a MarstekMultiDeviceModeButton bypassing __init__."""
    if coord is None:
        coord = _make_multi_coord(macs=[mac])
    if dev_coord is None:
        dev_coord = _make_dev_coord()
    btn = MarstekMultiDeviceModeButton.__new__(MarstekMultiDeviceModeButton)
    btn.coordinator = coord
    btn.device_coordinator = dev_coord
    btn.device_mac = mac
    btn._mode = mode or MODE_AUTO
    return btn


# ===========================================================================
# _mode_state_from_config
# ===========================================================================

class TestModeStateFromConfig:
    def test_auto_with_cfg(self):
        result = _mode_state_from_config(MODE_AUTO, {"auto_cfg": {"enable": 1}})
        assert result == {"mode": MODE_AUTO, "auto_cfg": {"enable": 1}}

    def test_auto_without_cfg(self):
        result = _mode_state_from_config(MODE_AUTO, {})
        assert result == {"mode": MODE_AUTO}

    def test_ai_with_cfg(self):
        result = _mode_state_from_config(MODE_AI, {"ai_cfg": {"enable": 1}})
        assert result == {"mode": MODE_AI, "ai_cfg": {"enable": 1}}

    def test_ai_without_cfg(self):
        result = _mode_state_from_config(MODE_AI, {})
        assert result == {"mode": MODE_AI}

    def test_manual_with_cfg(self):
        result = _mode_state_from_config(MODE_MANUAL, {"manual_cfg": {"power": 0}})
        assert result == {"mode": MODE_MANUAL, "manual_cfg": {"power": 0}}

    def test_manual_without_cfg(self):
        result = _mode_state_from_config(MODE_MANUAL, {})
        assert result == {"mode": MODE_MANUAL}

    def test_unknown_mode(self):
        result = _mode_state_from_config("Unknown", {"auto_cfg": {}, "ai_cfg": {}})
        assert result == {"mode": "Unknown"}


# ===========================================================================
# async_setup_entry
# ===========================================================================

class TestAsyncSetupEntry:
    async def test_single_device(self):
        coord = _make_coord()
        hass = MagicMock()
        entry = _make_entry()
        hass.data = {DOMAIN: {entry.entry_id: {DATA_COORDINATOR: coord}}}
        add_entities = MagicMock()

        await async_setup_entry(hass, entry, add_entities)

        add_entities.assert_called_once()
        entities = add_entities.call_args[0][0]
        assert len(entities) == 3
        assert all(isinstance(e, MarstekModeButton) for e in entities)

    async def test_multi_device(self):
        macs = ["aabbccddeeff", "112233445566"]
        coord = _make_multi_coord(macs=macs)
        hass = MagicMock()
        entry = _make_entry()
        hass.data = {DOMAIN: {entry.entry_id: {DATA_COORDINATOR: coord}}}
        add_entities = MagicMock()

        await async_setup_entry(hass, entry, add_entities)

        add_entities.assert_called_once()
        entities = add_entities.call_args[0][0]
        assert len(entities) == 6  # 3 buttons × 2 devices
        assert all(isinstance(e, MarstekMultiDeviceModeButton) for e in entities)


# ===========================================================================
# MarstekModeButton — __init__ via concrete subclasses
# ===========================================================================

class TestMarstekModeButtonInit:
    def test_auto_mode_button(self):
        btn = MarstekAutoModeButton(_make_coord(), _make_entry())
        assert btn._mode == MODE_AUTO
        assert btn._attr_name == "Auto mode"
        assert btn._attr_icon == "mdi:auto-mode"
        assert "aabbccddeeff" in btn._attr_unique_id

    def test_ai_mode_button(self):
        btn = MarstekAIModeButton(_make_coord(), _make_entry())
        assert btn._mode == MODE_AI
        assert btn._attr_name == "AI mode"
        assert btn._attr_icon == "mdi:brain"

    def test_manual_mode_button(self):
        btn = MarstekManualModeButton(_make_coord(), _make_entry())
        assert btn._mode == MODE_MANUAL
        assert btn._attr_name == "Manual mode"
        assert btn._attr_icon == "mdi:calendar-clock"

    def test_ble_mac_absent_falls_back_to_wifi_mac(self):
        btn = MarstekAutoModeButton(_make_coord(), _make_entry(ble_mac=None))
        assert "112233445566" in btn._attr_unique_id


# ===========================================================================
# MarstekModeButton.available
# ===========================================================================

class TestMarstekModeButtonAvailable:
    def test_available_with_data(self):
        assert _make_single_btn(data={"battery": {}}).available is True

    def test_not_available_data_none(self):
        btn = _make_single_btn()
        btn.coordinator.data = None
        assert btn.available is False

    def test_not_available_data_empty(self):
        assert _make_single_btn(data={}).available is False


# ===========================================================================
# MarstekModeButton._build_mode_config
# ===========================================================================

class TestMarstekModeButtonBuildModeConfig:
    def test_auto(self):
        config = _make_single_btn(mode=MODE_AUTO)._build_mode_config()
        assert config["mode"] == MODE_AUTO
        assert "auto_cfg" in config

    def test_ai(self):
        config = _make_single_btn(mode=MODE_AI)._build_mode_config()
        assert config["mode"] == MODE_AI
        assert "ai_cfg" in config

    def test_manual(self):
        config = _make_single_btn(mode=MODE_MANUAL)._build_mode_config()
        assert config["mode"] == MODE_MANUAL
        assert config["manual_cfg"] == dict(DEFAULT_MANUAL_MODE_CFG)

    def test_unknown_returns_empty(self):
        assert _make_single_btn(mode="Unknown")._build_mode_config() == {}


# ===========================================================================
# MarstekModeButton._update_cached_mode
# ===========================================================================

class TestMarstekModeButtonUpdateCachedMode:
    def test_no_existing_data(self):
        coord = _make_coord()
        coord.data = None
        btn = _make_single_btn(mode=MODE_AUTO, coord=coord)
        btn._update_cached_mode({"mode": MODE_AUTO, "auto_cfg": {"enable": 1}})
        new_data = coord.async_set_updated_data.call_args[0][0]
        assert new_data["mode"]["mode"] == MODE_AUTO

    def test_merges_with_existing_mode(self):
        coord = _make_coord()
        coord.data = {"mode": {"mode": MODE_MANUAL}, "battery": {}}
        btn = _make_single_btn(mode=MODE_AUTO, coord=coord)
        btn._update_cached_mode({"mode": MODE_AUTO, "auto_cfg": {"enable": 1}})
        new_data = coord.async_set_updated_data.call_args[0][0]
        assert new_data["mode"]["mode"] == MODE_AUTO
        assert "battery" in new_data


# ===========================================================================
# MarstekModeButton._refresh_mode_data
# ===========================================================================

class TestMarstekModeButtonRefreshModeData:
    async def test_succeeds(self):
        coord = _make_coord()
        btn = _make_single_btn(coord=coord)
        await btn._refresh_mode_data()
        coord.async_refresh.assert_called_once()

    async def test_exception_does_not_reraise(self):
        coord = _make_coord()
        coord.async_refresh = AsyncMock(side_effect=Exception("boom"))
        btn = _make_single_btn(coord=coord)
        await btn._refresh_mode_data()  # must not raise


# ===========================================================================
# MarstekModeButton.async_press
# ===========================================================================

class TestMarstekModeButtonAsyncPress:
    async def test_success_first_attempt(self):
        coord = _make_coord()
        btn = _make_single_btn(mode=MODE_AUTO, coord=coord)
        with patch("asyncio.sleep") as mock_sleep:
            await btn.async_press()
        coord.api.set_es_mode.assert_called_once()
        coord.async_set_updated_data.assert_called_once()
        coord.async_refresh.assert_called_once()
        mock_sleep.assert_not_called()

    async def test_rejected_then_succeeds(self):
        coord = _make_coord()
        coord.api.set_es_mode = AsyncMock(side_effect=[False, True])
        btn = _make_single_btn(mode=MODE_AI, coord=coord)
        with patch("asyncio.sleep", new=AsyncMock()):
            await btn.async_press()
        assert coord.api.set_es_mode.call_count == 2

    async def test_all_attempts_rejected_raises(self):
        coord = _make_coord()
        coord.api.set_es_mode = AsyncMock(return_value=False)
        btn = _make_single_btn(mode=MODE_AUTO, coord=coord)
        with patch("asyncio.sleep", new=AsyncMock()):
            with pytest.raises(Exception, match="device rejected"):
                await btn.async_press()
        assert coord.api.set_es_mode.call_count == MAX_RETRIES
        coord.async_refresh.assert_called_once()  # finally always runs

    async def test_exception_then_succeeds(self):
        coord = _make_coord()
        coord.api.set_es_mode = AsyncMock(side_effect=[RuntimeError("err"), True])
        btn = _make_single_btn(mode=MODE_MANUAL, coord=coord)
        with patch("asyncio.sleep", new=AsyncMock()):
            await btn.async_press()
        assert coord.api.set_es_mode.call_count == 2

    async def test_all_attempts_exception_raises_with_message(self):
        coord = _make_coord()
        coord.api.set_es_mode = AsyncMock(side_effect=RuntimeError("timeout"))
        btn = _make_single_btn(mode=MODE_AUTO, coord=coord)
        with patch("asyncio.sleep", new=AsyncMock()):
            with pytest.raises(Exception, match="timeout"):
                await btn.async_press()

    async def test_refresh_failure_does_not_reraise(self):
        coord = _make_coord()
        coord.async_refresh = AsyncMock(side_effect=Exception("refresh fail"))
        btn = _make_single_btn(mode=MODE_AUTO, coord=coord)
        with patch("asyncio.sleep"):
            await btn.async_press()  # must not raise despite refresh failure


# ===========================================================================
# MarstekMultiDeviceModeButton — __init__ via concrete subclasses
# ===========================================================================

class TestMarstekMultiDeviceModeButtonInit:
    _device_data = {"ble_mac": "aabbccddeeff", "device": "VenusA", "firmware": "147"}

    def test_auto_mode_button(self):
        btn = MarstekMultiDeviceAutoModeButton(
            _make_multi_coord(), _make_dev_coord(), "aabbccddeeff", self._device_data
        )
        assert btn._mode == MODE_AUTO
        assert btn._attr_name == "Auto mode"
        assert btn._attr_icon == "mdi:auto-mode"
        assert "aabbccddeeff" in btn._attr_unique_id

    def test_ai_mode_button(self):
        btn = MarstekMultiDeviceAIModeButton(
            _make_multi_coord(), _make_dev_coord(), "aabbccddeeff", self._device_data
        )
        assert btn._mode == MODE_AI
        assert btn._attr_icon == "mdi:brain"

    def test_manual_mode_button(self):
        btn = MarstekMultiDeviceManualModeButton(
            _make_multi_coord(), _make_dev_coord(), "aabbccddeeff", self._device_data
        )
        assert btn._mode == MODE_MANUAL
        assert btn._attr_icon == "mdi:calendar-clock"

    def test_device_name_includes_mac_suffix(self):
        btn = MarstekMultiDeviceAutoModeButton(
            _make_multi_coord(), _make_dev_coord(), "aabbccddeeff", self._device_data
        )
        # Last 4 hex chars of "aabbccddeeff" stripped of colons = "eeff"
        assert "eeff" in btn._attr_device_info["name"]

    def test_missing_device_fields_use_defaults(self):
        btn = MarstekMultiDeviceAutoModeButton(
            _make_multi_coord(), _make_dev_coord(), "aabb", {}
        )
        assert btn._attr_device_info["model"] == "Unknown"


# ===========================================================================
# MarstekMultiDeviceModeButton.available
# ===========================================================================

class TestMarstekMultiDeviceModeButtonAvailable:
    def test_available_with_data(self):
        coord = _make_multi_coord()
        coord.get_device_data = MagicMock(return_value={"battery": {}})
        assert _make_multi_btn(coord=coord).available is True

    def test_not_available_none(self):
        coord = _make_multi_coord()
        coord.get_device_data = MagicMock(return_value=None)
        assert _make_multi_btn(coord=coord).available is False

    def test_not_available_empty(self):
        coord = _make_multi_coord()
        coord.get_device_data = MagicMock(return_value={})
        assert _make_multi_btn(coord=coord).available is False


# ===========================================================================
# MarstekMultiDeviceModeButton._build_mode_config
# ===========================================================================

class TestMarstekMultiDeviceModeButtonBuildModeConfig:
    def test_auto(self):
        assert _make_multi_btn(mode=MODE_AUTO)._build_mode_config()["mode"] == MODE_AUTO

    def test_ai(self):
        assert _make_multi_btn(mode=MODE_AI)._build_mode_config()["mode"] == MODE_AI

    def test_manual(self):
        config = _make_multi_btn(mode=MODE_MANUAL)._build_mode_config()
        assert config["manual_cfg"] == dict(DEFAULT_MANUAL_MODE_CFG)

    def test_unknown_returns_empty(self):
        assert _make_multi_btn(mode="Unknown")._build_mode_config() == {}


# ===========================================================================
# MarstekMultiDeviceModeButton._update_device_cache / _update_cached_mode
# ===========================================================================

class TestMarstekMultiDeviceModeButtonUpdateCaches:
    def test_update_device_cache_no_existing_mode(self):
        dev_coord = _make_dev_coord()
        dev_coord.data = {}
        btn = _make_multi_btn(dev_coord=dev_coord)
        state = {"mode": MODE_AUTO}
        result = btn._update_device_cache(state)
        dev_coord.async_set_updated_data.assert_called_once()
        assert result["mode"] == state

    def test_update_device_cache_merges_existing_mode(self):
        dev_coord = _make_dev_coord()
        dev_coord.data = {"mode": {"mode": MODE_MANUAL}, "battery": {}}
        btn = _make_multi_btn(dev_coord=dev_coord)
        result = btn._update_device_cache({"mode": MODE_AUTO})
        assert result["mode"]["mode"] == MODE_AUTO

    def test_update_device_cache_none_data(self):
        dev_coord = _make_dev_coord()
        dev_coord.data = None
        btn = _make_multi_btn(dev_coord=dev_coord)
        result = btn._update_device_cache({"mode": MODE_AUTO})
        assert result["mode"]["mode"] == MODE_AUTO

    def test_update_cached_mode_no_system_data(self):
        coord = _make_multi_coord()
        coord.data = None
        dev_coord = _make_dev_coord()
        btn = _make_multi_btn(mode=MODE_AUTO, coord=coord, dev_coord=dev_coord)
        btn._update_cached_mode({"mode": MODE_AUTO, "auto_cfg": {"enable": 1}})
        coord.async_set_updated_data.assert_called_once()

    def test_update_cached_mode_no_devices_key(self):
        coord = _make_multi_coord()
        coord.data = {"aggregates": {}}  # "devices" key absent → uses {}
        dev_coord = _make_dev_coord()
        btn = _make_multi_btn(mode=MODE_AUTO, coord=coord, dev_coord=dev_coord)
        btn._update_cached_mode({"mode": MODE_AUTO, "auto_cfg": {"enable": 1}})
        new_system = coord.async_set_updated_data.call_args[0][0]
        assert "aabbccddeeff" in new_system["devices"]


# ===========================================================================
# MarstekMultiDeviceModeButton._refresh_mode_data
# ===========================================================================

class TestMarstekMultiDeviceModeButtonRefreshModeData:
    async def test_both_succeed(self):
        dev_coord = _make_dev_coord()
        coord = _make_multi_coord()
        btn = _make_multi_btn(coord=coord, dev_coord=dev_coord)
        await btn._refresh_mode_data()
        dev_coord.async_refresh.assert_called_once()
        coord.async_refresh.assert_called_once()

    async def test_device_refresh_fails_aggregate_still_called(self):
        dev_coord = _make_dev_coord()
        dev_coord.async_refresh = AsyncMock(side_effect=Exception("dev fail"))
        coord = _make_multi_coord()
        btn = _make_multi_btn(coord=coord, dev_coord=dev_coord)
        await btn._refresh_mode_data()  # must not raise
        coord.async_refresh.assert_called_once()

    async def test_aggregate_refresh_fails_no_reraise(self):
        dev_coord = _make_dev_coord()
        coord = _make_multi_coord()
        coord.async_refresh = AsyncMock(side_effect=Exception("agg fail"))
        btn = _make_multi_btn(coord=coord, dev_coord=dev_coord)
        await btn._refresh_mode_data()  # must not raise
        dev_coord.async_refresh.assert_called_once()

    async def test_both_refresh_fail_no_reraise(self):
        dev_coord = _make_dev_coord()
        dev_coord.async_refresh = AsyncMock(side_effect=Exception("dev"))
        coord = _make_multi_coord()
        coord.async_refresh = AsyncMock(side_effect=Exception("agg"))
        btn = _make_multi_btn(coord=coord, dev_coord=dev_coord)
        await btn._refresh_mode_data()  # must not raise


# ===========================================================================
# MarstekMultiDeviceModeButton.async_press
# ===========================================================================

class TestMarstekMultiDeviceModeButtonAsyncPress:
    async def test_success_first_attempt(self):
        dev_coord = _make_dev_coord()
        btn = _make_multi_btn(mode=MODE_AUTO, dev_coord=dev_coord)
        with patch("asyncio.sleep"):
            await btn.async_press()
        dev_coord.api.set_es_mode.assert_called_once()
        dev_coord.async_set_updated_data.assert_called_once()

    async def test_rejected_then_succeeds(self):
        dev_coord = _make_dev_coord()
        dev_coord.api.set_es_mode = AsyncMock(side_effect=[False, True])
        btn = _make_multi_btn(mode=MODE_AI, dev_coord=dev_coord)
        with patch("asyncio.sleep", new=AsyncMock()):
            await btn.async_press()
        assert dev_coord.api.set_es_mode.call_count == 2

    async def test_all_attempts_rejected_raises(self):
        dev_coord = _make_dev_coord()
        dev_coord.api.set_es_mode = AsyncMock(return_value=False)
        btn = _make_multi_btn(mode=MODE_AUTO, dev_coord=dev_coord)
        with patch("asyncio.sleep", new=AsyncMock()):
            with pytest.raises(Exception, match="device rejected"):
                await btn.async_press()
        assert dev_coord.api.set_es_mode.call_count == MAX_RETRIES

    async def test_exception_then_succeeds(self):
        dev_coord = _make_dev_coord()
        dev_coord.api.set_es_mode = AsyncMock(side_effect=[RuntimeError("err"), True])
        btn = _make_multi_btn(mode=MODE_MANUAL, dev_coord=dev_coord)
        with patch("asyncio.sleep", new=AsyncMock()):
            await btn.async_press()
        assert dev_coord.api.set_es_mode.call_count == 2

    async def test_all_attempts_exception_raises_with_message(self):
        dev_coord = _make_dev_coord()
        dev_coord.api.set_es_mode = AsyncMock(side_effect=RuntimeError("conn err"))
        btn = _make_multi_btn(mode=MODE_AUTO, dev_coord=dev_coord)
        with patch("asyncio.sleep", new=AsyncMock()):
            with pytest.raises(Exception, match="conn err"):
                await btn.async_press()

    async def test_refresh_failure_does_not_reraise(self):
        dev_coord = _make_dev_coord()
        dev_coord.async_refresh = AsyncMock(side_effect=Exception("fail"))
        coord = _make_multi_coord()
        coord.async_refresh = AsyncMock(side_effect=Exception("fail"))
        btn = _make_multi_btn(dev_coord=dev_coord, coord=coord)
        with patch("asyncio.sleep"):
            await btn.async_press()  # must not raise
