"""Operating mode configs shared by the mode buttons and the mode select."""
from __future__ import annotations

from .const import DRIVE_CD_TIME, MODE_AI, MODE_AUTO, MODE_MANUAL, MODE_PASSIVE, MODE_UPS

# Manual mode requires a schedule: send a disabled slot 9 so the user's own
# schedules are left untouched.
DEFAULT_MANUAL_MODE_CFG: dict[str, int | str] = {
    "time_num": 9,
    "start_time": "00:00",
    "end_time": "00:00",
    "week_set": 0,
    "power": 0,
    "enable": 0,
}

# Config key carrying each mode's settings in ES.SetMode
MODE_CFG_KEYS: dict[str, str] = {
    MODE_AUTO: "auto_cfg",
    MODE_AI: "ai_cfg",
    MODE_MANUAL: "manual_cfg",
    MODE_UPS: "ups_cfg",
}


def build_mode_config(mode: str) -> dict:
    """Build the ES.SetMode config that switches the battery to ``mode``."""
    if mode == MODE_MANUAL:
        return {"mode": MODE_MANUAL, "manual_cfg": dict(DEFAULT_MANUAL_MODE_CFG)}
    if mode in MODE_CFG_KEYS:
        return {"mode": mode, MODE_CFG_KEYS[mode]: {"enable": 1}}
    return {}


def build_drive_config(power: int) -> dict:
    """Build the Passive ES.SetMode config that applies a Drive setpoint."""
    return {"mode": MODE_PASSIVE, "passive_cfg": {"power": int(power), "cd_time": DRIVE_CD_TIME}}


def mode_state_from_config(mode: str, config: dict) -> dict:
    """Extract mode state information from a config payload."""
    state: dict[str, object] = {"mode": mode}

    cfg_key = MODE_CFG_KEYS.get(mode)
    if cfg_key and cfg_key in config:
        state[cfg_key] = dict(config[cfg_key])

    return state
