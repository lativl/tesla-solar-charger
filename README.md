# Tesla Solar Charger

Automatically maximizes solar self-consumption by dynamically adjusting Tesla EV charging current to match available solar surplus. Reads real-time inverter data from Solar Assistant via MQTT and controls the vehicle through either the Tesla Fleet API or a local ESPHome BLE bridge (ESP32).

## Features

- **Adaptive charging algorithm** — adjusts amps every 10 seconds to consume surplus solar without drawing from the grid or discharging the home battery
- **Two vehicle channels** — Tesla Fleet API (cloud) or ESPHome BLE (local, no rate limits, <1s latency)
- **Settings strategies** — named presets (Fleet API Default, BLE Aggressive) that bundle all algorithm parameters; switch in one click
- **Solar lux integration** — uses a Home Assistant lux sensor to predict and exploit inverter curtailment
- **Speculative start** — probes for hidden solar capacity when the inverter is curtailed
- **Charging schedules** — time-window charging at fixed amps for cheap night-rate electricity
- **Full vehicle control** — charging, climate, locks, windows, frunk/trunk, sentry mode, tire pressure via the Vehicle page
- **Power history chart** — up to 7-day chart of Solar, Battery, Grid, Load, and EV power
- **Real-time dashboard** — WebSocket-driven gauges and power flow cards
- **Event logger** — deduplicated algorithm decisions, Tesla commands, and MQTT events

## Architecture

```
Solar Panels → Deya Inverter → Solar Assistant → MQTT Broker
                                                       │
                                                   This App
                                                  /         \
                                   Tesla Fleet API          ESPHome BLE (ESP32)
                                   + HTTP Proxy                    │
                                         │                    Bluetooth
                                         └──────────────────── Tesla EV
```

**Stack:** Python 3.12, FastAPI, SQLite, Redis, MQTT (paho), Docker Compose

## Requirements

- Docker and Docker Compose v2
- Solar Assistant publishing inverter data to an MQTT broker
- **For Fleet API channel:** Tesla Developer App credentials + `tesla_fleet.key`
- **For BLE channel:** ESP32 running ESPHome with the Tesla BLE component

## Quick Start

### 1. Clone

```bash
git clone https://github.com/lativl/tesla-solar-charger.git
cd tesla-solar-charger
```

### 2. Configure environment

```bash
cp .env.example .env
nano .env
```

Minimum required values:

| Variable | Description | Example |
|---|---|---|
| `MQTT_HOST` | Solar Assistant MQTT broker IP or hostname | `192.168.1.50` |
| `MQTT_PORT` | MQTT port | `1883` |
| `TESLA_CLIENT_ID` | Tesla Developer App client ID | `abc123...` |
| `TESLA_CLIENT_SECRET` | Tesla Developer App client secret | `xyz789...` |
| `TESLA_REDIRECT_URI` | OAuth callback URL (your server) | `http://192.168.1.100:5050/api/tesla/callback` |
| `SECRET_KEY` | Random secret for session security | `change-me-to-random-string` |
| `TZ` | Your timezone | `Europe/London` |

Optional:

| Variable | Description |
|---|---|
| `HA_URL` | Home Assistant base URL (for Solar Lux) |
| `HA_TOKEN` | Home Assistant long-lived access token |
| `HA_LUX_ENTITY` | HA entity ID for lux sensor (e.g. `sensor.solar_lux`) |
| `APP_PORT` | Web UI port (default `5050`) |

### 3. Generate Fleet API keys

Skip this step if you plan to use BLE-only mode.

```bash
# Fleet API signing key
openssl ecparam -name prime256v1 -genkey -noout -out tesla_fleet.key
openssl ec -in tesla_fleet.key -pubout -out tesla_fleet.pub

# TLS certs for the HTTP proxy
mkdir -p tesla-proxy-certs
openssl ecparam -name prime256v1 -genkey -noout -out tesla-proxy-certs/tls-key.pem
openssl req -new -x509 -key tesla-proxy-certs/tls-key.pem \
  -out tesla-proxy-certs/tls-cert.pem -days 365 -subj "/CN=localhost"
```

Host `tesla_fleet.pub` at `https://your-domain/.well-known/appspecific/com.tesla.3p.public-key.pem` — Tesla verifies it during app registration.

### 4. Start

```bash
docker compose up -d
```

The app is available at `http://<server-ip>:5050`.

### 5. Connect Tesla (Fleet API)

Go to **Settings → Tesla Connection** and complete the OAuth flow:

1. Click **Open Tesla Login** — sign in with your Tesla account in the new tab.
2. After redirect, copy the `code` parameter from the URL.
3. Paste it and click **Connect**.

Tokens are stored in SQLite and auto-refreshed.

### 6. Connect Tesla (ESPHome BLE)

Go to **Settings → Communication Channel**, select **ESPHome BLE**, and enter your ESP32's IP address. See [ESPHome BLE Setup](#esphome-ble-setup) for details.

### 7. Select a strategy and start charging

Go to **Settings → Settings Strategies** and activate the strategy matching your channel:
- **Fleet API Default** — conservative 300s poll interval
- **BLE Aggressive** — fast 30s poll interval

Plug in the Tesla, then click **Solar Only** on the Dashboard.

---

## Updating

```bash
git pull
./deploy.sh
```

`deploy.sh` syncs files to the remote server, rebuilds the image, and restarts containers. Configure the deployment target in `.deploy.env` (gitignored):

```bash
# .deploy.env
REMOTE_USER=youruser
REMOTE_HOST=192.168.1.100
REMOTE_DIR=/home/youruser/tesla-solar-charger
```

---

## ESPHome BLE Setup

The BLE channel uses an ESP32 running ESPHome with the [Tesla BLE component](https://github.com/yoziru/esphome-tesla-ble). It communicates directly with the Tesla over Bluetooth — no cloud, no rate limits.

Required ESPHome entities:

| Type | Entity | Purpose |
|---|---|---|
| `sensor` | `battery_level`, `charging_current`, `charging_state`, `range`, `charging_rate`, `energy_added`, `outside_temperature`, `tpms_front_left/right`, `tpms_rear_left/right`, `charger_power` | Vehicle data |
| `switch` | `charging`, `sentry_mode`, `heated_steering` | Toggle controls |
| `number` | `charging_amps`, `charge_limit` | Set values |
| `cover` | `charge_port_door`, `windows`, `trunk`, `frunk` | Actuators |
| `lock` | `doors`, `charge_port_latch` | Lock controls |
| `climate` | `climate` | HVAC (inside temp + target temp) |
| `button` | `wake_vehicle`, `flash_lights`, `sound_horn`, `force_data_update` | One-shot actions |

ESPHome's **native web server** must be enabled (port 80). The app calls its REST API directly.

After flashing and pairing the ESP32 with the Tesla, configure the BLE host IP in **Settings → Communication Channel**.

---

## Algorithm

The charging algorithm runs every 10 seconds in **Solar Only** mode:

1. **Gate checks** — blocks charging if home battery SoC is below minimum, grid import exceeds the emergency limit, or lux is below the stop threshold.
2. **Surplus calculation** — `surplus = PV power − house load − protection buffer`
3. **Penalty system** — sustained grid import or battery discharge above thresholds reduces the target amps (with configurable delays to filter transient spikes).
4. **Speculative start** — if battery SoC is high and PV is producing, attempts charging at minimum amps to probe for inverter curtailment headroom.
5. **Lux model** — optionally predicts available PV headroom from historical lux→power data and adds it to the surplus estimate.
6. **Ramp control** — changes amps in configured steps with hold delays to prevent oscillation from cloud cover.

---

## Settings Strategies

A strategy is a named bundle of all algorithm parameters saved together. Activating a strategy applies all values instantly.

| Strategy | Poll Interval | Ramp Up Delay | Ramp Down Delay | Best For |
|---|---|---|---|---|
| Fleet API Default | 300s | 120s | 60s | Tesla Fleet API channel |
| BLE Aggressive | 30s | 30s | 20s | ESPHome BLE channel |

Create and duplicate strategies from **Settings → Settings Strategies**.

---

## Project Structure

```
tesla-solar-charger/
├── app/
│   ├── api/              # FastAPI route handlers
│   │   ├── dashboard.py  # /api/status, /api/history
│   │   ├── settings.py   # /api/settings, /api/strategies
│   │   ├── tesla.py      # /api/tesla/command, /api/tesla/channel
│   │   └── ws.py         # WebSocket /ws/status
│   ├── charger/
│   │   ├── algorithm.py  # Surplus calculation and ramp logic
│   │   ├── lux_model.py  # Historical lux→PV model
│   │   ├── scheduler.py  # Schedule activation
│   │   └── worker.py     # Main control loop (runs every 10s)
│   ├── ha/               # Home Assistant REST client (lux sensor)
│   ├── mqtt/             # MQTT subscriber + Solar Assistant topic map
│   ├── tesla/
│   │   ├── transport.py  # TeslaTransport ABC
│   │   ├── api.py        # Fleet API transport (tesla-http-proxy)
│   │   ├── ble.py        # ESPHome BLE transport
│   │   ├── manager.py    # TransportManager (channel switching)
│   │   ├── auth.py       # OAuth2 token management
│   │   └── models.py     # VehicleState dataclass
│   ├── static/           # Web UI (HTML + vanilla JS + CSS)
│   ├── config.py         # Settings from environment variables
│   ├── database.py       # SQLite (SQLAlchemy async)
│   ├── event_log.py      # In-memory ring buffer (last 500 events)
│   ├── models.py         # ORM models (Setting, Strategy, Schedule, History)
│   └── main.py           # FastAPI app + lifespan startup
├── tests/
│   └── test_algorithm.py
├── docker-compose.yml
├── Dockerfile
├── .env.example
└── deploy.sh
```

---

## Docker Services

| Container | Image | Purpose |
|---|---|---|
| `tesla-solar-charger` | Custom (FastAPI) | Main app: web UI, API, MQTT subscriber, charging worker |
| `tesla-solar-charger-redis` | redis:7-alpine | MQTT state cache |
| `tesla-http-proxy` | tesla/vehicle-command | Signs Fleet API commands with your private key (port 4443) |

---

## Troubleshooting

**Dashboard shows all zeros** — MQTT not connected. Check `docker logs tesla-solar-charger`. Verify the MQTT broker is reachable.

**Tesla shows "Not connected"** — Missing or expired tokens. Re-authorize via Settings. After `.env` changes, run `./deploy.sh` (not `docker restart` — it doesn't re-read `.env`).

**Commands return 403** — The `tesla-http-proxy` container may not be running (`docker ps`), or `tesla_fleet.key` doesn't match the registered public key.

**Key revoked** — Re-add via QR code in the Tesla app, then click **Clear Key Revoked** on the Vehicle page. Consider switching to BLE to eliminate this issue.

**BLE shows unreachable** — Verify the ESP32 IP, check that the ESPHome native web server is enabled, and assign a DHCP reservation for the ESP32.

**Solar lux not appearing** — Ensure `HA_URL`, `HA_TOKEN`, and `HA_LUX_ENTITY` are set in `.env` and the container was restarted with `./deploy.sh`.

Full documentation is available in the [in-app User Manual](/help.html) at `http://<server>:5050/help.html`.

---

## License

MIT
