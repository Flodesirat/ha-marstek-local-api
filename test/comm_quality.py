#!/usr/bin/env python3
"""Measure UDP communication quality with a Marstek device.

Sends repeated requests and tracks response rate, latency, and loss per method.
Results are printed live and summarised at the end.

Usage:
  python3 test/comm_quality.py                       # auto-discover, 60s
  python3 test/comm_quality.py --ip 192.168.0.104    # specific IP
  python3 test/comm_quality.py --duration 300        # 5-minute run
  python3 test/comm_quality.py --interval 5          # poll every 5s
  python3 test/comm_quality.py --method ES.GetStatus # single method
  python3 test/comm_quality.py --out results.json    # save JSON report
"""
from __future__ import annotations

import argparse
import asyncio
import json
import signal
import socket
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_PORT      = 30000
DISCOVERY_TIMEOUT = 9
COMMAND_TIMEOUT   = 2.0   # seconds to wait per request
POLL_METHODS      = [
    ("ES.GetStatus",    {"id": 0}),
    ("Bat.GetStatus",   {"id": 0}),
    ("EM.GetStatus",    {"id": 0}),
    ("ES.GetMode",      {"id": 0}),
]


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@dataclass
class MethodStats:
    method: str
    sent:      int = 0
    ok:        int = 0
    timeout:   int = 0
    error:     int = 0
    latencies: list[float] = field(default_factory=list)

    @property
    def loss_pct(self) -> float:
        return 100.0 * (self.sent - self.ok) / self.sent if self.sent else 0.0

    @property
    def avg_ms(self) -> float:
        return 1000.0 * sum(self.latencies) / len(self.latencies) if self.latencies else 0.0

    @property
    def min_ms(self) -> float:
        return 1000.0 * min(self.latencies) if self.latencies else 0.0

    @property
    def max_ms(self) -> float:
        return 1000.0 * max(self.latencies) if self.latencies else 0.0

    @property
    def p95_ms(self) -> float:
        if not self.latencies:
            return 0.0
        s = sorted(self.latencies)
        idx = max(0, int(len(s) * 0.95) - 1)
        return 1000.0 * s[idx]


# ---------------------------------------------------------------------------
# Minimal async UDP client (no HA dependency)
# ---------------------------------------------------------------------------

class _Proto(asyncio.DatagramProtocol):
    def __init__(self, queue: asyncio.Queue):
        self._q = queue

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        self._q.put_nowait((data, addr))

    def error_received(self, exc: Exception) -> None:
        pass  # Ignore ICMP unreachable etc.


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

    async def request(
        self, method: str, params: dict, timeout: float = COMMAND_TIMEOUT
    ) -> tuple[Optional[dict], float]:
        """Send one request. Returns (result_or_None, latency_seconds)."""
        self._seq = (self._seq + 1) % 1_000_000
        seq = self._seq
        payload = json.dumps({"id": seq, "method": method, "params": params}).encode()
        t0 = asyncio.get_running_loop().time()
        self._transport.sendto(payload, (self.host, self.port))

        deadline = t0 + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return None, timeout
            try:
                data, _ = await asyncio.wait_for(self._queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                return None, timeout
            try:
                msg = json.loads(data.decode())
            except json.JSONDecodeError:
                continue
            if msg.get("id") != seq:
                # Stale reply from a previous round — put back and keep waiting
                self._queue.put_nowait((data, _))
                await asyncio.sleep(0)
                continue
            latency = asyncio.get_running_loop().time() - t0
            if "error" in msg:
                return msg["error"], latency
            return msg.get("result"), latency


# ---------------------------------------------------------------------------
# Discovery (reused from capture_fixtures)
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
    addrs = _broadcast_addrs()
    print(f"Discovering on port {port} (broadcast: {', '.join(addrs)})…")
    found_ip: Optional[str] = None
    end = loop.time() + DISCOVERY_TIMEOUT
    last_send = 0.0
    try:
        while loop.time() < end:
            if loop.time() - last_send >= 2.0:
                for a in addrs:
                    transport.sendto(payload, (a, port))
                last_send = loop.time()
            try:
                data, addr = await asyncio.wait_for(queue.get(), timeout=2.0)
                msg = json.loads(data.decode())
                result = msg.get("result", {})
                if result.get("device"):
                    ip = result.get("ip", addr[0])
                    model = result.get("device", "?")
                    fw = result.get("ver", "?")
                    print(f"Found: {model} fw{fw} @ {ip}")
                    found_ip = ip
                    break
            except asyncio.TimeoutError:
                continue
            except Exception:
                continue
    finally:
        transport.close()
    return found_ip


# ---------------------------------------------------------------------------
# Live display
# ---------------------------------------------------------------------------

def _bar(ok: int, total: int, width: int = 20) -> str:
    if total == 0:
        return "─" * width
    filled = int(width * ok / total)
    return "█" * filled + "░" * (width - filled)


def _print_live(stats: dict[str, MethodStats], elapsed: float, total_dur: float) -> None:
    # Move cursor up (number of methods + header lines)
    n_methods = len(stats)
    if elapsed > 0:
        print(f"\033[{n_methods + 3}A", end="")

    pct = min(100.0, 100.0 * elapsed / total_dur) if total_dur else 100.0
    bar_width = 30
    filled = int(bar_width * pct / 100)
    prog_bar = "█" * filled + "░" * (bar_width - filled)
    print(f"  [{prog_bar}] {elapsed:5.0f}s / {total_dur:.0f}s")
    print()
    print(f"  {'Method':<22} {'Sent':>5}  {'OK':>5}  {'Loss':>6}  {'Avg':>7}  {'p95':>7}  {'Max':>7}  {'Quality':<22}")
    print(f"  {'─'*22} {'─'*5}  {'─'*5}  {'─'*6}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*22}")
    for s in stats.values():
        ok_rate = 100.0 - s.loss_pct
        quality = _bar(s.ok, s.sent)
        loss_str = f"{s.loss_pct:5.1f}%"
        avg_str  = f"{s.avg_ms:6.0f}ms" if s.latencies else "     N/A"
        p95_str  = f"{s.p95_ms:6.0f}ms" if s.latencies else "     N/A"
        max_str  = f"{s.max_ms:6.0f}ms" if s.latencies else "     N/A"
        print(f"  {s.method:<22} {s.sent:>5}  {s.ok:>5}  {loss_str}  {avg_str}  {p95_str}  {max_str}  {quality}")


def _print_summary(stats: dict[str, MethodStats], elapsed: float, target_ip: str, interval: float) -> None:
    print("\n" + "═" * 78)
    print("  SUMMARY")
    print("═" * 78)
    print(f"  Device:    {target_ip}")
    print(f"  Duration:  {elapsed:.1f}s")
    print(f"  Interval:  {interval}s")
    print()

    all_ok   = sum(s.ok for s in stats.values())
    all_sent = sum(s.sent for s in stats.values())
    overall_loss = 100.0 * (all_sent - all_ok) / all_sent if all_sent else 0.0
    print(f"  Overall:   {all_ok}/{all_sent} responses  loss={overall_loss:.1f}%")
    print()

    for s in stats.values():
        icon = "✅" if s.loss_pct < 5 else "⚠️ " if s.loss_pct < 20 else "❌"
        line = f"  {icon} {s.method:<22}  loss={s.loss_pct:5.1f}%"
        if s.latencies:
            line += f"  avg={s.avg_ms:.0f}ms  p95={s.p95_ms:.0f}ms  max={s.max_ms:.0f}ms"
        if s.timeout:
            line += f"  ({s.timeout} timeouts)"
        if s.error:
            line += f"  ({s.error} errors)"
        print(line)

    print("═" * 78)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def run(
    ip: Optional[str],
    port: int,
    duration: float,
    interval: float,
    methods: list[tuple[str, dict]],
    out: Optional[Path],
) -> None:
    target_ip = ip or await discover(port)
    if not target_ip:
        print("No device found. Use --ip <address>.")
        sys.exit(1)

    client = UDPClient(target_ip, port)
    await client.connect()

    stats = {m: MethodStats(method=m) for m, _ in methods}
    start = asyncio.get_running_loop().time()
    last_display = -1.0
    stop = False

    def _sigint(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _sigint)

    # Print blank lines so the live display can overwrite them
    n_lines = len(methods) + 4
    print("\n" * n_lines, end="")
    elapsed = 0.0

    try:
        while not stop and elapsed < duration:
            round_start = asyncio.get_running_loop().time()
            elapsed = round_start - start

            for method, params in methods:
                s = stats[method]
                s.sent += 1
                result, latency = await client.request(method, params)
                if result is None:
                    s.timeout += 1
                elif isinstance(result, dict) and "code" in result:
                    # API error response
                    s.error += 1
                else:
                    s.ok += 1
                    s.latencies.append(latency)

            _print_live(stats, elapsed, duration)

            # Wait for next interval
            spent = asyncio.get_running_loop().time() - round_start
            await asyncio.sleep(max(0.0, interval - spent))

    finally:
        await client.disconnect()

    elapsed = asyncio.get_running_loop().time() - start
    _print_summary(stats, elapsed, target_ip, interval)

    if out:
        report = {
            "_meta": {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "device_ip": target_ip,
                "duration_s": elapsed,
                "interval_s": interval,
            },
            "methods": {
                m: {
                    "sent":       s.sent,
                    "ok":         s.ok,
                    "timeout":    s.timeout,
                    "error":      s.error,
                    "loss_pct":   round(s.loss_pct, 2),
                    "avg_ms":     round(s.avg_ms, 1),
                    "min_ms":     round(s.min_ms, 1),
                    "max_ms":     round(s.max_ms, 1),
                    "p95_ms":     round(s.p95_ms, 1),
                    "latencies_ms": [round(l * 1000, 1) for l in s.latencies],
                }
                for m, s in stats.items()
            },
        }
        out.write_text(json.dumps(report, indent=2) + "\n")
        print(f"\n  Report saved: {out}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure UDP communication quality with a Marstek device",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--ip",       help="Device IP (skip discovery)")
    parser.add_argument("--port",     type=int,   default=DEFAULT_PORT, help="UDP port")
    parser.add_argument("--duration", type=float, default=60,           help="Test duration in seconds")
    parser.add_argument("--interval", type=float, default=10,           help="Poll interval in seconds")
    parser.add_argument("--method",   action="append", metavar="METHOD",
                        help="Method to poll (repeatable). Default: ES.GetStatus Bat.GetStatus EM.GetStatus ES.GetMode")
    parser.add_argument("--out",      type=Path,  default=None,         help="Save JSON report to this file")
    args = parser.parse_args()

    if args.method:
        methods = [(m, {"id": 0}) for m in args.method]
    else:
        methods = POLL_METHODS

    try:
        asyncio.run(run(args.ip, args.port, args.duration, args.interval, methods, args.out))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
