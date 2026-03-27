import json
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.charger.algorithm import ChargerSettings, ChargingMode
from app.charger.worker import get_mode, set_mode
from app.database import get_db
from app.event_log import log as elog, INFO
from app.models import Schedule, Setting, Strategy

router = APIRouter(prefix="/api")

# Charger algorithm settings (applied by active strategy or edited directly)
CHARGER_DEFAULTS = {
    "min_battery_soc": "25",
    "battery_soc_hysteresis": "5",
    "max_grid_import_w": "200",
    "emergency_grid_limit_w": "500",
    "min_charge_amps": "6",
    "max_charge_amps": "16",
    "ramp_up_step": "2",
    "ramp_down_step": "4",
    "charger_phases": "1",
    "charger_voltage": "230",
    "battery_protection_buffer_w": "200",
    "battery_discharge_threshold_w": "500",
    "battery_penalty_delay_s": "30",
    "battery_recovery_delay_s": "60",
    "grid_penalty_delay_s": "30",
    "grid_recovery_delay_s": "60",
    "ramp_up_delay_s": "120",
    "ramp_down_delay_s": "60",
    "tesla_poll_interval_s": "300",
    "speculative_start_soc": "80",
    "speculative_min_pv_w": "500",
    "high_soc_threshold": "95",
    "high_soc_discharge_allowance_w": "150",
    "lux_stop_threshold": "200",
    "lux_conservative_threshold": "5000",
    "lux_aggressive_threshold": "20000",
    "lux_model_curtailment_factor": "0.7",
    "lux_model_window_days": "30",
}

# Connection / channel settings (not part of strategies, configured separately)
CONNECTION_DEFAULTS = {
    "tesla_channel": "fleet_api",
    "ble_host": "",
    "ble_api_key": "",
    "ble_entity_battery_level": "sensor/Battery",
    "ble_entity_charging_current": "sensor/Charger%20Current",
    "ble_entity_charging_state": "text_sensor/Charging",
    "ble_entity_charge_limit_soc": "number/Charging%20Limit",
    "ble_entity_charger_voltage": "sensor/Charger%20Voltage",
    "ble_entity_charger_power": "sensor/Charger%20Power",
    "ble_entity_charging_switch": "switch/Charger",
    "ble_entity_charging_amps": "number/Charging%20Amps",
    "ble_entity_charge_limit": "number/Charging%20Limit",
    "ble_entity_wake_button": "button/Wake%20Up",
}

DEFAULTS = {**CHARGER_DEFAULTS, **CONNECTION_DEFAULTS}

# Tooltips for all charger settings (shown in UI)
SETTING_TOOLTIPS = {
    "min_battery_soc": "Stop EV charging if home battery drops below this %. Protects batteries from being drained by the car.",
    "battery_soc_hysteresis": "After low-battery lockout, resume only when SoC reaches min + this value. Prevents rapid on/off cycling.",
    "max_grid_import_w": "Algorithm reduces EV charging if grid import exceeds this. Set to 0 for strict zero-grid solar mode.",
    "emergency_grid_limit_w": "Hard safety stop — EV charging stops immediately if grid import exceeds this, regardless of other settings.",
    "min_charge_amps": "Minimum viable charging current. Below this, charging pauses. Most EVs require at least 5–6A.",
    "max_charge_amps": "Upper limit for charging current. Set based on your charger and home wiring capacity.",
    "ramp_up_step": "Max amps increase per 10s cycle. Lower = smoother response, higher = faster ramp to target.",
    "ramp_down_step": "Max amps decrease per cycle. Higher than ramp-up for safety — responds quickly to clouds and load spikes.",
    "charger_phases": "Number of AC phases your charger uses. Single-phase: 230W per amp. Three-phase: 690W per amp.",
    "charger_voltage": "Your local mains voltage. Used to convert watts ↔ amps in all calculations.",
    "battery_protection_buffer_w": "Reserved power headroom (watts) subtracted from surplus. Prevents borderline battery discharge during EV charging.",
    "battery_discharge_threshold_w": "If battery discharges more than this (watts), a penalty reduces EV charging power after the penalty delay.",
    "battery_penalty_delay_s": "Seconds of sustained discharge above threshold before penalty kicks in. Filters transient spikes like kettles.",
    "battery_recovery_delay_s": "Seconds discharge must stay below threshold before penalty clears. Prevents rapid on/off oscillation.",
    "grid_penalty_delay_s": "Seconds of sustained grid import above limit before penalty kicks in. Filters brief import spikes.",
    "grid_recovery_delay_s": "Seconds grid import must stay below limit before penalty clears. Prevents oscillation near threshold.",
    "ramp_up_delay_s": "After increasing amps, hold this long before next change. Prevents rapid oscillation from solar fluctuations.",
    "ramp_down_delay_s": "After decreasing amps, hold this long before next change. Shorter than ramp-up since ramp-downs are safety-oriented.",
    "tesla_poll_interval_s": "Seconds between vehicle data polls. BLE: 30s recommended (no rate limits). Fleet API: 300s recommended.",
    "speculative_start_soc": "When battery SoC ≥ this %, attempt charging even if calculated surplus is below minimum. Probes for curtailed solar.",
    "speculative_min_pv_w": "Minimum PV production (watts) to attempt speculative start. Prevents starts in genuinely low-light conditions.",
    "high_soc_threshold": "When battery SoC ≥ this %, tolerate slight discharge to keep EV charging stable.",
    "high_soc_discharge_allowance_w": "Watts of battery discharge tolerated when SoC is above threshold. Prevents unnecessary ramp-downs near full.",
    "lux_stop_threshold": "Below this lux, stop trying solar entirely — too dark or overcast. Requires Home Assistant integration.",
    "lux_conservative_threshold": "Below this lux, skip speculative starts (cloudy). Regular surplus charging still works.",
    "lux_aggressive_threshold": "Above this lux (bright sun), lower speculative start SoC requirement by 10% for more aggressive charging.",
    "lux_model_curtailment_factor": "Fraction (0–1) of predicted PV headroom added to surplus. 0 = disabled, 0.7 = conservative, 1 = full trust.",
    "lux_model_window_days": "Days of historical data used to learn the lux→PV relationship. Longer = more data but slower to adapt.",
}

# Built-in strategy presets (seeded once on first boot)
BUILTIN_STRATEGIES = {
    "Fleet API Default": {
        "tesla_poll_interval_s": "300",
        "ramp_up_delay_s": "120",
        "ramp_down_delay_s": "60",
        "ramp_up_step": "2",
        "ramp_down_step": "4",
        "battery_penalty_delay_s": "30",
        "battery_recovery_delay_s": "60",
        "grid_penalty_delay_s": "30",
        "grid_recovery_delay_s": "60",
    },
    "BLE Aggressive": {
        "tesla_poll_interval_s": "30",
        "ramp_up_delay_s": "30",
        "ramp_down_delay_s": "20",
        "ramp_up_step": "3",
        "ramp_down_step": "6",
        "battery_penalty_delay_s": "20",
        "battery_recovery_delay_s": "40",
        "grid_penalty_delay_s": "20",
        "grid_recovery_delay_s": "40",
    },
}


def seed_strategies(db: Session):
    """Create built-in strategies if they don't exist yet."""
    for name, settings_dict in BUILTIN_STRATEGIES.items():
        existing = db.query(Strategy).filter_by(name=name).first()
        if not existing:
            is_active = (name == "Fleet API Default")
            db.add(Strategy(
                name=name,
                settings_json=json.dumps(settings_dict),
                is_active=is_active,
            ))
    db.commit()


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    rows = db.query(Setting).all()
    stored = {r.key: r.value for r in rows}
    result = {**DEFAULTS, **stored}
    result["mode"] = get_mode().value
    return result


@router.get("/settings/tooltips")
def get_tooltips():
    return SETTING_TOOLTIPS


@router.put("/settings")
def update_settings(data: dict, db: Session = Depends(get_db)):
    if "mode" in data:
        try:
            set_mode(ChargingMode(data.pop("mode")))
        except ValueError:
            pass

    changed = []
    for key, value in data.items():
        if key in DEFAULTS:
            existing = db.query(Setting).get(key)
            if existing:
                if existing.value != str(value):
                    changed.append(f"{key}: {existing.value} → {value}")
                existing.value = str(value)
                existing.updated_at = datetime.utcnow()
            else:
                changed.append(f"{key}: {DEFAULTS[key]} → {value}")
                db.add(Setting(key=key, value=str(value)))

    # If BLE settings changed, reinitialize BLE transport
    ble_keys = {k for k in data if k.startswith("ble_")}
    db.commit()
    if ble_keys:
        from app.tesla.manager import transport_manager
        transport_manager.reinitialize_ble()

    if changed:
        elog(f"Settings updated: {', '.join(changed)}", INFO, "manual")
    return {"status": "ok"}


# --- Strategies ---

class StrategyCreate(BaseModel):
    name: str
    settings: dict = {}


@router.get("/strategies")
def list_strategies(db: Session = Depends(get_db)):
    seed_strategies(db)
    strategies = db.query(Strategy).order_by(Strategy.created_at).all()
    return [
        {
            "id": s.id,
            "name": s.name,
            "settings": json.loads(s.settings_json or "{}"),
            "is_active": s.is_active,
            "created_at": s.created_at.isoformat(),
        }
        for s in strategies
    ]


@router.post("/strategies")
def create_strategy(data: StrategyCreate, db: Session = Depends(get_db)):
    s = Strategy(name=data.name, settings_json=json.dumps(data.settings), is_active=False)
    db.add(s)
    db.commit()
    db.refresh(s)
    return {"id": s.id, "status": "created"}


@router.put("/strategies/{strategy_id}")
def update_strategy(strategy_id: int, data: StrategyCreate, db: Session = Depends(get_db)):
    s = db.query(Strategy).get(strategy_id)
    if not s:
        return {"error": "not found"}
    s.name = data.name
    s.settings_json = json.dumps(data.settings)
    db.commit()
    return {"status": "updated"}


@router.delete("/strategies/{strategy_id}")
def delete_strategy(strategy_id: int, db: Session = Depends(get_db)):
    s = db.query(Strategy).get(strategy_id)
    if not s:
        return {"error": "not found"}
    if s.is_active:
        return {"error": "cannot delete the active strategy"}
    db.delete(s)
    db.commit()
    return {"status": "deleted"}


@router.put("/strategies/{strategy_id}/activate")
def activate_strategy(strategy_id: int, db: Session = Depends(get_db)):
    s = db.query(Strategy).get(strategy_id)
    if not s:
        return {"error": "not found"}

    # Apply strategy settings to the flat settings table
    strategy_settings = json.loads(s.settings_json or "{}")
    changed = []
    for key, value in strategy_settings.items():
        if key in CHARGER_DEFAULTS:
            existing = db.query(Setting).get(key)
            if existing:
                if existing.value != str(value):
                    changed.append(f"{key}: {existing.value} → {value}")
                existing.value = str(value)
                existing.updated_at = datetime.utcnow()
            else:
                changed.append(f"{key}: → {value}")
                db.add(Setting(key=key, value=str(value)))

    # Mark this strategy active, deactivate others
    db.query(Strategy).filter(Strategy.is_active == True).update({"is_active": False})  # noqa
    s.is_active = True
    db.commit()

    if changed:
        elog(f"Strategy '{s.name}' activated: {', '.join(changed)}", INFO, "manual")
    else:
        elog(f"Strategy '{s.name}' activated (no changes)", INFO, "manual")

    return {"status": "activated", "applied": len(changed)}


@router.post("/strategies/{strategy_id}/duplicate")
def duplicate_strategy(strategy_id: int, db: Session = Depends(get_db)):
    s = db.query(Strategy).get(strategy_id)
    if not s:
        return {"error": "not found"}
    copy = Strategy(
        name=f"{s.name} (copy)",
        settings_json=s.settings_json,
        is_active=False,
    )
    db.add(copy)
    db.commit()
    db.refresh(copy)
    return {"id": copy.id, "status": "duplicated"}


# --- Schedules ---

class ScheduleCreate(BaseModel):
    name: str
    start_time: str
    end_time: str
    target_soc: int = 80
    allow_grid: bool = False
    max_grid_amps: int = 16
    days_of_week: str = "*"
    enabled: bool = True


@router.get("/schedules")
def list_schedules(db: Session = Depends(get_db)):
    schedules = db.query(Schedule).order_by(Schedule.start_time).all()
    return [
        {
            "id": s.id,
            "name": s.name,
            "start_time": s.start_time,
            "end_time": s.end_time,
            "target_soc": s.target_soc,
            "allow_grid": s.allow_grid,
            "max_grid_amps": s.max_grid_amps,
            "days_of_week": s.days_of_week,
            "enabled": s.enabled,
        }
        for s in schedules
    ]


@router.post("/schedules")
def create_schedule(data: ScheduleCreate, db: Session = Depends(get_db)):
    sched = Schedule(**data.model_dump())
    db.add(sched)
    db.commit()
    db.refresh(sched)
    return {"id": sched.id, "status": "created"}


@router.put("/schedules/{schedule_id}")
def update_schedule(schedule_id: int, data: ScheduleCreate, db: Session = Depends(get_db)):
    sched = db.query(Schedule).get(schedule_id)
    if not sched:
        return {"error": "not found"}, 404
    for key, value in data.model_dump().items():
        setattr(sched, key, value)
    db.commit()
    return {"status": "updated"}


@router.delete("/schedules/{schedule_id}")
def delete_schedule(schedule_id: int, db: Session = Depends(get_db)):
    sched = db.query(Schedule).get(schedule_id)
    if not sched:
        return {"error": "not found"}, 404
    db.delete(sched)
    db.commit()
    return {"status": "deleted"}
