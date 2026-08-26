"""Derived cumulative battery charge/discharge energy sensor.

The Marstek firmware exposes cumulative energy counters for solar, grid import,
grid export and load (via ``ES.GetStatus``), but none for the battery itself.
This module reconstructs battery charge/discharge energy from those 4 counters
using the energy conservation identity::

    charge - discharge = ΔPV + ΔGridImport - ΔLoad - ΔGridExport

On every poll, the delta of the 4 counters since the last poll is computed and
its net value is credited to the charge total (if positive) or the discharge
total (if negative). Round-trip battery losses show up as the charge total
slightly exceeding the discharge total over time, which is expected.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from homeassistant.components.sensor import RestoreSensor
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .coordinator import MarstekDataUpdateCoordinator, MarstekMultiDeviceCoordinator
    from .sensor import MarstekSensorEntityDescription


def _wh_to_kwh(value: float | int | None) -> float | None:
    """Convert a value in watt-hours to kilowatt-hours."""
    if value is None:
        return None
    return value / 1000


def _kwh_to_wh(value: float | int | None) -> float | None:
    """Convert a value in kilowatt-hours to watt-hours."""
    if value is None:
        return None
    return value * 1000


def es_energy_components(data: dict) -> tuple[float, float, float, float] | None:
    """Raw (pv, grid_import, grid_export, load) energy counters in Wh, from ES.GetStatus.

    All 4 fields are already scaled to Wh by the coordinator (via
    ``CompatibilityMatrix.scale_value``) before reaching this function.
    """
    es = data.get("es") or {}
    pv = es.get("total_pv_energy")
    grid_in = es.get("total_grid_input_energy")
    grid_out = es.get("total_grid_output_energy")
    load = es.get("total_load_energy")
    if None in (pv, grid_in, grid_out, load):
        return None
    return (pv, grid_in, grid_out, load)


def aggregate_energy_components(data: dict) -> tuple[float, float, float, float] | None:
    """Raw (pv, grid_import, grid_export, load) energy counters in Wh, summed across devices.

    All 4 fields are already scaled to Wh per-device before being summed into aggregates.
    """
    aggregates = data.get("aggregates") or {}
    pv = aggregates.get("total_pv_energy")
    grid_in = aggregates.get("total_grid_import")
    grid_out = aggregates.get("total_grid_export")
    load = aggregates.get("total_load_energy")
    if None in (pv, grid_in, grid_out, load):
        return None
    return (pv, grid_in, grid_out, load)


class _BatteryEnergyBalanceTracker:
    """Accumulates one side (charge or discharge) of the battery energy balance."""

    def __init__(self, direction: str) -> None:
        self._direction = direction  # "charge" or "discharge"
        self._baseline: tuple[float, float, float, float] | None = None
        self.total_wh: float = 0.0

    def seed(self, total_wh: float | None) -> None:
        """Restore the accumulated total from a previous Home Assistant run."""
        if total_wh is not None:
            self.total_wh = total_wh

    def update(self, components: tuple[float, float, float, float] | None) -> None:
        """Feed the latest raw counters and update the accumulated total."""
        if components is None:
            return
        if self._baseline is None:
            self._baseline = components
            return
        deltas = tuple(c - b for c, b in zip(components, self._baseline))
        if min(deltas) < 0:
            # A firmware counter went backwards (device reboot/reset): resynchronize
            # without contributing a delta rather than accumulating a bogus value.
            self._baseline = components
            return
        self._baseline = components
        d_pv, d_grid_in, d_grid_out, d_load = deltas
        net = d_pv + d_grid_in - d_load - d_grid_out
        if self._direction == "charge" and net > 0:
            self.total_wh += net
        elif self._direction == "discharge" and net < 0:
            self.total_wh += -net


class MarstekBatteryEnergyBalanceSensor(CoordinatorEntity, RestoreSensor):
    """Cumulative battery charge/discharge energy, derived from the PV/grid/load counters.

    Unlike other sensors, this one carries state across polls (the running total) and
    across Home Assistant restarts (via RestoreSensor), so it cannot be driven by the
    generic ``entity_description.value_fn`` used for the rest of the sensors.
    """

    entity_description: "MarstekSensorEntityDescription"

    def __init__(
        self,
        coordinator: "MarstekDataUpdateCoordinator | MarstekMultiDeviceCoordinator",
        entity_description: "MarstekSensorEntityDescription",
        unique_id: str,
        device_info: DeviceInfo,
        get_data_fn: Callable[[], dict],
        freshness_fn: Callable[[], bool] | None = None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self._attr_has_entity_name = True
        self._attr_unique_id = unique_id
        self._attr_device_info = device_info
        self._get_data_fn = get_data_fn
        self._freshness_fn = freshness_fn
        self._tracker = _BatteryEnergyBalanceTracker(entity_description.direction)

    async def async_added_to_hass(self) -> None:
        """Restore the accumulated total from the last Home Assistant run."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_sensor_data()
        if last_data is not None and last_data.native_value is not None:
            self._tracker.seed(_kwh_to_wh(float(last_data.native_value)))

    def _handle_coordinator_update(self) -> None:
        """Feed the latest raw counters to the tracker, then update entity state."""
        components = self.entity_description.components_fn(self._get_data_fn())
        self._tracker.update(components)
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> float:
        """Return the accumulated charge/discharge energy, in kWh."""
        return _wh_to_kwh(self._tracker.total_wh)

    @property
    def available(self) -> bool:
        """Return if entity is available - keep sensor available if we have data."""
        if self._freshness_fn is not None and not self._freshness_fn():
            return False
        data = self._get_data_fn()
        return data is not None and len(data) > 0


def build_single_device_entities(
    coordinator: "MarstekDataUpdateCoordinator",
    entry: "ConfigEntry",
    descriptions: list["MarstekSensorEntityDescription"],
) -> list[MarstekBatteryEnergyBalanceSensor]:
    """Build the battery energy balance sensors for single-device mode."""
    device_mac = entry.data.get("ble_mac") or entry.data.get("wifi_mac")
    device_info = DeviceInfo(
        identifiers={(DOMAIN, device_mac)},
        name=f"Marstek {entry.data['device']}",
        manufacturer="Marstek",
        model=entry.data["device"],
        sw_version=str(entry.data.get("firmware", "Unknown")),
    )
    return [
        MarstekBatteryEnergyBalanceSensor(
            coordinator=coordinator,
            entity_description=description,
            unique_id=f"{device_mac}_{description.key}",
            device_info=device_info,
            get_data_fn=lambda: coordinator.data,
            freshness_fn=lambda desc=description: coordinator.is_category_fresh(desc.category),
        )
        for description in descriptions
    ]


def build_multi_device_entities(
    coordinator: "MarstekMultiDeviceCoordinator",
    mac: str,
    device_coordinator: "MarstekDataUpdateCoordinator",
    device_data: dict,
    descriptions: list["MarstekSensorEntityDescription"],
) -> list[MarstekBatteryEnergyBalanceSensor]:
    """Build the per-device battery energy balance sensors for multi-device mode."""
    mac_suffix = mac.replace(":", "")[-4:]
    device_info = DeviceInfo(
        identifiers={(DOMAIN, mac)},
        name=f"Marstek {device_data.get('device', 'Device')} {mac_suffix}",
        manufacturer="Marstek",
        model=device_data.get("device", "Unknown"),
        sw_version=str(device_data.get("firmware", "Unknown")),
    )
    return [
        MarstekBatteryEnergyBalanceSensor(
            coordinator=coordinator,
            entity_description=description,
            unique_id=f"{mac}_{description.key}",
            device_info=device_info,
            get_data_fn=lambda mac=mac: coordinator.get_device_data(mac),
            freshness_fn=lambda dc=device_coordinator, desc=description: dc.is_category_fresh(desc.category),
        )
        for description in descriptions
    ]


def build_aggregate_entities(
    coordinator: "MarstekMultiDeviceCoordinator",
    system_unique_id: str,
    descriptions: list["MarstekSensorEntityDescription"],
) -> list[MarstekBatteryEnergyBalanceSensor]:
    """Build the system-wide battery energy balance sensors for multi-device mode."""
    device_info = DeviceInfo(
        identifiers={(DOMAIN, f"system_{system_unique_id}")},
        name="Marstek System",
        manufacturer="Marstek",
        model="System",
    )
    return [
        MarstekBatteryEnergyBalanceSensor(
            coordinator=coordinator,
            entity_description=description,
            unique_id=f"{system_unique_id}_{description.key}",
            device_info=device_info,
            get_data_fn=lambda: coordinator.data,
        )
        for description in descriptions
    ]
