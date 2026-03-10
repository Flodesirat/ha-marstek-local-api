# Marstek Local API for Home Assistant

> **Firmware warning:** Marstek’s Local API firmware is still immature, so most glitches originate in the batteries, not here.
> Report issues to Marstek unless you can clearly trace them to this project.

Home Assistant integration that talks directly to Marstek Venus A/C/D/E batteries over the official Local API. It delivers local-only telemetry, mode control, and fleet-wide aggregation without relying on the Marstek cloud.

---

## 1. Enable the Local API

1. Make sure your batteries are on the latest firmware.
2. Use the [Marstek Venus Monitor](https://rweijnen.github.io/marstek-venus-monitor/latest/) tool to enable *Local API / Open API* on each device.
3. Note the UDP port (default `30000`) and confirm the devices respond on your LAN.

<img width="230" height="129" alt="afbeelding" src="https://github.com/user-attachments/assets/035de357-fbe6-4224-8249-03abb3078fa1" />

---

## 2. Install the Integration

### Via HACS
1. Click this button:

[![Open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Flodesirat&repository=ha-marstek-local-api&category=integration)

Or:
1. Open **HACS → Integrations → Custom repositories**.
2. Add `https://github.com/Flodesirat/ha-marstek-local-api` as an *Integration*.
3. Install **Marstek Local API** and restart Home Assistant.

### Manual copy
1. Drop `custom_components/marstek_local_api` into your Home Assistant `custom_components` folder.
2. Restart Home Assistant.

---

## 3. Add Devices

1. Go to **Settings → Devices & Services → Add Integration** and search for **Marstek Local API**.
2. The discovery step lists every battery it finds on your network. Select one device or pick **All devices** to build a single multi-battery entry.
3. If discovery misses a unit, choose **Manual IP entry** and provide the host/port you noted earlier.

After setup you can return to **Settings → Devices & Services → Marstek Local API → Configure** to:
- Rename devices, add/remove batteries, or tune communication parameters (scan interval, command timeout, max retries, stale-data threshold).
- Trigger discovery again when new batteries join the network.

> **Important:** If you want all batteries to live under the same config entry (and keep the virtual **Marstek System** device), use the integration’s **Configure** button to add/remove batteries. The default Home Assistant “Add Device” button creates a brand-new config entry and a separate virtual system device.


<img width="442" height="442" alt="afbeelding" src="https://github.com/user-attachments/assets/45001642-412e-4c85-aace-b495639959ff" />

---

## 4. Single Entry vs. Virtual System Battery

- **Single-device entry**: created when you add an individual battery. Each entry exposes the battery’s entities and optional operating-mode controls.
- **Multi-device entry**: created when you pick *All devices* or add more batteries through the options flow. The integration keeps one config entry containing all members and exposes a synthetic device called **“Marstek System”**.  
  - The “system” device aggregates fleet metrics (total capacity, total grid import/export, combined state, etc.).  
  - Every physical battery still appears as its own device with per-pack entities.

<img width="1037" height="488" alt="afbeelding" src="https://github.com/user-attachments/assets/40bcb48a-02e6-4c85-85a4-73751265c6f8" />

---

## 5. Entities

| Category | Sensor (entity suffix) | Unit | Notes | Update tier | Default interval |
| --- | --- | --- | --- | --- | ---: |
| **Battery** | `battery_soc` | % | State of charge | Medium | 5 min |
|  | `battery_temperature` | °C | Pack temperature | Medium | 5 min |
|  | `battery_capacity` | kWh | Remaining capacity | Medium | 5 min |
|  | `battery_rated_capacity` | kWh | Rated pack capacity | Medium | 5 min |
|  | `battery_available_capacity` | kWh | Estimated energy still available before full charge | Medium | 5 min |
|  | `battery_voltage` | V | Pack voltage | Medium | 5 min |
|  | `battery_current` | A | Pack current (positive = charge) | Medium | 5 min |
| **Energy system (ES)** | `battery_power` | W | Pack power (positive = charge) | Fast | 30 s |
|  | `battery_power_in` / `battery_power_out` | W | Split charge/discharge power | Fast | 30 s |
|  | `battery_state` | text | `charging` / `discharging` / `idle` | Fast | 30 s |
|  | `grid_power` | W | Grid import/export (positive = import) | Fast | 30 s |
|  | `offgrid_power` | W | Off-grid load | Fast | 30 s |
|  | `pv_power_es` | W | Solar production reported via ES | Fast | 30 s |
|  | `total_pv_energy` | kWh | Lifetime PV energy | Fast | 30 s |
|  | `total_grid_import` / `total_grid_export` | kWh | Lifetime grid counters | Fast | 30 s |
|  | `total_load_energy` | kWh | Lifetime load energy | Fast | 30 s |
| **Energy meter / CT** | `ct_phase_a_power`, `ct_phase_b_power`, `ct_phase_c_power` | W | Per-phase measurements (if CTs installed) | Fast | 30 s |
|  | `ct_total_power` | W | CT aggregate | Fast | 30 s |
| **Mode** | `operating_mode` | text | Current mode (read-only sensor) | Slow | ~50 min |
| **PV (Venus A / D only)** | `pv_power`, `pv_voltage`, `pv_current` | W / V / A | MPPT telemetry | Medium | 5 min |
| **Network** | `wifi_rssi` | dBm | Wi-Fi signal | Slow | ~50 min |
|  | `wifi_ssid`, `wifi_ip`, `wifi_gateway`, `wifi_subnet`, `wifi_dns` | text | Wi-Fi configuration | Slow | ~50 min |
| **Device info** | `device_model`, `firmware_version`, `ble_mac`, `wifi_mac`, `device_ip` | text | Identification fields | Slow | ~50 min |
| **Diagnostics** | `last_message_received` | seconds | Time since the last successful poll | Fast | 30 s |

**Update tiers** (with the default 30 s scan interval): **Fast** = every scan (30 s) — `ES.GetStatus`, `EM.GetStatus`; **Medium** = every 10th scan (5 min) — `Bat.GetStatus`, `PV.GetStatus`; **Slow** = every 100th scan (~50 min) — `Marstek.GetDevice`, `Wifi.GetStatus`, `BLE.GetStatus`, `ES.GetMode`. The scan interval and all tiers scale proportionally when you change it in the integration options.

Every sensor listed above also exists in an aggregated form under the **Marstek System** device whenever you manage multiple batteries together (prefixed with `system_`).

### Mode Control Buttons

Each battery exposes three button entities for quick mode switching:

- `button.marstek_auto_mode` - Switch to Auto mode
- `button.marstek_ai_mode` - Switch to AI mode
- `button.marstek_manual_mode` - Switch to Manual mode

The `sensor.marstek_operating_mode` displays the current active mode (Auto, AI, Manual, or Passive). **Passive mode** requires parameters (power and duration) and can only be activated via the `set_passive_mode` service (see Services section below).

---

## 6. Services

### Data Synchronization

| Service | Description | Parameters |
| --- | --- | --- |
| `marstek_local_api.request_data_sync` | Triggers an immediate poll of every configured coordinator. | Optional `entry_id` (specific config entry) and/or `device_id` (single battery). |

### Manual Mode Scheduling

The integration provides three services for configuring manual mode schedules. Manual mode allows you to define up to 10 time-based schedules that control when the battery charges/discharges and at what power level.

> Select the **battery device** for all schedule services. The integration targets the correct device coordinator automatically.

> **Note:** The Marstek Local API does not support reading schedule configurations back from the device. Schedules are write-only, so the integration cannot display currently configured schedules.

| Service | Description |
| --- | --- |
| `marstek_local_api.set_manual_schedule` | Configure a single schedule slot (0-9) with time, days, and power settings. |
| `marstek_local_api.set_manual_schedules` | Configure multiple schedule slots at once using YAML. |
| `marstek_local_api.clear_manual_schedules` | Disable all 10 schedule slots. |

#### Setting a Single Schedule

Configure one schedule slot at a time through the Home Assistant UI:

```yaml
service: marstek_local_api.set_manual_schedule
data:
  device_id: "1234567890abcdef1234567890abcdef"
  time_num: 0  # Slot 0-9
  start_time: "08:00"
  end_time: "16:00"
  days:
    - mon
    - tue
    - wed
    - thu
    - fri
  power: -2000  # Negative = charge limit (2000W), positive = discharge limit
  enabled: true
```

#### Setting Multiple Schedules

Configure several slots at once using YAML mode in Developer Tools → Services:

```yaml
service: marstek_local_api.set_manual_schedules
data:
  device_id: "1234567890abcdef1234567890abcdef"
  schedules:
    - time_num: 0
      start_time: "08:00"
      end_time: "16:00"
      days: [mon, tue, wed, thu, fri]
      power: -2000  # Charge at max 2000W
      enabled: true
    - time_num: 1
      start_time: "18:00"
      end_time: "22:00"
      days: [mon, tue, wed, thu, fri]
      power: 800  # Discharge at max 800W
      enabled: true
```

#### Clearing All Schedules

Remove all configured schedules by disabling all 10 slots:

```yaml
service: marstek_local_api.clear_manual_schedules
data:
  device_id: "1234567890abcdef1234567890abcdef"
```

> Expect this call to run for several minutes—the Marstek API accepts only one slot at a time and rejects most writes on the first attempt, so the integration walks through all ten slots with retries and back-off until the device finally accepts them.

#### Schedule Parameters

- **time_num**: Schedule slot number (0-9). Each slot is independent.
- **start_time** / **end_time**: 24-hour format (HH:MM). Schedules can span midnight.
- **days**: List of weekdays (`mon`, `tue`, `wed`, `thu`, `fri`, `sat`, `sun`). Defaults to all days.
- **power**: Power limit in watts. **Important:** Use negative values for charging (e.g., `-2000` = 2000W charge limit) and positive values for discharging (e.g., `800` = 800W discharge limit). Use `0` for no limit.
- **enabled**: Whether this schedule is active (default: `true`).
- **device_id**: Home Assistant device ID of the target battery (required).

#### Important Notes

- Changing the operating mode to Manual via the button entity will **not** activate any schedules automatically. You must configure schedules using the services above.
- Multiple schedules can overlap. The device handles priority internally.
- Schedule configurations are stored on the device and persist across reboots.
- Since schedule reading is not supported, keep a copy of your schedule configuration in Home Assistant automations or scripts.

You can call these services from **Developer Tools → Services** or use them in automations and scripts.

### Passive Mode Control

The `marstek_local_api.set_passive_mode` service enables **Passive mode** for direct power control. Passive mode allows you to charge or discharge the selected battery at a specific power level for a defined duration.

**Important:** Power values use signed integers:
- **Negative values** = Charging (e.g., `-2000` means charge at 2000W)
- **Positive values** = Discharging (e.g., `1500` means discharge at 1500W)

#### Service Parameters

| Parameter | Required | Type | Range | Description |
| --- | --- | --- | --- | --- |
| `device_id` | Yes | string | - | Battery to control. The integration communicates with the selected device directly. |
| `power` | Yes | integer | -10000 to 10000 | Power in watts (negative = charge, positive = discharge) |
| `duration` | Yes | integer | 1 to 86400 | Duration in seconds (max 24 hours) |

#### Examples

**Charge at 2000W for 1 hour:**
```yaml
service: marstek_local_api.set_passive_mode
data:
  device_id: "1234567890abcdef1234567890abcdef"
  power: -2000  # Negative = charging
  duration: 3600  # 1 hour in seconds
```

**Discharge at 1500W for 30 minutes:**
```yaml
service: marstek_local_api.set_passive_mode
data:
  device_id: "1234567890abcdef1234567890abcdef"
  power: 1500  # Positive = discharging
  duration: 1800  # 30 minutes in seconds
```

**Use in an automation (charge during cheap electricity hours):**
```yaml
automation:
  - alias: "Charge battery during off-peak hours"
    trigger:
      - platform: time
        at: "02:00:00"
    action:
      - service: marstek_local_api.set_passive_mode
        data:
          device_id: "1234567890abcdef1234567890abcdef"
          power: -3000  # Charge at 3000W
          duration: 14400  # 4 hours
```

---

## 7. Tips & Troubleshooting

- The default scan interval is 10 s. Increasing it reduces UDP traffic but makes entities less reactive. Going below 10 s is not recommended — high-frequency polling can make devices unstable.
- If discovery fails, double-check that the Local API remains enabled after firmware upgrades and that UDP port `30000` is accessible from Home Assistant.
- For verbose logging, append the following to `configuration.yaml`:
  ```yaml
  logger:
    logs:
      custom_components.marstek_local_api: debug
  ```

## API maturity & known issues

Note: the Marstek Local API is still relatively new and evolving. Behavior can vary between hardware revisions (v2/v3) and firmware versions (EMS and BMS). When reporting issues, always include diagnostic data (logs and the integration's diagnostic fields).

Known issues:
- Polling too often might cause connection to be lost to the CT002/3
- Battery temperature may read 10× too high on older BMS versions.
- API call timeouts (shown as warnings in the log).
- Some API calls are not supported on older firmware — please ensure devices are updated before filing issues.
- Manual mode requests must include a schedule: the API rejects `ES.SetMode` without `manual_cfg`, and because schedules are write-only the integration always sends a disabled placeholder in slot 9. Reapply your own slot 9 schedule after toggling Manual mode if needed.
- Polling faster than 10 s is not advised; devices have been reported to become unstable (e.g. losing CT003 connection).
 - Energy counters / capacity fields may be reported in Wh instead of kWh on certain firmware (values appear 1000× off).
 - `ES.GetStatus` can be unresponsive on some Venus E v3 firmwares (reported on v137 / v139).
 - CT connection state may be reported as "disconnected" / power values might not be updated even when a CT is connected (appears fixed in HW v2 firmware v154+).

Most of these issues are resolved by updating the device to the latest firmware — Marstek staggers rollouts, so many systems still run older versions. The Local API is evolving quickly and should stabilise as updates are deployed.

Example warnings:

```
2025-10-21 10:01:34.986 WARNING (MainThread) [custom_components.marstek_local_api.api] Command ES.GetStatus timed out after 15s (attempt 1/3, host=192.168.0.47)
2025-10-21 10:02:28.693 ERROR (MainThread) [custom_components.marstek_local_api.api] Command EM.GetStatus failed after 3 attempt(s); returning no result
```

Quick note for issue reports (EN): always attach the integration diagnostics export and relevant HA logs when filing a bug — it is required for effective troubleshooting.


### Standalone tools

The `test/` directory contains several CLI utilities that work outside Home Assistant — no HA install, no config entry required. Run them with plain Python 3.

#### `test_tool.py` — diagnostics and control

Reuses the integration code to diagnose and control batteries:

```bash
cd test
python3 test_tool.py discover                        # discover devices and print full diagnostics
python3 test_tool.py discover --ip 192.168.7.101     # target a specific IP
python3 test_tool.py set-test-schedules              # apply two sample charge/discharge schedules
python3 test_tool.py clear-schedules                 # disable all 10 schedule slots
python3 test_tool.py set-passive --power -2000 --duration 3600
python3 test_tool.py set-mode auto --ip 192.168.7.101
```

The `discover` command runs the full diagnostic suite (device info, battery, ES, CT, WiFi, BLE). The other subcommands let you verify scheduling, passive mode, and operating-mode changes without going through Home Assistant.

---

#### `test/capture_fixtures.py` — collect real device data for tests

**Why:** The integration's sensor scaling and value functions depend on exact field names and raw values that vary by model and firmware. Testing against real device data ensures the code matches what the hardware actually sends, not just what the API documentation describes.

**What it does:** Queries every known API endpoint (`Marstek.GetDevice`, `ES.GetStatus`, `Bat.GetStatus`, `EM.GetStatus`, `ES.GetMode`, `PV.GetStatus`, `Wifi.GetStatus`, `BLE.GetStatus`) and saves the raw JSON responses as fixture files under `tests/fixtures/<model>_fw<version>/`.

```bash
# Auto-discover and capture
python3 test/capture_fixtures.py

# Direct IP (faster, no broadcast)
python3 test/capture_fixtures.py --ip 192.168.0.104

# Custom output directory
python3 test/capture_fixtures.py --ip 192.168.0.104 --out path/to/dir
```

Output structure after a successful run:

```
tests/fixtures/Venus_A_fw147/
├── all.json        ← combined fixture with metadata (used by the test suite)
├── battery.json
├── ble.json
├── device.json
├── em.json
├── es.json
├── mode.json
├── pv.json
└── wifi.json
```

The `all.json` file is loaded automatically by the test suite (`tests/conftest.py`). Adding fixtures for new models or firmware versions automatically extends test coverage — no code changes needed.

> **Tip:** Run this script whenever you update the device firmware or get access to a new model (Venus C, D, E). Each new fixture set is a regression baseline.

---

#### `test/comm_quality.py` — measure UDP communication reliability

**Why:** The Marstek Local API runs over UDP without any guaranteed delivery. Timeouts, packet loss, and high latency are the primary sources of integration instability. This tool measures communication quality directly on the wire so you can distinguish device-side problems (firmware, load, WiFi signal) from integration-side problems (polling rate, timeout tuning).

**What it does:** Sends repeated requests at a configurable interval and tracks per-method statistics: response rate, loss percentage, and latency (average, p95, max). Results are displayed live in the terminal and optionally exported as JSON.

```bash
# 60-second test, auto-discover
python3 test/comm_quality.py

# Direct IP, 5-minute test, save results
python3 test/comm_quality.py --ip 192.168.0.104 --duration 300 --out results.json

# Focus on a single method, fast polling
python3 test/comm_quality.py --ip 192.168.0.104 --method ES.GetStatus --interval 5

# Poll multiple specific methods
python3 test/comm_quality.py --ip 192.168.0.104 \
  --method ES.GetStatus --method Bat.GetStatus --interval 10
```

Live output example:

```
  [████████████░░░░░░░░░░░░░░░░░░]    40s / 60s

  Method                 Sent     OK    Loss      Avg      p95      Max  Quality
  ──────────────────────────────────────────────────────────────────────────────
  ES.GetStatus              4      4   0.0%    128ms    145ms    160ms  ████████████████████
  Bat.GetStatus             4      3  25.0%   112ms    130ms    130ms  ███████████████░░░░░
  EM.GetStatus              4      4   0.0%     95ms    110ms    118ms  ████████████████████
  ES.GetMode                4      4   0.0%    102ms    115ms    121ms  ████████████████████
```

Use `Ctrl+C` at any time to stop and display the final summary. The `--out` JSON report includes all individual latency samples for offline analysis.

> **Typical findings:** `ES.GetStatus` tends to have the highest loss rate on older firmware. If loss > 5% at the default 30 s interval, try increasing `--interval` to 40–60 s and check the device's WiFi signal (`wifi_rssi` sensor). Loss > 20% consistently suggests a firmware or hardware issue worth reporting to Marstek.

---

#### `test/mping.py` — ping-like latency tool

**Why:** `comm_quality.py` gives aggregated statistics over a long run. `mping.py` gives you *immediate* per-packet feedback — exactly like the standard `ping` command — so you can watch latency spike in real time, correlate losses with events (WiFi drop, battery state change), and quickly validate a timeout value.

**What it does:** Sends one request per second (configurable) and prints each result as it arrives. On exit (Ctrl+C or `--count` reached), prints min/avg/max RTT statistics.

```bash
# Default: ES.GetStatus, auto-discover
python3 test/mping.py

# Specific IP and command
python3 test/mping.py --ip 192.168.0.104
python3 test/mping.py --ip 192.168.0.104 --cmd Bat.GetStatus
python3 test/mping.py --ip 192.168.0.104 --cmd EM.GetStatus

# Send exactly 20 pings, one every 2 seconds
python3 test/mping.py --ip 192.168.0.104 --count 20 --interval 2

# Available commands: ES.GetStatus, Bat.GetStatus, EM.GetStatus,
#                     ES.GetMode, Marstek.GetDevice
```

Output example:

```
MPING 192.168.0.104: method=ES.GetStatus, timeout=2000ms
response seq=1  time=68.2ms
response seq=2  time=74.5ms
response seq=3  time=312.1ms
timeout    seq=4 (>2000ms)
response seq=5  time=1419.3ms
response seq=6  time=71.0ms
^C
--- 192.168.0.104 ES.GetStatus ping statistics ---
6 requests, 5 responses, 16.7% loss
rtt min/avg/max = 68.2/389.0/1419.3 ms
```

A timeout followed by a very high latency response (e.g. >1000ms) is the signature of a **WiFi reconnection** on the device side — the device dropped its WiFi association and rejoined. This is normal for Marstek batteries and is why the integration keeps the previous state for 5 minutes before marking entities as "unknown".

---

This repo is a fork from https://github.com/jaapp/ha-marstek-local-api (January 2026)
