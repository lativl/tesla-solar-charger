from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.charger.algorithm import ChargerSettings, ChargingMode
from app.charger.worker import get_mode, set_mode
from app.database import get_db
from app.event_log import log as elog, INFO
from app.models import Schedule, Setting

router = APIRouter(prefix="/api")

# Default settings for reference
DEFAULTS = {
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
}


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    rows = db.query(Setting).all()
    stored = {r.key: r.value for r in rows}
    result = {**DEFAULTS, **stored}
    result["mode"] = get_mode().value
    return result


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
    db.commit()
    if changed:
        elog(f"Settings updated: {', '.join(changed)}", INFO, "manual")
    return {"status": "ok"}


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
