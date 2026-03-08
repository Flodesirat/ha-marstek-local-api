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
        client = _make_client(command_timeout=2, command_max_attempts=1)
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
