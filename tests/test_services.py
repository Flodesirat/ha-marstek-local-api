"""Tests for services.py — all branches, service handlers, resolve/refresh helpers."""
from __future__ import annotations

import asyncio
from datetime import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from conftest import _load_integration_module

# ---------------------------------------------------------------------------
# Load modules
# ---------------------------------------------------------------------------
_services_mod = _load_integration_module("services")
_coordinator_mod = _load_integration_module("coordinator")
_const_mod = _load_integration_module("const")

_resolve_device_context = _services_mod._resolve_device_context
_refresh_after_write = _services_mod._refresh_after_write
_apply_local_mode_state = _services_mod._apply_local_mode_state
_async_refresh_entry = _services_mod._async_refresh_entry
_days_to_week_set = _services_mod._days_to_week_set
async_setup_services = _services_mod.async_setup_services
async_unload_services = _services_mod.async_unload_services
HomeAssistantError = _services_mod.HomeAssistantError

MarstekDataUpdateCoordinator = _coordinator_mod.MarstekDataUpdateCoordinator
MarstekMultiDeviceCoordinator = _coordinator_mod.MarstekMultiDeviceCoordinator

DOMAIN = _const_mod.DOMAIN
DATA_COORDINATOR = _const_mod.DATA_COORDINATOR
WEEKDAY_MAP = _const_mod.WEEKDAY_MAP
MAX_SCHEDULE_SLOTS = _const_mod.MAX_SCHEDULE_SLOTS
SERVICE_REQUEST_SYNC = _const_mod.SERVICE_REQUEST_SYNC
SERVICE_SET_MANUAL_SCHEDULE = _const_mod.SERVICE_SET_MANUAL_SCHEDULE
SERVICE_SET_MANUAL_SCHEDULES = _const_mod.SERVICE_SET_MANUAL_SCHEDULES
SERVICE_CLEAR_MANUAL_SCHEDULES = _const_mod.SERVICE_CLEAR_MANUAL_SCHEDULES
SERVICE_SET_PASSIVE_MODE = _const_mod.SERVICE_SET_PASSIVE_MODE
MODE_MANUAL = _const_mod.MODE_MANUAL
MODE_PASSIVE = _const_mod.MODE_PASSIVE


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_hass(domain_data: dict | None = None) -> MagicMock:
    hass = MagicMock()
    hass.data = {DOMAIN: domain_data} if domain_data is not None else {}
    hass.services = MagicMock()
    hass.services.has_service.return_value = False
    # Schedule coroutines so they don't leak as unawaited
    hass.async_create_task = lambda coro: asyncio.create_task(coro)
    return hass


def _make_single_coord() -> MagicMock:
    coord = MagicMock()
    coord.__class__ = MarstekDataUpdateCoordinator
    coord.data = {"mode": {}}
    coord.async_request_refresh = AsyncMock()
    coord.async_set_updated_data = MagicMock()
    coord.api = MagicMock()
    coord.api.set_es_mode = AsyncMock(return_value=True)
    return coord


def _make_multi_coord(macs: list[str] | None = None) -> MagicMock:
    macs = macs or ["aa:bb:cc:dd:ee:ff"]
    multi = MagicMock()
    multi.__class__ = MarstekMultiDeviceCoordinator
    multi.data = {"aggregates": {}, "devices": {}}
    multi.async_request_refresh = AsyncMock()
    multi.async_set_updated_data = MagicMock()
    device_coords = {}
    for mac in macs:
        dc = _make_single_coord()
        device_coords[mac] = dc
    multi.device_coordinators = device_coords
    return multi


def _make_device_entry(
    config_entries: set | None = None,
    identifiers: list | None = None,
) -> MagicMock:
    entry = MagicMock()
    entry.config_entries = {"entry1"} if config_entries is None else config_entries
    entry.identifiers = [(DOMAIN, "aa:bb:cc:dd:ee:ff")] if identifiers is None else identifiers
    return entry


def _make_call(data: dict) -> MagicMock:
    call = MagicMock()
    call.data = data
    return call


def _make_registry(device_entry: MagicMock | None = None) -> MagicMock:
    reg = MagicMock()
    reg.async_get = MagicMock(return_value=device_entry)
    return reg


async def _setup_handlers(hass: MagicMock) -> dict:
    """Call async_setup_services and return {service_name: handler}."""
    handlers: dict = {}

    def _capture(domain, name, handler, **kwargs):
        handlers[name] = handler

    hass.services.async_register.side_effect = _capture
    await async_setup_services(hass)
    return handlers


# ---------------------------------------------------------------------------
# _days_to_week_set
# ---------------------------------------------------------------------------

class TestDaysToWeekSet:
    def test_single_day(self):
        assert _days_to_week_set(["mon"]) == 1

    def test_multiple_days(self):
        assert _days_to_week_set(["mon", "wed", "fri"]) == 1 + 4 + 16

    def test_all_days(self):
        expected = sum(WEEKDAY_MAP.values())
        assert _days_to_week_set(list(WEEKDAY_MAP.keys())) == expected

    def test_empty(self):
        assert _days_to_week_set([]) == 0


# ---------------------------------------------------------------------------
# _resolve_device_context
# ---------------------------------------------------------------------------

class TestResolveDeviceContext:

    def _call(self, hass, device_entry=None, coordinator=None, entry_id="entry1",
              identifiers=None):
        """Helper to call _resolve_device_context with common mocks."""
        if device_entry is None:
            device_entry = _make_device_entry(
                config_entries={entry_id},
                identifiers=identifiers if identifiers is not None else [(DOMAIN, "aa:bb:cc:dd:ee:ff")],
            )
        registry = _make_registry(device_entry)
        domain_data = {}
        if coordinator is not None:
            domain_data[entry_id] = {DATA_COORDINATOR: coordinator}
        hass.data = {DOMAIN: domain_data}
        with patch.object(_services_mod.dr, "async_get", create=True, return_value=registry):
            return _resolve_device_context(hass, "dev1")

    def test_no_domain_data_raises(self):
        hass = MagicMock()
        hass.data = {}
        with pytest.raises(HomeAssistantError, match="no active entries"):
            _resolve_device_context(hass, "dev1")

    def test_unknown_device_id_raises(self):
        hass = MagicMock()
        # domain_data must be truthy to pass the "no active entries" guard
        hass.data = {DOMAIN: {"some_entry": {"coordinator": MagicMock()}}}
        registry = _make_registry(device_entry=None)
        with patch.object(_services_mod.dr, "async_get", create=True, return_value=registry):
            with pytest.raises(HomeAssistantError, match="Unknown device_id"):
                _resolve_device_context(hass, "dev1")

    def test_device_no_config_entries_raises(self):
        hass = MagicMock()
        hass.data = {DOMAIN: {"some_entry": {"coordinator": MagicMock()}}}
        device_entry = _make_device_entry(config_entries=set())
        registry = _make_registry(device_entry)
        with patch.object(_services_mod.dr, "async_get", create=True, return_value=registry):
            with pytest.raises(HomeAssistantError, match="not associated"):
                _resolve_device_context(hass, "dev1")

    def test_no_matching_entry_payload_raises(self):
        hass = MagicMock()
        # entry_id present in config_entries but not in domain_data
        device_entry = _make_device_entry(config_entries={"unknown_entry"})
        # domain_data must be truthy but must not contain "unknown_entry"
        hass.data = {DOMAIN: {"other_entry": {"coordinator": MagicMock()}}}
        registry = _make_registry(device_entry)
        with patch.object(_services_mod.dr, "async_get", create=True, return_value=registry):
            with pytest.raises(HomeAssistantError, match="not part of an active"):
                _resolve_device_context(hass, "dev1")

    def test_no_coordinator_in_payload_raises(self):
        hass = MagicMock()
        device_entry = _make_device_entry(config_entries={"entry1"})
        hass.data = {DOMAIN: {"entry1": {DATA_COORDINATOR: None}}}
        registry = _make_registry(device_entry)
        with patch.object(_services_mod.dr, "async_get", create=True, return_value=registry):
            with pytest.raises(HomeAssistantError, match="not part of an active"):
                _resolve_device_context(hass, "dev1")

    def test_single_device_coordinator_returns_directly(self):
        hass = MagicMock()
        coord = _make_single_coord()
        result = self._call(hass, coordinator=coord)
        assert result == (coord, None, None)

    def test_multi_device_no_domain_identifier_raises(self):
        hass = MagicMock()
        multi = _make_multi_coord()
        # identifiers contains no DOMAIN entry
        with pytest.raises(HomeAssistantError, match="lacks Marstek identifiers"):
            self._call(hass, coordinator=multi, identifiers=[("other_domain", "val")])

    def test_multi_device_system_identifier_raises(self):
        hass = MagicMock()
        multi = _make_multi_coord()
        with pytest.raises(HomeAssistantError, match="aggregate system"):
            self._call(hass, coordinator=multi, identifiers=[(DOMAIN, "system_abc")])

    def test_multi_device_direct_match(self):
        hass = MagicMock()
        mac = "aa:bb:cc:dd:ee:ff"
        multi = _make_multi_coord(macs=[mac])
        device_coord, agg_coord, identifier = self._call(
            hass, coordinator=multi, identifiers=[(DOMAIN, mac)]
        )
        assert device_coord is multi.device_coordinators[mac]
        assert agg_coord is multi
        assert identifier == mac

    def test_multi_device_case_insensitive_fallback(self):
        hass = MagicMock()
        mac = "AA:BB:CC:DD:EE:FF"
        multi = _make_multi_coord(macs=[mac])
        # identifier in lower case → fallback loop
        device_coord, agg_coord, identifier = self._call(
            hass, coordinator=multi, identifiers=[(DOMAIN, mac.lower())]
        )
        assert device_coord is multi.device_coordinators[mac]
        assert identifier == mac

    def test_multi_device_no_device_coord_raises(self):
        hass = MagicMock()
        multi = _make_multi_coord(macs=["bb:cc:dd:ee:ff:00"])
        with pytest.raises(HomeAssistantError, match="Could not find device coordinator"):
            self._call(hass, coordinator=multi, identifiers=[(DOMAIN, "totally_different_mac")])


# ---------------------------------------------------------------------------
# _refresh_after_write
# ---------------------------------------------------------------------------

class TestRefreshAfterWrite:

    async def test_refresh_success_no_aggregate(self):
        coord = _make_single_coord()
        await _refresh_after_write(coord, None)
        coord.async_request_refresh.assert_awaited_once()

    async def test_refresh_device_exception_is_caught(self):
        coord = _make_single_coord()
        coord.async_request_refresh.side_effect = Exception("fail")
        # Should not raise
        await _refresh_after_write(coord, None)

    async def test_refresh_with_aggregate(self):
        coord = _make_single_coord()
        agg = _make_multi_coord()
        await _refresh_after_write(coord, agg)
        coord.async_request_refresh.assert_awaited_once()
        agg.async_request_refresh.assert_awaited_once()

    async def test_refresh_aggregate_exception_is_caught(self):
        coord = _make_single_coord()
        agg = _make_multi_coord()
        agg.async_request_refresh.side_effect = Exception("agg fail")
        # Should not raise
        await _refresh_after_write(coord, agg)


# ---------------------------------------------------------------------------
# _apply_local_mode_state
# ---------------------------------------------------------------------------

class TestApplyLocalModeState:

    def test_apply_without_aggregate(self):
        coord = _make_single_coord()
        _apply_local_mode_state(coord, None, None, MODE_MANUAL)
        coord.async_set_updated_data.assert_called_once()
        data = coord.async_set_updated_data.call_args[0][0]
        assert data["mode"]["mode"] == MODE_MANUAL

    def test_apply_with_mode_payload(self):
        coord = _make_single_coord()
        _apply_local_mode_state(coord, None, None, MODE_PASSIVE, {"passive_cfg": {"power": 100}})
        data = coord.async_set_updated_data.call_args[0][0]
        assert "passive_cfg" in data["mode"]

    def test_apply_with_aggregate(self):
        coord = _make_single_coord()
        agg = _make_multi_coord()
        _apply_local_mode_state(coord, agg, "aa:bb:cc:dd:ee:ff", MODE_MANUAL)
        agg.async_set_updated_data.assert_called_once()
        agg_data = agg.async_set_updated_data.call_args[0][0]
        assert "aa:bb:cc:dd:ee:ff" in agg_data["devices"]

    def test_apply_no_existing_mode_data(self):
        coord = _make_single_coord()
        coord.data = {}  # no "mode" key
        _apply_local_mode_state(coord, None, None, MODE_MANUAL)
        data = coord.async_set_updated_data.call_args[0][0]
        assert data["mode"]["mode"] == MODE_MANUAL


# ---------------------------------------------------------------------------
# _async_refresh_entry
# ---------------------------------------------------------------------------

class TestAsyncRefreshEntry:

    async def test_no_coordinator(self):
        await _async_refresh_entry("e1", {DATA_COORDINATOR: None})
        # No exception — just logs and returns

    async def test_multi_device_coordinator(self):
        multi = _make_multi_coord(macs=["aa:bb:cc:dd:ee:ff"])
        await _async_refresh_entry("e1", {DATA_COORDINATOR: multi})
        multi.async_request_refresh.assert_awaited_once()
        multi.device_coordinators["aa:bb:cc:dd:ee:ff"].async_request_refresh.assert_awaited_once()

    async def test_single_device_coordinator(self):
        coord = _make_single_coord()
        await _async_refresh_entry("e1", {DATA_COORDINATOR: coord})
        coord.async_request_refresh.assert_awaited_once()

    async def test_unknown_coordinator_type(self):
        unknown = MagicMock()  # not MarstekDataUpdateCoordinator or Multi
        await _async_refresh_entry("e1", {DATA_COORDINATOR: unknown})
        # Falls to else branch — logs but no exception


# ---------------------------------------------------------------------------
# async_setup_services and async_unload_services
# ---------------------------------------------------------------------------

class TestAsyncSetupServices:

    async def test_already_registered_returns_early(self):
        hass = _make_hass()
        hass.services.has_service.return_value = True
        await async_setup_services(hass)
        hass.services.async_register.assert_not_called()

    async def test_registers_all_five_services(self):
        hass = _make_hass()
        await async_setup_services(hass)
        registered_names = {
            call[0][1]  # second positional arg
            for call in hass.services.async_register.call_args_list
        }
        assert SERVICE_REQUEST_SYNC in registered_names
        assert SERVICE_SET_MANUAL_SCHEDULE in registered_names
        assert SERVICE_SET_MANUAL_SCHEDULES in registered_names
        assert SERVICE_CLEAR_MANUAL_SCHEDULES in registered_names
        assert SERVICE_SET_PASSIVE_MODE in registered_names


class TestAsyncUnloadServices:

    async def test_unload_all_registered(self):
        hass = _make_hass()
        hass.services.has_service.return_value = True
        await async_unload_services(hass)
        assert hass.services.async_remove.call_count == 5

    async def test_unload_none_registered(self):
        hass = _make_hass()
        hass.services.has_service.return_value = False
        await async_unload_services(hass)
        hass.services.async_remove.assert_not_called()


# ---------------------------------------------------------------------------
# _async_request_sync handler
# ---------------------------------------------------------------------------

class TestRequestSyncHandler:

    async def _get_handler(self, hass):
        handlers = await _setup_handlers(hass)
        return handlers[SERVICE_REQUEST_SYNC]

    async def test_no_domain_data_returns_silently(self):
        hass = _make_hass()
        handler = await self._get_handler(hass)
        hass.data = {}  # remove DOMAIN key
        await handler(_make_call({}))  # should not raise

    async def test_device_id_unknown_device_raises(self):
        hass = _make_hass(domain_data={"entry1": {DATA_COORDINATOR: _make_single_coord()}})
        handler = await self._get_handler(hass)
        registry = _make_registry(device_entry=None)
        with patch.object(_services_mod.dr, "async_get", create=True, return_value=registry):
            with pytest.raises(HomeAssistantError, match="Unknown device_id"):
                await handler(_make_call({"device_id": "dev1"}))

    async def test_device_id_no_config_entries_raises(self):
        hass = _make_hass(domain_data={"entry1": {DATA_COORDINATOR: _make_single_coord()}})
        handler = await self._get_handler(hass)
        device_entry = _make_device_entry(config_entries=set())
        registry = _make_registry(device_entry)
        with patch.object(_services_mod.dr, "async_get", create=True, return_value=registry):
            with pytest.raises(HomeAssistantError, match="not associated"):
                await handler(_make_call({"device_id": "dev1"}))

    async def test_device_id_refreshes_entry(self):
        coord = _make_single_coord()
        hass = _make_hass(domain_data={"entry1": {DATA_COORDINATOR: coord}})
        handler = await self._get_handler(hass)
        device_entry = _make_device_entry(config_entries={"entry1"})
        registry = _make_registry(device_entry)
        with patch.object(_services_mod.dr, "async_get", create=True, return_value=registry):
            await handler(_make_call({"device_id": "dev1"}))
        coord.async_request_refresh.assert_awaited()

    async def test_device_id_not_in_active_entry_raises(self):
        # domain_data must be truthy but must not contain "entry_missing"
        hass = _make_hass(domain_data={"other_entry": {DATA_COORDINATOR: _make_single_coord()}})
        handler = await self._get_handler(hass)
        device_entry = _make_device_entry(config_entries={"entry_missing"})
        registry = _make_registry(device_entry)
        with patch.object(_services_mod.dr, "async_get", create=True, return_value=registry):
            with pytest.raises(HomeAssistantError, match="not part of an active"):
                await handler(_make_call({"device_id": "dev1"}))

    async def test_entry_id_unknown_logs_warning(self):
        # domain_data must be truthy but must not contain "missing_entry"
        hass = _make_hass(domain_data={"other_entry": {DATA_COORDINATOR: _make_single_coord()}})
        handler = await self._get_handler(hass)
        await handler(_make_call({"entry_id": "missing_entry"}))  # no exception

    async def test_entry_id_refreshes_coordinator(self):
        coord = _make_single_coord()
        hass = _make_hass(domain_data={"entry1": {DATA_COORDINATOR: coord}})
        handler = await self._get_handler(hass)
        await handler(_make_call({"entry_id": "entry1"}))
        coord.async_request_refresh.assert_awaited()

    async def test_no_filter_refreshes_all_entries(self):
        coord1 = _make_single_coord()
        coord2 = _make_single_coord()
        hass = _make_hass(domain_data={
            "e1": {DATA_COORDINATOR: coord1},
            "e2": {DATA_COORDINATOR: coord2},
        })
        handler = await self._get_handler(hass)
        await handler(_make_call({}))
        coord1.async_request_refresh.assert_awaited()
        coord2.async_request_refresh.assert_awaited()


# ---------------------------------------------------------------------------
# _async_set_manual_schedule handler
# ---------------------------------------------------------------------------

class TestSetManualScheduleHandler:

    async def _setup(self, coord):
        mac = "aa:bb:cc:dd:ee:ff"
        hass = _make_hass(domain_data={"entry1": {DATA_COORDINATOR: coord}})
        handlers = await _setup_handlers(hass)
        handler = handlers[SERVICE_SET_MANUAL_SCHEDULE]
        device_entry = _make_device_entry(
            config_entries={"entry1"}, identifiers=[(DOMAIN, mac)]
        )
        registry = _make_registry(device_entry)
        return hass, handler, registry, mac

    async def test_success(self):
        coord = _make_single_coord()
        hass, handler, registry, mac = await self._setup(coord)
        call_data = {
            "device_id": "dev1", "time_num": 0,
            "start_time": time(8, 0), "end_time": time(18, 0),
            "days": ["mon", "tue"], "power": -500, "enabled": True,
        }
        with patch.object(_services_mod.dr, "async_get", create=True, return_value=registry):
            await handler(_make_call(call_data))
        coord.api.set_es_mode.assert_awaited_once()
        coord.async_set_updated_data.assert_called_once()

    async def test_rejected_raises(self):
        coord = _make_single_coord()
        coord.api.set_es_mode = AsyncMock(return_value=False)
        hass, handler, registry, mac = await self._setup(coord)
        call_data = {
            "device_id": "dev1", "time_num": 1,
            "start_time": time(0, 0), "end_time": time(23, 59),
            "days": ["wed"], "power": 0, "enabled": False,
        }
        with patch.object(_services_mod.dr, "async_get", create=True, return_value=registry):
            with pytest.raises(HomeAssistantError, match="Failed to set manual schedule"):
                await handler(_make_call(call_data))

    async def test_api_exception_raises(self):
        coord = _make_single_coord()
        coord.api.set_es_mode = AsyncMock(side_effect=RuntimeError("boom"))
        hass, handler, registry, mac = await self._setup(coord)
        call_data = {
            "device_id": "dev1", "time_num": 2,
            "start_time": time(6, 0), "end_time": time(22, 0),
            "days": ["fri"], "power": 100, "enabled": True,
        }
        with patch.object(_services_mod.dr, "async_get", create=True, return_value=registry):
            with pytest.raises(HomeAssistantError, match="Failed to set manual schedule"):
                await handler(_make_call(call_data))


# ---------------------------------------------------------------------------
# _async_set_manual_schedules handler
# ---------------------------------------------------------------------------

class TestSetManualSchedulesHandler:

    def _make_schedule(self, time_num: int, success: bool | Exception = True):
        return {
            "time_num": time_num,
            "start_time": time(8, 0),
            "end_time": time(18, 0),
            "days": ["mon"],
            "power": 0,
            "enabled": True,
            "_success": success,  # used to configure the mock
        }

    async def _run(self, schedules_results: list):
        """
        schedules_results: list of True/False/Exception to configure set_es_mode.
        Returns (hass, raised_error_or_None).
        """
        call_count = 0
        results = schedules_results

        async def _set_es_mode(config):
            nonlocal call_count
            r = results[call_count]
            call_count += 1
            if isinstance(r, Exception):
                raise r
            return r

        coord = _make_single_coord()
        coord.api.set_es_mode = _set_es_mode

        mac = "aa:bb:cc:dd:ee:ff"
        hass = _make_hass(domain_data={"entry1": {DATA_COORDINATOR: coord}})
        handlers = await _setup_handlers(hass)
        handler = handlers[SERVICE_SET_MANUAL_SCHEDULES]
        device_entry = _make_device_entry(config_entries={"entry1"}, identifiers=[(DOMAIN, mac)])
        registry = _make_registry(device_entry)

        schedules = [
            {
                "time_num": i,
                "start_time": time(8, 0), "end_time": time(18, 0),
                "days": ["mon"], "power": 0, "enabled": True,
            }
            for i in range(len(schedules_results))
        ]

        error = None
        with patch.object(_services_mod.dr, "async_get", create=True, return_value=registry):
            with patch.object(_services_mod.asyncio, "sleep", AsyncMock()):
                try:
                    await handler(_make_call({"device_id": "dev1", "schedules": schedules}))
                except HomeAssistantError as e:
                    error = e
        return coord, error

    async def test_all_success(self):
        coord, error = await self._run([True, True])
        assert error is None
        coord.async_set_updated_data.assert_called()

    async def test_some_rejected_raises(self):
        coord, error = await self._run([True, False])
        assert error is not None
        assert "Failed to set schedules" in str(error)

    async def test_exception_in_slot_raises(self):
        coord, error = await self._run([RuntimeError("fail"), True])
        assert error is not None

    async def test_all_fail_no_apply(self):
        coord, error = await self._run([False, False])
        # any_success=False → _apply_local_mode_state NOT called
        coord.async_set_updated_data.assert_not_called()
        assert error is not None


# ---------------------------------------------------------------------------
# _async_clear_manual_schedules handler
# ---------------------------------------------------------------------------

class TestClearManualSchedulesHandler:

    async def _run(self, results: list):
        """results: list of True/False/Exception, length = MAX_SCHEDULE_SLOTS."""
        call_count = 0

        async def _set_es_mode(config):
            nonlocal call_count
            r = results[call_count % len(results)]
            call_count += 1
            if isinstance(r, Exception):
                raise r
            return r

        coord = _make_single_coord()
        coord.api.set_es_mode = _set_es_mode

        mac = "aa:bb:cc:dd:ee:ff"
        hass = _make_hass(domain_data={"entry1": {DATA_COORDINATOR: coord}})
        handlers = await _setup_handlers(hass)
        handler = handlers[SERVICE_CLEAR_MANUAL_SCHEDULES]
        device_entry = _make_device_entry(config_entries={"entry1"}, identifiers=[(DOMAIN, mac)])
        registry = _make_registry(device_entry)

        error = None
        with patch.object(_services_mod.dr, "async_get", create=True, return_value=registry):
            with patch.object(_services_mod.asyncio, "sleep", AsyncMock()):
                try:
                    await handler(_make_call({"device_id": "dev1"}))
                except HomeAssistantError as e:
                    error = e
        return coord, error

    async def test_all_success(self):
        coord, error = await self._run([True])
        assert error is None
        coord.async_set_updated_data.assert_called()

    async def test_some_rejected_raises(self):
        # Alternate success/fail across 10 slots
        results = [True, False] * (MAX_SCHEDULE_SLOTS // 2)
        coord, error = await self._run(results)
        assert error is not None
        assert "Failed to clear schedules" in str(error)

    async def test_exception_in_slot_raises(self):
        results = [RuntimeError("err")] + [True] * (MAX_SCHEDULE_SLOTS - 1)
        coord, error = await self._run(results)
        assert error is not None

    async def test_all_fail_no_apply(self):
        coord, error = await self._run([False])
        coord.async_set_updated_data.assert_not_called()
        assert error is not None


# ---------------------------------------------------------------------------
# _async_set_passive_mode handler
# ---------------------------------------------------------------------------

class TestSetPassiveModeHandler:

    async def _setup(self, coord):
        mac = "aa:bb:cc:dd:ee:ff"
        hass = _make_hass(domain_data={"entry1": {DATA_COORDINATOR: coord}})
        handlers = await _setup_handlers(hass)
        handler = handlers[SERVICE_SET_PASSIVE_MODE]
        device_entry = _make_device_entry(
            config_entries={"entry1"}, identifiers=[(DOMAIN, mac)]
        )
        registry = _make_registry(device_entry)
        return handler, registry

    async def test_success(self):
        coord = _make_single_coord()
        handler, registry = await self._setup(coord)
        with patch.object(_services_mod.dr, "async_get", create=True, return_value=registry):
            await handler(_make_call({"device_id": "dev1", "power": -500, "duration": 3600}))
        coord.api.set_es_mode.assert_awaited_once()
        coord.async_set_updated_data.assert_called_once()

    async def test_rejected_raises(self):
        coord = _make_single_coord()
        coord.api.set_es_mode = AsyncMock(return_value=False)
        handler, registry = await self._setup(coord)
        with patch.object(_services_mod.dr, "async_get", create=True, return_value=registry):
            with pytest.raises(HomeAssistantError, match="Failed to set passive mode"):
                await handler(_make_call({"device_id": "dev1", "power": 0, "duration": 60}))

    async def test_exception_raises(self):
        coord = _make_single_coord()
        coord.api.set_es_mode = AsyncMock(side_effect=RuntimeError("timeout"))
        handler, registry = await self._setup(coord)
        with patch.object(_services_mod.dr, "async_get", create=True, return_value=registry):
            with pytest.raises(HomeAssistantError, match="Failed to set passive mode"):
                await handler(_make_call({"device_id": "dev1", "power": 200, "duration": 120}))
