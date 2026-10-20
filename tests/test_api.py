"""Tests for MarstekUDPClient (api.py) — no real sockets, no real HA."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from conftest import _load_integration_module

# ---------------------------------------------------------------------------
# Load modules under test
# ---------------------------------------------------------------------------
_api_mod = _load_integration_module("api")
_const_mod = _load_integration_module("const")

MarstekUDPClient = _api_mod.MarstekUDPClient
MarstekProtocol = _api_mod.MarstekProtocol
MarstekAPIError = _api_mod.MarstekAPIError

ERROR_METHOD_NOT_FOUND = _const_mod.ERROR_METHOD_NOT_FOUND
ALL_API_METHODS = _const_mod.ALL_API_METHODS
COMMAND_BACKOFF_BASE = _const_mod.COMMAND_BACKOFF_BASE
COMMAND_BACKOFF_MAX = _const_mod.COMMAND_BACKOFF_MAX
COMMAND_BACKOFF_JITTER = _const_mod.COMMAND_BACKOFF_JITTER


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(**kwargs) -> MarstekUDPClient:
    """Create a client with a fake hass and fast timeouts."""
    defaults = dict(
        hass=MagicMock(),
        host="192.168.1.100",
        port=30000,
        command_timeout=1,
        command_max_attempts=1,
    )
    defaults.update(kwargs)
    return MarstekUDPClient(**defaults)


def _make_mock_transport() -> MagicMock:
    transport = MagicMock()
    transport.sendto = MagicMock()
    transport.close = MagicMock()
    transport.get_extra_info = MagicMock(return_value=None)
    return transport


def _inject_connected(client: MarstekUDPClient, port: int | None = None) -> MagicMock:
    """Inject a mock transport and mark the client as connected."""
    port = port or client.port
    transport = _make_mock_transport()
    protocol = MarstekProtocol()
    protocol.port = port

    _api_mod._shared_transports[port] = transport
    _api_mod._shared_protocols[port] = protocol
    _api_mod._transport_refcounts[port] = 1
    _api_mod._clients_by_port[port] = [client]

    client.transport = transport
    client.protocol = protocol
    client._connected = True
    return transport


@pytest.fixture(autouse=True)
def _clean_globals():
    """Reset module-level globals between tests."""
    yield
    for d in (
        _api_mod._shared_transports,
        _api_mod._shared_protocols,
        _api_mod._transport_refcounts,
        _api_mod._clients_by_port,
    ):
        d.clear()


# ---------------------------------------------------------------------------
# Backoff delay
# ---------------------------------------------------------------------------

class TestBackoffDelay:

    def test_first_attempt_equals_base(self):
        """Attempt 1 → base × factor^0 = BACKOFF_BASE (no jitter)."""
        client = _make_client()
        with patch("random.uniform", return_value=0):
            delay = client._compute_backoff_delay(1)
        assert delay == pytest.approx(COMMAND_BACKOFF_BASE)

    def test_delay_grows_with_attempts(self):
        client = _make_client()
        with patch("random.uniform", return_value=0):
            d1 = client._compute_backoff_delay(1)
            d2 = client._compute_backoff_delay(2)
            d3 = client._compute_backoff_delay(3)
        assert d1 < d2 < d3

    def test_delay_capped_at_max(self):
        """Very high attempt → delay ≤ BACKOFF_MAX + jitter."""
        client = _make_client()
        with patch("random.uniform", return_value=COMMAND_BACKOFF_JITTER):
            delay = client._compute_backoff_delay(100)
        assert delay <= COMMAND_BACKOFF_MAX + COMMAND_BACKOFF_JITTER + 1e-6

    def test_jitter_is_added(self):
        """Jitter is included in the returned delay."""
        client = _make_client()
        with patch("random.uniform", return_value=0.3) as mock_rand:
            delay = client._compute_backoff_delay(1)
        mock_rand.assert_called_once()
        assert delay > COMMAND_BACKOFF_BASE


# ---------------------------------------------------------------------------
# Command statistics
# ---------------------------------------------------------------------------

class TestCommandStats:

    def test_success_increments_counts(self):
        client = _make_client()
        client._record_command_result(
            "Bat.GetStatus", success=True, attempt=1, latency=0.1, timeout=False, error=None
        )
        s = client.get_command_stats("Bat.GetStatus")
        assert s["total_attempts"] == 1
        assert s["total_success"] == 1
        assert s["total_timeouts"] == 0
        assert s["total_failures"] == 0
        assert s["last_success"] is True
        assert s["supported"] is True

    def test_timeout_increments_timeout_count(self):
        client = _make_client()
        client._record_command_result(
            "Bat.GetStatus", success=False, attempt=1, latency=None, timeout=True, error="timeout"
        )
        s = client.get_command_stats("Bat.GetStatus")
        assert s["total_timeouts"] == 1
        assert s["total_success"] == 0
        assert s["last_timeout"] is True
        assert s["supported"] is None  # Unknown: timeout ≠ unsupported

    def test_failure_increments_failure_count(self):
        client = _make_client()
        client._record_command_result(
            "Bat.GetStatus", success=False, attempt=1, latency=None, timeout=False, error="oops"
        )
        s = client.get_command_stats("Bat.GetStatus")
        assert s["total_failures"] == 1
        assert s["last_error"] == "oops"

    def test_method_not_found_unsupported_after_two_errors(self):
        client = _make_client()
        client._record_command_result(
            "X.Method", success=False, attempt=1, latency=None,
            timeout=False, error="not found", error_code=ERROR_METHOD_NOT_FOUND,
        )
        assert client.get_command_stats("X.Method")["supported"] is None  # Only 1 error

        client._record_command_result(
            "X.Method", success=False, attempt=2, latency=None,
            timeout=False, error="not found", error_code=ERROR_METHOD_NOT_FOUND,
        )
        assert client.get_command_stats("X.Method")["supported"] is False  # ≥2 errors

    def test_get_command_stats_unknown_method_returns_none(self):
        assert _make_client().get_command_stats("never.called") is None

    def test_get_all_command_stats_includes_every_method(self):
        client = _make_client()
        all_stats = client.get_all_command_stats()
        for method in ALL_API_METHODS:
            assert method in all_stats
            assert all_stats[method]["total_attempts"] == 0

    def test_success_stores_payload(self):
        client = _make_client()
        payload = {"result": {"soc": 80}}
        client._record_command_result(
            "Bat.GetStatus", success=True, attempt=1, latency=0.05,
            timeout=False, error=None, response=payload,
        )
        assert client.get_command_stats("Bat.GetStatus")["last_success_payload"] == payload

    def test_multiple_attempts_accumulate(self):
        client = _make_client()
        for i in range(1, 3):
            client._record_command_result(
                "Bat.GetStatus", success=False, attempt=i, latency=None, timeout=True, error="timeout"
            )
        client._record_command_result(
            "Bat.GetStatus", success=True, attempt=3, latency=0.1, timeout=False, error=None
        )
        s = client.get_command_stats("Bat.GetStatus")
        assert s["total_attempts"] == 3
        assert s["total_success"] == 1
        assert s["total_timeouts"] == 2


# ---------------------------------------------------------------------------
# Handler registration
# ---------------------------------------------------------------------------

class TestHandlerRegistration:

    def test_register_handler(self):
        client = _make_client()
        h = lambda msg, addr: None
        client.register_handler(h)
        assert h in client._handlers

    def test_register_same_handler_twice_deduplicates(self):
        client = _make_client()
        h = lambda msg, addr: None
        client.register_handler(h)
        client.register_handler(h)
        assert client._handlers.count(h) == 1

    def test_unregister_handler(self):
        client = _make_client()
        h = lambda msg, addr: None
        client.register_handler(h)
        client.unregister_handler(h)
        assert h not in client._handlers

    def test_unregister_unknown_handler_no_error(self):
        """Unregistering a handler that was never registered must not raise."""
        _make_client().unregister_handler(lambda msg, addr: None)


# ---------------------------------------------------------------------------
# _handle_message
# ---------------------------------------------------------------------------

class TestHandleMessage:

    async def test_valid_json_calls_handler(self):
        client = _make_client()
        received = []
        client.register_handler(lambda msg, addr: received.append((msg, addr)))
        msg = {"id": 1, "result": {"soc": 80}}
        await client._handle_message(json.dumps(msg).encode(), ("192.168.1.1", 30000))
        assert len(received) == 1
        assert received[0][0] == msg

    async def test_invalid_json_does_not_raise(self):
        client = _make_client()
        await client._handle_message(b"not json {{{", ("192.168.1.1", 30000))

    async def test_async_handler_is_awaited(self):
        client = _make_client()
        called = []

        async def async_handler(msg, addr):
            called.append(msg)

        client.register_handler(async_handler)
        msg = {"id": 2, "result": {}}
        await client._handle_message(json.dumps(msg).encode(), ("192.168.1.1", 30000))
        assert len(called) == 1

    async def test_failing_handler_does_not_stop_other_handlers(self):
        client = _make_client()
        good = []

        def bad_handler(msg, addr):
            raise RuntimeError("boom")

        client.register_handler(bad_handler)
        client.register_handler(lambda msg, addr: good.append(msg))
        msg = {"id": 3, "result": {}}
        await client._handle_message(json.dumps(msg).encode(), ("1.2.3.4", 30000))
        assert len(good) == 1


# ---------------------------------------------------------------------------
# send_command
# ---------------------------------------------------------------------------

class TestSendCommand:
    """Tests for send_command using mock transports that auto-respond."""

    def _setup_with_result(self, result: dict, addr=("192.168.1.100", 30000)):
        """Client whose transport auto-responds with a success result."""
        client = _make_client(command_timeout=2, command_max_attempts=1, command_min_interval=0.0)
        transport = _inject_connected(client)

        async def fake_sendto(data, dest):
            msg = json.loads(data.decode())
            response = {"id": msg["id"], "result": result}
            await client._handle_message(json.dumps(response).encode(), addr)

        transport.sendto = MagicMock(
            side_effect=lambda data, dest: asyncio.create_task(fake_sendto(data, dest))
        )
        return client

    def _setup_with_error(self, error: dict, addr=("192.168.1.100", 30000)):
        """Client whose transport auto-responds with an error."""
        client = _make_client(command_timeout=2, command_max_attempts=1)
        transport = _inject_connected(client)

        async def fake_sendto(data, dest):
            msg = json.loads(data.decode())
            response = {"id": msg["id"], "error": error}
            await client._handle_message(json.dumps(response).encode(), addr)

        transport.sendto = MagicMock(
            side_effect=lambda data, dest: asyncio.create_task(fake_sendto(data, dest))
        )
        return client

    async def test_successful_command_returns_result(self):
        client = self._setup_with_result({"soc": 75})
        result = await client.send_command("Bat.GetStatus", {"id": 0})
        assert result == {"soc": 75}

    async def test_successful_command_records_stats(self):
        client = self._setup_with_result({"soc": 75})
        await client.send_command("Bat.GetStatus", {"id": 0})
        s = client.get_command_stats("Bat.GetStatus")
        assert s["total_success"] == 1
        assert s["last_success"] is True

    async def test_api_error_raises_marstek_api_error(self):
        client = self._setup_with_error({"code": ERROR_METHOD_NOT_FOUND, "message": "Method not found"})
        with pytest.raises(MarstekAPIError, match="Method not found"):
            await client.send_command("Unknown.Method", {"id": 0})

    async def test_float_command_max_attempts_does_not_crash(self):
        """Regression test for #10.

        Saving the options form via HA's NumberSelector previously stored
        command_max_attempts as a float (e.g. 3.0). `range(1, attempt_limit + 1)`
        raises `TypeError: 'float' object cannot be interpreted as an integer`
        for any non-integer attempt_limit, breaking every command.
        """
        client = self._setup_with_result({"soc": 75})
        client.command_max_attempts = 3.0
        result = await client.send_command("Bat.GetStatus", {"id": 0})
        assert result == {"soc": 75}

    async def test_float_max_attempts_argument_does_not_crash(self):
        """Same as above, but passing the float directly as max_attempts."""
        client = self._setup_with_result({"soc": 75})
        result = await client.send_command("Bat.GetStatus", {"id": 0}, max_attempts=3.0)
        assert result == {"soc": 75}

    async def test_timeout_returns_none(self):
        """No response → all attempts time out → returns None."""
        client = _make_client(command_timeout=0.05, command_max_attempts=1)
        _inject_connected(client)
        with patch.object(_api_mod.asyncio, "sleep", new_callable=AsyncMock):
            result = await client.send_command("Bat.GetStatus", {"id": 0})
        assert result is None

    async def test_timeout_records_timeout_stats(self):
        client = _make_client(command_timeout=0.05, command_max_attempts=1)
        _inject_connected(client)
        with patch.object(_api_mod.asyncio, "sleep", new_callable=AsyncMock):
            await client.send_command("Bat.GetStatus", {"id": 0})
        assert client.get_command_stats("Bat.GetStatus")["total_timeouts"] == 1

    async def test_ignores_response_from_wrong_host(self):
        """Response from a different IP must not unblock the wait."""
        client = _make_client(host="192.168.1.100", command_timeout=0.05, command_max_attempts=1)
        transport = _inject_connected(client)

        async def fake_sendto(data, dest):
            msg = json.loads(data.decode())
            # Reply from wrong host
            response = {"id": msg["id"], "result": {"soc": 50}}
            await client._handle_message(json.dumps(response).encode(), ("10.0.0.99", 30000))

        transport.sendto = MagicMock(
            side_effect=lambda d, dest: asyncio.create_task(fake_sendto(d, dest))
        )
        with patch.object(_api_mod.asyncio, "sleep", new_callable=AsyncMock):
            result = await client.send_command("Bat.GetStatus", {"id": 0})
        assert result is None  # Timed out — wrong host was ignored

    async def test_response_with_wrong_id_increments_stale_counter(self):
        client = _make_client(host="192.168.1.100", command_timeout=0.05, command_max_attempts=1)
        transport = _inject_connected(client)

        async def fake_sendto(data, dest):
            response = {"id": 99999, "result": {}}
            await client._handle_message(json.dumps(response).encode(), ("192.168.1.100", 30000))

        transport.sendto = MagicMock(
            side_effect=lambda d, dest: asyncio.create_task(fake_sendto(d, dest))
        )
        with patch.object(_api_mod.asyncio, "sleep", new_callable=AsyncMock):
            await client.send_command("Bat.GetStatus", {"id": 0})
        assert client._stale_message_counter > 0

    async def test_handler_unregistered_after_success(self):
        client = self._setup_with_result({})
        initial = len(client._handlers)
        await client.send_command("Bat.GetStatus", {"id": 0})
        assert len(client._handlers) == initial

    async def test_handler_unregistered_after_timeout(self):
        client = _make_client(command_timeout=0.05, command_max_attempts=1)
        _inject_connected(client)
        initial = len(client._handlers)
        with patch.object(_api_mod.asyncio, "sleep", new_callable=AsyncMock):
            await client.send_command("Bat.GetStatus", {"id": 0})
        assert len(client._handlers) == initial

    async def test_msg_id_increments_per_call(self):
        client = self._setup_with_result({})
        await client.send_command("Bat.GetStatus", {"id": 0})
        id_after_first = client._msg_id_counter
        await client.send_command("Bat.GetStatus", {"id": 0})
        assert client._msg_id_counter == id_after_first + 1

    async def test_default_params_used_when_none(self):
        """send_command with params=None should still send a valid payload."""
        client = self._setup_with_result({"ok": True})
        # If params defaults are not applied, sendto would fail — just check it succeeds
        result = await client.send_command("Bat.GetStatus")
        assert result == {"ok": True}


# ---------------------------------------------------------------------------
# connect / disconnect
# ---------------------------------------------------------------------------

class TestReconnectAndConnectionLost:

    async def test_reconnect_replaces_shared_transport(self):
        port = 39995
        c1, c2 = _make_client(port=port), _make_client(port=port)
        old = _inject_connected(c1, port)
        _api_mod._clients_by_port[port] = [c1, c2]
        _api_mod._transport_refcounts[port] = 2
        c2.transport, c2.protocol, c2._connected = c1.transport, c1.protocol, True
        new_t, new_p = _make_mock_transport(), MarstekProtocol()

        async def fake_endpoint(factory, **kwargs):
            return new_t, new_p

        loop = asyncio.get_event_loop()
        with patch.object(loop, "create_datagram_endpoint", side_effect=fake_endpoint):
            await c1.reconnect()

        old.close.assert_called_once()
        assert _api_mod._shared_transports[port] is new_t
        assert c1.transport is new_t and c2.transport is new_t
        assert c2.protocol is new_p
        assert _api_mod._transport_refcounts[port] == 2

    async def test_connection_lost_invalidates_clients(self):
        client = _make_client(port=39996)
        _inject_connected(client, 39996)
        client.protocol.connection_lost(None)
        assert client._connected is False
        assert client.transport is None
        assert 39996 not in _api_mod._shared_transports
        assert 39996 not in _api_mod._clients_by_port

    async def test_connection_lost_ignored_for_replaced_protocol(self):
        client = _make_client(port=39997)
        _inject_connected(client, 39997)
        stale = MarstekProtocol()
        stale.connection_lost(None)
        assert client._connected is True
        assert 39997 in _api_mod._shared_transports


class TestConnectDisconnect:

    async def test_connect_creates_shared_transport(self):
        client = _make_client(port=39991)
        mock_transport = _make_mock_transport()
        mock_protocol = MarstekProtocol()

        async def fake_endpoint(factory, **kwargs):
            return mock_transport, mock_protocol

        loop = asyncio.get_event_loop()
        with patch.object(loop, "create_datagram_endpoint", side_effect=fake_endpoint):
            await client.connect()

        assert client._connected is True
        assert _api_mod._transport_refcounts[39991] == 1
        assert client in _api_mod._clients_by_port[39991]

    async def test_connect_reuses_existing_shared_transport(self):
        """Second client on same port must reuse the existing socket."""
        port = 39992
        client1 = _make_client(port=port)
        client2 = _make_client(port=port)
        mock_transport = _make_mock_transport()
        mock_protocol = MarstekProtocol()
        call_count = 0

        async def fake_endpoint(factory, **kwargs):
            nonlocal call_count
            call_count += 1
            return mock_transport, mock_protocol

        loop = asyncio.get_event_loop()
        with patch.object(loop, "create_datagram_endpoint", side_effect=fake_endpoint):
            await client1.connect()
            await client2.connect()

        assert call_count == 1  # Socket created only once
        assert _api_mod._transport_refcounts[port] == 2
        assert client2 in _api_mod._clients_by_port[port]

    async def test_connect_already_connected_is_noop(self):
        client = _make_client(port=39993)
        _inject_connected(client, port=39993)
        initial_refcount = _api_mod._transport_refcounts[39993]
        await client.connect()
        assert _api_mod._transport_refcounts[39993] == initial_refcount

    async def test_disconnect_decrements_refcount(self):
        port = 39994
        client = _make_client(port=port)
        _inject_connected(client, port=port)
        _api_mod._transport_refcounts[port] = 2  # Simulate a second client

        await client.disconnect()

        assert _api_mod._transport_refcounts[port] == 1
        assert client._connected is False

    async def test_disconnect_last_client_closes_transport(self):
        port = 39995
        client = _make_client(port=port)
        transport = _inject_connected(client, port=port)

        await client.disconnect()

        transport.close.assert_called_once()
        assert port not in _api_mod._shared_transports

    async def test_disconnect_removes_client_from_registry(self):
        port = 39996
        client = _make_client(port=port)
        _inject_connected(client, port=port)

        await client.disconnect()

        assert client not in _api_mod._clients_by_port.get(port, [])

    async def test_disconnect_when_not_connected_is_noop(self):
        client = _make_client()
        await client.disconnect()  # Must not raise


# ---------------------------------------------------------------------------
# set_es_mode
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# command_min_interval — rate limiting and lock serialization
# ---------------------------------------------------------------------------

class TestCommandMinInterval:
    """Verify that send_command enforces a minimum inter-frame delay."""

    # ------------------------------------------------------------------
    # Configuration / storage
    # ------------------------------------------------------------------

    def test_default_value(self):
        """Default command_min_interval matches the const."""
        from conftest import _load_integration_module
        const = _load_integration_module("const")
        client = _make_client()
        assert client.command_min_interval == const.COMMAND_MIN_INTERVAL

    def test_custom_value_stored(self):
        """A custom command_min_interval is stored on the client."""
        client = _make_client(command_min_interval=2.0)
        assert client.command_min_interval == 2.0

    # ------------------------------------------------------------------
    # Rate-limiting sleep
    # ------------------------------------------------------------------

    async def test_rate_limiting_wait_applied_when_too_soon(self):
        """When last send was very recent, the frame waits for the next slot."""
        client = _make_client(command_min_interval=0.3, command_timeout=0.05, command_max_attempts=1)
        transport = _inject_connected(client)

        # Pretend a command was sent 0.1 s ago → 0.2 s still needed
        loop = asyncio.get_running_loop()
        client._last_send_time = loop.time() - 0.1
        started = loop.time()
        send_times = []
        transport.sendto = MagicMock(side_effect=lambda d, dest: send_times.append(loop.time()))

        await client.send_command("Bat.GetStatus")

        assert len(send_times) == 1
        assert send_times[0] - started == pytest.approx(0.2, abs=0.05)

    async def test_no_rate_limiting_sleep_when_interval_elapsed(self):
        """When the interval has already elapsed, no extra sleep is added."""
        client = _make_client(command_min_interval=0.5, command_timeout=0.05, command_max_attempts=1)
        _inject_connected(client)
        # _last_send_time = 0.0 by default; loop.time() is always >> 0.5 → no wait

        sleep_args = []

        async def capture(t):
            sleep_args.append(t)

        with patch.object(_api_mod.asyncio, "sleep", side_effect=capture):
            await client.send_command("Bat.GetStatus")

        positive = [t for t in sleep_args if t > 0]
        assert not positive, f"Unexpected rate-limiting sleep(s): {positive}"

    # ------------------------------------------------------------------
    # _last_send_time update
    # ------------------------------------------------------------------

    async def test_last_send_time_updated_after_successful_command(self):
        """_last_send_time is set to the moment of send after a successful command."""
        client = _make_client(command_min_interval=0.0)
        transport = _inject_connected(client)

        async def fake_sendto(data, dest):
            msg = json.loads(data.decode())
            response = {"id": msg["id"], "result": {"ok": True}}
            await client._handle_message(json.dumps(response).encode(), ("192.168.1.100", 30000))

        transport.sendto = MagicMock(
            side_effect=lambda data, dest: asyncio.create_task(fake_sendto(data, dest))
        )

        before = asyncio.get_running_loop().time()
        await client.send_command("Bat.GetStatus")
        after = asyncio.get_running_loop().time()

        assert before <= client._last_send_time <= after

    async def test_last_send_time_updated_even_on_timeout(self):
        """_last_send_time is also updated when the command times out."""
        client = _make_client(command_min_interval=0.0, command_timeout=0.05, command_max_attempts=1)
        _inject_connected(client)

        before = asyncio.get_running_loop().time()
        with patch.object(_api_mod.asyncio, "sleep", new_callable=AsyncMock):
            await client.send_command("Bat.GetStatus")
        after = asyncio.get_running_loop().time()

        assert before <= client._last_send_time <= after

    # ------------------------------------------------------------------
    # Lock lifecycle
    # ------------------------------------------------------------------

    async def test_lock_released_after_success(self):
        """The send lock is released after a successful command."""
        client = _make_client(command_min_interval=0.0)
        transport = _inject_connected(client)

        async def fake_sendto(data, dest):
            msg = json.loads(data.decode())
            response = {"id": msg["id"], "result": {}}
            await client._handle_message(json.dumps(response).encode(), ("192.168.1.100", 30000))

        transport.sendto = MagicMock(
            side_effect=lambda data, dest: asyncio.create_task(fake_sendto(data, dest))
        )

        await client.send_command("Bat.GetStatus")
        assert not client._send_lock.locked()

    async def test_lock_released_after_timeout(self):
        """The send lock is released even when the command times out."""
        client = _make_client(command_min_interval=0.0, command_timeout=0.05, command_max_attempts=1)
        _inject_connected(client)

        with patch.object(_api_mod.asyncio, "sleep", new_callable=AsyncMock):
            await client.send_command("Bat.GetStatus")

        assert not client._send_lock.locked()

    async def test_lock_released_after_api_error(self):
        """The send lock is released even when the device returns an error response."""
        client = _make_client(command_min_interval=0.0, command_timeout=2, command_max_attempts=1)
        transport = _inject_connected(client)

        async def fake_sendto(data, dest):
            msg = json.loads(data.decode())
            response = {"id": msg["id"], "error": {"code": -32601, "message": "Method not found"}}
            await client._handle_message(json.dumps(response).encode(), ("192.168.1.100", 30000))

        transport.sendto = MagicMock(
            side_effect=lambda data, dest: asyncio.create_task(fake_sendto(data, dest))
        )

        with pytest.raises(_api_mod.MarstekAPIError):
            await client.send_command("Unknown.Method")

        assert not client._send_lock.locked()

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    async def test_concurrent_commands_never_overlap(self):
        """Two concurrent send_command calls must never be in _send_to_host simultaneously."""
        client = _make_client(command_min_interval=0.0, command_timeout=0.05, command_max_attempts=1)
        _inject_connected(client)

        max_concurrent = [0]
        in_flight = [0]

        original_send = client._send_to_host

        async def tracking_send(payload):
            in_flight[0] += 1
            max_concurrent[0] = max(max_concurrent[0], in_flight[0])
            await original_send(payload)
            in_flight[0] -= 1

        client._send_to_host = tracking_send

        await asyncio.gather(
            client.send_command("Bat.GetStatus"),
            client.send_command("ES.GetStatus"),
            return_exceptions=True,
        )

        assert max_concurrent[0] == 1, (
            f"Commands overlapped: max {max_concurrent[0]} in flight at once"
        )

    async def test_sequential_commands_respect_min_interval(self):
        """Two sequential sends are separated by at least command_min_interval seconds."""
        min_interval = 0.1
        client = _make_client(command_min_interval=min_interval, command_timeout=0.05, command_max_attempts=1)
        _inject_connected(client)

        send_times = []
        original_send = client._send_to_host

        async def tracking_send(payload):
            send_times.append(asyncio.get_running_loop().time())
            await original_send(payload)

        client._send_to_host = tracking_send

        await client.send_command("Bat.GetStatus")
        await client.send_command("ES.GetStatus")

        assert len(send_times) == 2
        gap = send_times[1] - send_times[0]
        assert gap >= min_interval - 0.02, (
            f"Gap between sends ({gap:.3f}s) is less than min_interval ({min_interval}s)"
        )


# ---------------------------------------------------------------------------

class TestSetEsMode:

    async def test_returns_true_on_success(self):
        client = _make_client()
        with patch.object(client, "send_command", new_callable=AsyncMock, return_value={"set_result": True}):
            assert await client.set_es_mode({"mode": "Auto"}) is True

    async def test_returns_false_when_set_result_false(self):
        client = _make_client()
        with patch.object(client, "send_command", new_callable=AsyncMock, return_value={"set_result": False}):
            assert await client.set_es_mode({"mode": "Auto"}) is False

    async def test_returns_false_on_none_response(self):
        client = _make_client()
        with patch.object(client, "send_command", new_callable=AsyncMock, return_value=None):
            assert await client.set_es_mode({"mode": "Auto"}) is False

    async def test_priority_flag_forwarded(self):
        client = _make_client()
        with patch.object(client, "send_command", new_callable=AsyncMock, return_value={"set_result": True}) as send:
            assert await client.set_es_mode({"mode": "Passive"}, priority=True) is True
        assert send.await_args.kwargs["priority"] is True


# ---------------------------------------------------------------------------
# Send scheduling — high-priority queue (#15)
# ---------------------------------------------------------------------------

# Real timings scaled down: 1 s → 20 ms. The schedule is checked by shape.
_S = 0.02
_MIN_INTERVAL = 5 * _S
_TIMEOUT = 2 * _S
_TOL = 0.05
_HOST_ADDR = ("192.168.1.100", 30000)


class _FakeDevice:
    """Records every frame sent and replies through ``answer(msg)``."""

    def __init__(self, client: MarstekUDPClient, answer=None) -> None:
        self.client = client
        self.frames: list[tuple[float, dict]] = []
        self.answer = answer or (lambda msg: [])
        transport = _inject_connected(client)
        transport.sendto = MagicMock(side_effect=self._sendto)

    def _sendto(self, data, dest) -> None:
        msg = json.loads(data.decode())
        self.frames.append((asyncio.get_running_loop().time(), msg))
        for response in self.answer(msg):
            self.reply(response)

    def reply(self, response: dict) -> None:
        asyncio.create_task(
            self.client._handle_message(json.dumps(response).encode(), _HOST_ADDR)
        )

    def methods(self) -> list[str]:
        return [msg["method"] for _, msg in self.frames]

    def gaps(self) -> list[float]:
        times = [t for t, _ in self.frames]
        return [b - a for a, b in zip(times, times[1:])]

    async def wait_frames(self, count: int, limit: float = 5.0) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + limit
        while len(self.frames) < count:
            assert loop.time() < deadline, f"only {len(self.frames)} frame(s) sent"
            await asyncio.sleep(0.005)


def _answer_set_mode(msg: dict) -> list[dict]:
    """Device that answers ES.SetMode only."""
    if msg["method"] == "ES.SetMode":
        return [{"id": msg["id"], "result": {"set_result": True}}]
    return []


def _scheduling_client(max_attempts: int = 3) -> MarstekUDPClient:
    client = _make_client(
        command_min_interval=_MIN_INTERVAL,
        command_timeout=_TIMEOUT,
        command_max_attempts=max_attempts,
    )
    client.priority_retry_delays = tuple(d * _S for d in _const_mod.PRIORITY_RETRY_DELAYS)
    # Low-priority backoff shorter than the slot spacing, as with real values
    client._compute_backoff_delay = lambda attempt: 1.5 * _S
    return client


async def _cancel(task: asyncio.Task) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


class TestPriorityScheduling:

    def test_retry_schedule_constant(self):
        assert _const_mod.PRIORITY_RETRY_DELAYS == (5, 10, 15, 30, 60)

    async def test_priority_preempts_in_flight_polling(self):
        """A high-priority frame takes the next slot; polling resumes after it."""
        client = _scheduling_client(max_attempts=3)
        device = _FakeDevice(client, _answer_set_mode)

        low = asyncio.create_task(client.send_command("Bat.GetStatus"))
        await device.wait_frames(1)
        assert await client.set_es_mode({"mode": "Passive"}, priority=True) is True
        assert await low is None

        assert device.methods() == ["Bat.GetStatus", "ES.SetMode", "Bat.GetStatus", "Bat.GetStatus"]
        # Worst-case latency is one slot spacing
        assert device.gaps()[0] == pytest.approx(_MIN_INTERVAL, abs=_TOL)
        # The paused request kept its id and its attempt count
        low_ids = {msg["id"] for _, msg in device.frames if msg["method"] == "Bat.GetStatus"}
        assert len(low_ids) == 1
        assert client.get_command_stats("Bat.GetStatus")["total_timeouts"] == 3

    async def test_new_priority_frame_replaces_unsent_one(self):
        """Only the latest high-priority frame is sent; the replaced caller is told."""
        client = _scheduling_client(max_attempts=1)
        device = _FakeDevice(client, _answer_set_mode)

        low = asyncio.create_task(client.send_command("Bat.GetStatus"))
        await device.wait_frames(1)
        first = asyncio.create_task(client.set_es_mode({"mode": "Passive", "power": 100}, priority=True))
        await asyncio.sleep(0)
        second = asyncio.create_task(client.set_es_mode({"mode": "Passive", "power": 200}, priority=True))

        with pytest.raises(_api_mod.MarstekCommandSuperseded):
            await first
        assert await second is True
        await low

        assert device.methods() == ["Bat.GetStatus", "ES.SetMode"]
        assert device.frames[1][1]["params"]["config"]["power"] == 200

    async def test_replacement_restarts_retry_schedule(self):
        """A pending retry is replaced too, and the schedule restarts for the new frame."""
        client = _scheduling_client()
        device = _FakeDevice(client)  # Never answers

        first = asyncio.create_task(client.send_command("ES.SetMode", {"id": 0}, priority=True))
        await device.wait_frames(3)  # Sent, then retried after 5 s and 10 s
        second = asyncio.create_task(client.send_command("ES.SetMode", {"id": 0}, priority=True))
        with pytest.raises(_api_mod.MarstekCommandSuperseded):
            await first
        await device.wait_frames(6)
        await _cancel(second)

        ids = [msg["id"] for _, msg in device.frames]
        assert len(set(ids[:3])) == 1 and len(set(ids[3:])) == 1 and ids[0] != ids[3]
        # Replacement goes out at the next slot, then 5 s and 10 s again
        assert device.gaps()[2] == pytest.approx(_MIN_INTERVAL, abs=_TOL)
        assert device.gaps()[3:] == pytest.approx([5 * _S, 10 * _S], abs=_TOL)
        assert client._priority_frame is None

    async def test_priority_retry_schedule(self):
        """Retries after 5, 10, 15, 30 s, then every 60 s while unanswered."""
        client = _scheduling_client()
        device = _FakeDevice(client)  # Never answers

        task = asyncio.create_task(client.send_command("ES.SetMode", {"id": 0}, priority=True))
        await device.wait_frames(7, limit=10)
        await _cancel(task)

        assert device.gaps() == pytest.approx([d * _S for d in (5, 10, 15, 30, 60, 60)], abs=_TOL)
        assert client.get_command_stats("ES.SetMode")["total_timeouts"] >= 6

    async def test_min_interval_kept_between_all_frames(self):
        """Polling uses the slots between retries; every frame keeps the spacing."""
        client = _scheduling_client(max_attempts=2)
        device = _FakeDevice(client)  # Never answers

        high = asyncio.create_task(client.send_command("ES.SetMode", {"id": 0}, priority=True))
        await device.wait_frames(1)

        async def polling():
            for _ in range(3):
                await client.send_command("Bat.GetStatus")

        await asyncio.wait_for(polling(), timeout=10)
        await _cancel(high)

        methods = device.methods()
        assert methods.count("Bat.GetStatus") == 6
        assert all(gap >= _MIN_INTERVAL - 0.01 for gap in device.gaps()), device.gaps()
        # The high-priority retries stayed on schedule despite the polling
        high_times = [t for t, msg in device.frames if msg["method"] == "ES.SetMode"]
        high_gaps = [b - a for a, b in zip(high_times, high_times[1:])]
        expected = [d * _S for d in (5, 10, 15, 30, 60)][: len(high_gaps)]
        assert high_gaps == pytest.approx(expected, abs=_TOL)

    async def test_late_low_answer_still_matched(self):
        """A late answer to a paused polling request completes it without a resend."""
        client = _scheduling_client(max_attempts=3)
        low_id = []

        def answer(msg):
            if msg["method"] == "Bat.GetStatus":
                low_id.append(msg["id"])
                return []
            # The battery answers the old polling request during the high exchange
            return [{"id": low_id[0], "result": {"soc": 42}}] + _answer_set_mode(msg)

        device = _FakeDevice(client, answer)
        low = asyncio.create_task(client.send_command("Bat.GetStatus"))
        await device.wait_frames(1)
        assert await client.set_es_mode({"mode": "Passive"}, priority=True) is True

        assert await low == {"soc": 42}
        assert device.methods() == ["Bat.GetStatus", "ES.SetMode"]
        stats = client.get_command_stats("Bat.GetStatus")
        assert stats["last_success"] is True and stats["last_attempt"] == 1

    async def test_late_priority_answer_still_matched(self):
        """An answer to an earlier high-priority attempt stops the retries."""
        client = _scheduling_client()
        device = _FakeDevice(client)

        task = asyncio.create_task(client.send_command("ES.SetMode", {"id": 0}, priority=True))
        await device.wait_frames(1)
        await asyncio.sleep(_TIMEOUT + 0.01)  # First attempt timed out
        device.reply({"id": device.frames[0][1]["id"], "result": {"set_result": True}})

        assert await task == {"set_result": True}
        assert len(device.frames) == 1
        assert client._priority_frame is None

    async def test_replaced_frame_in_flight_still_gets_its_answer(self):
        """A frame already sent is not recalled: its answer is returned."""
        client = _scheduling_client()
        device = _FakeDevice(client)

        first = asyncio.create_task(client.send_command("ES.SetMode", {"id": 0}, priority=True))
        await device.wait_frames(1)
        second = asyncio.create_task(client.send_command("ES.SetMode", {"id": 0}, priority=True))
        await asyncio.sleep(0)
        device.reply({"id": device.frames[0][1]["id"], "result": {"set_result": True}})

        assert await first == {"set_result": True}
        await device.wait_frames(2)
        assert device.gaps()[0] >= _MIN_INTERVAL - 0.01
        await _cancel(second)

    async def test_priority_api_error_raises(self):
        """An error answer ends a high-priority request."""
        client = _scheduling_client()
        _FakeDevice(client, lambda msg: [{"id": msg["id"], "error": {"code": -32602, "message": "Invalid params"}}])

        with pytest.raises(MarstekAPIError, match="Invalid params"):
            await client.send_command("ES.SetMode", {"id": 0}, priority=True)
        assert client._priority_frame is None

    async def test_priority_send_error_is_retried(self):
        """A send failure is retried on the high-priority schedule."""
        client = _scheduling_client()
        _FakeDevice(client, _answer_set_mode)
        real_send = client._send_to_host
        calls = []

        async def flaky_send(payload):
            calls.append(payload)
            if len(calls) == 1:
                raise OSError("network unreachable")
            await real_send(payload)

        client._send_to_host = flaky_send
        assert await client.set_es_mode({"mode": "Passive"}, priority=True) is True
        assert len(calls) == 2
        assert client.get_command_stats("ES.SetMode")["total_failures"] == 1

    async def test_cancelled_priority_frame_frees_the_queue(self):
        """Cancelling the caller drops its frame; polling is no longer held back."""
        client = _scheduling_client(max_attempts=1)
        device = _FakeDevice(client)

        low = asyncio.create_task(client.send_command("Bat.GetStatus"))
        await device.wait_frames(1)
        high = asyncio.create_task(client.send_command("ES.SetMode", {"id": 0}, priority=True))
        await asyncio.sleep(0)
        await _cancel(high)
        await low
        await client.send_command("Bat.GetStatus")

        assert device.methods() == ["Bat.GetStatus", "Bat.GetStatus"]
        assert client._priority_frame is None and not client._in_flight
