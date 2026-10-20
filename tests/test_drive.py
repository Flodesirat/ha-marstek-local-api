"""Tests for drive.py (Drive mode controller)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import _load_integration_module

_const_mod = _load_integration_module("const")
_api_mod = _load_integration_module("api")
_drive_mod = _load_integration_module("drive")

MarstekDriveController = _drive_mod.MarstekDriveController
drive_power_limits = _drive_mod.drive_power_limits
model_power_limits = _drive_mod.model_power_limits
MarstekCommandSuperseded = _api_mod.MarstekCommandSuperseded

_MAC = "aabbccddeeff"


def _drive_config(power):
    return {"mode": "Passive", "passive_cfg": {"power": power, "cd_time": 300}}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_device_coord(mode="Auto", model="VenusE"):
    coord = MagicMock()
    coord.device_model = model
    coord.data = {"battery": {}, "mode": {"mode": mode}}
    coord.category_last_updated = {}
    coord.api = MagicMock()
    coord.api.set_es_mode = AsyncMock(return_value=True)
    coord.async_add_listener = MagicMock(return_value=MagicMock())
    return coord


def _make_entry(options=None):
    entry = MagicMock()
    entry.options = options or {}
    entry.async_create_background_task = MagicMock(
        side_effect=lambda hass, coro, name: asyncio.ensure_future(coro)
    )
    return entry


def _make_controller(mode="Auto", model="VenusE", options=None, listen=None):
    coord = _make_device_coord(mode, model)
    controller = MarstekDriveController(MagicMock(), _make_entry(options), coord, listen or coord, _MAC)
    return controller, coord


async def _settle():
    """Let the send tasks run."""
    for _ in range(5):
        await asyncio.sleep(0)


def _poll(coord, mode, at):
    """Simulate an ES.GetMode poll reading ``mode``."""
    coord.data = {**coord.data, "mode": {"mode": mode}}
    coord.category_last_updated["mode"] = at


def _refresh_delay(controller):
    handle = controller._refresh_handle
    assert handle is not None
    return handle.when() - asyncio.get_running_loop().time()


# ===========================================================================
# Power limits
# ===========================================================================

class TestPowerLimits:
    @pytest.mark.parametrize(
        ("model", "expected"),
        [
            ("VenusE", (2500, 2500)),
            ("VenusE 3.0", (2500, 2500)),
            ("Venus A", (1500, 1500)),
            ("VenusC", (2500, 2500)),
            ("VenusD", (2200, 2200)),
            ("Unknown", (2500, 2500)),
            ("", (2500, 2500)),
        ],
    )
    def test_model_defaults(self, model, expected):
        assert model_power_limits(model) == expected

    def test_option_overrides_the_model(self):
        options = {"drive_power_limits": {_MAC: {"max_charge": 1000, "max_discharge": 800}}}
        assert drive_power_limits(options, _MAC, "VenusE") == (1000, 800)
        assert drive_power_limits(options, "other", "VenusE") == (2500, 2500)

    def test_controller_range(self):
        controller, _ = _make_controller(model="Venus A")
        assert (controller.min_power, controller.max_power) == (-1500, 1500)

    def test_controller_range_from_options(self):
        options = {"drive_power_limits": {_MAC: {"max_charge": 1500, "max_discharge": 600}}}
        controller, _ = _make_controller(options=options)
        assert (controller.min_power, controller.max_power) == (-1500, 600)


# ===========================================================================
# Entering / leaving Drive
# ===========================================================================

class TestStartStop:
    async def test_start_resets_to_zero_and_sends_with_cd_time(self):
        controller, coord = _make_controller()
        controller.setpoint = 500

        controller.start()
        await _settle()

        assert controller.active
        assert controller.setpoint == 0
        coord.api.set_es_mode.assert_awaited_once_with(_drive_config(0), priority=True)
        assert controller.accepted_power == 0
        controller.stop()

    async def test_accepted_command_updates_cache_without_resetting_polling(self):
        controller, coord = _make_controller()

        controller.start()
        await _settle()

        assert coord.data["mode"] == {"mode": "Passive", "passive_cfg": {"power": 0, "cd_time": 300}}
        coord.async_update_listeners.assert_called()
        coord.async_set_updated_data.assert_not_called()
        controller.stop()

    async def test_multi_device_updates_system_cache(self):
        system = MagicMock()
        system.data = {"devices": {_MAC: {}}, "aggregates": {"x": 1}}
        controller, coord = _make_controller(listen=system)

        controller.start()
        await _settle()

        assert system.data["devices"][_MAC]["mode"]["mode"] == "Passive"
        assert system.data["aggregates"] == {"x": 1}
        system.async_update_listeners.assert_called()
        controller.stop()

    async def test_stop_cancels_send_and_refresh(self):
        controller, coord = _make_controller()
        gate = asyncio.Event()

        async def slow_send(config, **kwargs):
            await gate.wait()
            return True

        coord.api.set_es_mode = AsyncMock(side_effect=slow_send)
        controller.start()
        await _settle()
        task = controller._send_task

        controller.stop()
        await _settle()

        assert task.cancelled()
        assert not controller.active
        assert controller._refresh_handle is None
        assert controller.accepted_power is None

    async def test_stop_cancels_refresh_timer(self):
        controller, _ = _make_controller()
        controller.start()
        await _settle()
        handle = controller._refresh_handle

        controller.stop()

        assert handle.cancelled()

    async def test_listeners_notified(self):
        controller, _ = _make_controller()
        listener = MagicMock()
        remove = controller.add_listener(listener)

        controller.start()
        assert listener.call_count == 1
        controller.stop()
        assert listener.call_count == 2
        controller.stop()  # Already stopped: no notification
        assert listener.call_count == 2

        remove()
        remove()  # Removing twice is harmless
        controller.set_setpoint(100)
        assert listener.call_count == 2


# ===========================================================================
# Sending rules
# ===========================================================================

class TestSendingRules:
    async def test_setpoint_ignored_while_inactive(self):
        controller, coord = _make_controller()

        controller.set_setpoint(400)
        await _settle()

        assert controller.setpoint == 400
        coord.api.set_es_mode.assert_not_called()

    @pytest.mark.parametrize("value", [29, -29, 10.4])
    async def test_small_change_waits_for_refresh(self, value):
        controller, coord = _make_controller()
        controller.start()
        await _settle()
        coord.api.set_es_mode.reset_mock()

        controller.set_setpoint(value)
        await _settle()

        coord.api.set_es_mode.assert_not_called()
        controller.stop()

    @pytest.mark.parametrize("value", [30, -30, 1000])
    async def test_change_of_30w_sent_at_once(self, value):
        controller, coord = _make_controller()
        controller.start()
        await _settle()
        coord.api.set_es_mode.reset_mock()

        controller.set_setpoint(value)
        await _settle()

        coord.api.set_es_mode.assert_awaited_once_with(_drive_config(value), priority=True)
        assert controller.accepted_power == value
        controller.stop()

    async def test_step_measured_from_last_accepted_value(self):
        controller, coord = _make_controller()
        controller.start()
        await _settle()
        controller.set_setpoint(20)
        controller.set_setpoint(40)  # 40 W away from the accepted 0 W
        await _settle()

        coord.api.set_es_mode.assert_awaited_with(_drive_config(40), priority=True)
        assert coord.api.set_es_mode.await_count == 2
        controller.stop()

    async def test_refresh_due_60s_after_accepted_command(self):
        controller, _ = _make_controller()
        controller.start()
        await _settle()

        assert 59 < _refresh_delay(controller) <= 60
        controller.stop()

    async def test_refresh_sends_exact_setpoint(self):
        controller, coord = _make_controller()
        controller.start()
        await _settle()
        controller.set_setpoint(20)
        await _settle()
        coord.api.set_es_mode.reset_mock()

        controller._cancel_refresh()  # Stands for the timer firing
        controller._refresh()
        await _settle()

        coord.api.set_es_mode.assert_awaited_once_with(_drive_config(20), priority=True)
        assert controller.accepted_power == 20
        assert 59 < _refresh_delay(controller) <= 60
        controller.stop()

    async def test_refresh_skipped_while_sending_or_inactive(self):
        controller, coord = _make_controller()
        controller._refresh()
        coord.api.set_es_mode.assert_not_called()

        controller.active = True
        controller._pending_power = 0
        controller._refresh()
        coord.api.set_es_mode.assert_not_called()

    async def test_setpoint_clamped_to_limits(self):
        controller, coord = _make_controller(model="Venus A")
        controller.start()
        await _settle()

        controller.set_setpoint(5000)
        await _settle()
        assert controller.setpoint == 1500
        controller.set_setpoint(-5000)
        await _settle()
        assert controller.setpoint == -1500
        coord.api.set_es_mode.assert_awaited_with(_drive_config(-1500), priority=True)
        controller.stop()

    @pytest.mark.parametrize("outcome", [False, Exception("timeout")])
    async def test_failed_command_retried_on_refresh(self, outcome):
        controller, coord = _make_controller()
        coord.api.set_es_mode = AsyncMock(side_effect=[outcome] if isinstance(outcome, Exception) else None,
                                          return_value=outcome)

        controller.start()
        await _settle()

        assert controller.accepted_power is None
        assert controller._pending_power is None
        assert 59 < _refresh_delay(controller) <= 60
        assert coord.data["mode"]["mode"] == "Auto"
        controller.stop()

    async def test_superseded_command_is_dropped(self):
        controller, coord = _make_controller()
        gate = asyncio.Event()
        calls = []

        async def set_es_mode(config, **kwargs):
            calls.append(config["passive_cfg"]["power"])
            if len(calls) == 1:
                await gate.wait()
                raise MarstekCommandSuperseded("replaced")
            return True

        coord.api.set_es_mode = AsyncMock(side_effect=set_es_mode)
        controller.start()
        await _settle()

        controller.set_setpoint(500)  # 500 W away from the 0 W being sent
        await _settle()
        gate.set()
        await _settle()

        assert calls == [0, 500]
        assert controller.accepted_power == 500
        assert controller._pending_power is None
        controller.stop()

    async def test_same_value_as_pending_not_resent(self):
        controller, coord = _make_controller()
        gate = asyncio.Event()

        async def slow_send(config, **kwargs):
            await gate.wait()
            return True

        coord.api.set_es_mode = AsyncMock(side_effect=slow_send)
        controller.start()
        await _settle()

        controller.set_setpoint(10)  # Within 30 W of the 0 W being sent
        await _settle()

        assert coord.api.set_es_mode.await_count == 1
        gate.set()
        await _settle()
        controller.stop()

    async def test_stale_answer_after_newer_send_is_ignored(self):
        controller, coord = _make_controller()
        controller.active = True
        controller._pending_power = 500

        await controller._async_send(0)  # An older send finishing late

        assert controller.accepted_power is None
        assert controller._pending_power == 500


# ===========================================================================
# Polling: mode kept up, Passive treated as Drive
# ===========================================================================

class TestPolling:
    async def test_setup_subscribes_and_reads_current_data(self):
        controller, coord = _make_controller(mode="Passive")
        _poll(coord, "Passive", 1.0)

        controller.async_setup()
        await _settle()

        coord.async_add_listener.assert_called_once_with(controller._handle_coordinator_update)
        assert controller.active
        coord.api.set_es_mode.assert_awaited_once_with(_drive_config(0), priority=True)

        controller.async_shutdown()
        coord.async_add_listener.return_value.assert_called_once()
        assert not controller.active
        controller.async_shutdown()  # Second shutdown is harmless

    async def test_other_mode_polled_in_drive_reimposes_passive(self):
        controller, coord = _make_controller()
        controller.start()
        await _settle()
        controller.set_setpoint(20)
        coord.api.set_es_mode.reset_mock()

        _poll(coord, "Manual", 1.0)
        controller._handle_coordinator_update()
        await _settle()

        coord.api.set_es_mode.assert_awaited_once_with(_drive_config(20), priority=True)
        controller.stop()

    async def test_cache_only_update_is_ignored(self):
        controller, coord = _make_controller()
        controller.start()
        await _settle()
        _poll(coord, "Passive", 1.0)
        controller._handle_coordinator_update()
        coord.api.set_es_mode.reset_mock()

        coord.data = {**coord.data, "mode": {"mode": "Manual"}}  # Same poll time
        controller._handle_coordinator_update()
        await _settle()

        coord.api.set_es_mode.assert_not_called()
        controller.stop()

    async def test_no_poll_yet_is_ignored(self):
        controller, coord = _make_controller(mode="Passive")
        controller._handle_coordinator_update()
        assert not controller.active

    async def test_other_mode_ignored_while_a_send_is_pending(self):
        controller, coord = _make_controller()
        controller.active = True
        controller._pending_power = 0

        _poll(coord, "Manual", 1.0)
        controller._handle_coordinator_update()
        await _settle()

        coord.api.set_es_mode.assert_not_called()

    async def test_polled_passive_activates_drive_with_current_setpoint(self):
        controller, coord = _make_controller()
        controller.set_setpoint(300)

        _poll(coord, "Passive", 1.0)
        controller._handle_coordinator_update()
        await _settle()

        assert controller.active
        assert controller.setpoint == 300
        coord.api.set_es_mode.assert_awaited_once_with(_drive_config(300), priority=True)
        controller.stop()

    async def test_passive_still_polled_after_stop_does_not_reactivate(self):
        controller, coord = _make_controller()
        _poll(coord, "Passive", 1.0)
        controller._handle_coordinator_update()
        await _settle()
        controller.stop()
        coord.api.set_es_mode.reset_mock()

        _poll(coord, "Passive", 2.0)
        controller._handle_coordinator_update()
        await _settle()

        assert not controller.active
        coord.api.set_es_mode.assert_not_called()

    async def test_other_modes_do_nothing_while_inactive(self):
        controller, coord = _make_controller()
        _poll(coord, "Auto", 1.0)
        controller._handle_coordinator_update()
        await _settle()

        assert not controller.active
        coord.api.set_es_mode.assert_not_called()
