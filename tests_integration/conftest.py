"""Shared fixtures for integration tests using pytest-homeassistant-custom-component."""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

# Ensure project root is on sys.path so custom_components can be imported
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
DOMAIN = "marstek_local_api"


@pytest.fixture(scope="session")
def fixture_data() -> dict:
    """Raw fixture data for Venus A fw147."""
    path = FIXTURES_DIR / "Venus_A_fw147" / "all.json"
    data = json.loads(path.read_text())
    return {k: v for k, v in data.items() if not k.startswith("_")}


@pytest.fixture
def mock_api(fixture_data) -> AsyncMock:
    """Mock MarstekUDPClient returning Venus A fw147 fixture data."""
    api = AsyncMock()
    api.get_device_info = AsyncMock(return_value=fixture_data["device"])
    api.get_battery_status = AsyncMock(return_value=fixture_data["battery"])
    api.get_pv_status = AsyncMock(return_value=fixture_data["pv"])
    api.get_es_status = AsyncMock(return_value=None)  # ES absent — mirrors first-poll behaviour
    api.get_em_status = AsyncMock(return_value=fixture_data["em"])
    api.get_es_mode = AsyncMock(return_value={"mode": "Auto"})
    api.get_wifi_status = AsyncMock(return_value=fixture_data["wifi"])
    api.get_ble_status = AsyncMock(return_value=fixture_data["ble"])
    # get_command_stats is a sync method — must be MagicMock, not AsyncMock
    api.get_command_stats = MagicMock(return_value=None)
    return api


@pytest.fixture
def config_entry(hass) -> MockConfigEntry:
    """Config entry for Venus A single-device setup."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "192.168.0.104",
            "port": 50000,
            "device": "Venus A",
            "firmware": 147,
            "wifi_mac": "b4b024a2887a",
            "ble_mac": "bc2a33600dca",
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def setup_integration(hass, config_entry, mock_api) -> MockConfigEntry:
    """Set up the integration with a mocked UDP client; returns the config entry."""
    # HA's loader imports custom_components as a namespace package pointing only
    # at testing_config/custom_components/.  Extend __path__ and clear the
    # cached (empty) integration dict so HA re-scans on first lookup.
    import custom_components as _cc
    from homeassistant import loader as _loader

    _cc_path = str(_ROOT / "custom_components")
    if _cc_path not in _cc.__path__:
        _cc.__path__.append(_cc_path)

    # Drop the pre-populated empty cache so async_get_custom_components re-scans
    hass.data.pop(_loader.DATA_CUSTOM_COMPONENTS, None)

    with (
        patch("custom_components.marstek_local_api.MarstekUDPClient", return_value=mock_api),
        patch("custom_components.marstek_local_api.coordinator.asyncio.sleep"),
    ):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
    return config_entry
