"""Tests for MarstekDataUpdateCoordinator and MarstekMultiDeviceCoordinator."""
from __future__ import annotations

import sys
import time
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from conftest import _load_integration_module

# ---------------------------------------------------------------------------
# Load modules
# ---------------------------------------------------------------------------
_coordinator_mod = _load_integration_module("coordinator")
MarstekDataUpdateCoordinator = _coordinator_mod.MarstekDataUpdateCoordinator
MarstekMultiDeviceCoordinator = _coordinator_mod.MarstekMultiDeviceCoordinator

_api_mod = _load_integration_module("api")
MarstekAPIError = _api_mod.MarstekAPIError

_compat_mod = _load_integration_module("compatibility")
CompatibilityMatrix = _compat_mod.CompatibilityMatrix

_const_mod = _load_integration_module("const")
DEVICE_MODEL_VENUS_A = _const_mod.DEVICE_MODEL_VENUS_A
DEVICE_MODEL_VENUS_D = _const_mod.DEVICE_MODEL_VENUS_D
DEVICE_MODEL_VENUS_C = _const_mod.DEVICE_MODEL_VENUS_C
DOD_DEFAULT = _const_mod.DOD_DEFAULT
METHOD_ES_STATUS = _const_mod.METHOD_ES_STATUS
METHOD_BATTERY_STATUS = _const_mod.METHOD_BATTERY_STATUS

from homeassistant.helpers.update_coordinator import UpdateFailed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_api():
    api = MagicMock()
    api.get_device_info = AsyncMock(return_value=None)
    api.get_es_status = AsyncMock(return_value=None)
    api.get_em_status = AsyncMock(return_value=None)
    api.get_battery_status = AsyncMock(return_value=None)
    api.get_pv_status = AsyncMock(return_value=None)
    api.get_wifi_status = AsyncMock(return_value=None)
    api.get_ble_status = AsyncMock(return_value=None)
    api.get_es_mode = AsyncMock(return_value=None)
    api.get_command_stats = MagicMock(return_value=None)
    api.connect = AsyncMock()
    return api


def _make_coord(**overrides):
    """Create a MarstekDataUpdateCoordinator without calling __init__."""
    coord = MarstekDataUpdateCoordinator.__new__(MarstekDataUpdateCoordinator)
    coord.data = None
    coord.api = _make_api()
    coord.update_count = 1
    coord.update_interval = timedelta(seconds=10)
    coord.device_model = DEVICE_MODEL_VENUS_A
    coord.firmware_version = 147
    coord.category_last_updated = {}
    coord.last_message_timestamp = None
    coord._last_update_start = None
    coord.dod_percent = DOD_DEFAULT
    coord.stale_data_threshold = 300
    coord.static_categories = {"device", "wifi", "ble", "mode", "_diagnostic", "aggregates"}
    coord.staleness_threshold = 10
    coord._config_entry = None
    coord._device_mac = None
    coord.hass = MagicMock()
    coord.poll_jitter = 0.0
    for k, v in overrides.items():
        setattr(coord, k, v)
    # Build compatibility after overrides so device_model/firmware_version are correct
    if "compatibility" not in overrides:
        coord.compatibility = CompatibilityMatrix(
            device_model=coord.device_model, firmware_version=coord.firmware_version
        )
    return coord


# ---------------------------------------------------------------------------
# MarstekDataUpdateCoordinator.__init__
# ---------------------------------------------------------------------------

class TestMarstekDataUpdateCoordinatorInit:
    def test_basic_init(self):
        hass = MagicMock()
        api = _make_api()
        coord = MarstekDataUpdateCoordinator(
            hass, api,
            device_name="Test Device",
            firmware_version=147,
            device_model=DEVICE_MODEL_VENUS_A,
            scan_interval=10,
        )
        assert coord.firmware_version == 147
        assert coord.device_model == DEVICE_MODEL_VENUS_A
        assert coord.dod_percent == DOD_DEFAULT
        assert coord.update_count == 1
        assert coord.last_message_timestamp is None
        assert coord._last_update_start is None
        assert coord._config_entry is None
        assert coord._device_mac is None
        assert coord.update_interval == timedelta(seconds=10)

    def test_custom_params(self):
        hass = MagicMock()
        api = _make_api()
        entry = MagicMock()
        coord = MarstekDataUpdateCoordinator(
            hass, api,
            device_name="Test Device",
            firmware_version=200,
            device_model=DEVICE_MODEL_VENUS_D,
            scan_interval=30,
            config_entry=entry,
            device_mac="aa:bb:cc:dd:ee:ff",
            command_timeout=5,
            command_max_attempts=5,
            stale_data_threshold=600,
            dod_percent=70,
        )
        assert coord.firmware_version == 200
        assert coord.device_model == DEVICE_MODEL_VENUS_D
        assert coord.dod_percent == 70
        assert coord.stale_data_threshold == 600
        assert coord._config_entry is entry
        assert coord._device_mac == "aa:bb:cc:dd:ee:ff"
        assert coord.update_interval == timedelta(seconds=30)
        assert coord.command_timeout == 5
        assert coord.command_max_attempts == 5


# ---------------------------------------------------------------------------
# MarstekMultiDeviceCoordinator.__init__
# ---------------------------------------------------------------------------

class TestMarstekMultiDeviceCoordinatorInit:
    def test_basic_init(self):
        hass = MagicMock()
        devices = [{"host": "192.168.1.1", "port": 30000, "ble_mac": "aabbccddeeff", "device": "VenusA", "firmware": 147}]
        coord = MarstekMultiDeviceCoordinator(hass, devices)
        assert coord.devices is devices
        assert coord.device_coordinators == {}
        assert coord.update_count == 1
        assert coord.dod_percent == DOD_DEFAULT

    def test_custom_params(self):
        hass = MagicMock()
        entry = MagicMock()
        coord = MarstekMultiDeviceCoordinator(
            hass, [],
            scan_interval=20,
            config_entry=entry,
            dod_percent=60,
            stale_data_threshold=120,
        )
        assert coord.dod_percent == 60
        assert coord._config_entry is entry
        assert coord.update_interval == timedelta(seconds=20)
        assert coord.stale_data_threshold == 120


# ---------------------------------------------------------------------------
# MarstekMultiDeviceCoordinator.async_setup
# ---------------------------------------------------------------------------

class TestMarstekMultiDeviceCoordinatorAsyncSetup:
    async def test_successful_setup_ble_mac(self):
        hass = MagicMock()
        devices = [{
            "host": "192.168.1.1",
            "port": 30000,
            "ble_mac": "aabbccddeeff",
            "device": "VenusA",
            "firmware": 147,
        }]
        coord = MarstekMultiDeviceCoordinator(hass, devices)
        mock_api = _make_api()

        with patch.object(_coordinator_mod, "MarstekUDPClient", return_value=mock_api):
            await coord.async_setup()

        assert "aabbccddeeff" in coord.device_coordinators

    async def test_successful_setup_wifi_mac(self):
        hass = MagicMock()
        devices = [{
            "host": "192.168.1.2",
            "port": 30000,
            "wifi_mac": "112233445566",
            "device": "VenusC",
            "firmware": 200,
        }]
        coord = MarstekMultiDeviceCoordinator(hass, devices)
        mock_api = _make_api()

        with patch.object(_coordinator_mod, "MarstekUDPClient", return_value=mock_api):
            await coord.async_setup()

        assert "112233445566" in coord.device_coordinators

    async def test_connect_failure_skips_device(self):
        hass = MagicMock()
        devices = [{
            "host": "192.168.1.1",
            "port": 30000,
            "ble_mac": "aabbccddeeff",
            "device": "VenusA",
            "firmware": 147,
        }]
        coord = MarstekMultiDeviceCoordinator(hass, devices)
        mock_api = _make_api()
        mock_api.connect = AsyncMock(side_effect=Exception("Connection refused"))

        with patch.object(_coordinator_mod, "MarstekUDPClient", return_value=mock_api):
            await coord.async_setup()

        assert "aabbccddeeff" not in coord.device_coordinators


# ---------------------------------------------------------------------------
# MarstekMultiDeviceCoordinator.get_device_data
# ---------------------------------------------------------------------------

class TestGetDeviceMacs:
    def test_returns_list_of_macs(self):
        coord = MarstekMultiDeviceCoordinator.__new__(MarstekMultiDeviceCoordinator)
        coord.device_coordinators = {"aabbcc": MagicMock(), "112233": MagicMock()}
        macs = coord.get_device_macs()
        assert sorted(macs) == ["112233", "aabbcc"]

    def test_empty(self):
        coord = MarstekMultiDeviceCoordinator.__new__(MarstekMultiDeviceCoordinator)
        coord.device_coordinators = {}
        assert coord.get_device_macs() == []


class TestGetDeviceData:
    def test_known_mac_with_data(self):
        coord = MarstekMultiDeviceCoordinator.__new__(MarstekMultiDeviceCoordinator)
        coord.device_coordinators = {}
        device_coord = MagicMock()
        device_coord.data = {"battery": {"soc": 50}}
        coord.device_coordinators["aabbcc"] = device_coord
        assert coord.get_device_data("aabbcc") == {"battery": {"soc": 50}}

    def test_known_mac_with_none_data(self):
        coord = MarstekMultiDeviceCoordinator.__new__(MarstekMultiDeviceCoordinator)
        coord.device_coordinators = {}
        device_coord = MagicMock()
        device_coord.data = None
        coord.device_coordinators["aabbcc"] = device_coord
        assert coord.get_device_data("aabbcc") == {}

    def test_unknown_mac(self):
        coord = MarstekMultiDeviceCoordinator.__new__(MarstekMultiDeviceCoordinator)
        coord.device_coordinators = {}
        assert coord.get_device_data("unknown") == {}


# ---------------------------------------------------------------------------
# MarstekMultiDeviceCoordinator._async_update_data
# ---------------------------------------------------------------------------

class TestMultiDeviceAsyncUpdateData:
    async def test_basic_update(self):
        coord = MarstekMultiDeviceCoordinator.__new__(MarstekMultiDeviceCoordinator)
        coord.dod_percent = 80
        coord.update_interval = timedelta(seconds=10)
        device_coord = _make_coord()
        coord.device_coordinators = {"aabbcc": device_coord}

        with patch("custom_components.marstek_local_api.coordinator.asyncio.sleep", new=AsyncMock()):
            data = await coord._async_update_data()

        assert "devices" in data
        assert "aggregates" in data
        assert data["_config"]["dod_percent"] == 80

    async def test_device_update_exception_returns_old_data(self):
        coord = MarstekMultiDeviceCoordinator.__new__(MarstekMultiDeviceCoordinator)
        coord.dod_percent = 80
        coord.update_interval = timedelta(seconds=10)
        device_coord = _make_coord(data={"old": "data"})

        async def _raise(*a, **kw):
            raise Exception("Device error")
        device_coord._async_update_data = _raise
        coord.device_coordinators = {"aabbcc": device_coord}

        with patch("custom_components.marstek_local_api.coordinator.asyncio.sleep", new=AsyncMock()):
            data = await coord._async_update_data()

        # Old data preserved on error
        assert data["devices"]["aabbcc"] == {"old": "data"}


# ---------------------------------------------------------------------------
# _update_device_version
# ---------------------------------------------------------------------------

class TestUpdateDeviceVersion:
    def test_no_change(self):
        coord = _make_coord(firmware_version=147, device_model=DEVICE_MODEL_VENUS_A)
        original_compat = coord.compatibility
        coord._update_device_version({"ver": 147, "device": DEVICE_MODEL_VENUS_A})
        # No reinitialize — same compatibility object
        assert coord.compatibility is original_compat
        coord.hass.config_entries.async_update_entry.assert_not_called()

    def test_firmware_changed_only(self):
        coord = _make_coord(firmware_version=147, device_model=DEVICE_MODEL_VENUS_A)
        coord._update_device_version({"ver": 200, "device": DEVICE_MODEL_VENUS_A})
        assert coord.firmware_version == 200
        assert coord.device_model == DEVICE_MODEL_VENUS_A
        assert isinstance(coord.compatibility, CompatibilityMatrix)

    def test_model_changed_only(self):
        coord = _make_coord(firmware_version=147, device_model=DEVICE_MODEL_VENUS_A)
        coord._update_device_version({"ver": 147, "device": DEVICE_MODEL_VENUS_D})
        assert coord.firmware_version == 147
        assert coord.device_model == DEVICE_MODEL_VENUS_D

    def test_both_changed_no_config_entry(self):
        coord = _make_coord(firmware_version=147, device_model=DEVICE_MODEL_VENUS_A)
        coord._config_entry = None
        coord._update_device_version({"ver": 200, "device": DEVICE_MODEL_VENUS_D})
        assert coord.firmware_version == 200
        assert coord.device_model == DEVICE_MODEL_VENUS_D
        coord.hass.config_entries.async_update_entry.assert_not_called()

    def test_firmware_changed_single_device_config_entry(self):
        coord = _make_coord(firmware_version=147, device_model=DEVICE_MODEL_VENUS_A)
        entry = MagicMock()
        entry.data = {"firmware": 147, "device": DEVICE_MODEL_VENUS_A}
        coord._config_entry = entry
        coord._device_mac = None
        coord._update_device_version({"ver": 200, "device": DEVICE_MODEL_VENUS_A})
        coord.hass.config_entries.async_update_entry.assert_called_once()
        called_data = coord.hass.config_entries.async_update_entry.call_args[1]["data"]
        assert called_data["firmware"] == 200
        assert "device" not in called_data or called_data["device"] == DEVICE_MODEL_VENUS_A

    def test_model_changed_single_device_config_entry(self):
        coord = _make_coord(firmware_version=147, device_model=DEVICE_MODEL_VENUS_A)
        entry = MagicMock()
        entry.data = {"firmware": 147, "device": DEVICE_MODEL_VENUS_A}
        coord._config_entry = entry
        coord._device_mac = None
        coord._update_device_version({"ver": 147, "device": DEVICE_MODEL_VENUS_D})
        called_data = coord.hass.config_entries.async_update_entry.call_args[1]["data"]
        assert called_data["device"] == DEVICE_MODEL_VENUS_D

    def test_multi_device_config_entry_mac_match(self):
        coord = _make_coord(firmware_version=147, device_model=DEVICE_MODEL_VENUS_A)
        entry = MagicMock()
        entry.data = {
            "devices": [
                {"ble_mac": "aabbccddeeff", "firmware": 147, "device": DEVICE_MODEL_VENUS_A},
            ]
        }
        coord._config_entry = entry
        coord._device_mac = "aabbccddeeff"
        coord._update_device_version({"ver": 200, "device": DEVICE_MODEL_VENUS_D})
        called_data = coord.hass.config_entries.async_update_entry.call_args[1]["data"]
        assert called_data["devices"][0]["firmware"] == 200
        assert called_data["devices"][0]["device"] == DEVICE_MODEL_VENUS_D

    def test_multi_device_config_entry_no_mac_match(self):
        coord = _make_coord(firmware_version=147, device_model=DEVICE_MODEL_VENUS_A)
        entry = MagicMock()
        entry.data = {
            "devices": [
                {"ble_mac": "112233445566", "firmware": 147, "device": DEVICE_MODEL_VENUS_A},
            ]
        }
        coord._config_entry = entry
        coord._device_mac = "aabbccddeeff"  # Different MAC
        coord._update_device_version({"ver": 200, "device": DEVICE_MODEL_VENUS_D})
        # Entry updated but no device modified (no match found)
        coord.hass.config_entries.async_update_entry.assert_called_once()
        called_data = coord.hass.config_entries.async_update_entry.call_args[1]["data"]
        # Original device not modified
        assert called_data["devices"][0]["firmware"] == 147


# ---------------------------------------------------------------------------
# _get_seconds_since_last_message
# ---------------------------------------------------------------------------

class TestGetSecondsSinceLastMessage:
    def test_returns_none_when_no_timestamp(self):
        coord = _make_coord()
        assert coord._get_seconds_since_last_message() is None

    def test_returns_seconds_when_timestamp_set(self):
        coord = _make_coord()
        coord.last_message_timestamp = time.time() - 30
        result = coord._get_seconds_since_last_message()
        assert isinstance(result, int)
        assert 28 <= result <= 35


# ---------------------------------------------------------------------------
# is_category_fresh
# ---------------------------------------------------------------------------

class TestIsCategoryFresh:
    def test_static_categories_always_fresh(self):
        coord = _make_coord()
        for cat in ("device", "wifi", "ble", "mode", "_diagnostic", "aggregates"):
            assert coord.is_category_fresh(cat) is True

    def test_dynamic_category_never_received(self):
        coord = _make_coord()
        assert coord.is_category_fresh("battery") is False
        assert coord.is_category_fresh("es") is False

    def test_dynamic_category_recently_updated(self):
        coord = _make_coord()
        coord.category_last_updated["battery"] = time.time() - 10
        assert coord.is_category_fresh("battery") is True

    def test_dynamic_category_stale(self):
        coord = _make_coord()
        coord.category_last_updated["battery"] = time.time() - 1000
        assert coord.is_category_fresh("battery") is False


# ---------------------------------------------------------------------------
# _build_command_diagnostics
# ---------------------------------------------------------------------------

class TestBuildCommandDiagnostics:
    def test_none_stats_returns_empty(self):
        coord = _make_coord()
        assert coord._build_command_diagnostics("es", None) == {}

    def test_empty_dict_returns_empty(self):
        coord = _make_coord()
        assert coord._build_command_diagnostics("es", {}) == {}

    def test_full_stats_with_attempts(self):
        coord = _make_coord()
        stats = {
            "total_attempts": 10,
            "total_success": 8,
            "total_timeouts": 2,
            "last_latency": 0.15,
            "last_attempt": 1000.0,
            "last_success": True,
            "last_error": None,
        }
        diag = coord._build_command_diagnostics("es", stats)
        assert diag["es_success_total"] == 8
        assert diag["es_request_total"] == 10
        assert diag["es_success_rate"] == pytest.approx(80.0)
        assert diag["es_last_success"] == 1
        assert diag["es_timeout_total"] == 2
        assert diag["es_last_latency"] == 0.15

    def test_zero_attempts_success_rate_none(self):
        coord = _make_coord()
        stats = {"total_attempts": 0, "total_success": 0, "total_timeouts": 0}
        diag = coord._build_command_diagnostics("bat", stats)
        assert diag["bat_success_rate"] is None

    def test_last_success_none(self):
        coord = _make_coord()
        stats = {"total_attempts": 5, "total_success": 5, "total_timeouts": 0, "last_success": None}
        diag = coord._build_command_diagnostics("es", stats)
        assert diag["es_last_success"] is None

    def test_last_success_falsy_int_zero(self):
        coord = _make_coord()
        stats = {"total_attempts": 5, "total_success": 0, "total_timeouts": 5, "last_success": False}
        diag = coord._build_command_diagnostics("es", stats)
        assert diag["es_last_success"] == 0


# ---------------------------------------------------------------------------
# _async_update_data
# ---------------------------------------------------------------------------

class TestAsyncUpdateData:
    @pytest.fixture(autouse=True)
    def patch_sleep(self):
        with patch("custom_components.marstek_local_api.coordinator.asyncio.sleep", new=AsyncMock()):
            yield

    async def test_first_update_all_succeed(self):
        """First update: all APIs succeed, all scaling branches taken."""
        coord = _make_coord()
        coord.api.get_device_info = AsyncMock(return_value={"ver": 147, "device": DEVICE_MODEL_VENUS_A})
        coord.api.get_es_status = AsyncMock(return_value={
            "bat_power": 100,
            "total_grid_input_energy": 1000,
            "total_grid_output_energy": 500,
            "total_load_energy": 200,
            "pv_power": 300,
        })
        coord.api.get_em_status = AsyncMock(return_value={"ongrid_power": 50})
        coord.api.get_battery_status = AsyncMock(return_value={
            "soc": 80,
            "bat_temp": 250,
            "bat_capacity": 3328,
            "bat_voltage": 52000,
            "bat_current": 1000,
            "rated_capacity": 4160,
        })
        coord.api.get_pv_status = AsyncMock(return_value={"pv_power": 300})

        data = await coord._async_update_data()

        assert "device" in data
        assert "es" in data
        assert "em" in data
        assert "battery" in data
        assert "pv" in data
        assert data["_config"]["dod_percent"] == DOD_DEFAULT
        assert coord.last_message_timestamp is not None

    async def test_first_update_no_data_received(self):
        """First update: all APIs return None → had_success=False → warning logged."""
        coord = _make_coord()
        data = await coord._async_update_data()
        assert "_diagnostic" in data
        assert "_config" in data

    async def test_non_first_update_medium_tier(self):
        """Non-first update at count=10: medium tier runs (battery + PV)."""
        coord = _make_coord(data={"old": "data"}, update_count=10)
        coord.api.get_es_status = AsyncMock(return_value={"bat_power": 50})
        coord.api.get_battery_status = AsyncMock(return_value={"soc": 70, "rated_capacity": 4160})
        data = await coord._async_update_data()
        assert "es" in data
        assert "battery" in data

    async def test_non_first_update_slow_tier(self):
        """Non-first update at count=100: slow tier runs (device/wifi/ble/mode)."""
        coord = _make_coord(data={"old": "data"}, update_count=100)
        coord.api.get_device_info = AsyncMock(return_value={"ver": 147, "device": DEVICE_MODEL_VENUS_A})
        coord.api.get_wifi_status = AsyncMock(return_value={"ssid": "MyWifi"})
        coord.api.get_ble_status = AsyncMock(return_value={"state": "connected"})
        coord.api.get_es_mode = AsyncMock(return_value={"mode": "Auto"})
        data = await coord._async_update_data()
        assert "device" in data
        assert "wifi" in data
        assert "ble" in data
        assert "mode" in data

    async def test_slow_tier_mode_without_mode_key(self):
        """mode_status returned but lacks 'mode' key → mode not stored."""
        coord = _make_coord(data={"old": "data"}, update_count=100)
        coord.api.get_es_mode = AsyncMock(return_value={"other_key": "value"})
        data = await coord._async_update_data()
        assert "mode" not in data

    async def test_es_status_exception_handled(self):
        coord = _make_coord(data={"old": "data"})
        coord.api.get_es_status = AsyncMock(side_effect=Exception("Timeout"))
        data = await coord._async_update_data()
        assert "es" not in data

    async def test_em_status_exception_handled(self):
        coord = _make_coord(data={"old": "data"})
        coord.api.get_em_status = AsyncMock(side_effect=Exception("Timeout"))
        # Should not raise
        data = await coord._async_update_data()
        assert "_diagnostic" in data

    async def test_battery_status_exception_handled(self):
        coord = _make_coord(data={"old": "data"}, update_count=10)
        coord.api.get_battery_status = AsyncMock(side_effect=Exception("Timeout"))
        data = await coord._async_update_data()
        assert "battery" not in data

    async def test_pv_status_exception_handled(self):
        coord = _make_coord(data={"old": "data"}, update_count=10)
        coord.api.get_battery_status = AsyncMock(return_value={"soc": 70})
        coord.api.get_pv_status = AsyncMock(side_effect=Exception("Timeout"))
        data = await coord._async_update_data()
        assert "pv" not in data

    async def test_pv_not_queried_for_venus_c(self):
        """Device model VenusC: PV not queried during medium tier."""
        coord = _make_coord(data={"old": "data"}, update_count=10, device_model=DEVICE_MODEL_VENUS_C)
        coord.api.get_battery_status = AsyncMock(return_value={"soc": 70})
        await coord._async_update_data()
        coord.api.get_pv_status.assert_not_called()

    async def test_pv_queried_for_venus_a_with_space(self):
        """Device reporting 'Venus A' (with space) should still query PV."""
        coord = _make_coord(data={"old": "data"}, update_count=10, device_model="Venus A")
        coord.api.get_battery_status = AsyncMock(return_value={"soc": 70})
        coord.api.get_pv_status = AsyncMock(return_value={"pv_power": 500})
        await coord._async_update_data()
        coord.api.get_pv_status.assert_called_once()

    async def test_slow_tier_device_info_exception(self):
        coord = _make_coord(data={"old": "data"}, update_count=100)
        coord.api.get_device_info = AsyncMock(side_effect=Exception("Timeout"))
        data = await coord._async_update_data()
        assert "_diagnostic" in data

    async def test_slow_tier_wifi_exception(self):
        coord = _make_coord(data={"old": "data"}, update_count=100)
        coord.api.get_wifi_status = AsyncMock(side_effect=Exception("Timeout"))
        data = await coord._async_update_data()
        assert "_diagnostic" in data

    async def test_slow_tier_ble_exception(self):
        coord = _make_coord(data={"old": "data"}, update_count=100)
        coord.api.get_ble_status = AsyncMock(side_effect=Exception("Timeout"))
        data = await coord._async_update_data()
        assert "_diagnostic" in data

    async def test_slow_tier_mode_exception(self):
        coord = _make_coord(data={"old": "data"}, update_count=100)
        coord.api.get_es_mode = AsyncMock(side_effect=Exception("Timeout"))
        data = await coord._async_update_data()
        assert "_diagnostic" in data

    async def test_first_update_no_success_disables_medium_tier(self):
        """First update with no success → run_medium forced to False."""
        coord = _make_coord()  # data=None, update_count=1
        # All APIs return None → had_success stays False
        data = await coord._async_update_data()
        # Medium tier disabled by first-update guard
        coord.api.get_battery_status.assert_not_called()

    async def test_first_update_no_success_disables_slow_tier(self):
        """First update at count=100 with no success → run_slow forced to False."""
        coord = _make_coord(update_count=100)  # data=None → first update
        data = await coord._async_update_data()
        # Slow tier would normally run at count=100 but is disabled due to no success
        coord.api.get_wifi_status.assert_not_called()

    async def test_first_update_device_info_exception_skips_medium(self):
        """First update: device_info raises → had_success=False → medium tier skipped."""
        coord = _make_coord()
        coord.api.get_device_info = AsyncMock(side_effect=Exception("Failed"))
        await coord._async_update_data()
        coord.api.get_battery_status.assert_not_called()

    async def test_outer_except_marstek_api_error_first_update(self):
        """get_command_stats raises MarstekAPIError on first update → UpdateFailed raised."""
        coord = _make_coord()  # data=None → is_first_update=True
        coord.api.get_command_stats = MagicMock(side_effect=MarstekAPIError("API error"))
        with pytest.raises(UpdateFailed):
            await coord._async_update_data()

    async def test_outer_except_marstek_api_error_not_first_update_with_data(self):
        """get_command_stats raises on non-first update → old data returned."""
        coord = _make_coord(data={"old": "data"})
        coord.api.get_command_stats = MagicMock(side_effect=MarstekAPIError("API error"))
        result = await coord._async_update_data()
        assert result == {"old": "data"}

    async def test_outer_except_marstek_api_error_not_first_update_empty_data(self):
        """get_command_stats raises on non-first update with empty data → {} returned."""
        coord = _make_coord(data={})
        coord.api.get_command_stats = MagicMock(side_effect=MarstekAPIError("API error"))
        result = await coord._async_update_data()
        assert result == {}

    async def test_outer_except_generic_first_update(self):
        """Generic exception on first update → UpdateFailed raised."""
        coord = _make_coord()  # data=None → is_first_update=True
        coord.api.get_command_stats = MagicMock(side_effect=RuntimeError("Unexpected"))
        with pytest.raises(UpdateFailed):
            await coord._async_update_data()

    async def test_outer_except_generic_not_first_update_with_data(self):
        """Generic exception on non-first update → old data returned."""
        coord = _make_coord(data={"old": "data"})
        coord.api.get_command_stats = MagicMock(side_effect=RuntimeError("Unexpected"))
        result = await coord._async_update_data()
        assert result == {"old": "data"}

    async def test_outer_except_generic_not_first_update_empty_data(self):
        """Generic exception on non-first update with empty data → {} returned."""
        coord = _make_coord(data={})
        coord.api.get_command_stats = MagicMock(side_effect=RuntimeError("Unexpected"))
        result = await coord._async_update_data()
        assert result == {}

    async def test_had_success_false_does_not_update_timestamp(self):
        """All APIs return None → had_success=False → last_message_timestamp unchanged."""
        coord = _make_coord(data={"old": "data"})
        coord.last_message_timestamp = None
        await coord._async_update_data()
        assert coord.last_message_timestamp is None

    async def test_had_success_true_updates_timestamp(self):
        """ES status succeeds → had_success=True → last_message_timestamp updated."""
        coord = _make_coord(data={"old": "data"})
        coord.api.get_es_status = AsyncMock(return_value={"bat_power": 0})
        coord.last_message_timestamp = None
        await coord._async_update_data()
        assert coord.last_message_timestamp is not None

    async def test_actual_interval_calculated_when_previous_start(self):
        """When _last_update_start is set, actual_interval is calculated."""
        coord = _make_coord(data={"old": "data"})
        coord._last_update_start = time.time() - 5.0
        data = await coord._async_update_data()
        assert data["_diagnostic"]["actual_interval"] is not None
        assert data["_diagnostic"]["actual_interval"] >= 0

    async def test_actual_interval_none_on_first_call(self):
        """When _last_update_start is None, actual_interval is None."""
        coord = _make_coord(data={"old": "data"})
        coord._last_update_start = None
        data = await coord._async_update_data()
        assert data["_diagnostic"]["actual_interval"] is None

    async def test_command_diagnostics_with_stats(self):
        """get_command_stats returns stats → diagnostic fields populated."""
        coord = _make_coord(data={"old": "data"})
        coord.api.get_command_stats = MagicMock(return_value={
            "total_attempts": 5,
            "total_success": 5,
            "total_timeouts": 0,
            "last_success": True,
        })
        data = await coord._async_update_data()
        assert "es_success_total" in data["_diagnostic"]
        assert "bat_success_total" in data["_diagnostic"]

    async def test_es_status_without_bat_power(self):
        """es_status without bat_power → bat_power scaling branch not taken."""
        coord = _make_coord(data={"old": "data"})
        coord.api.get_es_status = AsyncMock(return_value={"total_grid_input_energy": 500})
        data = await coord._async_update_data()
        assert "es" in data

    async def test_battery_status_without_scaling_fields(self):
        """battery_status without scaling fields → no scaling applied."""
        coord = _make_coord(data={"old": "data"}, update_count=10)
        coord.api.get_battery_status = AsyncMock(return_value={"soc": 70, "rated_capacity": 4160})
        data = await coord._async_update_data()
        assert "battery" in data

    async def test_pv_status_with_pv_power_field(self):
        """pv_status with pv_power → pv_power scaling applied."""
        coord = _make_coord(data={"old": "data"}, update_count=10)
        coord.api.get_battery_status = AsyncMock(return_value={"soc": 70})
        coord.api.get_pv_status = AsyncMock(return_value={"pv_power": 1500})
        data = await coord._async_update_data()
        assert "pv" in data
        assert "pv_power" in data["pv"]

    async def test_update_count_incremented(self):
        """update_count is incremented each call."""
        coord = _make_coord(data={"old": "data"}, update_count=5)
        await coord._async_update_data()
        assert coord.update_count == 6

    async def test_config_data_injected(self):
        """_config with dod_percent is always injected into data."""
        coord = _make_coord(data={"old": "data"}, dod_percent=75)
        data = await coord._async_update_data()
        assert data["_config"]["dod_percent"] == 75
