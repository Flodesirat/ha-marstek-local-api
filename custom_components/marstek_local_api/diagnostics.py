"""Diagnostics support for Marstek Local API."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.redact import async_redact_data

from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import MarstekDataUpdateCoordinator, MarstekMultiDeviceCoordinator

TO_REDACT = ["wifi_name", "ssid", "ble_mac", "wifi_mac", "device_ip"]
RECENT_FRAMES_LIMIT = 8


def _command_compatibility_summary(command_stats: dict[str, Any]) -> dict[str, Any]:
    """Generate compatibility summary from command statistics."""
    supported = []
    unsupported = []
    unknown = []

    for method, stats in command_stats.items():
        support_status = stats.get("supported")
        if support_status is True:
            supported.append(method)
        elif support_status is False:
            unsupported.append(method)
        else:
            unknown.append(method)

    return {
        "supported_commands": supported,
        "unsupported_commands": unsupported,
        "unknown_commands": unknown,
        "support_ratio": f"{len(supported)}/{len(command_stats)}",
    }


def _command_stats_snapshot(coordinator: MarstekDataUpdateCoordinator) -> dict[str, Any]:
    """Get all command statistics for compatibility tracking."""
    return coordinator.api.get_all_command_stats()


def _coordinator_snapshot(coordinator: MarstekDataUpdateCoordinator) -> dict[str, Any]:
    update_interval = coordinator.update_interval.total_seconds() if coordinator.update_interval else None

    # Get device identification from coordinator data
    device_info = coordinator.data.get("device", {}) if coordinator.data else {}

    # Get command stats
    command_stats = _command_stats_snapshot(coordinator)
    compatibility_summary = _command_compatibility_summary(command_stats)

    # Strip source IP/port from raw frames before exposing them
    raw_frames = coordinator.api.get_recent_frames()[-RECENT_FRAMES_LIMIT:]
    recent_frames = [{"ts": f["ts"], "frame": f["frame"]} for f in raw_frames]

    snapshot = {
        # Device identification
        "device_model": device_info.get("device") or coordinator.device_model,
        "firmware_version": device_info.get("ver") or coordinator.firmware_version,
        "ble_mac": device_info.get("ble_mac"),
        "wifi_mac": device_info.get("wifi_mac"),
        "wifi_name": device_info.get("wifi_name"),
        "device_ip": device_info.get("ip"),

        # Coordinator info
        "update_interval": update_interval,
        "update_count": coordinator.update_count,

        # Current sensor data
        "sensor_data": coordinator.data,

        # Command compatibility matrix
        "command_compatibility": command_stats,
        "compatibility_summary": compatibility_summary,

        # Raw frames — last RECENT_FRAMES_LIMIT messages received from the device.
        # Use this to verify whether unexpected values come from the device itself
        # or from an integration bug.
        "recent_raw_frames": recent_frames,
    }

    return async_redact_data(snapshot, TO_REDACT)


def _multi_diagnostics(coordinator: MarstekMultiDeviceCoordinator) -> dict[str, Any]:
    # Use indexed keys to avoid leaking MAC addresses
    devices: dict[str, Any] = {
        f"device_{i}": _coordinator_snapshot(device_coordinator)
        for i, device_coordinator in enumerate(coordinator.device_coordinators.values())
    }

    aggregates = coordinator.data.get("aggregates") if coordinator.data else None

    return {
        "requested_interval": coordinator.update_interval.total_seconds() if coordinator.update_interval else None,
        "devices": devices,
        "aggregates": aggregates,
    }


def _entity_states_snapshot(hass: HomeAssistant, entry_id: str) -> dict[str, Any]:
    """Get current state of all registered entities for this config entry."""
    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, entry_id)

    result = {}
    for entity_entry in sorted(entries, key=lambda e: e.entity_id):
        if not any(mot in entity_entry.entity_id for mot in TO_REDACT):
            state = hass.states.get(entity_entry.entity_id)
            result[entity_entry.entity_id] = {
                "state": state.state if state else None,
                "unit": state.attributes.get("unit_of_measurement") if state else None,
                "last_updated": state.last_updated.isoformat() if state else None,
            }
        else:
            result[entity_entry.entity_id] = {
                "state": "__REDACTED__",
                "unit": state.attributes.get("unit_of_measurement") if state else None,
                "last_updated": state.last_updated.isoformat() if state else None,
            }

    return result


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not data:
        return {"error": "integration_not_initialized"}

    coordinator = data.get(DATA_COORDINATOR)
    entity_states = _entity_states_snapshot(hass, entry.entry_id)

    if isinstance(coordinator, MarstekMultiDeviceCoordinator):
        return {
            "entry": {
                "title": entry.title,
                "device_count": len(coordinator.device_coordinators),
            },
            "entity_states": entity_states,
            "multi": _multi_diagnostics(coordinator),
        }

    if isinstance(coordinator, MarstekDataUpdateCoordinator):
        return {
            "entry": {
                "title": entry.title,
                "device": entry.data.get("device"),
            },
            "entity_states": entity_states,
            "device": _coordinator_snapshot(coordinator),
        }

    return {"error": "unknown_coordinator"}
