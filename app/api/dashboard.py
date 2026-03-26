from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.charger.algorithm import ChargingMode
from app.charger.worker import get_mode
from app.database import get_db
from app.event_log import get_events, clear as clear_events
from app.ha.client import ha_client
from app.models import ChargingSession, Metric
from app.mqtt.client import mqtt_client
from app.tesla.manager import transport_manager

router = APIRouter(prefix="/api")


@router.get("/status")
async def get_status():
    solar = mqtt_client.get_solar_status()
    ev = transport_manager.active.last_state
    mode = get_mode()
    solar_lux = await ha_client.get_solar_lux() if ha_client.configured else None

    return {
        "solar": solar,
        "ev": {
            "name": ev.name,
            "state": ev.state,
            "battery_level": ev.battery_level,
            "charge_state": ev.charge_state,
            "charging_amps": ev.charging_amps,
            "charge_amps_request": ev.charge_amps_request,
            "charge_limit_soc": ev.charge_limit_soc,
            "charger_voltage": ev.charger_voltage,
            "charger_power": ev.charger_power,
            "time_to_full": ev.time_to_full,
            "is_plugged_in": ev.is_plugged_in,
        },
        "charger": {
            "mode": mode.value,
        },
        "ha": {
            "solar_lux": solar_lux,
            "configured": ha_client.configured,
        },
    }


@router.get("/history")
def get_history(
    hours: int = Query(24, ge=1, le=720),
    db: Session = Depends(get_db),
):
    since = datetime.utcnow() - timedelta(hours=hours)
    metrics = (
        db.query(Metric)
        .filter(Metric.timestamp >= since)
        .order_by(Metric.timestamp)
        .all()
    )
    return [
        {
            "timestamp": m.timestamp.isoformat(),
            "pv_power": m.pv_power,
            "battery_power": m.battery_power,
            "battery_soc": m.battery_soc,
            "grid_power": m.grid_power,
            "load_power": m.load_power,
            "ev_charging_amps": m.ev_charging_amps,
            "ev_soc": m.ev_soc,
            "solar_lux": m.solar_lux,
        }
        for m in metrics
    ]


@router.get("/sessions")
def get_sessions(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    sessions = (
        db.query(ChargingSession)
        .order_by(ChargingSession.started_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": s.id,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "ended_at": s.ended_at.isoformat() if s.ended_at else None,
            "energy_kwh": s.energy_kwh,
            "solar_kwh": s.solar_kwh,
            "grid_kwh": s.grid_kwh,
            "start_soc": s.start_soc,
            "end_soc": s.end_soc,
            "avg_amps": s.avg_amps,
        }
        for s in sessions
    ]


@router.get("/events")
def get_event_log(
    limit: int = Query(200, ge=1, le=500),
    category: str = Query(None),
):
    return get_events(limit=limit, category=category)


@router.delete("/events")
def clear_event_log():
    clear_events()
    return {"ok": True}
