"""Tests for number.py (Drive grid power setpoint)."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

from tests.conftest import _load_integration_module

sys.modules.pop("custom_components.marstek_local_api.number", None)

_const_mod = _load_integration_module("const")
_coordinator_mod = _load_integration_module("coordinator")
_number_mod = _load_integration_module("number")

MarstekDriveGridPowerNumber = _number_mod.MarstekDriveGridPowerNumber
async_setup_entry = _number_mod.async_setup_entry
MarstekMultiDeviceCoordinator = _coordinator_mod.MarstekMultiDeviceCoordinator

DOMAIN = _const_mod.DOMAIN
DATA_COORDINATOR = _const_mod.DATA_COORDINATOR

_MAC = "aabbccddeeff"


def _make_dev_coord():
    coord = MagicMock()
    coord.data = {"battery": {}}
    drive = MagicMock()
    drive.min_power = -2500
    drive.max_power = 1200
    drive.setpoint = 0
    drive.add_listener = MagicMock(return_value="unsub")
    coord.drive_controller = drive
    return coord


def _make_entry():
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {"ble_mac": _MAC, "wifi_mac": "112233445566", "device": "VenusA", "firmware": 147}
    return entry


def _make_number():
    coord = _make_dev_coord()
    return MarstekDriveGridPowerNumber(coord, coord, _MAC, {}), coord


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
        assert entities[0]._attr_unique_id == f"{_MAC}_drive_grid_power"
        assert entities[0]._attr_device_info["name"] == "Marstek VenusA"

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
        assert [e._attr_unique_id for e in entities] == [f"{m}_drive_grid_power" for m in macs]
        assert entities[1].device_coordinator is coord.device_coordinators[macs[1]]
        assert entities[1]._attr_device_info["name"] == "Marstek VenusE 4455"


class TestNumber:
    def test_attributes(self):
        number, _ = _make_number()
        assert number._attr_native_unit_of_measurement == "W"
        assert number._attr_device_class == "power"
        assert number._attr_mode == "box"
        assert (number._attr_native_min_value, number._attr_native_max_value) == (-2500, 1200)

    def test_value_is_the_controller_setpoint(self):
        number, coord = _make_number()
        coord.drive_controller.setpoint = -800
        assert number.native_value == -800

    def test_available(self):
        number, coord = _make_number()
        assert number.available
        coord.data = None
        assert not number.available

    async def test_set_value_goes_to_the_controller(self):
        number, coord = _make_number()
        await number.async_set_native_value(450.0)
        coord.drive_controller.set_setpoint.assert_called_once_with(450.0)

    async def test_follows_controller_updates(self):
        number, coord = _make_number()
        number.async_on_remove = MagicMock()
        number.async_write_ha_state = MagicMock()

        await number.async_added_to_hass()

        coord.drive_controller.add_listener.assert_called_once_with(number.async_write_ha_state)
        number.async_on_remove.assert_called_once_with("unsub")
