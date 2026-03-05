#!/usr/bin/env python3
"""Diagnose root causes of UDP packet loss with a Marstek device.

Runs three targeted tests and produces a verdict:

  Test 1 — Port interference
    Tries to detect whether another process (e.g. Home Assistant) is sharing
    the same UDP port and stealing response packets.

  Test 2 — Isolation baseline
    Polls a single method at a time with a generous gap between requests.
    Near-zero loss here means combined/fast polling is overwhelming the device.
    Significant loss even here points to firmware rate limiting or a deeper issue.

  Test 3 — Rate sweep
    Polls all methods at progressively slower intervals (5 → 10 → 20 → 30 s)
    and finds the minimum safe interval where loss stays below the target threshold.

Usage:
  python3 test/diagnose_comms.py --ip 192.168.0.104
  python3 test/diagnose_comms.py --ip 192.168.0.104 --target-loss 5
  python3 test/diagnose_comms.py --ip 192.168.0.104 --out diagnosis.json
  python3 test/diagnose_comms.py --ip 192.168.0.104 --skip-sweep   # faster run
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
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

DEFAULT_PORT   = 50000
CMD_TIMEOUT    = 2.0   # seconds to wait per request
METHODS        = [
    ("ES.GetStatus",  {"id": 0}),
    ("Bat.GetStatus", {"id": 0}),
    ("EM.GetStatus",  {"id": 0}),
    ("ES.GetMode",    {"id": 0}),
]
ISOLATION_REPS  = 12   # requests per method in isolation test
ISOLATION_GAP   = 3.0  # seconds between isolation requests
SWEEP_INTERVALS = [30, 20, 15, 10, 5]   # seconds, longest first
SWEEP_REPS      = 10   # rounds per interval in the sweep


# ---------------------------------------------------------------------------
# Minimal async UDP client (no HA dependency)
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

    async def request(
        self, method: str, params: dict, timeout: float = CMD_TIMEOUT
    ) -> tuple[Optional[dict], float, str]:
        """Send one request. Returns (result, latency_s, status)."""
        self._seq = (self._seq + 1) % 1_000_000
        seq = self._seq
        payload = json.dumps({"id": seq, "method": method, "params": params}).encode()
        t0 = asyncio.get_running_loop().time()
        self._transport.sendto(payload, (self.host, self.port))

        deadline = t0 + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return None, timeout, "timeout"
            try:
                data, addr = await asyncio.wait_for(self._queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                return None, timeout, "timeout"
            try:
                msg = json.loads(data.decode())
            except json.JSONDecodeError:
                continue
            if msg.get("id") != seq:
                self._queue.put_nowait((data, addr))
                await asyncio.sleep(0)
                continue
            latency = asyncio.get_running_loop().time() - t0
            if "error" in msg:
                return msg["error"], latency, "error"
            return msg.get("result"), latency, "ok"

    async def flush_stale(self, window: float = 0.3) -> int:
        """Drain any queued packets from previous rounds. Returns count discarded."""
        discarded = 0
        deadline = asyncio.get_running_loop().time() + window
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(self._queue.get(), timeout=remaining)
                discarded += 1
            except asyncio.TimeoutError:
                break
        return discarded


# ---------------------------------------------------------------------------
# Test 1 — Port interference
# ---------------------------------------------------------------------------

def check_port_interference(port: int) -> dict:
    """Attempt to detect processes competing for port responses."""
    result = {
        "port": port,
        "exclusive_bind_ok": False,
        "pids_on_port": [],
        "verdict": "",
    }

    # Try exclusive bind (no SO_REUSEPORT) → fails if something already owns it
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        s.bind(("0.0.0.0", port))
        s.close()
        result["exclusive_bind_ok"] = True
    except OSError:
        result["exclusive_bind_ok"] = False

    # Try to identify pids via /proc/net/udp (Linux only)
    try:
        target_hex = f"{port:04X}"
        with open("/proc/net/udp") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 10 and parts[1].endswith(f":{target_hex}"):
                    inode = parts[9]
                    result["pids_on_port"].append({"inode": inode})

        # Resolve inodes → pids
        pids = []
        for entry in result["pids_on_port"]:
            inode = entry["inode"]
            for pid in os.listdir("/proc"):
                if not pid.isdigit():
                    continue
                try:
                    fd_dir = f"/proc/{pid}/fd"
                    for fd in os.listdir(fd_dir):
                        link = os.readlink(f"{fd_dir}/{fd}")
                        if f"socket:[{inode}]" in link:
                            try:
                                with open(f"/proc/{pid}/comm") as c:
                                    comm = c.read().strip()
                            except Exception:
                                comm = "?"
                            pids.append({"pid": int(pid), "name": comm})
                except (PermissionError, FileNotFoundError):
                    continue
        result["pids_on_port"] = pids
    except Exception:
        pass  # /proc not available (macOS, etc.)

    if not result["exclusive_bind_ok"] and result["pids_on_port"]:
        names = ", ".join(p["name"] for p in result["pids_on_port"])
        result["verdict"] = (
            f"⚠️  INTERFERENCE DETECTED — process(es) [{names}] also have port {port} open. "
            f"Each UDP response is delivered to only one socket, splitting traffic and causing apparent packet loss. "
            f"Stop Home Assistant (or the competing process) before measuring."
        )
    elif not result["exclusive_bind_ok"]:
        result["verdict"] = (
            f"⚠️  Port {port} is already in use by another process (could not identify pid). "
            f"Stop Home Assistant before measuring to get accurate results."
        )
    else:
        result["verdict"] = f"✅  No interference detected on port {port}."

    return result


# ---------------------------------------------------------------------------
# Test 2 — Isolation baseline
# ---------------------------------------------------------------------------

async def test_isolation(client: UDPClient, reps: int, gap: float) -> dict:
    """Poll each method alone with a generous gap. Minimal loss = healthy device."""
    results = {}
    total_methods = len(METHODS)
    for method_idx, (method, params) in enumerate(METHODS):
        ok = timeouts = errors = 0
        latencies = []
        print(f"  [{method_idx + 1}/{total_methods}] {method} × {reps} (gap={gap}s) …", flush=True)
        for i in range(reps):
            stale = await client.flush_stale()
            result, latency, status = await client.request(method, params)
            if status == "ok":
                ok += 1
                latencies.append(latency)
            elif status == "timeout":
                timeouts += 1
            else:
                errors += 1
            if i < reps - 1:
                await asyncio.sleep(gap)
        loss = 100.0 * (reps - ok) / reps
        avg  = 1000.0 * sum(latencies) / len(latencies) if latencies else 0
        results[method] = {
            "sent": reps, "ok": ok, "timeout": timeouts, "error": errors,
            "loss_pct": round(loss, 1),
            "avg_ms": round(avg, 1),
        }
        icon = "✅" if loss < 5 else "⚠️ " if loss < 20 else "❌"
        print(f"       → {icon} loss={loss:.0f}%  avg={avg:.0f}ms  ({timeouts} timeouts)")
        await asyncio.sleep(gap)  # rest between methods

    # Interpret
    avg_loss = sum(r["loss_pct"] for r in results.values()) / len(results)
    if avg_loss < 5:
        verdict = (
            "✅  Isolation loss is near zero — the device responds reliably when not overloaded. "
            "Root cause is likely COMBINED POLLING RATE: requests are sent faster than the device "
            "can process them (or a concurrent process is competing on the port)."
        )
    elif avg_loss < 20:
        verdict = (
            "⚠️  Moderate loss even in isolation. The device may have a firmware rate limit "
            "or occasional processing delays. Try increasing the poll interval."
        )
    else:
        verdict = (
            "❌  High loss even in isolation. Likely causes: persistent firmware bug, "
            "device CPU overloaded by other tasks (BLE, cloud sync), or poor UDP path."
        )

    return {"per_method": results, "avg_loss_pct": round(avg_loss, 1), "verdict": verdict}


# ---------------------------------------------------------------------------
# Test 3 — Rate sweep
# ---------------------------------------------------------------------------

async def test_rate_sweep(
    client: UDPClient, intervals: list[int], reps: int, target_loss: float
) -> dict:
    """Poll all methods at each interval, longest first. Find the safe interval."""
    results = {}
    safe_interval: Optional[int] = None

    print(f"  Sweeping {len(intervals)} intervals: {intervals} seconds")
    print(f"  {reps} rounds per interval, target loss < {target_loss:.0f}%\n")

    for interval in intervals:
        ok = sent = 0
        per_method: dict[str, dict] = {m: {"ok": 0, "sent": 0} for m, _ in METHODS}

        for rnd in range(reps):
            round_start = asyncio.get_running_loop().time()
            stale = await client.flush_stale()
            for method, params in METHODS:
                per_method[method]["sent"] += 1
                sent += 1
                _, latency, status = await client.request(method, params)
                if status == "ok":
                    ok += 1
                    per_method[method]["ok"] += 1
            spent = asyncio.get_running_loop().time() - round_start
            remaining = interval - spent
            if remaining > 0 and rnd < reps - 1:
                await asyncio.sleep(remaining)

        loss = 100.0 * (sent - ok) / sent if sent else 0.0
        icon = "✅" if loss < target_loss else "❌"
        print(f"  {icon} interval={interval:>3}s  loss={loss:5.1f}%  ({ok}/{sent} ok)")
        results[interval] = {"sent": sent, "ok": ok, "loss_pct": round(loss, 1), "per_method": per_method}

        if loss < target_loss and safe_interval is None:
            safe_interval = interval

    if safe_interval:
        verdict = (
            f"✅  Safe interval found: {safe_interval}s (loss < {target_loss:.0f}%). "
            f"Set the integration poll interval to at least {safe_interval}s."
        )
    else:
        verdict = (
            f"❌  Loss stayed above {target_loss:.0f}% even at {max(intervals)}s interval. "
            f"The problem is not polling rate — check port interference (Test 1) and device firmware."
        )

    return {"per_interval": results, "safe_interval": safe_interval, "verdict": verdict}


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
                    ip = msg["result"].get("ip", addr[0])
                    print(f"Found: {msg['result']['device']} @ {ip}")
                    found_ip = ip
                    break
            except (asyncio.TimeoutError, Exception):
                continue
    finally:
        transport.close()
    return found_ip


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(
    ip: Optional[str],
    port: int,
    target_loss: float,
    skip_sweep: bool,
    out: Optional[Path],
) -> None:
    target_ip = ip or await discover(port)
    if not target_ip:
        print("No device found. Use --ip <address>.")
        sys.exit(1)

    print(f"\n{'═' * 70}")
    print(f"  Marstek Communication Diagnostics")
    print(f"  Device: {target_ip}:{port}")
    print(f"{'═' * 70}\n")

    report: dict = {
        "_meta": {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "device_ip": target_ip,
            "port": port,
        }
    }

    # ── Test 1: Port interference ──────────────────────────────────────────
    print("▶  Test 1/3 — Port interference check")
    print("─" * 70)
    interference = check_port_interference(port)
    print(f"  Exclusive bind: {'OK' if interference['exclusive_bind_ok'] else 'FAILED'}")
    if interference["pids_on_port"]:
        for p in interference["pids_on_port"]:
            print(f"  Process on port: pid={p['pid']}  name={p['name']}")
    print(f"\n  {interference['verdict']}\n")
    report["test1_interference"] = interference

    # ── Test 2: Isolation ─────────────────────────────────────────────────
    print("▶  Test 2/3 — Isolation baseline")
    print("─" * 70)
    print(f"  Each method polled alone, {ISOLATION_REPS}× with {ISOLATION_GAP}s gap\n")
    client = UDPClient(target_ip, port)
    await client.connect()
    try:
        isolation = await test_isolation(client, ISOLATION_REPS, ISOLATION_GAP)
        print(f"\n  Average isolation loss: {isolation['avg_loss_pct']:.1f}%")
        print(f"\n  {isolation['verdict']}\n")
        report["test2_isolation"] = isolation

        # ── Test 3: Rate sweep ─────────────────────────────────────────────
        if not skip_sweep:
            print("▶  Test 3/3 — Rate sweep (all methods combined)")
            print("─" * 70)
            sweep = await test_rate_sweep(client, SWEEP_INTERVALS, SWEEP_REPS, target_loss)
            print(f"\n  {sweep['verdict']}\n")
            report["test3_rate_sweep"] = sweep
        else:
            print("▶  Test 3/3 — Rate sweep  [skipped]\n")
            report["test3_rate_sweep"] = {"skipped": True}

    finally:
        await client.disconnect()

    # ── Final verdict ─────────────────────────────────────────────────────
    print("═" * 70)
    print("  DIAGNOSIS")
    print("═" * 70)

    interference_detected = not interference["exclusive_bind_ok"]
    isolation_ok = isolation["avg_loss_pct"] < 5
    safe_interval = report.get("test3_rate_sweep", {}).get("safe_interval")

    if interference_detected and isolation_ok:
        print("\n  🔴 PRIMARY CAUSE: Port interference")
        print("     Another process (likely Home Assistant) is competing on port")
        print(f"     {port}. Stop HA before running the integration or the test tools.")
        print("     Each UDP datagram is delivered to only one subscriber, so ~50%")
        print("     of responses are going to the wrong socket.")

    elif not interference_detected and isolation_ok and safe_interval:
        print(f"\n  🟡 PRIMARY CAUSE: Polling too fast")
        print(f"     Device is healthy in isolation (loss < 5%) but overwhelmed")
        print(f"     when polled at 10s with multiple methods concurrently.")
        print(f"     ➜  Recommended interval: {safe_interval}s")
        print(f"     Set 'scan_interval: {safe_interval}' in the integration options.")

    elif not interference_detected and isolation_ok and not safe_interval:
        print(f"\n  🟡 PRIMARY CAUSE: Polling too fast (even 30s may not be enough)")
        print(f"     Consider splitting the diagnostic further — the device may")
        print(f"     need a longer cooldown between requests.")

    elif not isolation_ok and interference_detected:
        print("\n  🔴 MULTIPLE CAUSES: Port interference + device-side issues")
        print("     Fix the port conflict first, then re-run to isolate the rest.")

    else:
        print("\n  🔴 PRIMARY CAUSE: Device-side (firmware or hardware)")
        print("     Loss persists even in isolation with no port conflict.")
        print("     → Update device firmware.")
        print("     → Check BLE, cloud sync activity on the device.")
        print("     → Report to Marstek with this diagnostic output.")

    print()

    if out:
        out.write_text(json.dumps(report, indent=2) + "\n")
        print(f"  Report saved: {out}")

    print("═" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diagnose root causes of Marstek UDP packet loss",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--ip",          help="Device IP (skip discovery)")
    parser.add_argument("--port",        type=int,   default=DEFAULT_PORT)
    parser.add_argument("--target-loss", type=float, default=5.0,
                        help="Loss %% threshold below which an interval is considered safe")
    parser.add_argument("--skip-sweep",  action="store_true",
                        help="Skip the rate sweep (Tests 1+2 only, much faster)")
    parser.add_argument("--out",         type=Path,  default=None,
                        help="Save JSON diagnosis report to this file")
    args = parser.parse_args()

    try:
        asyncio.run(run(args.ip, args.port, args.target_loss, args.skip_sweep, args.out))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(0)
    except PermissionError as e:
        print(f"\nPermission error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
