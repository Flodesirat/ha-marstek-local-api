"""Marstek Local API UDP client."""
from __future__ import annotations

import asyncio
from collections import deque
import json
import logging
import random
import socket
import sys
import time
from copy import deepcopy
from typing import Any

from .const import (
    ALL_API_METHODS,
    COMMAND_BACKOFF_BASE,
    COMMAND_BACKOFF_FACTOR,
    COMMAND_BACKOFF_JITTER,
    COMMAND_BACKOFF_MAX,
    COMMAND_MAX_ATTEMPTS,
    COMMAND_MIN_INTERVAL,
    COMMAND_TIMEOUT,
    DEFAULT_PORT,
    DIAGNOSTIC_MAX_FRAMES,
    DISCOVERY_BROADCAST_INTERVAL,
    DISCOVERY_TIMEOUT,
    ERROR_METHOD_NOT_FOUND,
    METHOD_BATTERY_STATUS,
    METHOD_BLE_STATUS,
    METHOD_EM_STATUS,
    METHOD_ES_MODE,
    METHOD_ES_SET_MODE,
    METHOD_ES_STATUS,
    METHOD_GET_DEVICE,
    METHOD_PV_STATUS,
    METHOD_WIFI_STATUS,
    PRIORITY_RETRY_DELAYS,
)

_LOGGER = logging.getLogger(__name__)

# Shared transports and protocols per port to ensure all clients on the same port
# share the same UDP socket and can receive all messages
_shared_transports = {}
_shared_protocols = {}
_transport_refcounts = {}
_clients_by_port = {}  # Map port -> list of clients


class MarstekUDPClient:
    """UDP client for Marstek Local API communication."""

    def __init__(
        self,
        hass,
        host: str | None = None,
        port: int = DEFAULT_PORT,
        remote_port: int | None = None,
        command_timeout: int = COMMAND_TIMEOUT,
        command_max_attempts: int = COMMAND_MAX_ATTEMPTS,
        command_min_interval: float = COMMAND_MIN_INTERVAL,
    ) -> None:
        """Initialize the UDP client.

        Args:
            hass: Home Assistant instance
            host: Target host IP (None for broadcast)
            port: Local port to bind to (0 for ephemeral)
            remote_port: Remote port to send to (defaults to DEFAULT_PORT)
            command_timeout: Timeout per attempt in seconds
            command_max_attempts: Max attempts per command
        """
        self.hass = hass
        self.host = host
        self.port = port
        self.remote_port = remote_port or DEFAULT_PORT
        self.command_timeout = command_timeout
        self.command_max_attempts = command_max_attempts
        self.command_min_interval = command_min_interval
        self.transport: asyncio.DatagramTransport | None = None
        self.protocol: MarstekProtocol | None = None
        self._handlers: list = []
        self._connected = False
        self._stale_message_counter = 0
        self._command_stats: dict[str, dict[str, Any]] = {}
        self._msg_id_counter = 0  # Counter for integer message IDs
        self._recent_frames: deque = deque(maxlen=DIAGNOSTIC_MAX_FRAMES)
        # Send scheduling (see docs/DESIGN.md, "Send Scheduling")
        self._send_lock: asyncio.Lock = asyncio.Lock()  # Serialises low-priority requests
        self._last_send_time: float = 0.0
        self._in_flight = False  # A frame was sent and waits for its answer or timeout
        self._priority_frame: _PriorityFrame | None = None  # The single high-priority slot
        self._slot_wakeup = asyncio.Event()
        self.priority_retry_delays: tuple[float, ...] = PRIORITY_RETRY_DELAYS

    async def connect(self) -> None:
        """Connect to the UDP socket."""
        if self._connected and self.transport:
            _LOGGER.debug("Already connected on port %s", self.port)
            return

        _LOGGER.info(
            "Connecting UDP socket: local_port=%s, remote_host=%s, remote_port=%s",
            self.port, self.host or "broadcast", self.remote_port
        )

        try:
            # Use shared transport/protocol for this port to ensure all clients
            # on the same port can receive all UDP messages
            if self.port not in _shared_transports:
                # Create shared UDP endpoint for this port
                transport, protocol = await self._create_endpoint()
                _shared_transports[self.port] = transport
                _shared_protocols[self.port] = protocol
                _transport_refcounts[self.port] = 0

                _LOGGER.info(
                    "Created shared UDP socket on port %s",
                    self.port
                )

            # Use the shared transport/protocol
            self.transport = _shared_transports[self.port]
            self.protocol = _shared_protocols[self.port]
            _transport_refcounts[self.port] += 1

            # Register this client for message dispatching
            if self.port not in _clients_by_port:
                _clients_by_port[self.port] = []
            if self not in _clients_by_port[self.port]:
                _clients_by_port[self.port].append(self)

            self._connected = True
            sock = self.transport.get_extra_info('socket')
            _LOGGER.info(
                "UDP socket connected: local_port=%s, socket=%s, refcount=%d, clients=%d",
                self.port, sock.getsockname() if sock else "unknown",
                _transport_refcounts[self.port], len(_clients_by_port[self.port])
            )
        except Exception as err:
            _LOGGER.error(
                "Failed to connect UDP socket on port %s: %s",
                self.port, err, exc_info=True
            )
            raise

    async def _create_endpoint(self) -> tuple[asyncio.DatagramTransport, "MarstekProtocol"]:
        """Create a new UDP endpoint bound to this client's local port."""
        endpoint_kwargs = {
            "local_addr": ("0.0.0.0", self.port),
            "allow_broadcast": True,
        }
        # reuse_port is not supported on Windows
        if sys.platform != "win32":
            endpoint_kwargs["reuse_port"] = True
        return await asyncio.get_running_loop().create_datagram_endpoint(
            MarstekProtocol,
            **endpoint_kwargs,
        )

    async def reconnect(self) -> None:
        """Force recreation of the shared UDP socket for this port.

        Used to recover when the socket is silently dead (commands are sent but
        no reply is ever received). Every client sharing the port is moved to
        the new transport.
        """
        if not self._connected:
            await self.connect()
            return

        port = self.port
        _LOGGER.warning("Recreating shared UDP socket on port %s", port)

        # Drop the registry entries *before* closing: close() schedules
        # connection_lost(), which must not see the old protocol as current.
        old_transport = _shared_transports.pop(port, None)
        _shared_protocols.pop(port, None)
        if old_transport is not None:
            try:
                old_transport.close()
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.warning("Error closing transport: %s", err)

        transport, protocol = await self._create_endpoint()
        _shared_transports[port] = transport
        _shared_protocols[port] = protocol
        _transport_refcounts.setdefault(port, 0)
        clients = _clients_by_port.setdefault(port, [])
        if self not in clients:
            clients.append(self)
            _transport_refcounts[port] += 1
        for client in clients:
            client.transport = transport
            client.protocol = protocol
            client._connected = True  # pylint: disable=protected-access

    async def disconnect(self) -> None:
        """Disconnect from the UDP socket."""
        if not self._connected:
            return

        if self.port in _transport_refcounts:
            # Unregister this client from message dispatching
            if self.port in _clients_by_port and self in _clients_by_port[self.port]:
                _clients_by_port[self.port].remove(self)

            _transport_refcounts[self.port] -= 1

            # Only close the shared transport when last client disconnects
            if _transport_refcounts[self.port] <= 0:
                if self.transport:
                    try:
                        self.transport.close()
                    except Exception as err:
                        _LOGGER.warning("Error closing transport: %s", err)

                if self.port in _shared_transports:
                    del _shared_transports[self.port]
                if self.port in _shared_protocols:
                    del _shared_protocols[self.port]
                if self.port in _transport_refcounts:
                    del _transport_refcounts[self.port]
                if self.port in _clients_by_port:
                    del _clients_by_port[self.port]
                _LOGGER.debug("Closed shared UDP socket on port %s", self.port)
            else:
                _LOGGER.debug(
                    "UDP socket disconnected, %d clients still connected on port %s",
                    _transport_refcounts[self.port], self.port
                )

        self.transport = None
        self.protocol = None
        self._connected = False

    def register_handler(self, handler) -> None:
        """Register a message handler."""
        if handler not in self._handlers:
            self._handlers.append(handler)

    def unregister_handler(self, handler) -> None:
        """Unregister a message handler."""
        if handler in self._handlers:
            self._handlers.remove(handler)

    async def _handle_message(self, data: bytes, addr: tuple) -> None:
        """Handle incoming UDP message.

        This method is called by the shared protocol and needs to dispatch
        the message to all clients sharing this port.
        """
        try:
            message = json.loads(data.decode())
            self._recent_frames.append({
                "ts": time.time(),
                "src": f"{addr[0]}:{addr[1]}",
                "frame": message,
            })
            _LOGGER.debug(
                "Received UDP message from %s:%s (size=%d bytes): %s",
                addr[0], addr[1], len(data), message
            )

            # Call all registered handlers from THIS client
            handlers_called = 0
            for handler in self._handlers:
                try:
                    # Handler can be sync or async
                    result = handler(message, addr)
                    if asyncio.iscoroutine(result):
                        await result
                    handlers_called += 1
                except Exception as err:
                    _LOGGER.error("Error in message handler: %s", err, exc_info=True)

            _LOGGER.debug("Called %d handler(s) for message from %s", handlers_called, addr[0])

        except json.JSONDecodeError as err:
            _LOGGER.error("Failed to decode JSON message from %s: %s (data: %s)", addr, err, data[:200])

    async def send_command(
        self,
        method: str,
        params: dict | None = None,
        timeout: int | None = None,
        max_attempts: int | None = None,
        *,
        priority: bool = False,
    ) -> dict | None:
        """Send a command and wait for response.

        With ``priority=True`` the frame goes into the high-priority queue: it
        takes the next send slot ahead of polling, replaces any high-priority
        frame not yet answered, and is retried on ``PRIORITY_RETRY_DELAYS``
        until the device answers (``max_attempts`` is ignored). A replaced
        call raises ``MarstekCommandSuperseded``.
        """
        if not self._connected:
            await self.connect()

        if params is None:
            params = {"id": 0}

        effective_timeout = timeout if timeout is not None else self.command_timeout

        # Generate unique integer message ID (required for Venus E firmware V139+)
        self._msg_id_counter = (self._msg_id_counter + 1) % 1000000  # Wrap at 1 million
        exchange = _Exchange(self, method, self._msg_id_counter, params)

        _LOGGER.debug(
            "Sending command: method=%s, id=%s, host=%s, port=%s, transport=%s, priority=%s",
            method, exchange.msg_id, self.host, self.remote_port, self.transport is not None, priority
        )

        if priority:
            return await self._send_high_priority(exchange, effective_timeout)

        attempt_limit = int(max_attempts if max_attempts is not None else self.command_max_attempts)
        return await self._send_low_priority(exchange, effective_timeout, attempt_limit)

    async def _send_low_priority(
        self, exchange: "_Exchange", timeout: float, attempt_limit: int
    ) -> dict | None:
        """Run a low-priority request: bounded attempts with exponential backoff.

        ``_send_lock`` keeps low-priority requests in order across all their
        attempts; a high-priority frame may still take the slot between two
        attempts.
        """
        last_exception: Exception | None = None

        async with self._send_lock:
            self.register_handler(exchange.handler)
            try:
                # Allow the event loop to process any pending datagrams before we start
                await asyncio.sleep(0)

                for attempt in range(1, attempt_limit + 1):
                    if not await self._acquire_slot(None, exchange.response):
                        # Late answer to the previous attempt, received while paused
                        return self._complete(exchange, attempt - 1)

                    answered, last_exception = await self._run_attempt(
                        exchange, attempt, attempt_limit, timeout
                    )
                    if answered:
                        return self._complete(exchange, attempt)

                    if attempt < attempt_limit:
                        delay = self._compute_backoff_delay(attempt)
                        _LOGGER.debug(
                            "Waiting %.2fs before retrying %s (attempt %d/%d)",
                            delay,
                            exchange.method,
                            attempt + 1,
                            attempt_limit,
                        )
                        await asyncio.sleep(delay)
            finally:
                self.unregister_handler(exchange.handler)

        if last_exception:
            raise last_exception

        _LOGGER.error(
            "Command %s failed after %d attempt(s); returning no result",
            exchange.method,
            attempt_limit,
        )
        return None

    async def _send_high_priority(self, exchange: "_Exchange", timeout: float) -> dict | None:
        """Run a high-priority request until it is answered or replaced."""
        frame = _PriorityFrame(due=asyncio.get_running_loop().time())
        if self._priority_frame is not None:
            _LOGGER.debug("Replacing pending high-priority command with %s", exchange.method)
        self._priority_frame = frame
        self._notify_scheduler()

        self.register_handler(exchange.handler)
        try:
            attempt = 0
            while True:
                if not await self._acquire_slot(frame, exchange.response):
                    if exchange.response.is_set():
                        # Late answer to the previous attempt
                        return self._complete(exchange, attempt)
                    _LOGGER.debug(
                        "High-priority %s (id=%s) replaced before being answered",
                        exchange.method,
                        exchange.msg_id,
                    )
                    raise MarstekCommandSuperseded(
                        f"{exchange.method} replaced by a newer high-priority command"
                    )

                attempt += 1
                answered, _ = await self._run_attempt(exchange, attempt, None, timeout)
                if answered:
                    return self._complete(exchange, attempt)

                delays = self.priority_retry_delays
                delay = delays[min(attempt, len(delays)) - 1]
                frame.due = exchange.sent_at + delay
                self._notify_scheduler()
                _LOGGER.debug(
                    "High-priority %s not answered, retrying in %ss (attempt %d)",
                    exchange.method,
                    delay,
                    attempt + 1,
                )
        finally:
            self.unregister_handler(exchange.handler)
            if self._priority_frame is frame:
                self._priority_frame = None
                self._notify_scheduler()

    def _notify_scheduler(self) -> None:
        """Wake every coroutine waiting for a send slot so it re-evaluates."""
        self._slot_wakeup.set()
        self._slot_wakeup = asyncio.Event()

    async def _acquire_slot(
        self, frame: "_PriorityFrame | None", answered: asyncio.Event
    ) -> bool:
        """Wait for the next send slot and mark a frame in flight.

        ``frame`` is the high-priority frame asking for the slot, or None for
        low-priority traffic. A slot is granted when no frame is in flight and
        ``command_min_interval`` has elapsed since the last send. A due
        high-priority frame always wins; low-priority traffic may use the slots
        before the next high-priority retry.

        Returns False without taking the slot when ``answered`` is set (a late
        answer arrived) or when ``frame`` was replaced.
        """
        loop = asyncio.get_running_loop()
        while True:
            if answered.is_set():
                return False
            if frame is not None and self._priority_frame is not frame:
                return False

            wakeup = self._slot_wakeup
            now = loop.time()
            ready_at = self._last_send_time + self.command_min_interval
            if frame is not None:
                ready_at = max(ready_at, frame.due)

            delay: float | None = None  # None: wait for a state change
            if not self._in_flight:
                pending = self._priority_frame
                if frame is None and pending is not None and pending.due <= ready_at:
                    pass  # The high-priority frame takes this slot
                elif now >= ready_at:
                    self._in_flight = True
                    return True
                else:
                    delay = ready_at - now
                    _LOGGER.debug("Rate-limiting: waiting %.2fs for the next send slot", delay)

            try:
                await asyncio.wait_for(wakeup.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    def _release_slot(self) -> None:
        """Mark the in-flight frame as answered or timed out."""
        self._in_flight = False
        self._notify_scheduler()

    async def _run_attempt(
        self,
        exchange: "_Exchange",
        attempt: int,
        attempt_limit: int | None,
        timeout: float,
    ) -> tuple[bool, Exception | None]:
        """Send one frame in the slot already held and wait for its answer.

        Returns ``(answered, exception)``. Timeouts and send errors are
        recorded here; the answer itself is processed by ``_complete``.
        """
        limit_label = attempt_limit if attempt_limit is not None else "∞"
        exchange.response.clear()
        exchange.data.clear()
        try:
            _LOGGER.debug(
                "Sending payload (attempt %d/%s) to %s:%s: %s",
                attempt,
                limit_label,
                self.host or "broadcast",
                self.remote_port,
                exchange.payload_str,
            )
            # Yield once more to ensure pending packets are processed before sending
            await asyncio.sleep(0)
            # Spacing and high-priority retries are both counted from this time,
            # so a retry due one spacing later wins that slot over polling
            self._last_send_time = exchange.sent_at = asyncio.get_running_loop().time()
            await self._send_to_host(exchange.payload_str)

            await asyncio.wait_for(exchange.response.wait(), timeout=timeout)
            return True, None

        except asyncio.TimeoutError:
            self._record_command_result(
                exchange.method,
                success=False,
                attempt=attempt,
                latency=None,
                timeout=True,
                error="timeout",
            )
            _LOGGER.warning(
                "Command %s timed out after %ss (attempt %d/%s, host=%s)",
                exchange.method,
                timeout,
                attempt,
                limit_label,
                self.host,
            )
            return False, None
        except Exception as err:
            self._record_command_result(
                exchange.method,
                success=False,
                attempt=attempt,
                latency=None,
                timeout=False,
                error=str(err),
            )
            _LOGGER.error(
                "Error sending command %s to %s on attempt %d/%s: %s",
                exchange.method,
                self.host,
                attempt,
                limit_label,
                err,
                exc_info=True,
            )
            return False, err
        finally:
            self._release_slot()

    def _complete(self, exchange: "_Exchange", attempt: int) -> dict | None:
        """Process the answer matched for ``exchange``."""
        response_data = exchange.data
        if "error" in response_data:
            error = response_data["error"]
            error_code = error.get('code')
            error_msg = error.get('message')
            # Record the error with its code for diagnostics
            self._record_command_result(
                exchange.method,
                success=False,
                attempt=attempt,
                latency=None,
                timeout=False,
                error=error_msg,
                error_code=error_code,
            )
            raise MarstekAPIError(
                f"API error {error_code}: {error_msg}"
            )

        latency = asyncio.get_running_loop().time() - exchange.sent_at
        self._stale_message_counter = 0
        self._record_command_result(
            exchange.method,
            success=True,
            attempt=attempt,
            latency=latency,
            timeout=False,
            error=None,
            error_code=None,
            response=response_data,
        )
        _LOGGER.debug(
            "Command %s completed successfully in %.2fs (attempt %d)",
            exchange.method,
            latency,
            attempt,
        )
        return response_data.get("result")

    async def _send_to_host(self, message: str) -> None:
        """Send message to specific host or broadcast."""
        if not self.transport:
            raise MarstekAPIError("Not connected")

        if self.host:
            # Send to specific host on remote port
            self.transport.sendto(
                message.encode(),
                (self.host, self.remote_port)
            )
        else:
            # Broadcast
            await self.broadcast(message)

    def _compute_backoff_delay(self, attempt: int) -> float:
        """Compute exponential backoff with jitter for retries."""
        base_delay = COMMAND_BACKOFF_BASE * (COMMAND_BACKOFF_FACTOR ** (attempt - 1))
        capped = min(base_delay, COMMAND_BACKOFF_MAX)
        return capped + random.uniform(0, COMMAND_BACKOFF_JITTER)

    def _record_command_result(
        self,
        method: str,
        *,
        success: bool,
        attempt: int,
        latency: float | None,
        timeout: bool,
        error: str | None,
        error_code: int | None = None,
        response: dict[str, Any] | None = None,
    ) -> None:
        """Track command attempt statistics for diagnostics."""
        stats = self._command_stats.setdefault(
            method,
            {
                "total_attempts": 0,
                "total_success": 0,
                "total_timeouts": 0,
                "total_failures": 0,
                "last_success": None,
                "last_attempt": None,
                "last_latency": None,
                "last_timeout": False,
                "last_error": None,
                "last_error_code": None,
                "last_updated": None,
                "last_success_at": None,
                "last_success_payload": None,
                "unsupported_error_count": 0,
                "supported": None,  # None=unknown, True=supported, False=unsupported
            },
        )

        stats["total_attempts"] += 1
        if success:
            stats["total_success"] += 1
            stats["supported"] = True  # Command works on this device
        elif timeout:
            stats["total_timeouts"] += 1
        else:
            stats["total_failures"] += 1

        stats["last_success"] = success
        stats["last_attempt"] = attempt
        stats["last_latency"] = latency
        stats["last_timeout"] = timeout
        stats["last_error"] = error
        stats["last_error_code"] = error_code
        stats["last_updated"] = time.time()
        if success:
            stats["last_success_at"] = stats["last_updated"]
            stats["last_success_payload"] = deepcopy(response) if response is not None else None

        # Track "Method not found" errors to detect unsupported commands
        if error_code == ERROR_METHOD_NOT_FOUND:
            stats["unsupported_error_count"] = stats.get("unsupported_error_count", 0) + 1
            # Mark as unsupported after 2+ method-not-found errors
            if stats["unsupported_error_count"] >= 2:
                stats["supported"] = False

    def get_command_stats(self, method: str) -> dict[str, Any] | None:
        """Return snapshot of command statistics."""
        stats = self._command_stats.get(method)
        if stats is None:
            return None
        return dict(stats)

    def get_all_command_stats(self) -> dict[str, dict[str, Any]]:
        """Return snapshot of all command statistics including never-attempted commands."""
        all_stats = {}
        for method in ALL_API_METHODS:
            if method in self._command_stats:
                all_stats[method] = dict(self._command_stats[method])
            else:
                # Never attempted - return default structure
                all_stats[method] = {
                    "total_attempts": 0,
                    "total_success": 0,
                    "total_timeouts": 0,
                    "total_failures": 0,
                    "last_success": None,
                    "last_attempt": None,
                    "last_latency": None,
                    "last_timeout": False,
                    "last_error": None,
                    "last_error_code": None,
                    "last_updated": None,
                    "last_success_at": None,
                    "last_success_payload": None,
                    "unsupported_error_count": 0,
                    "supported": None,
                }
        return all_stats

    def get_recent_frames(self) -> list[dict]:
        """Return a snapshot of the most recent raw frames received from the device."""
        return list(self._recent_frames)

    async def broadcast(self, message: str) -> None:
        """Broadcast a message."""
        if not self.transport:
            await self.connect()

        # Get broadcast address
        broadcast_addr = self._get_broadcast_address()

        self.transport.sendto(
            message.encode(),
            (broadcast_addr, self.remote_port)
        )
        _LOGGER.debug("Broadcast message: %s", message)

    def _get_broadcast_addresses(self) -> list[str]:
        """Get all broadcast addresses for available networks.

        Uses simple heuristic: broadcast on /24 of primary interface and global broadcast.
        This works for most home networks and avoids VPN interfaces.
        """
        import struct
        import subprocess

        broadcast_addrs = set()

        try:
            # Parse ifconfig to get all network interfaces and their IPs
            result = subprocess.run(['ifconfig'], capture_output=True, text=True, timeout=2, check=False)

            for line in result.stdout.split('\n'):
                # Parse inet lines
                if '\tinet ' in line:
                    parts = line.strip().split()
                    if len(parts) >= 2 and parts[0] == 'inet':
                        ip = parts[1]

                        # Skip loopback
                        if ip.startswith('127.'):
                            continue

                        # Parse netmask if present
                        netmask = None
                        if 'netmask' in parts:
                            idx = parts.index('netmask')
                            if idx + 1 < len(parts):
                                mask_hex = parts[idx + 1]
                                # Skip point-to-point /32 (VPN) interfaces
                                if mask_hex == '0xffffffff':
                                    continue

                                # Convert hex netmask to dotted decimal
                                try:
                                    mask_int = int(mask_hex, 16)
                                    netmask = socket.inet_ntoa(struct.pack('>I', mask_int))
                                except (ValueError, OSError):
                                    pass

                        # Check for explicit broadcast address
                        if 'broadcast' in parts:
                            idx = parts.index('broadcast')
                            if idx + 1 < len(parts):
                                broadcast_addrs.add(parts[idx + 1])
                        elif netmask:
                            # Calculate broadcast address
                            try:
                                ip_int = struct.unpack('>I', socket.inet_aton(ip))[0]
                                mask_int = struct.unpack('>I', socket.inet_aton(netmask))[0]
                                broadcast_int = ip_int | (~mask_int & 0xffffffff)
                                broadcast = socket.inet_ntoa(struct.pack('>I', broadcast_int))
                                broadcast_addrs.add(broadcast)
                            except (ValueError, OSError):
                                pass
                        else:
                            # Assume /24 network
                            parts_ip = ip.split(".")
                            if len(parts_ip) == 4:
                                broadcast_addrs.add(f"{parts_ip[0]}.{parts_ip[1]}.{parts_ip[2]}.255")

        except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as err:
            _LOGGER.debug("Could not parse ifconfig: %s, using fallback", err)

        # If we found nothing, use global broadcast as fallback
        if not broadcast_addrs:
            broadcast_addrs.add("255.255.255.255")

        return list(broadcast_addrs)

    def _get_broadcast_address(self) -> str:
        """Get primary broadcast address (for backward compatibility)."""
        addrs = self._get_broadcast_addresses()
        return addrs[0] if addrs else "255.255.255.255"

    async def discover_devices(self, timeout: int = DISCOVERY_TIMEOUT) -> list[dict]:
        """Discover Marstek devices on the network."""
        devices = []
        discovered_macs = set()

        def handler(message, addr):
            """Handle discovery responses."""
            msg_id = message.get("id")
            has_result = "result" in message
            _LOGGER.debug("Discovery handler called: id=%s, expected=0, match=%s, has_result=%s",
                         msg_id, msg_id == 0, has_result)

            if msg_id == 0 and has_result:
                result = message["result"]
                wifi_mac = result.get("wifi_mac")
                ble_mac = result.get("ble_mac")
                ip = addr[0]

                if not ble_mac:
                    _LOGGER.debug(
                        "Skipping discovery response without BLE MAC: wifi_mac=%s ip=%s",
                        wifi_mac,
                        ip,
                    )
                    return

                if ble_mac in discovered_macs:
                    _LOGGER.debug(
                        "Discovery response for %s already processed (ip=%s)",
                        ble_mac,
                        ip,
                    )
                    return

                discovered_macs.add(ble_mac)
                device = {
                    "name": result.get("device", "Unknown"),
                    "ip": ip,
                    "mac": ble_mac,
                    "firmware": result.get("ver", 0),
                    "ble_mac": ble_mac,
                    "wifi_mac": wifi_mac,
                    "wifi_name": result.get("wifi_name"),
                }
                devices.append(device)
                _LOGGER.info("Added discovered device: %s", device)

        # Register handler
        self.register_handler(handler)

        try:
            # Get all broadcast addresses
            broadcast_addrs = self._get_broadcast_addresses()
            _LOGGER.debug("Broadcasting to networks: %s", broadcast_addrs)

            # Broadcast discovery message repeatedly on all networks
            end_time = asyncio.get_event_loop().time() + timeout
            message = json.dumps({
                "id": 0,
                "method": METHOD_GET_DEVICE,
                "params": {"ble_mac": "0"}
            })

            while asyncio.get_event_loop().time() < end_time:
                # Broadcast to all networks
                for broadcast_addr in broadcast_addrs:
                    if self.transport:
                        self.transport.sendto(
                            message.encode(),
                            (broadcast_addr, self.remote_port)
                        )
                await asyncio.sleep(DISCOVERY_BROADCAST_INTERVAL)

            # Wait a bit longer for any delayed responses
            _LOGGER.debug("Waiting for delayed responses...")
            await asyncio.sleep(2)

        finally:
            self.unregister_handler(handler)
            _LOGGER.info("Discovery complete - found %d device(s)", len(devices))

        return devices

    # API method helpers
    async def get_device_info(
        self,
        *,
        timeout: int | None = None,
        max_attempts: int | None = None,
    ) -> dict | None:
        """Get device information."""
        return await self.send_command(
            METHOD_GET_DEVICE,
            {"ble_mac": "0"},
            timeout=timeout,
            max_attempts=max_attempts,
        )

    async def get_wifi_status(
        self,
        *,
        timeout: int | None = None,
        max_attempts: int | None = None,
    ) -> dict | None:
        """Get WiFi status."""
        return await self.send_command(
            METHOD_WIFI_STATUS,
            timeout=timeout,
            max_attempts=max_attempts,
        )

    async def get_ble_status(
        self,
        *,
        timeout: int | None = None,
        max_attempts: int | None = None,
    ) -> dict | None:
        """Get Bluetooth status."""
        return await self.send_command(
            METHOD_BLE_STATUS,
            timeout=timeout,
            max_attempts=max_attempts,
        )

    async def get_battery_status(
        self,
        *,
        timeout: int | None = None,
        max_attempts: int | None = None,
    ) -> dict | None:
        """Get battery status."""
        return await self.send_command(
            METHOD_BATTERY_STATUS,
            timeout=timeout,
            max_attempts=max_attempts,
        )

    async def get_pv_status(
        self,
        *,
        timeout: int | None = None,
        max_attempts: int | None = None,
    ) -> dict | None:
        """Get PV (solar) status."""
        return await self.send_command(
            METHOD_PV_STATUS,
            timeout=timeout,
            max_attempts=max_attempts,
        )

    async def get_es_status(
        self,
        *,
        timeout: int | None = None,
        max_attempts: int | None = None,
    ) -> dict | None:
        """Get energy system status."""
        return await self.send_command(
            METHOD_ES_STATUS,
            timeout=timeout,
            max_attempts=max_attempts,
        )

    async def get_es_mode(
        self,
        *,
        timeout: int | None = None,
        max_attempts: int | None = None,
    ) -> dict | None:
        """Get energy system operating mode."""
        return await self.send_command(
            METHOD_ES_MODE,
            timeout=timeout,
            max_attempts=max_attempts,
        )

    async def get_em_status(
        self,
        *,
        timeout: int | None = None,
        max_attempts: int | None = None,
    ) -> dict | None:
        """Get energy meter (CT) status."""
        return await self.send_command(
            METHOD_EM_STATUS,
            timeout=timeout,
            max_attempts=max_attempts,
        )

    async def set_es_mode(self, config: dict, *, priority: bool = False) -> bool:
        """Set energy system operating mode.

        With ``priority=True`` the command preempts polling and replaces any
        high-priority command not yet answered (see ``send_command``).
        """
        result = await self.send_command(
            METHOD_ES_SET_MODE,
            {"id": 0, "config": config},
            priority=priority,
        )

        if result and result.get("set_result"):
            return True
        return False


class MarstekProtocol(asyncio.DatagramProtocol):
    """Protocol for handling UDP datagrams.

    This protocol is shared across all clients on the same port.
    It dispatches incoming messages to all registered clients.
    """

    def __init__(self) -> None:
        """Initialize the protocol."""
        self.port = None  # Will be set when socket is bound

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        """Handle received datagram.

        Dispatch to all clients registered on this port.
        """
        # Get the local port from the transport
        if self.port is None:
            try:
                # Try to get port from connection (we'll set it properly below)
                for port, protocol in _shared_protocols.items():
                    if protocol is self:
                        self.port = port
                        break
            except Exception:
                pass

        # Dispatch to all clients on this port
        if self.port and self.port in _clients_by_port:
            for client in _clients_by_port[self.port]:
                asyncio.create_task(client._handle_message(data, addr))
        else:
            _LOGGER.warning("Received message but no clients registered for port %s", self.port)

    def error_received(self, exc: Exception) -> None:
        """Handle protocol errors."""
        _LOGGER.error("Protocol error: %s", exc)

    def connection_lost(self, exc: Exception | None) -> None:
        """Invalidate the shared socket if it was lost unexpectedly.

        Deliberate closes (disconnect/reconnect) remove this protocol from the
        registry first, so they are ignored here.
        """
        for port, protocol in list(_shared_protocols.items()):
            if protocol is not self:
                continue
            _LOGGER.warning("UDP socket on port %s lost unexpectedly: %s", port, exc)
            _shared_transports.pop(port, None)
            _shared_protocols.pop(port, None)
            _transport_refcounts.pop(port, None)
            for client in _clients_by_port.pop(port, []):
                client.transport = None
                client.protocol = None
                client._connected = False  # pylint: disable=protected-access


class _PriorityFrame:  # pylint: disable=too-few-public-methods
    """The high-priority frame waiting for (or using) a send slot."""

    __slots__ = ("due",)

    def __init__(self, due: float) -> None:
        self.due = due  # Loop time from which the next attempt may be sent


class _Exchange:  # pylint: disable=too-few-public-methods
    """One request: its payload and the matching of its answer by id."""

    def __init__(self, client: MarstekUDPClient, method: str, msg_id: int, params: dict) -> None:
        self._client = client
        self.method = method
        self.msg_id = msg_id
        self.payload_str = json.dumps({"id": msg_id, "method": method, "params": params})
        self.response = asyncio.Event()
        self.data: dict[str, Any] = {}
        self.sent_at = 0.0  # Loop time of the latest attempt

    def handler(self, message, addr) -> None:
        """Handle command response."""
        client = self._client
        if message.get("id") == self.msg_id:
            if client.host and addr[0] != client.host:
                _LOGGER.debug("Ignoring response from wrong host: %s (expected %s)", addr[0], client.host)
                return  # Wrong device
            _LOGGER.debug("Matched response for %s from %s", self.method, addr)
            self.data.clear()
            self.data.update(message)
            self.response.set()
            # Wake the request if it is paused waiting for a send slot
            client._notify_scheduler()  # pylint: disable=protected-access
        else:
            # Track stray messages so we know if queues are backing up
            client._stale_message_counter += 1  # pylint: disable=protected-access
            stale = client._stale_message_counter  # pylint: disable=protected-access
            if stale <= 5 or stale % 25 == 0:
                _LOGGER.debug(
                    "Ignoring stale message while waiting for %s: got id=%s from %s (total stales=%d)",
                    self.method,
                    message.get("id"),
                    addr[0],
                    stale,
                )


class MarstekAPIError(Exception):
    """Exception for Marstek API errors."""


class MarstekCommandSuperseded(MarstekAPIError):
    """A high-priority command was replaced by a newer one before being answered."""
