"""Number platform for Marstek Local API (Drive mode grid power setpoint)."""
from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import MarstekDataUpdateCoordinator, MarstekMultiDeviceCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Drive grid power setpoints based on a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]

    entities = []

    if isinstance(coordinator, MarstekMultiDeviceCoordinator):
        for mac in coordinator.get_device_macs():
            device_data = next(d for d in coordinator.devices if (d.get("ble_mac") or d.get("wifi_mac")) == mac)
            # Last 4 chars of the MAC tell batteries of the same model apart
            mac_suffix = mac.replace(":", "")[-4:]
            entities.append(
                MarstekDriveGridPowerNumber(
                    coordinator=coordinator,
                    device_coordinator=coordinator.device_coordinators[mac],
                    device_mac=mac,
                    device_info=DeviceInfo(
                        identifiers={(DOMAIN, mac)},
                        name=f"Marstek {device_data.get('device', 'Device')} {mac_suffix}",
                        manufacturer="Marstek",
                        model=device_data.get("device", "Unknown"),
                        sw_version=str(device_data.get("firmware", "Unknown")),
                    ),
                )
            )
    else:
        device_mac = entry.data.get("ble_mac") or entry.data.get("wifi_mac")
        entities.append(
            MarstekDriveGridPowerNumber(
                coordinator=coordinator,
                device_coordinator=coordinator,
                device_mac=device_mac,
                device_info=DeviceInfo(
                    identifiers={(DOMAIN, device_mac)},
                    name=f"Marstek {entry.data['device']}",
                    manufacturer="Marstek",
                    model=entry.data["device"],
                    sw_version=str(entry.data.get("firmware", "Unknown")),
                ),
            )
        )

    async_add_entities(entities)


class MarstekDriveGridPowerNumber(CoordinatorEntity, NumberEntity):
    """Grid power setpoint applied while the operating mode is Drive.

    + = discharge to the grid, − = charge. The value lives in the battery's
    Drive controller: it is not restored after a restart (Drive restarts at
    0 W) and entering Drive resets it to 0 W.
    """

    _attr_has_entity_name = True
    _attr_name = "Drive grid power"
    _attr_icon = "mdi:transmission-tower-export"
    _attr_device_class = NumberDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: MarstekDataUpdateCoordinator | MarstekMultiDeviceCoordinator,
        device_coordinator: MarstekDataUpdateCoordinator,
        device_mac: str,
        device_info: DeviceInfo,
    ) -> None:
        """Initialize the number."""
        super().__init__(coordinator)
        self.device_coordinator = device_coordinator
        self.device_mac = device_mac
        self._drive = device_coordinator.drive_controller
        self._attr_unique_id = f"{device_mac}_drive_grid_power"
        self._attr_device_info = device_info
        self._attr_native_min_value = self._drive.min_power
        self._attr_native_max_value = self._drive.max_power

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return len(self.device_coordinator.data or {}) > 0

    @property
    def native_value(self) -> float:
        """Return the current setpoint."""
        return self._drive.setpoint

    async def async_added_to_hass(self) -> None:
        """Follow setpoint resets made by the controller."""
        await super().async_added_to_hass()
        self.async_on_remove(self._drive.add_listener(self.async_write_ha_state))

    async def async_set_native_value(self, value: float) -> None:
        """Store the setpoint; the controller sends it while in Drive."""
        self._drive.set_setpoint(value)
