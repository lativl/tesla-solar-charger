# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment
- ALWAYS use the remote sandbox environment for application hosting, testing, analysis
- NEVER run code locally — use SSH to the remote host defined in `.deploy.env` (`REMOTE_USER@REMOTE_HOST`)
- Local computer is used for code only (development, reading, committing to GitHub)
- Always deploy via `bash deploy.sh` (reads `.deploy.env` for remote target — gitignored)
- Never deploy manually unless it's a PoC or test script unrelated to the main app
- Remote directory and SSH credentials are in `.deploy.env` (`REMOTE_HOST`, `REMOTE_USER`, `REMOTE_DIR`)

## Commands

```bash
# Deploy to remote (rsync + docker compose build + up)
bash deploy.sh

# View live logs on remote (substitute values from .deploy.env)
ssh $REMOTE_USER@$REMOTE_HOST 'docker logs tesla-solar-charger --tail 50 -f'

# Restart app only (no rebuild)
ssh $REMOTE_USER@$REMOTE_HOST "cd $REMOTE_DIR && docker compose restart app"

# Query remote DB
ssh $REMOTE_USER@$REMOTE_HOST 'docker exec tesla-solar-charger python3 -c "
from app.database import SessionLocal; from app.models import Setting
db = SessionLocal()
for r in db.query(Setting).all(): print(r.key, \"=\", r.value)
"'

# Run tests locally
python -m pytest tests/

# Test a single file
python -m pytest tests/test_algorithm.py -v
```

## Architecture

FastAPI app (`app/main.py`) with four main subsystems:

**1. Charging Worker** (`app/charger/worker.py`)
The core loop runs every 10s. Each tick: reads solar/battery state from MQTT, polls Tesla (rate-limited by `tesla_poll_interval_s`), runs `ChargerAlgorithm.decide()`, sends amps/start/stop commands. Modes: `solar_only`, `schedule`, `manual`, `paused`. Managed by `ChargingMode` enum and `get_mode()`/`set_mode()`.

**2. Tesla Transport** (`app/tesla/`)
- `transport.py` — abstract `TeslaTransport` interface
- `api.py` — Fleet API transport (OAuth + HTTP proxy for signed commands)
- `ble.py` — ESPHome BLE transport (HTTP REST to ESP32 at `ble_host`). All entity URLs use new-style ESPHome format (`sensor/Battery`, `sensor/Charger%20Current`, etc.) required from ESPHome 2026.7.0
- `manager.py` — singleton `transport_manager` switches between `fleet_api` and `ble` channels; persists active channel in DB; reads `ble_host` and entity overrides from `settings` table

**3. Algorithm** (`app/charger/algorithm.py`)
Pure function `ChargerAlgorithm.decide(state, settings) → ChargingAction`. Inputs: solar surplus, battery SoC/power, grid power, EV state. Settings tunable via UI strategy presets.

**4. Lux→PV Model** (`app/charger/lux_model.py`)
Learns lux→PV relationship from `metrics` table (uncurtailed observations only). Groups into 1000-lux buckets, stores P90/max per bucket in `lux_pv_buckets`. Used by algorithm for speculative charge starts. Refreshes hourly. API: `GET /api/lux-model`.

**Data flow:**
```
MQTT (solar_assistant/#) → mqtt_client → worker → algorithm → Tesla transport
                                       → metrics table → lux_pv_model
HA REST API (ecowitt lux) → ha_client → worker / /api/status
```

**DB** (`data/tesla_charger.db`, SQLite via SQLAlchemy): `settings`, `schedules`, `sessions`, `metrics`, `lux_pv_buckets`, `strategies`, `tesla_tokens`. All settings (charger params + BLE entity paths) are key/value rows in `settings` table. `DEFAULT_ENTITY_MAP` in `ble.py` is the fallback; non-empty DB rows override it.

**Frontend**: Static HTML/JS/CSS in `app/static/`. No framework. Pages communicate via `GET /api/status` (REST) and `ws://host/ws/status` (WebSocket for real-time updates). `nav.js` runs on every page (vehicle tab name, app version).

## Troubleshooting
- Check remote docker logs for errors
- Query DB directly via `docker exec` + Python
- Use `curl` on remote to test API endpoints
- BLE entity path issues: check `settings` table for `ble_entity_*` rows that may override `DEFAULT_ENTITY_MAP`

## Security
- NEVER commit `.env`, API keys, tokens, usernames or passwords
- If you see hardcoded credentials, flag them immediately and refuse to commit
- Always check `git diff` before any `git commit` or `git push`

## Versioning
- Format: `MAJOR.MINOR.PATCH` — version lives in `VERSION` file (root)
- MAJOR: user request only
- MINOR: major features or changes
- PATCH: bug fixes and minor improvements
- Update user manual (`help.html`) when MAJOR or MINOR changes

## Python Standards
- Python 3.12, type hints on all new code
- Format with `black`, lint with `ruff`
- New public functions must have docstrings
