"""Drive mode controller: keeps one battery applying an HA-held grid power setpoint."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .api import MarstekCommandSuperseded
from .compatibility import get_base_model
from .const import (
    CONF_DRIVE_MAX_CHARGE,
    CONF_DRIVE_MAX_DISCHARGE,
    CONF_DRIVE_POWER_LIMITS,
    DOMAIN,
    DRIVE_DEFAULT_POWER_LIMITS,
    DRIVE_MIN_STEP,
    DRIVE_POWER_LIMITS,
    DRIVE_REFRESH_INTERVAL,
    MODE_PASSIVE,
)
from .modes import build_drive_config

_LOGGER = logging.getLogger(__name__)


def model_power_limits(device_model: str) -> tuple[int, int]:
    """Return the default (max charge W, max discharge W) for a device model."""
    return DRIVE_POWER_LIMITS.get(get_base_model(device_model or ""), DRIVE_DEFAULT_POWER_LIMITS)


def drive_power_limits(options: dict, device_mac: str, device_model: str) -> tuple[int, int]:
    """Return (max charge W, max discharge W): the entry option, else the model default."""
    charge, discharge = model_power_limits(device_model)
    override = (options.get(CONF_DRIVE_POWER_LIMITS) or {}).get(device_mac) or {}
    return (
        int(override.get(CONF_DRIVE_MAX_CHARGE, charge)),
        int(override.get(CONF_DRIVE_MAX_DISCHARGE, discharge)),
    )


class MarstekDriveController:
    """Apply a grid power setpoint through Passive mode, for one battery.

    The setpoint (+ = discharge to the grid, − = charge) is sent with
    ``ES.SetMode`` Passive on the high-priority queue:

    - at once when it moves by ``DRIVE_MIN_STEP`` W or more from the value
      last accepted (or being sent);
    - otherwise on the refresh, ``DRIVE_REFRESH_INTERVAL`` s after the last
      accepted command (or after a rejected one);
    - at once when polling reads any mode other than Passive while active.

    A polled transition to Passive while inactive activates Drive with the
    current setpoint. ``device_coordinator`` owns the battery's API and data;
    ``listen_coordinator`` is the coordinator whose updates carry new polls
    (the system coordinator in multi-device entries).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        device_coordinator: Any,
        listen_coordinator: Any,
        device_mac: str,
    ) -> None:
        """Initialize the controller (inactive, setpoint 0 W)."""
        self.hass = hass
        self._entry = entry
        self.device_coordinator = device_coordinator
        self.listen_coordinator = listen_coordinator
        self.device_mac = device_mac
        charge, discharge = drive_power_limits(entry.options, device_mac, device_coordinator.device_model)
        self.min_power = -charge
        self.max_power = discharge

        self.active = False
        self.setpoint = 0
        self.accepted_power: int | None = None  # Last value the battery accepted
        self._pending_power: int | None = None  # Value of the send in progress
        self._send_task: asyncio.Task | None = None
        self._refresh_handle: asyncio.TimerHandle | None = None
        self._last_poll_at: float | None = None
        self._last_polled_mode: str | None = None
        self._listeners: list[Callable[[], None]] = []
        self._unsub_coordinator: Callable[[], None] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def async_setup(self) -> None:
        """Start watching polls (and process the data already fetched)."""
        self._unsub_coordinator = self.listen_coordinator.async_add_listener(self._handle_coordinator_update)
        self._handle_coordinator_update()

    def async_shutdown(self) -> None:
        """Stop the controller and stop watching polls (entry unload)."""
        self.stop()
        if self._unsub_coordinator is not None:
            self._unsub_coordinator()
            self._unsub_coordinator = None

    def add_listener(self, update_callback: Callable[[], None]) -> Callable[[], None]:
        """Call ``update_callback`` when the active state or setpoint changes."""
        self._listeners.append(update_callback)

        def remove() -> None:
            if update_callback in self._listeners:
                self._listeners.remove(update_callback)

        return remove

    def start(self) -> None:
        """Enter Drive: reset the setpoint to 0 W and send it at once."""
        _LOGGER.info("Entering Drive mode on %s at 0 W", self.device_mac)
        self.setpoint = 0
        self._activate()

    def stop(self) -> None:
        """Leave Drive: stop refreshing and drop the send in progress."""
        was_active = self.active
        self.active = False
        self.accepted_power = None
        self._pending_power = None
        self._cancel_refresh()
        if self._send_task is not None and not self._send_task.done():
            self._send_task.cancel()
        self._send_task = None
        if was_active:
            _LOGGER.info("Leaving Drive mode on %s", self.device_mac)
            self._notify()

    def set_setpoint(self, power: float) -> None:
        """Store a new setpoint; send it at once if it moved enough."""
        power = min(max(int(round(power)), self.min_power), self.max_power)
        self.setpoint = power
        if self.active:
            reference = self._pending_power if self._pending_power is not None else self.accepted_power
            if reference is None or abs(power - reference) >= DRIVE_MIN_STEP:
                self._send_now()
        self._notify()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _activate(self) -> None:
        self.active = True
        self.accepted_power = None
        self._send_now()
        self._notify()

    def _notify(self) -> None:
        for update_callback in list(self._listeners):
            update_callback()

    def _send_now(self) -> None:
        """Send the current setpoint; a send in progress is superseded."""
        self._cancel_refresh()
        power = self.setpoint
        self._pending_power = power
        self._send_task = self._entry.async_create_background_task(
            self.hass,
            self._async_send(power),
            f"{DOMAIN}_drive_{self.device_mac}",
        )

    async def _async_send(self, power: int) -> None:
        config = build_drive_config(power)
        try:
            accepted = await self.device_coordinator.api.set_es_mode(config, priority=True)
        except MarstekCommandSuperseded:
            return  # A newer setpoint is being sent
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.warning("Drive command (%d W) to %s failed: %s", power, self.device_mac, err)
            accepted = False

        if not self.active or self._pending_power != power:
            return  # Stopped, or a newer send took over meanwhile
        self._pending_power = None

        if accepted:
            _LOGGER.debug("Drive setpoint %d W accepted by %s", power, self.device_mac)
            self.accepted_power = power
            self._update_cached_mode(config)
        else:
            _LOGGER.warning("Device %s rejected Drive setpoint %d W", self.device_mac, power)
        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        self._cancel_refresh()
        self._refresh_handle = asyncio.get_running_loop().call_later(DRIVE_REFRESH_INTERVAL, self._refresh)

    def _cancel_refresh(self) -> None:
        if self._refresh_handle is not None:
            self._refresh_handle.cancel()
            self._refresh_handle = None

    def _refresh(self) -> None:
        self._refresh_handle = None
        if self.active and self._pending_power is None:
            self._send_now()

    def _handle_coordinator_update(self) -> None:
        """React to a new ES.GetMode poll (cache-only updates are ignored)."""
        data = self.device_coordinator.data or {}
        polled_at = (getattr(self.device_coordinator, "category_last_updated", None) or {}).get("mode")
        if polled_at is None or polled_at == self._last_poll_at:
            return
        self._last_poll_at = polled_at

        mode = (data.get("mode") or {}).get("mode")
        previous, self._last_polled_mode = self._last_polled_mode, mode

        if self.active:
            if mode != MODE_PASSIVE and self._pending_power is None:
                _LOGGER.warning(
                    "%s reports %s while in Drive mode: re-applying Passive at %d W",
                    self.device_mac, mode, self.setpoint,
                )
                self._send_now()
        elif mode == MODE_PASSIVE and previous != MODE_PASSIVE:
            _LOGGER.info("%s reports Passive: treating it as Drive at %d W", self.device_mac, self.setpoint)
            self._activate()

    def _update_cached_mode(self, config: dict) -> None:
        """Show Passive in the caches without resetting the polling timers."""
        current = self.device_coordinator.data or {}
        updated = dict(current)
        updated["mode"] = {
            **(current.get("mode") or {}),
            "mode": MODE_PASSIVE,
            "passive_cfg": dict(config["passive_cfg"]),
        }
        self.device_coordinator.data = updated
        self.device_coordinator.async_update_listeners()

        if self.listen_coordinator is not self.device_coordinator:
            system = self.listen_coordinator.data or {}
            devices = dict(system.get("devices") or {})
            devices[self.device_mac] = updated
            self.listen_coordinator.data = {**system, "devices": devices}
            self.listen_coordinator.async_update_listeners()
