"""Tests for config_flow.py — 100% coverage."""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import _DhcpServiceInfo, _load_integration_module

# Ensure config_flow is loaded fresh with our stubs (not with real HA modules
# that pytest-homeassistant-custom-component may have pre-loaded).
sys.modules.pop("custom_components.marstek_local_api.config_flow", None)

# Load api first (config_flow imports from it)
_api_mod = _load_integration_module("api")
_cf = _load_integration_module("config_flow")

ConfigFlow = _cf.ConfigFlow
OptionsFlow = _cf.OptionsFlow
CannotConnect = _cf.CannotConnect
validate_input = _cf.validate_input
DOMAIN = _cf.DOMAIN
DEFAULT_PORT = _cf.DEFAULT_PORT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config_flow():
    """Return a ConfigFlow instance with all parent methods mocked."""
    flow = ConfigFlow()
    flow.hass = MagicMock()
    flow.hass.data = {}
    flow.context = {}
    flow._async_current_entries = MagicMock(return_value=[])
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    flow.async_show_form = MagicMock(side_effect=lambda **kw: {"type": "form", **kw})
    flow.async_create_entry = MagicMock(side_effect=lambda **kw: {"type": "create_entry", **kw})
    flow.async_abort = MagicMock(side_effect=lambda **kw: {"type": "abort", **kw})
    return flow


def _make_options_flow(devices=None, options=None, entry_data=None):
    """Return an OptionsFlow instance with all parent methods mocked."""
    flow = OptionsFlow()
    flow.hass = MagicMock()
    flow.hass.data = {}
    flow.hass.config_entries = MagicMock()
    entry = MagicMock()
    entry.options = options or {}
    if entry_data is not None:
        entry.data = entry_data
    elif devices is not None:
        entry.data = {"devices": devices}
    else:
        entry.data = {}
    # Set via _config_entry to avoid the deprecated property setter in real HA
    # (OptionsFlow.config_entry getter reads from self._config_entry when present)
    flow._config_entry = entry
    flow.async_show_form = MagicMock(side_effect=lambda **kw: {"type": "form", **kw})
    flow.async_create_entry = MagicMock(side_effect=lambda **kw: {"type": "create_entry", **kw})
    flow.async_abort = MagicMock(side_effect=lambda **kw: {"type": "abort", **kw})
    return flow


def _mock_api(discover_result=None, connect_raises=None, discover_raises=None):
    """Return a mock MarstekUDPClient class."""
    mock_instance = AsyncMock()
    if connect_raises:
        mock_instance.connect.side_effect = connect_raises
    if discover_raises:
        mock_instance.discover_devices.side_effect = discover_raises
    elif discover_result is not None:
        mock_instance.discover_devices.return_value = discover_result
    else:
        mock_instance.discover_devices.return_value = []
    mock_cls = MagicMock(return_value=mock_instance)
    return mock_cls, mock_instance


_SAMPLE_DEVICE = {
    "mac": "aabbccddeeff",
    "ble_mac": "aabbccddeeff",
    "wifi_mac": "aabbccdd0011",
    "ip": "192.168.1.100",
    "name": "Venus A",
    "firmware": "147",
}

_SAMPLE_DEVICE2 = {
    "mac": "112233445566",
    "ble_mac": "112233445566",
    "wifi_mac": "112233445500",
    "ip": "192.168.1.101",
    "name": "Venus B",
    "firmware": "147",
}


# ---------------------------------------------------------------------------
# validate_input
# ---------------------------------------------------------------------------

class TestValidateInput:
    async def test_success(self):
        mock_cls, mock_api = _mock_api()
        mock_api.get_device_info.return_value = {
            "device": "VenusA",
            "ver": "147",
            "wifi_mac": "aabb",
            "ble_mac": "ccdd",
        }
        with patch.object(_cf, "MarstekUDPClient", mock_cls):
            result = await validate_input(MagicMock(), {"host": "192.168.1.1", "port": 8899})

        assert result["device"] == "VenusA"
        assert result["ble_mac"] == "ccdd"
        mock_api.disconnect.assert_called_once()

    async def test_no_device_info_raises(self):
        mock_cls, mock_api = _mock_api()
        mock_api.get_device_info.return_value = None
        with patch.object(_cf, "MarstekUDPClient", mock_cls):
            with pytest.raises(CannotConnect):
                await validate_input(MagicMock(), {"host": "192.168.1.1", "port": 8899})

        mock_api.disconnect.assert_called_once()

    async def test_api_error_raises(self):
        mock_cls, mock_api = _mock_api()
        mock_api.connect.side_effect = _api_mod.MarstekAPIError("timeout")
        with patch.object(_cf, "MarstekUDPClient", mock_cls):
            with pytest.raises(CannotConnect):
                await validate_input(MagicMock(), {"host": "192.168.1.1", "port": 8899})

        mock_api.disconnect.assert_called_once()

    async def test_title_uses_ble_mac(self):
        mock_cls, mock_api = _mock_api()
        mock_api.get_device_info.return_value = {
            "device": "VenusA",
            "ver": "147",
            "ble_mac": "aabbccddeeff",
        }
        with patch.object(_cf, "MarstekUDPClient", mock_cls):
            result = await validate_input(MagicMock(), {"host": "192.168.1.1", "port": 8899})

        assert "aabbccddeeff" in result["title"]

    async def test_title_fallback_to_wifi_mac(self):
        mock_cls, mock_api = _mock_api()
        mock_api.get_device_info.return_value = {
            "device": "VenusA",
            "ver": "147",
            "wifi_mac": "wifi123",
        }
        with patch.object(_cf, "MarstekUDPClient", mock_cls):
            result = await validate_input(MagicMock(), {"host": "192.168.1.1", "port": 8899})

        assert "wifi123" in result["title"]


# ---------------------------------------------------------------------------
# ConfigFlow basics
# ---------------------------------------------------------------------------

class TestConfigFlowInit:
    def test_init(self):
        flow = _make_config_flow()
        assert flow._discovered_devices == []

    async def test_async_step_user_calls_discovery(self):
        flow = _make_config_flow()
        mock_cls, mock_api = _mock_api(discover_result=[_SAMPLE_DEVICE])
        with patch.object(_cf, "MarstekUDPClient", mock_cls), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await flow.async_step_user()

        assert result["type"] == "form"

    def test_async_get_options_flow(self):
        flow = _make_config_flow()
        opts_flow = flow.async_get_options_flow(MagicMock())
        assert isinstance(opts_flow, OptionsFlow)


# ---------------------------------------------------------------------------
# ConfigFlow.async_step_discovery — user_input=None
# ---------------------------------------------------------------------------

class TestConfigFlowDiscoveryNoInput:
    async def test_single_device_shows_form(self):
        flow = _make_config_flow()
        mock_cls, _ = _mock_api(discover_result=[_SAMPLE_DEVICE])
        with patch.object(_cf, "MarstekUDPClient", mock_cls), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await flow.async_step_discovery(user_input=None)

        assert result["type"] == "form"
        assert result["step_id"] == "discovery"

    async def test_multiple_devices_includes_all_option(self):
        flow = _make_config_flow()
        mock_cls, _ = _mock_api(discover_result=[_SAMPLE_DEVICE, _SAMPLE_DEVICE2])
        with patch.object(_cf, "MarstekUDPClient", mock_cls), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await flow.async_step_discovery(user_input=None)

        assert result["type"] == "form"

    async def test_zero_devices_goes_to_manual(self):
        flow = _make_config_flow()
        mock_cls, _ = _mock_api(discover_result=[])
        with patch.object(_cf, "MarstekUDPClient", mock_cls), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await flow.async_step_discovery(user_input=None)

        # manual step shows a form too
        assert result["type"] == "form"
        assert result["step_id"] == "manual"

    async def test_discovery_exception_goes_to_manual(self):
        flow = _make_config_flow()
        mock_cls, mock_api = _mock_api()
        mock_api.connect.side_effect = OSError("connection refused")
        with patch.object(_cf, "MarstekUDPClient", mock_cls), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await flow.async_step_discovery(user_input=None)

        assert result["type"] == "form"
        assert result["step_id"] == "manual"

    async def test_discovery_exception_disconnect_also_fails(self):
        """Cover the inner except: pass when disconnect raises during error handling."""
        flow = _make_config_flow()
        mock_cls, mock_api = _mock_api()
        mock_api.connect.side_effect = OSError("oops")
        mock_api.disconnect.side_effect = OSError("also oops")
        with patch.object(_cf, "MarstekUDPClient", mock_cls), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await flow.async_step_discovery(user_input=None)

        assert result["type"] == "form"

    async def test_existing_entry_single_device_coordinator_paused(self):
        """Cover single-device coordinator pause/resume path."""
        flow = _make_config_flow()

        entry = MagicMock()
        entry.entry_id = "entry1"
        entry.title = "Test Device"
        flow._async_current_entries = MagicMock(return_value=[entry])

        mock_coordinator = MagicMock(spec=["api"])
        mock_coordinator.api = AsyncMock()
        flow.hass.data = {DOMAIN: {"entry1": {_cf.DATA_COORDINATOR: mock_coordinator}}}

        mock_cls, _ = _mock_api(discover_result=[_SAMPLE_DEVICE])
        with patch.object(_cf, "MarstekUDPClient", mock_cls), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await flow.async_step_discovery(user_input=None)

        mock_coordinator.api.disconnect.assert_called_once()
        mock_coordinator.api.connect.assert_called_once()
        assert result["type"] == "form"

    async def test_existing_entry_multi_device_coordinator_paused(self):
        """Cover multi-device coordinator pause/resume path."""
        flow = _make_config_flow()

        entry = MagicMock()
        entry.entry_id = "entry1"
        entry.title = "Multi Device"
        flow._async_current_entries = MagicMock(return_value=[entry])

        sub_api = AsyncMock()
        mock_coordinator = MagicMock(spec=["device_coordinators"])
        mock_coordinator.device_coordinators = {"dev1": MagicMock(api=sub_api)}
        flow.hass.data = {DOMAIN: {"entry1": {_cf.DATA_COORDINATOR: mock_coordinator}}}

        mock_cls, _ = _mock_api(discover_result=[_SAMPLE_DEVICE])
        with patch.object(_cf, "MarstekUDPClient", mock_cls), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await flow.async_step_discovery(user_input=None)

        sub_api.disconnect.assert_called_once()
        sub_api.connect.assert_called_once()
        assert result["type"] == "form"

    async def test_existing_entry_no_coordinator_skipped(self):
        """Entry with no coordinator is silently skipped."""
        flow = _make_config_flow()

        entry = MagicMock()
        entry.entry_id = "entry1"
        flow._async_current_entries = MagicMock(return_value=[entry])
        flow.hass.data = {DOMAIN: {"entry1": {}}}

        mock_cls, _ = _mock_api(discover_result=[_SAMPLE_DEVICE])
        with patch.object(_cf, "MarstekUDPClient", mock_cls), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await flow.async_step_discovery(user_input=None)

        assert result["type"] == "form"

    async def test_resume_client_fails_logs_warning(self):
        """Cover warning log when resumed client.connect() raises."""
        flow = _make_config_flow()

        entry = MagicMock()
        entry.entry_id = "entry1"
        flow._async_current_entries = MagicMock(return_value=[entry])

        mock_coordinator = MagicMock(spec=["api"])
        mock_coordinator.api = AsyncMock()
        mock_coordinator.api.connect.side_effect = OSError("reconnect failed")
        flow.hass.data = {DOMAIN: {"entry1": {_cf.DATA_COORDINATOR: mock_coordinator}}}

        mock_cls, _ = _mock_api(discover_result=[_SAMPLE_DEVICE])
        with patch.object(_cf, "MarstekUDPClient", mock_cls), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await flow.async_step_discovery(user_input=None)

        # Warning is logged, flow continues
        assert result["type"] == "form"

    async def test_entry_not_in_domain_data_skipped(self):
        """Entry whose entry_id is not in hass.data[DOMAIN] is skipped."""
        flow = _make_config_flow()

        entry = MagicMock()
        entry.entry_id = "entry_unknown"
        flow._async_current_entries = MagicMock(return_value=[entry])
        flow.hass.data = {DOMAIN: {}}  # entry_id not present

        mock_cls, _ = _mock_api(discover_result=[_SAMPLE_DEVICE])
        with patch.object(_cf, "MarstekUDPClient", mock_cls), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await flow.async_step_discovery(user_input=None)

        assert result["type"] == "form"


# ---------------------------------------------------------------------------
# ConfigFlow.async_step_discovery — user_input provided
# ---------------------------------------------------------------------------

class TestConfigFlowDiscoveryWithInput:
    def _flow_with_devices(self, devices):
        flow = _make_config_flow()
        flow._discovered_devices = devices
        return flow

    async def test_select_manual(self):
        flow = self._flow_with_devices([_SAMPLE_DEVICE])
        result = await flow.async_step_discovery(user_input={"device": "manual"})
        assert result["step_id"] == "manual"

    async def test_select_all_with_ble_macs(self):
        flow = self._flow_with_devices([_SAMPLE_DEVICE, _SAMPLE_DEVICE2])
        result = await flow.async_step_discovery(user_input={"device": "__all__"})
        assert result["type"] == "create_entry"
        assert "devices" in result["data"]
        assert len(result["data"]["devices"]) == 2
        flow.async_set_unique_id.assert_called_once()

    async def test_select_all_without_ble_macs(self):
        d1 = {**_SAMPLE_DEVICE, "ble_mac": None}
        d2 = {**_SAMPLE_DEVICE2, "ble_mac": None}
        flow = self._flow_with_devices([d1, d2])
        result = await flow.async_step_discovery(user_input={"device": "__all__"})
        assert result["type"] == "create_entry"
        # unique_id is empty → async_set_unique_id NOT called
        flow.async_set_unique_id.assert_not_called()

    async def test_select_single_with_ble_mac(self):
        flow = self._flow_with_devices([_SAMPLE_DEVICE])
        result = await flow.async_step_discovery(user_input={"device": _SAMPLE_DEVICE["mac"]})
        assert result["type"] == "create_entry"
        assert result["data"][_cf.CONF_HOST] == _SAMPLE_DEVICE["ip"]
        flow.async_set_unique_id.assert_called_once_with(_SAMPLE_DEVICE["ble_mac"])

    async def test_select_single_no_ble_mac(self):
        d = {**_SAMPLE_DEVICE, "ble_mac": None}
        flow = self._flow_with_devices([d])
        result = await flow.async_step_discovery(user_input={"device": d["mac"]})
        assert result["type"] == "create_entry"
        flow.async_set_unique_id.assert_not_called()

    async def test_select_unknown_device_error(self):
        flow = self._flow_with_devices([_SAMPLE_DEVICE])
        result = await flow.async_step_discovery(user_input={"device": "nonexistent_mac"})
        assert result["type"] == "form"
        assert result["errors"]["base"] == "device_not_found"


# ---------------------------------------------------------------------------
# ConfigFlow.async_step_manual
# ---------------------------------------------------------------------------

class TestConfigFlowManual:
    async def test_no_input_shows_form(self):
        flow = _make_config_flow()
        result = await flow.async_step_manual(user_input=None)
        assert result["type"] == "form"
        assert result["step_id"] == "manual"

    async def test_success_with_ble_mac(self):
        flow = _make_config_flow()
        mock_cls, mock_api = _mock_api()
        mock_api.get_device_info.return_value = {
            "device": "VenusA", "ver": "147",
            "ble_mac": "aabb", "wifi_mac": "ccdd",
        }
        user_input = {"host": "192.168.1.1", "port": 8899}
        with patch.object(_cf, "MarstekUDPClient", mock_cls):
            result = await flow.async_step_manual(user_input=user_input)

        assert result["type"] == "create_entry"
        flow.async_set_unique_id.assert_called_once_with("aabb")

    async def test_success_no_ble_mac(self):
        flow = _make_config_flow()
        mock_cls, mock_api = _mock_api()
        mock_api.get_device_info.return_value = {
            "device": "VenusA", "ver": "147",
            "wifi_mac": "ccdd",
        }
        user_input = {"host": "192.168.1.1", "port": 8899}
        with patch.object(_cf, "MarstekUDPClient", mock_cls):
            result = await flow.async_step_manual(user_input=user_input)

        assert result["type"] == "create_entry"
        flow.async_set_unique_id.assert_not_called()

    async def test_cannot_connect_sets_error(self):
        flow = _make_config_flow()
        mock_cls, mock_api = _mock_api()
        mock_api.connect.side_effect = _api_mod.MarstekAPIError("timeout")
        user_input = {"host": "192.168.1.1", "port": 8899}
        with patch.object(_cf, "MarstekUDPClient", mock_cls):
            result = await flow.async_step_manual(user_input=user_input)

        assert result["type"] == "form"
        assert result["errors"]["base"] == "cannot_connect"

    async def test_unknown_exception_sets_error(self):
        flow = _make_config_flow()
        mock_cls, mock_api = _mock_api()
        mock_api.connect.side_effect = RuntimeError("unexpected")
        user_input = {"host": "192.168.1.1", "port": 8899}
        with patch.object(_cf, "MarstekUDPClient", mock_cls):
            result = await flow.async_step_manual(user_input=user_input)

        assert result["type"] == "form"
        assert result["errors"]["base"] == "unknown"


# ---------------------------------------------------------------------------
# ConfigFlow.async_step_dhcp
# ---------------------------------------------------------------------------

class TestConfigFlowDhcp:
    async def test_success_with_ble_mac(self):
        flow = _make_config_flow()
        mock_cls, mock_api = _mock_api()
        mock_api.get_device_info.return_value = {
            "device": "VenusA", "ver": "147",
            "ble_mac": "aabb", "wifi_mac": "ccdd",
        }
        with patch.object(_cf, "MarstekUDPClient", mock_cls):
            result = await flow.async_step_dhcp(_DhcpServiceInfo(ip="192.168.1.1"))

        assert result["type"] == "form"
        assert result["step_id"] == "discovery_confirm"
        flow.async_set_unique_id.assert_called_once_with("aabb")

    async def test_success_no_ble_mac(self):
        flow = _make_config_flow()
        mock_cls, mock_api = _mock_api()
        mock_api.get_device_info.return_value = {
            "device": "VenusA", "ver": "147",
            "wifi_mac": "ccdd",
        }
        with patch.object(_cf, "MarstekUDPClient", mock_cls):
            result = await flow.async_step_dhcp(_DhcpServiceInfo(ip="192.168.1.1"))

        assert result["type"] == "form"
        flow.async_set_unique_id.assert_not_called()

    async def test_cannot_connect_aborts(self):
        flow = _make_config_flow()
        mock_cls, mock_api = _mock_api()
        mock_api.connect.side_effect = _api_mod.MarstekAPIError("timeout")
        with patch.object(_cf, "MarstekUDPClient", mock_cls):
            result = await flow.async_step_dhcp(_DhcpServiceInfo(ip="192.168.1.1"))

        flow.async_abort.assert_called_once_with(reason="cannot_connect")

    async def test_unknown_exception_aborts(self):
        flow = _make_config_flow()
        mock_cls, mock_api = _mock_api()
        mock_api.connect.side_effect = RuntimeError("unexpected")
        with patch.object(_cf, "MarstekUDPClient", mock_cls):
            result = await flow.async_step_dhcp(_DhcpServiceInfo(ip="192.168.1.1"))

        flow.async_abort.assert_called_once_with(reason="unknown")


# ---------------------------------------------------------------------------
# ConfigFlow.async_step_discovery_confirm
# ---------------------------------------------------------------------------

class TestConfigFlowDiscoveryConfirm:
    async def test_no_input_shows_form(self):
        flow = _make_config_flow()
        flow.context = {"title_placeholders": {"name": "VenusA"}}
        result = await flow.async_step_discovery_confirm(user_input=None)
        assert result["type"] == "form"
        assert result["step_id"] == "discovery_confirm"

    async def test_with_input_creates_entry(self):
        device_info = {"host": "192.168.1.1", "port": 8899}
        flow = _make_config_flow()
        flow.context = {
            "title_placeholders": {"name": "VenusA"},
            "device_info": device_info,
        }
        result = await flow.async_step_discovery_confirm(user_input={"confirm": True})
        assert result["type"] == "create_entry"
        assert result["data"] == device_info


# ---------------------------------------------------------------------------
# OptionsFlow.async_step_init
# ---------------------------------------------------------------------------

class TestOptionsFlowInit:
    async def test_no_devices_shows_limited_actions(self):
        flow = _make_options_flow()
        result = await flow.async_step_init(user_input=None)
        assert result["type"] == "form"
        assert result["step_id"] == "init"

    async def test_with_devices_shows_all_actions(self):
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE])
        result = await flow.async_step_init(user_input=None)
        assert result["type"] == "form"

    async def test_devices_populated_from_config_entry(self):
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE])
        # _devices is initially empty; init populates from config_entry
        assert flow._devices == []
        await flow.async_step_init(user_input=None)
        assert flow._devices == [_SAMPLE_DEVICE]

    async def test_action_scan_interval(self):
        flow = _make_options_flow()
        result = await flow.async_step_init(user_input={"action": "scan_interval"})
        assert result["step_id"] == "scan_interval"

    async def test_action_battery_settings(self):
        flow = _make_options_flow()
        result = await flow.async_step_init(user_input={"action": "battery_settings"})
        assert result["step_id"] == "battery_settings"

    async def test_action_rename_device(self):
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE])
        result = await flow.async_step_init(user_input={"action": "rename_device"})
        assert result["step_id"] == "rename_device"

    async def test_action_remove_device(self):
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE, _SAMPLE_DEVICE2])
        result = await flow.async_step_init(user_input={"action": "remove_device"})
        assert result["step_id"] == "remove_device"

    async def test_action_add_device(self):
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE])
        mock_cls, _ = _mock_api(discover_result=[])
        with patch.object(_cf, "MarstekUDPClient", mock_cls), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await flow.async_step_init(user_input={"action": "add_device"})
        assert result["step_id"] == "add_device"


# ---------------------------------------------------------------------------
# OptionsFlow.async_step_scan_interval
# ---------------------------------------------------------------------------

class TestOptionsFlowScanInterval:
    async def test_no_input_shows_form(self):
        flow = _make_options_flow()
        result = await flow.async_step_scan_interval(user_input=None)
        assert result["type"] == "form"
        assert result["step_id"] == "scan_interval"

    async def test_with_input_merges_options_and_creates(self):
        flow = _make_options_flow(options={"dod_percent": 80})
        result = await flow.async_step_scan_interval(user_input={"scan_interval": 30})
        assert result["type"] == "create_entry"
        # merged: keeps dod_percent and adds scan_interval
        assert result["data"]["dod_percent"] == 80
        assert result["data"]["scan_interval"] == 30


# ---------------------------------------------------------------------------
# OptionsFlow.async_step_battery_settings
# ---------------------------------------------------------------------------

class TestOptionsFlowBatterySettings:
    async def test_no_input_shows_form(self):
        flow = _make_options_flow()
        result = await flow.async_step_battery_settings(user_input=None)
        assert result["type"] == "form"
        assert result["step_id"] == "battery_settings"

    async def test_with_input_merges_options_and_creates(self):
        flow = _make_options_flow(options={"scan_interval": 30})
        result = await flow.async_step_battery_settings(user_input={"dod_percent": 90})
        assert result["type"] == "create_entry"
        assert result["data"]["scan_interval"] == 30
        assert result["data"]["dod_percent"] == 90


# ---------------------------------------------------------------------------
# OptionsFlow.async_step_rename_device
# ---------------------------------------------------------------------------

class TestOptionsFlowRenameDevice:
    async def test_no_devices_aborts(self):
        flow = _make_options_flow()
        result = await flow.async_step_rename_device(user_input=None)
        flow.async_abort.assert_called_once_with(reason="unknown")

    async def test_no_input_shows_form(self):
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE])
        flow._devices = [_SAMPLE_DEVICE]
        result = await flow.async_step_rename_device(user_input=None)
        assert result["type"] == "form"
        assert result["step_id"] == "rename_device"

    async def test_empty_name_sets_error(self):
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE])
        flow._devices = [_SAMPLE_DEVICE]
        result = await flow.async_step_rename_device(user_input={"device": 0, "name": "  "})
        assert result["errors"]["name"] == "invalid_name"

    async def test_index_out_of_range_sets_error(self):
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE])
        flow._devices = [_SAMPLE_DEVICE]
        result = await flow.async_step_rename_device(user_input={"device": 99, "name": "NewName"})
        assert result["errors"]["base"] == "device_not_found"

    async def test_same_name_creates_entry_noop(self):
        d = {**_SAMPLE_DEVICE, "device": "Venus A"}
        flow = _make_options_flow(devices=[d])
        flow._devices = [d]
        result = await flow.async_step_rename_device(user_input={"device": 0, "name": "Venus A"})
        assert result["type"] == "create_entry"
        # config_entry.data NOT updated
        flow.hass.config_entries.async_update_entry.assert_not_called()

    async def test_new_name_updates_entry(self):
        d = {**_SAMPLE_DEVICE, "device": "Old Name"}
        flow = _make_options_flow(devices=[d])
        flow._devices = [d]
        flow.config_entry.data = {"devices": [d]}
        result = await flow.async_step_rename_device(user_input={"device": 0, "name": "New Name"})
        assert result["type"] == "create_entry"
        flow.hass.config_entries.async_update_entry.assert_called_once()
        assert flow._devices[0]["device"] == "New Name"


# ---------------------------------------------------------------------------
# OptionsFlow.async_step_remove_device
# ---------------------------------------------------------------------------

class TestOptionsFlowRemoveDevice:
    async def test_no_devices_aborts(self):
        flow = _make_options_flow()
        result = await flow.async_step_remove_device(user_input=None)
        flow.async_abort.assert_called_once_with(reason="unknown")

    async def test_single_device_no_input_shows_form_with_error(self):
        """len=1 sets error but still shows form (user_input=None)."""
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE])
        flow._devices = [_SAMPLE_DEVICE]
        result = await flow.async_step_remove_device(user_input=None)
        assert result["type"] == "form"
        assert result["errors"]["base"] == "cannot_remove_last_device"

    async def test_multiple_devices_no_input_shows_form(self):
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE, _SAMPLE_DEVICE2])
        flow._devices = [_SAMPLE_DEVICE, _SAMPLE_DEVICE2]
        result = await flow.async_step_remove_device(user_input=None)
        assert result["type"] == "form"
        assert result["errors"] == {}

    async def test_single_device_with_input_shows_form_with_error(self):
        """len=1 sets error; user_input provided → show form (not remove)."""
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE])
        flow._devices = [_SAMPLE_DEVICE]
        result = await flow.async_step_remove_device(user_input={"device": 0})
        assert result["type"] == "form"
        assert result["errors"]["base"] == "cannot_remove_last_device"

    async def test_index_out_of_range_sets_error(self):
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE, _SAMPLE_DEVICE2])
        flow._devices = [_SAMPLE_DEVICE, _SAMPLE_DEVICE2]
        result = await flow.async_step_remove_device(user_input={"device": 99})
        assert result["errors"]["base"] == "device_not_found"

    async def test_success_removes_device(self):
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE, _SAMPLE_DEVICE2])
        flow._devices = [_SAMPLE_DEVICE, _SAMPLE_DEVICE2]
        flow.config_entry.data = {"devices": [_SAMPLE_DEVICE, _SAMPLE_DEVICE2]}
        result = await flow.async_step_remove_device(user_input={"device": 0})
        assert result["type"] == "create_entry"
        assert len(flow._devices) == 1
        flow.hass.config_entries.async_update_entry.assert_called_once()


# ---------------------------------------------------------------------------
# OptionsFlow.async_step_add_device
# ---------------------------------------------------------------------------

class TestOptionsFlowAddDevice:
    async def test_no_devices_aborts(self):
        flow = _make_options_flow()
        result = await flow.async_step_add_device(user_input=None)
        flow.async_abort.assert_called_once_with(reason="unknown")

    async def test_no_input_discovers_and_shows_form(self):
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE])
        flow._devices = [_SAMPLE_DEVICE]
        mock_cls, _ = _mock_api(discover_result=[_SAMPLE_DEVICE2])
        with patch.object(_cf, "MarstekUDPClient", mock_cls), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await flow.async_step_add_device(user_input=None)

        assert result["type"] == "form"
        assert result["step_id"] == "add_device"

    async def test_no_input_filters_existing_macs(self):
        """Existing device MACs are filtered out of the discovered list."""
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE])
        flow._devices = [_SAMPLE_DEVICE]
        # discover same device as already configured
        mock_cls, _ = _mock_api(discover_result=[_SAMPLE_DEVICE])
        with patch.object(_cf, "MarstekUDPClient", mock_cls), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await flow.async_step_add_device(user_input=None)

        assert result["type"] == "form"

    async def test_select_manual_goes_to_add_device_manual(self):
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE])
        flow._devices = [_SAMPLE_DEVICE]
        flow._discovered_devices = []
        result = await flow.async_step_add_device(user_input={"device": "manual"})
        assert result["step_id"] == "add_device_manual"

    async def test_device_not_found_sets_error(self):
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE])
        flow._devices = [_SAMPLE_DEVICE]
        flow._discovered_devices = [_SAMPLE_DEVICE2]
        result = await flow.async_step_add_device(user_input={"device": "badmac"})
        assert result["errors"]["base"] == "device_not_found"

    async def test_device_already_configured_ble_mac(self):
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE])
        flow._devices = [_SAMPLE_DEVICE]
        # discovered device has same ble_mac as existing
        d = {**_SAMPLE_DEVICE2, "ble_mac": _SAMPLE_DEVICE["ble_mac"]}
        flow._discovered_devices = [d]
        result = await flow.async_step_add_device(user_input={"device": d["mac"]})
        assert result["errors"]["base"] == "device_already_configured"

    async def test_device_already_configured_wifi_mac(self):
        # existing device has NO ble_mac → existing_macs built from wifi_mac
        existing = {**_SAMPLE_DEVICE, "ble_mac": None}
        flow = _make_options_flow(devices=[existing])
        flow._devices = [existing]
        # new device has same wifi_mac as existing
        d = {**_SAMPLE_DEVICE2, "wifi_mac": _SAMPLE_DEVICE["wifi_mac"]}
        flow._discovered_devices = [d]
        result = await flow.async_step_add_device(user_input={"device": d["mac"]})
        assert result["errors"]["base"] == "device_already_configured"

    async def test_success_adds_device(self):
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE])
        flow._devices = [_SAMPLE_DEVICE]
        flow._discovered_devices = [_SAMPLE_DEVICE2]
        flow.config_entry.data = {"devices": [_SAMPLE_DEVICE]}
        result = await flow.async_step_add_device(user_input={"device": _SAMPLE_DEVICE2["mac"]})
        assert result["type"] == "create_entry"
        assert len(flow._devices) == 2


# ---------------------------------------------------------------------------
# OptionsFlow.async_step_add_device_manual
# ---------------------------------------------------------------------------

class TestOptionsFlowAddDeviceManual:
    async def test_no_devices_aborts(self):
        flow = _make_options_flow()
        result = await flow.async_step_add_device_manual(user_input=None)
        flow.async_abort.assert_called_once_with(reason="unknown")

    async def test_no_input_shows_form(self):
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE])
        flow._devices = [_SAMPLE_DEVICE]
        result = await flow.async_step_add_device_manual(user_input=None)
        assert result["type"] == "form"
        assert result["step_id"] == "add_device_manual"

    async def test_mac_already_configured_sets_error(self):
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE])
        flow._devices = [_SAMPLE_DEVICE]
        mock_cls, mock_api = _mock_api()
        mock_api.get_device_info.return_value = {
            "device": "VenusA", "ver": "147",
            "ble_mac": _SAMPLE_DEVICE["ble_mac"],
        }
        user_input = {"host": "192.168.1.1", "port": 8899}
        with patch.object(_cf, "MarstekUDPClient", mock_cls):
            result = await flow.async_step_add_device_manual(user_input=user_input)

        assert result["errors"]["base"] == "device_already_configured"

    async def test_success_new_device(self):
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE])
        flow._devices = [_SAMPLE_DEVICE]
        flow.config_entry.data = {"devices": [_SAMPLE_DEVICE]}
        mock_cls, mock_api = _mock_api()
        mock_api.get_device_info.return_value = {
            "device": "VenusB", "ver": "147",
            "ble_mac": "new_ble_mac", "wifi_mac": "new_wifi_mac",
        }
        user_input = {"host": "192.168.1.2", "port": 8899}
        with patch.object(_cf, "MarstekUDPClient", mock_cls):
            result = await flow.async_step_add_device_manual(user_input=user_input)

        assert result["type"] == "create_entry"
        assert len(flow._devices) == 2

    async def test_success_no_mac_no_conflict(self):
        """mac is None → any(...) is False → device is added."""
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE])
        flow._devices = [_SAMPLE_DEVICE]
        flow.config_entry.data = {"devices": [_SAMPLE_DEVICE]}
        mock_cls, mock_api = _mock_api()
        mock_api.get_device_info.return_value = {
            "device": "VenusC", "ver": "147",
        }
        user_input = {"host": "192.168.1.3", "port": 8899}
        with patch.object(_cf, "MarstekUDPClient", mock_cls):
            result = await flow.async_step_add_device_manual(user_input=user_input)

        assert result["type"] == "create_entry"

    async def test_cannot_connect_sets_error(self):
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE])
        flow._devices = [_SAMPLE_DEVICE]
        mock_cls, mock_api = _mock_api()
        mock_api.connect.side_effect = _api_mod.MarstekAPIError("timeout")
        user_input = {"host": "192.168.1.1", "port": 8899}
        with patch.object(_cf, "MarstekUDPClient", mock_cls):
            result = await flow.async_step_add_device_manual(user_input=user_input)

        assert result["errors"]["base"] == "cannot_connect"

    async def test_unknown_exception_sets_error(self):
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE])
        flow._devices = [_SAMPLE_DEVICE]
        mock_cls, mock_api = _mock_api()
        mock_api.connect.side_effect = RuntimeError("unexpected")
        user_input = {"host": "192.168.1.1", "port": 8899}
        with patch.object(_cf, "MarstekUDPClient", mock_cls):
            result = await flow.async_step_add_device_manual(user_input=user_input)

        assert result["errors"]["base"] == "unknown"


# ---------------------------------------------------------------------------
# OptionsFlow._async_discover_devices
# ---------------------------------------------------------------------------

class TestOptionsFlowAsyncDiscoverDevices:
    async def test_no_domain_in_hass_data(self):
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE])
        flow._devices = [_SAMPLE_DEVICE]
        flow.hass.data = {}  # no DOMAIN key
        mock_cls, _ = _mock_api(discover_result=[_SAMPLE_DEVICE2])
        with patch.object(_cf, "MarstekUDPClient", mock_cls), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await flow._async_discover_devices()

        assert _SAMPLE_DEVICE2 in flow._discovered_devices

    async def test_multi_device_coordinator_paused(self):
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE])
        flow._devices = [_SAMPLE_DEVICE]
        sub_api = AsyncMock()
        coordinator = MagicMock(spec=["device_coordinators"])
        coordinator.device_coordinators = {"dev1": MagicMock(api=sub_api)}
        flow.hass.data = {DOMAIN: {"entry1": {_cf.DATA_COORDINATOR: coordinator}}}
        mock_cls, _ = _mock_api(discover_result=[])
        with patch.object(_cf, "MarstekUDPClient", mock_cls), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await flow._async_discover_devices()

        sub_api.disconnect.assert_called_once()
        sub_api.connect.assert_called_once()

    async def test_single_device_coordinator_paused(self):
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE])
        flow._devices = [_SAMPLE_DEVICE]
        coordinator = MagicMock(spec=["api"])
        coordinator.api = AsyncMock()
        flow.hass.data = {DOMAIN: {"entry1": {_cf.DATA_COORDINATOR: coordinator}}}
        mock_cls, _ = _mock_api(discover_result=[])
        with patch.object(_cf, "MarstekUDPClient", mock_cls), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await flow._async_discover_devices()

        coordinator.api.disconnect.assert_called_once()
        coordinator.api.connect.assert_called_once()

    async def test_no_coordinator_skipped(self):
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE])
        flow._devices = [_SAMPLE_DEVICE]
        flow.hass.data = {DOMAIN: {"entry1": {}}}  # no DATA_COORDINATOR
        mock_cls, _ = _mock_api(discover_result=[])
        with patch.object(_cf, "MarstekUDPClient", mock_cls), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await flow._async_discover_devices()

        assert flow._discovered_devices == []

    async def test_connect_fails_handled(self):
        """api.connect() raises → devices stays empty, no crash."""
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE])
        flow._devices = [_SAMPLE_DEVICE]
        flow.hass.data = {}
        mock_cls, mock_api = _mock_api()
        mock_api.connect.side_effect = OSError("refused")
        with patch.object(_cf, "MarstekUDPClient", mock_cls), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await flow._async_discover_devices()

        assert flow._discovered_devices == []

    async def test_disconnect_in_finally_raises_swallowed(self):
        """api.disconnect() in finally raises → swallowed."""
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE])
        flow._devices = [_SAMPLE_DEVICE]
        flow.hass.data = {}
        mock_cls, mock_api = _mock_api(discover_result=[])
        mock_api.disconnect.side_effect = OSError("disconnect fail")
        with patch.object(_cf, "MarstekUDPClient", mock_cls), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await flow._async_discover_devices()  # should not raise

    async def test_resume_client_fails_logs_warning(self):
        """client.connect() raises during resume → warning logged."""
        flow = _make_options_flow(devices=[_SAMPLE_DEVICE])
        flow._devices = [_SAMPLE_DEVICE]
        coordinator = MagicMock(spec=["api"])
        coordinator.api = AsyncMock()
        coordinator.api.connect.side_effect = OSError("reconnect failed")
        flow.hass.data = {DOMAIN: {"entry1": {_cf.DATA_COORDINATOR: coordinator}}}
        mock_cls, _ = _mock_api(discover_result=[])
        with patch.object(_cf, "MarstekUDPClient", mock_cls), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            await flow._async_discover_devices()  # should not raise


# ---------------------------------------------------------------------------
# CannotConnect
# ---------------------------------------------------------------------------

class TestCannotConnect:
    def test_is_exception(self):
        err = CannotConnect("msg")
        assert isinstance(err, Exception)
        assert str(err) == "msg"
