#!/usr/bin/env python3
"""Ping-like UDP latency tool for Marstek devices.

Sends one request per second (configurable) and prints each result
immediately, like the standard ping command.

Usage:
  python3 test/mping.py --ip 192.168.0.104
  python3 test/mping.py --ip 192.168.0.104 --cmd Bat.GetStatus
  python3 test/mping.py --ip 192.168.0.104 --cmd EM.GetStatus --interval 2
  python3 test/mping.py --ip 192.168.0.104 --count 10
  python3 test/mping.py                                # auto-discover
"""
from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DEFAULT_PORT    = 50000
DEFAULT_CMD     = "ES.GetStatus"
DEFAULT_PARAMS  = {"id": 0}
DEFAULT_TIMEOUT = 2.0   # seconds — matches COMMAND_TIMEOUT in const.py
DEFAULT_INTERVAL = 1.0  # seconds between pings


# ---------------------------------------------------------------------------
# Minimal async UDP client
# ---------------------------------------------------------------------------

class _Proto(asyncio.DatagramProtocol):
    def __init__(self, queue: asyncio.Queue):
        self._q = queue

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        self._q.put_nowait((data, addr))

    def error_received(self, exc: Exception) -> None:
        pass


class UDPClient:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._transport: asyncio.DatagramTransport | None = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._seq = 0

    async def connect(self) -> None:
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _Proto(self._queue),
            local_addr=("0.0.0.0", self.port),
            allow_broadcast=True,
            reuse_port=True,
        )

    async def disconnect(self) -> None:
        if self._transport:
            self._transport.close()
            self._transport = None

    async def ping(
        self, method: str, params: dict, seq: int, timeout: float
    ) -> tuple[Optional[float], str]:
        """Send one request. Returns (latency_ms, status)."""
        payload = json.dumps({"id": seq, "method": method, "params": params}).encode()
        t0 = asyncio.get_running_loop().time()
        self._transport.sendto(payload, (self.host, self.port))

        deadline = t0 + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return None, "timeout"
            try:
                data, addr = await asyncio.wait_for(self._queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                return None, "timeout"
            try:
                msg = json.loads(data.decode())
            except json.JSONDecodeError:
                continue
            if msg.get("id") != seq:
                self._queue.put_nowait((data, addr))
                await asyncio.sleep(0)
                continue
            latency_ms = (asyncio.get_running_loop().time() - t0) * 1000
            if "error" in msg:
                return latency_ms, f"error {msg['error'].get('code', '?')}: {msg['error'].get('message', '')}"
            return latency_ms, "ok"


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _broadcast_addrs() -> list[str]:
    import subprocess
    addrs: set[str] = set()
    try:
        r = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=2)
        for line in r.stdout.split("\n"):
            if "\tinet " in line:
                parts = line.strip().split()
                if "broadcast" in parts:
                    idx = parts.index("broadcast")
                    if idx + 1 < len(parts):
                        addrs.add(parts[idx + 1])
    except Exception:
        pass
    addrs.add("255.255.255.255")
    return list(addrs)


async def discover(port: int) -> Optional[str]:
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: _Proto(queue),
        local_addr=("0.0.0.0", port),
        allow_broadcast=True,
        reuse_port=True,
    )
    payload = json.dumps({"id": 0, "method": "Marstek.GetDevice", "params": {"ble_mac": "0"}}).encode()
    found_ip = None
    end = loop.time() + 9
    last_send = 0.0
    try:
        while loop.time() < end:
            if loop.time() - last_send >= 2.0:
                for a in _broadcast_addrs():
                    transport.sendto(payload, (a, port))
                last_send = loop.time()
            try:
                data, addr = await asyncio.wait_for(queue.get(), timeout=2.0)
                msg = json.loads(data.decode())
                if msg.get("result", {}).get("device"):
                    found_ip = msg["result"].get("ip", addr[0])
                    model = msg["result"].get("device", "?")
                    fw = msg["result"].get("ver", "?")
                    print(f"MPING {model} fw{fw} ({found_ip}): method={args_method}, timeout={args_timeout*1000:.0f}ms")
                    break
            except (asyncio.TimeoutError, Exception):
                continue
    finally:
        transport.close()
    return found_ip


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# Globals used for signal handler and discovery header
args_method  = DEFAULT_CMD
args_timeout = DEFAULT_TIMEOUT


async def run(
    ip: Optional[str],
    port: int,
    method: str,
    params: dict,
    timeout: float,
    interval: float,
    count: Optional[int],
) -> None:
    global args_method, args_timeout
    args_method  = method
    args_timeout = timeout

    target_ip = ip or await discover(port)
    if not target_ip:
        print("No device found. Use --ip <address>.")
        sys.exit(1)

    if ip:
        print(f"MPING {target_ip}: method={method}, timeout={timeout*1000:.0f}ms")

    client = UDPClient(target_ip, port)
    await client.connect()

    sent = received = 0
    latencies: list[float] = []
    stop = False

    def _sigint(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _sigint)

    seq = 1
    try:
        while not stop and (count is None or seq <= count):
            round_start = asyncio.get_running_loop().time()
            sent += 1

            latency_ms, status = await client.ping(method, params, seq, timeout)

            if status == "ok":
                received += 1
                latencies.append(latency_ms)
                print(f"response seq={seq} time={latency_ms:.1f}ms")
            elif status == "timeout":
                print(f"timeout    seq={seq} (>{timeout*1000:.0f}ms)")
            else:
                print(f"error      seq={seq} {status}")

            seq += 1

            spent = asyncio.get_running_loop().time() - round_start
            wait = interval - spent
            if wait > 0 and (count is None or seq <= count):
                await asyncio.sleep(wait)

    finally:
        await client.disconnect()

    # Statistics
    loss = 100.0 * (sent - received) / sent if sent else 0.0
    print(f"\n--- {target_ip} {method} ping statistics ---")
    print(f"{sent} requests, {received} responses, {loss:.1f}% loss")
    if latencies:
        print(
            f"rtt min/avg/max = "
            f"{min(latencies):.1f}/{sum(latencies)/len(latencies):.1f}/{max(latencies):.1f} ms"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ping-like latency tool for Marstek UDP API",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--ip",       help="Device IP (skip discovery)")
    parser.add_argument("--port",     type=int,   default=DEFAULT_PORT)
    parser.add_argument("--cmd",      default=DEFAULT_CMD,
                        help="API method to send  (e.g. ES.GetStatus, Bat.GetStatus, EM.GetStatus, ES.GetMode, Marstek.GetDevice)")
    parser.add_argument("--timeout",  type=float, default=DEFAULT_TIMEOUT,
                        help="Reply timeout in seconds")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                        help="Seconds between pings")
    parser.add_argument("--count",    type=int,   default=None,
                        help="Stop after N pings (default: run until Ctrl+C)")
    args = parser.parse_args()

    params = DEFAULT_PARAMS.copy()
    if args.cmd == "Marstek.GetDevice":
        params = {"ble_mac": "0"}

    try:
        asyncio.run(run(args.ip, args.port, args.cmd, params, args.timeout, args.interval, args.count))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
