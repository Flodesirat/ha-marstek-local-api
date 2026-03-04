#!/usr/bin/env python3
"""Capture real device responses as JSON fixtures for unit tests.

Queries every known API method and saves the raw responses under
tests/fixtures/<device_model>/ so they can be used as test data.

Usage:
  python3 test/capture_fixtures.py                   # auto-discover
  python3 test/capture_fixtures.py --ip 192.168.1.10 # specific IP
  python3 test/capture_fixtures.py --ip 192.168.1.10 --port 50000
  python3 test/capture_fixtures.py --out path/to/dir # custom output dir
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
INTEGRATION_PATH = REPO_ROOT / "custom_components" / "marstek_local_api"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "tests" / "fixtures"

DEFAULT_PORT = 50000
DISCOVERY_TIMEOUT = 9
COMMAND_TIMEOUT = 10
MAX_ATTEMPTS = 3

# ---------------------------------------------------------------------------
# Minimal async UDP client (no HA dependency)
# ---------------------------------------------------------------------------

class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, queue: asyncio.Queue):
        self._queue = queue

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        self._queue.put_nowait((data, addr))

    def error_received(self, exc: Exception) -> None:
        print(f"[UDP] Protocol error: {exc}", file=sys.stderr)


class UDPClient:
    """Lightweight async UDP client without HA dependency."""

    def __init__(self, host: str | None, port: int) -> None:
        self.host = host
        self.port = port
        self._transport: asyncio.DatagramTransport | None = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._msg_counter = 0

    async def connect(self) -> None:
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _Protocol(self._queue),
            local_addr=("0.0.0.0", self.port),
            allow_broadcast=True,
            reuse_port=True,
        )
        print(f"[UDP] Bound to port {self.port}")

    async def disconnect(self) -> None:
        if self._transport:
            self._transport.close()
            self._transport = None

    def _next_id(self) -> int:
        self._msg_counter = (self._msg_counter + 1) % 1_000_000
        return self._msg_counter

    async def send_command(
        self,
        method: str,
        params: dict | None = None,
        host: str | None = None,
        timeout: float = COMMAND_TIMEOUT,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> dict | None:
        if params is None:
            params = {"id": 0}

        target = host or self.host
        if not target:
            raise ValueError("No target host")

        msg_id = self._next_id()
        payload = json.dumps({"id": msg_id, "method": method, "params": params}).encode()

        for attempt in range(1, max_attempts + 1):
            self._transport.sendto(payload, (target, self.port))
            deadline = asyncio.get_running_loop().time() + timeout
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    data, addr = await asyncio.wait_for(self._queue.get(), timeout=remaining)
                except asyncio.TimeoutError:
                    break

                try:
                    msg = json.loads(data.decode())
                except json.JSONDecodeError:
                    continue

                if msg.get("id") != msg_id:
                    # Put back for other waiters (best-effort)
                    self._queue.put_nowait((data, addr))
                    await asyncio.sleep(0)
                    continue

                if "error" in msg:
                    err = msg["error"]
                    raise RuntimeError(f"API error {err.get('code')}: {err.get('message')}")

                return msg.get("result")

            if attempt < max_attempts:
                delay = 1.5 * (2.0 ** (attempt - 1))
                print(f"  [retry {attempt}/{max_attempts}] {method} timed out, waiting {delay:.1f}s")
                await asyncio.sleep(delay)

        return None

    async def broadcast(self, method: str, params: dict, timeout: float) -> list[dict]:
        """Broadcast and collect all responses within the timeout window."""
        payload = json.dumps({"id": 0, "method": method, "params": params}).encode()
        seen_macs: set[str] = set()
        devices: list[dict] = []

        # Get broadcast addresses
        broadcast_addrs = _get_broadcast_addresses()
        print(f"[Discovery] Broadcasting to: {', '.join(broadcast_addrs)}")

        end = asyncio.get_running_loop().time() + timeout
        last_send = 0.0

        while asyncio.get_running_loop().time() < end:
            now = asyncio.get_running_loop().time()
            if now - last_send >= 2.0:
                for addr in broadcast_addrs:
                    self._transport.sendto(payload, (addr, self.port))
                last_send = now

            remaining = min(2.0, end - asyncio.get_running_loop().time())
            if remaining <= 0:
                break

            try:
                data, addr = await asyncio.wait_for(self._queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                continue

            try:
                msg = json.loads(data.decode())
            except json.JSONDecodeError:
                continue

            result = msg.get("result")
            if not result:
                continue

            mac = result.get("ble_mac") or result.get("wifi_mac")
            if not mac or mac in seen_macs:
                continue

            seen_macs.add(mac)
            devices.append({"ip": addr[0], **result})
            print(f"[Discovery] Found: {result.get('device', '?')} at {addr[0]}")

        return devices


def _get_broadcast_addresses() -> list[str]:
    import struct
    import subprocess

    addrs: set[str] = set()
    try:
        result = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=2)
        for line in result.stdout.split("\n"):
            if "\tinet " in line:
                parts = line.strip().split()
                if "broadcast" in parts:
                    idx = parts.index("broadcast")
                    if idx + 1 < len(parts):
                        addrs.add(parts[idx + 1])
                elif len(parts) >= 2 and parts[0] == "inet":
                    ip = parts[1]
                    if not ip.startswith("127."):
                        octets = ip.split(".")
                        if len(octets) == 4:
                            addrs.add(f"{octets[0]}.{octets[1]}.{octets[2]}.255")
    except Exception:
        pass

    addrs.add("255.255.255.255")
    return list(addrs)


# ---------------------------------------------------------------------------
# API methods to capture
# ---------------------------------------------------------------------------

API_METHODS = [
    ("Marstek.GetDevice",  {"ble_mac": "0"},  "device"),
    ("Wifi.GetStatus",     {"id": 0},          "wifi"),
    ("BLE.GetStatus",      {"id": 0},          "ble"),
    ("Bat.GetStatus",      {"id": 0},          "battery"),
    ("PV.GetStatus",       {"id": 0},          "pv"),
    ("ES.GetStatus",       {"id": 0},          "es"),
    ("ES.GetMode",         {"id": 0},          "mode"),
    ("EM.GetStatus",       {"id": 0},          "em"),
]


# ---------------------------------------------------------------------------
# Capture logic
# ---------------------------------------------------------------------------

async def capture(ip: str, port: int, output_dir: Path) -> dict:
    """Query all API methods and return a dict of raw results."""
    client = UDPClient(ip, port)
    await client.connect()

    captured: dict[str, dict | None] = {}
    errors: dict[str, str] = {}

    try:
        for method, params, key in API_METHODS:
            print(f"  Querying {method}...", end=" ", flush=True)
            try:
                result = await client.send_command(method, params)
                if result is not None:
                    captured[key] = result
                    print("OK")
                else:
                    captured[key] = None
                    print("no response (timeout)")
            except RuntimeError as err:
                captured[key] = None
                errors[key] = str(err)
                print(f"ERROR: {err}")
            await asyncio.sleep(0.5)
    finally:
        await client.disconnect()

    return captured, errors


async def run(ip: str | None, port: int, output_dir: Path) -> None:
    client = UDPClient(None, port)
    await client.connect()

    # Discovery or direct
    if ip:
        devices = [{"ip": ip, "device": "Unknown", "ver": 0}]
        # Fetch device name
        print(f"\nConnecting to {ip}:{port}...")
        try:
            info = await client.send_command("Marstek.GetDevice", {"ble_mac": "0"}, host=ip, timeout=10)
            if info:
                devices[0].update(info)
        except Exception as err:
            print(f"  Warning: could not get device info: {err}")
    else:
        print(f"\nDiscovering devices (up to {DISCOVERY_TIMEOUT}s)...")
        devices = await client.broadcast(
            "Marstek.GetDevice", {"ble_mac": "0"}, timeout=DISCOVERY_TIMEOUT
        )

    await client.disconnect()

    if not devices:
        print("\nNo devices found. Try --ip <address>")
        sys.exit(1)

    print(f"\nFound {len(devices)} device(s).")

    for device in devices:
        model = device.get("device", "Unknown").replace(" ", "_")
        firmware = device.get("ver", 0)
        device_ip = device["ip"]

        slug = f"{model}_fw{firmware}"
        out_dir = output_dir / slug
        out_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n{'='*60}")
        print(f"Device: {model}  firmware={firmware}  ip={device_ip}")
        print(f"Output: {out_dir}")
        print(f"{'='*60}")

        captured, errors = await capture(device_ip, port, out_dir)

        # Write individual fixture files
        for key, data in captured.items():
            if data is not None:
                fixture_path = out_dir / f"{key}.json"
                fixture_path.write_text(json.dumps(data, indent=2) + "\n")
                print(f"  Saved {fixture_path.name}")

        # Write combined fixture
        meta = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "device_ip": device_ip,
            "device_model": model,
            "firmware_version": firmware,
        }
        combined = {
            "_meta": meta,
            **{k: v for k, v in captured.items() if v is not None},
        }
        if errors:
            combined["_errors"] = errors

        combined_path = out_dir / "all.json"
        combined_path.write_text(json.dumps(combined, indent=2) + "\n")
        print(f"\n  Combined fixture: {combined_path}")

        if errors:
            print(f"\n  Methods with errors: {list(errors.keys())}")
            for key, msg in errors.items():
                print(f"    {key}: {msg}")

    print("\nDone.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture Marstek API responses as test fixtures")
    parser.add_argument("--ip", help="Device IP (skip discovery)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"UDP port (default: {DEFAULT_PORT})")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory")
    args = parser.parse_args()

    try:
        asyncio.run(run(args.ip, args.port, args.out))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(0)
    except PermissionError as err:
        print(f"\nPermission error: {err}")
        print(f"Try: sudo python3 {Path(__file__).name}")
        sys.exit(1)


if __name__ == "__main__":
    main()
