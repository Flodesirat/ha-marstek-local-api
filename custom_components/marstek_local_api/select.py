"""Select platform for Marstek Local API (operating mode)."""
from __future__ import annotations

import asyncio
import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DATA_COORDINATOR,
    DOMAIN,
    MAX_RETRIES,
    MODE_AI,
    MODE_AUTO,
    MODE_MANUAL,
    MODE_UPS,
    RETRY_DELAY,
)
from .coordinator import MarstekDataUpdateCoordinator, MarstekMultiDeviceCoordinator
from .modes import build_mode_config, mode_state_from_config

_LOGGER = logging.getLogger(__name__)

OPTION_PROGRAMMED = "Programmed"

# Select option -> mode sent to (and reported by) the battery.
# "Programmed" is only a label: the API value stays "Manual".
OPTION_TO_MODE: dict[str, str] = {
    MODE_AUTO: MODE_AUTO,
    MODE_AI: MODE_AI,
    OPTION_PROGRAMMED: MODE_MANUAL,
    MODE_UPS: MODE_UPS,
}
MODE_TO_OPTION: dict[str, str] = {mode: option for option, mode in OPTION_TO_MODE.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Marstek operating mode selects based on a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]

    entities = []

    if isinstance(coordinator, MarstekMultiDeviceCoordinator):
        for mac in coordinator.get_device_macs():
            device_data = next(d for d in coordinator.devices if (d.get("ble_mac") or d.get("wifi_mac")) == mac)
            # Last 4 chars of the MAC tell batteries of the same model apart
            mac_suffix = mac.replace(":", "")[-4:]
            entities.append(
                MarstekOperatingModeSelect(
                    entry=entry,
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
            MarstekOperatingModeSelect(
                entry=entry,
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


class MarstekOperatingModeSelect(CoordinatorEntity, SelectEntity, RestoreEntity):
    """Operating mode of one battery.

    The mode is sent once when selected; nothing maintains it afterwards. The
    select shows what the battery reports (ES.GetMode). On HA restart the last
    shown option is re-applied once.

    In single-device mode ``coordinator`` and ``device_coordinator`` are the
    same object. In multi-device mode ``coordinator`` is the system coordinator
    the entity listens to, and ``device_coordinator`` owns the battery's API.
    """

    _attr_has_entity_name = True
    _attr_name = "Operating mode"
    _attr_icon = "mdi:battery-sync"
    _attr_options = list(OPTION_TO_MODE)

    def __init__(
        self,
        entry: ConfigEntry,
        coordinator: MarstekDataUpdateCoordinator | MarstekMultiDeviceCoordinator,
        device_coordinator: MarstekDataUpdateCoordinator,
        device_mac: str,
        device_info: DeviceInfo,
    ) -> None:
        """Initialize the select."""
        super().__init__(coordinator)
        self._entry = entry
        self.device_coordinator = device_coordinator
        self.device_mac = device_mac
        self._attr_unique_id = f"{device_mac}_operating_mode"
        self._attr_device_info = device_info

    @property
    def _is_multi_device(self) -> bool:
        return self.coordinator is not self.device_coordinator

    @property
    def _device_data(self) -> dict:
        return self.device_coordinator.data or {}

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return len(self._device_data) > 0

    @property
    def current_option(self) -> str | None:
        """Return the option matching the mode the battery reports."""
        mode = (self._device_data.get("mode") or {}).get("mode")
        return MODE_TO_OPTION.get(mode)

    async def async_added_to_hass(self) -> None:
        """Re-apply the last selected option once after a restart."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is None or last_state.state not in self.options:
            return

        self._entry.async_create_background_task(
            self.hass,
            self._async_reapply(last_state.state),
            f"{DOMAIN}_reapply_operating_mode_{self.device_mac}",
        )

    async def async_select_option(self, option: str) -> None:
        """Send the selected operating mode to the battery."""
        await self._async_apply(option)

    async def _async_reapply(self, option: str) -> None:
        """Re-apply a restored option, logging instead of raising on failure."""
        _LOGGER.info("Re-applying operating mode %s to %s after restart", option, self.device_mac)
        try:
            await self._async_apply(option)
        except HomeAssistantError as err:
            _LOGGER.warning("Could not re-apply operating mode after restart: %s", err)

    async def _async_apply(self, option: str) -> None:
        """Send ES.SetMode for ``option``, with retries."""
        if option not in OPTION_TO_MODE:
            raise HomeAssistantError(f"Unknown operating mode: {option}")

        mode = OPTION_TO_MODE[option]
        config = build_mode_config(mode)
        last_error: str | None = None

        try:
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    if await self.device_coordinator.api.set_es_mode(config):
                        self._update_cached_mode(mode, config)
                        _LOGGER.info("Set operating mode of %s to %s", self.device_mac, option)
                        return

                    last_error = "device rejected mode change"
                    _LOGGER.warning(
                        "Device %s rejected mode change to %s (attempt %d/%d)",
                        self.device_mac, option, attempt, MAX_RETRIES,
                    )
                except Exception as err:
                    last_error = str(err)
                    _LOGGER.error(
                        "Error setting mode of %s to %s (attempt %d/%d): %s",
                        self.device_mac, option, attempt, MAX_RETRIES, err,
                    )

                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY)
        finally:
            await self._refresh_mode_data()

        raise HomeAssistantError(
            f"Failed to set operating mode of {self.device_mac} to {option}: {last_error}"
        )

    async def _refresh_mode_data(self) -> None:
        """Force a refresh so entities reflect the latest state."""
        coordinators = [self.device_coordinator]
        if self._is_multi_device:
            coordinators.append(self.coordinator)
        for coordinator in coordinators:
            try:
                await coordinator.async_refresh()
            except Exception as err:
                _LOGGER.warning("Failed to refresh data after mode change for %s: %s", self.device_mac, err)

    def _update_cached_mode(self, mode: str, config: dict) -> None:
        """Update the coordinator caches so entities show the new mode at once.

        ES.GetMode is only polled on the medium tier, so without this the
        select would keep showing the old mode for up to 5 minutes.
        """
        current_device = self._device_data
        updated_device = dict(current_device)
        updated_device["mode"] = {**(current_device.get("mode") or {}), **mode_state_from_config(mode, config)}
        self.device_coordinator.async_set_updated_data(updated_device)

        if self._is_multi_device:
            current_system = self.coordinator.data or {}
            devices = dict(current_system.get("devices") or {})
            devices[self.device_mac] = updated_device
            self.coordinator.async_set_updated_data({**current_system, "devices": devices})
