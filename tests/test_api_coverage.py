"""Additional coverage tests for api.py — branches not covered by test_api.py."""
from __future__ import annotations

import asyncio
import json
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from conftest import _load_integration_module
from test_api import (
    MarstekAPIError,
    MarstekProtocol,
    MarstekUDPClient,
    _api_mod,
    _const_mod,
    _inject_connected,
    _make_client,
    _make_mock_transport,
)

ALL_API_METHODS = _const_mod.ALL_API_METHODS
METHOD_GET_DEVICE = _const_mod.METHOD_GET_DEVICE
METHOD_WIFI_STATUS = _const_mod.METHOD_WIFI_STATUS
METHOD_BLE_STATUS = _const_mod.METHOD_BLE_STATUS
METHOD_BATTERY_STATUS = _const_mod.METHOD_BATTERY_STATUS
METHOD_PV_STATUS = _const_mod.METHOD_PV_STATUS
METHOD_ES_STATUS = _const_mod.METHOD_ES_STATUS
METHOD_ES_MODE = _const_mod.METHOD_ES_MODE
METHOD_EM_STATUS = _const_mod.METHOD_EM_STATUS


# ---------------------------------------------------------------------------
# connect() — exception path (lines 139-144)
# ---------------------------------------------------------------------------

class TestConnectException:

    async def test_connect_propagates_create_endpoint_failure(self):
        """create_datagram_endpoint failure → exception is re-raised."""
        client = _make_client(port=39101)
        loop = asyncio.get_event_loop()
        with patch.object(loop, "create_datagram_endpoint", side_effect=OSError("bind failed")):
            with pytest.raises(OSError, match="bind failed"):
                await client.connect()
        assert not client._connected


# ---------------------------------------------------------------------------
# disconnect() — transport.close() exception (lines 163-164)
# ---------------------------------------------------------------------------

class TestDisconnectException:

    async def test_disconnect_close_exception_swallowed(self):
        """transport.close() raising must not prevent cleanup."""
        port = 39102
        client = _make_client(port=port)
        transport = _inject_connected(client, port=port)
        transport.close.side_effect = RuntimeError("close error")

        await client.disconnect()  # Must not raise

        assert not client._connected
        assert port not in _api_mod._shared_transports


# ---------------------------------------------------------------------------
# send_command — auto-connect (line 234)
# ---------------------------------------------------------------------------

class TestSendCommandAutoConnect:

    async def test_send_command_connects_when_not_connected(self):
        """send_command calls connect() if _connected is False."""
        client = _make_client(command_timeout=0.001, command_max_attempts=1)
        assert not client._connected

        connect_called = []

        async def fake_connect():
            connect_called.append(True)
            _inject_connected(client)

        with patch.object(client, "connect", side_effect=fake_connect):
            with patch.object(_api_mod.asyncio, "sleep", new_callable=AsyncMock):
                result = await client.send_command("Bat.GetStatus")

        assert len(connect_called) == 1
        assert result is None  # timed out, but connect was invoked


# ---------------------------------------------------------------------------
# send_command — generic exception, retry backoff, raise last_exception
# (lines 372-390, 393-401, 407)
# ---------------------------------------------------------------------------

class TestSendCommandRetry:

    async def test_generic_exception_retries_and_raises(self):
        """Generic exception is recorded per-attempt, then re-raised after all retries."""
        client = _make_client(command_timeout=0.001, command_max_attempts=2)
        _inject_connected(client)

        attempt_count = [0]

        async def fake_send(msg):
            attempt_count[0] += 1
            raise RuntimeError("network failure")

        with patch.object(client, "_send_to_host", side_effect=fake_send):
            with patch.object(_api_mod.asyncio, "sleep", new_callable=AsyncMock):
                with pytest.raises(RuntimeError, match="network failure"):
                    await client.send_command("Bat.GetStatus")

        assert attempt_count[0] == 2  # Both attempts ran
        s = client.get_command_stats("Bat.GetStatus")
        assert s["total_failures"] == 2
        assert s["total_attempts"] == 2

    async def test_backoff_sleep_called_between_attempts(self):
        """asyncio.sleep is called with a backoff delay between failed attempts."""
        client = _make_client(command_timeout=0.001, command_max_attempts=2)
        _inject_connected(client)

        sleep_values = []

        async def fake_sleep(t):
            sleep_values.append(t)

        async def fake_send(msg):
            raise RuntimeError("err")

        with patch.object(client, "_send_to_host", side_effect=fake_send):
            with patch.object(_api_mod.asyncio, "sleep", side_effect=fake_sleep):
                with pytest.raises(RuntimeError):
                    await client.send_command("Bat.GetStatus")

        # sleep(0) is called at start of each attempt + sleep(backoff) between attempts
        backoff_sleeps = [t for t in sleep_values if t > 0]
        assert len(backoff_sleeps) == 1  # Exactly 1 backoff between 2 attempts


# ---------------------------------------------------------------------------
# _send_to_host — no transport (line 419), no host → broadcast (line 429)
# ---------------------------------------------------------------------------

class TestSendToHost:

    async def test_raises_marstek_api_error_when_no_transport(self):
        """_send_to_host without transport → MarstekAPIError."""
        client = _make_client()
        assert client.transport is None
        with pytest.raises(MarstekAPIError, match="Not connected"):
            await client._send_to_host("payload")

    async def test_calls_broadcast_when_no_host(self):
        """_send_to_host with host=None calls broadcast()."""
        client = _make_client(host=None, port=39103)
        _inject_connected(client, port=39103)

        with patch.object(client, "broadcast", new_callable=AsyncMock) as mock_bc:
            await client._send_to_host("hello")

        mock_bc.assert_awaited_once_with("hello")


# ---------------------------------------------------------------------------
# get_all_command_stats — method already in _command_stats (line 512)
# ---------------------------------------------------------------------------

class TestGetAllCommandStatsExisting:

    def test_returns_recorded_stats_for_known_method(self):
        """get_all_command_stats includes real stats for already-recorded methods."""
        client = _make_client()
        first_method = ALL_API_METHODS[0]

        client._record_command_result(
            first_method, success=True, attempt=1, latency=0.05, timeout=False, error=None
        )

        all_stats = client.get_all_command_stats()
        assert all_stats[first_method]["total_success"] == 1
        assert all_stats[first_method]["last_success"] is True


# ---------------------------------------------------------------------------
# broadcast() (lines 536-546)
# ---------------------------------------------------------------------------

class TestBroadcast:

    async def test_broadcast_sends_to_broadcast_addr(self):
        """broadcast() encodes and sends to the broadcast address."""
        client = _make_client(host=None, port=39104)
        transport = _inject_connected(client, port=39104)

        with patch.object(client, "_get_broadcast_address", return_value="192.168.1.255"):
            await client.broadcast("hello")

        transport.sendto.assert_called_once_with(b"hello", ("192.168.1.255", client.remote_port))

    async def test_broadcast_connects_first_if_no_transport(self):
        """broadcast() calls connect() if transport is None."""
        client = _make_client(host=None, port=39105)

        async def fake_connect():
            _inject_connected(client, port=39105)

        with patch.object(client, "connect", side_effect=fake_connect):
            with patch.object(client, "_get_broadcast_address", return_value="255.255.255.255"):
                await client.broadcast("ping")

        client.transport.sendto.assert_called_once()


# ---------------------------------------------------------------------------
# _get_broadcast_addresses() — all parsing branches (lines 554-619)
# ---------------------------------------------------------------------------

def _mock_ifconfig(stdout: str) -> MagicMock:
    result = MagicMock()
    result.stdout = stdout
    return result


class TestGetBroadcastAddresses:

    def _call(self, stdout: str | None = None, raise_exc: Exception | None = None):
        client = _make_client()
        if raise_exc is not None:
            with patch("subprocess.run", side_effect=raise_exc):
                return client._get_broadcast_addresses()
        with patch("subprocess.run", return_value=_mock_ifconfig(stdout or "")):
            return client._get_broadcast_addresses()

    def test_subprocess_exception_returns_global_broadcast(self):
        """Any subprocess error → fallback to 255.255.255.255."""
        addrs = self._call(raise_exc=OSError("no ifconfig"))
        assert addrs == ["255.255.255.255"]

    def test_empty_output_returns_global_broadcast(self):
        """No inet lines in ifconfig output → fallback."""
        addrs = self._call(stdout="en0: flags=...\n")
        assert addrs == ["255.255.255.255"]

    def test_loopback_is_skipped(self):
        """127.x.x.x addresses are skipped."""
        stdout = "lo0: flags=...\n\tinet 127.0.0.1 netmask 0xff000000\n"
        addrs = self._call(stdout=stdout)
        assert addrs == ["255.255.255.255"]

    def test_vpn_32_mask_is_skipped(self):
        """netmask 0xffffffff (/32) VPN interfaces are skipped."""
        stdout = "utun0: flags=...\n\tinet 10.8.0.1 netmask 0xffffffff peer 10.8.0.2\n"
        addrs = self._call(stdout=stdout)
        assert addrs == ["255.255.255.255"]

    def test_explicit_broadcast_address_used(self):
        """Lines with an explicit 'broadcast' field use that address."""
        stdout = "en0: flags=...\n\tinet 192.168.1.50 netmask 0xffffff00 broadcast 192.168.1.255\n"
        addrs = self._call(stdout=stdout)
        assert "192.168.1.255" in addrs

    def test_broadcast_calculated_from_netmask(self):
        """When no explicit broadcast, calculate from IP + netmask."""
        stdout = "en0: flags=...\n\tinet 192.168.1.50 netmask 0xffffff00\n"
        addrs = self._call(stdout=stdout)
        assert "192.168.1.255" in addrs

    def test_invalid_hex_netmask_falls_back_to_slash24(self):
        """Unparseable hex netmask → netmask stays None → assume /24."""
        stdout = "en0: flags=...\n\tinet 192.168.1.50 netmask INVALID_HEX\n"
        addrs = self._call(stdout=stdout)
        assert "192.168.1.255" in addrs  # /24 assumption: 192.168.1.255

    def test_invalid_ip_with_valid_netmask_skipped(self):
        """Unparseable IP with valid netmask → OSError caught, address skipped."""
        stdout = "en0: flags=...\n\tinet BADIP netmask 0xffffff00\n"
        addrs = self._call(stdout=stdout)
        # BADIP fails socket.inet_aton → except at line 604 → no address added
        assert addrs == ["255.255.255.255"]

    def test_no_netmask_no_broadcast_assumes_slash24(self):
        """inet line with no netmask and no broadcast → assume /24."""
        stdout = "en0: flags=...\n\tinet 10.0.0.50\n"
        addrs = self._call(stdout=stdout)
        assert "10.0.0.255" in addrs


# ---------------------------------------------------------------------------
# _get_broadcast_address() (lines 623-624)
# ---------------------------------------------------------------------------

class TestGetBroadcastAddress:

    def test_returns_first_address(self):
        """_get_broadcast_address returns the first element from _get_broadcast_addresses."""
        client = _make_client()
        with patch.object(client, "_get_broadcast_addresses", return_value=["192.168.1.255", "10.0.0.255"]):
            addr = client._get_broadcast_address()
        assert addr == "192.168.1.255"

    def test_returns_fallback_when_list_empty(self):
        """_get_broadcast_address returns 255.255.255.255 for empty list."""
        client = _make_client()
        with patch.object(client, "_get_broadcast_addresses", return_value=[]):
            addr = client._get_broadcast_address()
        assert addr == "255.255.255.255"


# ---------------------------------------------------------------------------
# discover_devices() (lines 628-707)
# ---------------------------------------------------------------------------

class TestDiscoverDevices:

    async def test_discover_finds_device_and_deduplicates(self):
        """
        Full discover_devices flow:
        - while loop runs exactly once (mocked timing)
        - discovery handler captures one valid device
        - no-ble_mac response is skipped
        - duplicate ble_mac is skipped
        - non-zero id is ignored
        """
        port = 39110
        client = _make_client(host=None, port=port)
        transport = _inject_connected(client, port=port)

        sleep_count = [0]

        async def fake_sleep(t):
            sleep_count[0] += 1
            if sleep_count[0] == 1:
                # Inject all handler-branch scenarios during broadcast interval
                for h in list(client._handlers):
                    # Valid device → added
                    h(
                        {"id": 0, "result": {
                            "ble_mac": "aabbccddee", "wifi_mac": "112233",
                            "device": "VenusE", "ver": 147,
                        }},
                        ("192.168.1.50", port),
                    )
                    # No ble_mac → skipped (line 645-647)
                    h({"id": 0, "result": {"wifi_mac": "112233"}}, ("192.168.1.51", port))
                    # Duplicate ble_mac → skipped (line 649-651)
                    h(
                        {"id": 0, "result": {"ble_mac": "aabbccddee", "wifi_mac": "xxyyzz"}},
                        ("192.168.1.52", port),
                    )
                    # Wrong id → ignored (handler checks id==0)
                    h({"id": 99, "result": {"ble_mac": "zzyyxx"}}, ("192.168.1.53", port))

        # Control loop timing: exactly 1 iteration
        mock_loop = MagicMock()
        mock_loop.time.side_effect = [100.0, 100.0, 200.0]

        with patch.object(client, "_get_broadcast_addresses", return_value=["192.168.1.255"]):
            with patch.object(_api_mod.asyncio, "sleep", side_effect=fake_sleep):
                with patch.object(_api_mod.asyncio, "get_event_loop", return_value=mock_loop):
                    result = await client.discover_devices(timeout=1)

        assert len(result) == 1
        assert result[0]["ble_mac"] == "aabbccddee"
        assert result[0]["ip"] == "192.168.1.50"
        transport.sendto.assert_called_once()

    async def test_discover_empty_when_no_response(self):
        """discover_devices with no responses returns empty list."""
        port = 39111
        client = _make_client(host=None, port=port)
        _inject_connected(client, port=port)

        mock_loop = MagicMock()
        mock_loop.time.side_effect = [100.0, 200.0]  # end_time=101, first check fails → 0 iterations

        with patch.object(client, "_get_broadcast_addresses", return_value=["255.255.255.255"]):
            with patch.object(_api_mod.asyncio, "sleep", new_callable=AsyncMock):
                with patch.object(_api_mod.asyncio, "get_event_loop", return_value=mock_loop):
                    result = await client.discover_devices(timeout=0)

        assert result == []


# ---------------------------------------------------------------------------
# API method helpers (lines 717, 731, 744, 757, 770, 783, 796, 809)
# ---------------------------------------------------------------------------

class TestAPIHelpers:

    async def test_all_helpers_delegate_to_send_command(self):
        """Each convenience method calls send_command with the correct method name."""
        client = _make_client()
        helpers_and_methods = [
            (client.get_device_info, METHOD_GET_DEVICE),
            (client.get_wifi_status, METHOD_WIFI_STATUS),
            (client.get_ble_status, METHOD_BLE_STATUS),
            (client.get_battery_status, METHOD_BATTERY_STATUS),
            (client.get_pv_status, METHOD_PV_STATUS),
            (client.get_es_status, METHOD_ES_STATUS),
            (client.get_es_mode, METHOD_ES_MODE),
            (client.get_em_status, METHOD_EM_STATUS),
        ]

        for helper, expected_method in helpers_and_methods:
            with patch.object(
                client, "send_command", new_callable=AsyncMock, return_value={"ok": True}
            ) as mock_send:
                result = await helper()

            assert result == {"ok": True}, f"{helper.__name__} returned unexpected result"
            called_method = mock_send.call_args[0][0]
            assert called_method == expected_method, (
                f"{helper.__name__} used method '{called_method}', expected '{expected_method}'"
            )


# ---------------------------------------------------------------------------
# MarstekProtocol.datagram_received() (lines 844-859)
# MarstekProtocol.error_received()   (line 863)
# ---------------------------------------------------------------------------

class TestMarstekProtocolDispatch:

    async def test_datagram_received_looks_up_port_and_dispatches(self):
        """When port is None, datagram_received discovers its port from _shared_protocols."""
        port = 39120
        client = _make_client(port=port)
        _inject_connected(client, port=port)

        protocol = _api_mod._shared_protocols[port]
        protocol.port = None  # Reset so the lookup runs

        received = []
        client.register_handler(lambda msg, addr: received.append(msg))

        msg = {"id": 1, "result": {"soc": 80}}
        protocol.datagram_received(json.dumps(msg).encode(), ("192.168.1.100", port))

        await asyncio.sleep(0)  # Let the created task execute

        assert protocol.port == port
        assert received == [msg]

    async def test_datagram_received_when_port_already_set(self):
        """When port is already set, dispatch happens without the lookup."""
        port = 39121
        client = _make_client(port=port)
        _inject_connected(client, port=port)

        protocol = _api_mod._shared_protocols[port]
        # port is already set by _inject_connected via protocol.port = port

        received = []
        client.register_handler(lambda msg, addr: received.append(msg))

        msg = {"id": 2, "result": {"power": 500}}
        protocol.datagram_received(json.dumps(msg).encode(), ("192.168.1.100", port))
        await asyncio.sleep(0)

        assert received == [msg]

    def test_datagram_received_no_clients_logs_warning(self):
        """No clients registered for the port → warning, no exception."""
        protocol = MarstekProtocol()
        protocol.port = 99998  # Port not in _clients_by_port

        data = json.dumps({"id": 1}).encode()
        protocol.datagram_received(data, ("1.2.3.4", 30000))  # Must not raise

    def test_datagram_received_lookup_exception_handled(self):
        """Exception during _shared_protocols iteration is swallowed (lines 849-850)."""
        protocol = MarstekProtocol()
        # port is None → triggers the lookup

        bad_protocols = MagicMock()
        bad_protocols.items.side_effect = RuntimeError("dict iteration error")

        with patch.object(_api_mod, "_shared_protocols", bad_protocols):
            # Must not raise — the except block swallows the error
            protocol.datagram_received(b'{"id": 1}', ("1.2.3.4", 30000))

        assert protocol.port is None  # Port not found due to exception

    def test_error_received_does_not_raise(self):
        """error_received() logs and does not raise."""
        protocol = MarstekProtocol()
        protocol.error_received(OSError("UDP error"))  # Must not raise
