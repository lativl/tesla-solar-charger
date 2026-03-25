import logging

from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.charger.algorithm import ChargingMode
from app.charger.worker import set_mode
from app.event_log import log as elog, INFO, SUCCESS, ERROR
from app.tesla.api import tesla_api
from app.tesla.auth import exchange_code, get_authorize_url, get_valid_token
from app.tesla.models import vehicle_state_to_dict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tesla")


class CodeExchange(BaseModel):
    code: str


@router.get("/auth")
def start_auth():
    """Returns the Tesla OAuth URL (uses HA redirect URI)."""
    url = get_authorize_url()
    return {"url": url}


@router.get("/callback")
async def auth_callback(code: str = Query(...), state: str = Query("")):
    await exchange_code(code)
    return RedirectResponse("/settings.html?tesla=connected")


@router.post("/exchange")
async def exchange_auth_code(body: CodeExchange):
    """Exchange an authorization code obtained via the HA redirect relay."""
    try:
        await exchange_code(body.code)
        return {"ok": True, "message": "Tesla connected successfully"}
    except Exception as e:
        logger.error(f"Code exchange failed: {e}")
        return {"ok": False, "error": str(e)}


@router.get("/status")
async def tesla_status():
    token = get_valid_token()
    if not token:
        return {"connected": False, "vehicle": None}

    state = tesla_api.last_state
    return {
        "connected": True,
        "vehicle": vehicle_state_to_dict(state),
        "key_revoked": tesla_api.key_revoked,
    }


@router.post("/command")
async def tesla_command(data: dict):
    cmd = data.get("command")
    if cmd == "start":
        set_mode(ChargingMode.MANUAL)
        elog("Manual: Start charging", INFO, "manual")
        ok = await tesla_api.start_charging()
        elog("Manual: Charging started" if ok else "Manual: Start charging failed", SUCCESS if ok else ERROR, "manual")
    elif cmd == "stop":
        set_mode(ChargingMode.MANUAL)
        elog("Manual: Stop charging", INFO, "manual")
        ok = await tesla_api.stop_charging()
        elog("Manual: Charging stopped" if ok else "Manual: Stop charging failed", SUCCESS if ok else ERROR, "manual")
    elif cmd == "set_amps":
        set_mode(ChargingMode.MANUAL)
        amps = int(data.get("amps", 8))
        elog(f"Manual: Set amps to {amps}A", INFO, "manual")
        ok = await tesla_api.set_charging_amps(amps)
        elog(f"Manual: Amps set to {amps}A" if ok else f"Manual: Set amps failed", SUCCESS if ok else ERROR, "manual")
    elif cmd == "wake":
        elog("Manual: Wake up vehicle", INFO, "manual")
        ok = await tesla_api.wake_up()
        elog("Manual: Wake command sent" if ok else "Manual: Wake failed", SUCCESS if ok else ERROR, "manual")
    elif cmd == "set_charge_limit":
        percent = int(data.get("percent", 80))
        elog(f"Manual: Set charge limit to {percent}%", INFO, "manual")
        ok = await tesla_api.set_charge_limit(percent)
        elog(f"Manual: Charge limit set to {percent}%" if ok else "Manual: Set charge limit failed", SUCCESS if ok else ERROR, "manual")
    elif cmd == "charge_port_open":
        elog("Manual: Open charge port", INFO, "manual")
        ok = await tesla_api.charge_port_door_open()
        elog("Manual: Charge port opened" if ok else "Manual: Open charge port failed", SUCCESS if ok else ERROR, "manual")
    elif cmd == "charge_port_close":
        elog("Manual: Close charge port", INFO, "manual")
        ok = await tesla_api.charge_port_door_close()
        elog("Manual: Charge port closed" if ok else "Manual: Close charge port failed", SUCCESS if ok else ERROR, "manual")
    elif cmd == "door_lock":
        elog("Manual: Lock doors", INFO, "manual")
        ok = await tesla_api.door_lock()
        elog("Manual: Doors locked" if ok else "Manual: Lock doors failed", SUCCESS if ok else ERROR, "manual")
    elif cmd == "door_unlock":
        elog("Manual: Unlock doors", INFO, "manual")
        ok = await tesla_api.door_unlock()
        elog("Manual: Doors unlocked" if ok else "Manual: Unlock doors failed", SUCCESS if ok else ERROR, "manual")
    elif cmd == "climate_start":
        elog("Manual: Start climate", INFO, "manual")
        ok = await tesla_api.climate_start()
        elog("Manual: Climate started" if ok else "Manual: Start climate failed", SUCCESS if ok else ERROR, "manual")
    elif cmd == "climate_stop":
        elog("Manual: Stop climate", INFO, "manual")
        ok = await tesla_api.climate_stop()
        elog("Manual: Climate stopped" if ok else "Manual: Stop climate failed", SUCCESS if ok else ERROR, "manual")
    elif cmd == "set_temps":
        driver = float(data.get("driver_temp", 20))
        passenger = float(data.get("passenger_temp", 20))
        elog(f"Manual: Set temps to {driver}/{passenger}°C", INFO, "manual")
        ok = await tesla_api.set_temps(driver, passenger)
        elog(f"Manual: Temps set to {driver}/{passenger}°C" if ok else "Manual: Set temps failed", SUCCESS if ok else ERROR, "manual")
    elif cmd == "trunk_open":
        elog("Manual: Open trunk", INFO, "manual")
        ok = await tesla_api.actuate_trunk("rear")
        elog("Manual: Trunk opened" if ok else "Manual: Open trunk failed", SUCCESS if ok else ERROR, "manual")
    elif cmd == "frunk_open":
        elog("Manual: Open frunk", INFO, "manual")
        ok = await tesla_api.actuate_trunk("front")
        elog("Manual: Frunk opened" if ok else "Manual: Open frunk failed", SUCCESS if ok else ERROR, "manual")
    elif cmd == "flash_lights":
        elog("Manual: Flash lights", INFO, "manual")
        ok = await tesla_api.flash_lights()
        elog("Manual: Lights flashed" if ok else "Manual: Flash lights failed", SUCCESS if ok else ERROR, "manual")
    elif cmd == "honk_horn":
        elog("Manual: Honk horn", INFO, "manual")
        ok = await tesla_api.honk_horn()
        elog("Manual: Horn honked" if ok else "Manual: Honk horn failed", SUCCESS if ok else ERROR, "manual")
    elif cmd == "sentry_on":
        elog("Manual: Enable sentry mode", INFO, "manual")
        ok = await tesla_api.set_sentry_mode(True)
        elog("Manual: Sentry mode enabled" if ok else "Manual: Enable sentry failed", SUCCESS if ok else ERROR, "manual")
    elif cmd == "sentry_off":
        elog("Manual: Disable sentry mode", INFO, "manual")
        ok = await tesla_api.set_sentry_mode(False)
        elog("Manual: Sentry mode disabled" if ok else "Manual: Disable sentry failed", SUCCESS if ok else ERROR, "manual")
    elif cmd == "vent_windows":
        elog("Manual: Vent windows", INFO, "manual")
        ok = await tesla_api.window_control("vent")
        elog("Manual: Windows vented" if ok else "Manual: Vent windows failed", SUCCESS if ok else ERROR, "manual")
    elif cmd == "close_windows":
        elog("Manual: Close windows", INFO, "manual")
        ok = await tesla_api.window_control("close")
        elog("Manual: Windows closed" if ok else "Manual: Close windows failed", SUCCESS if ok else ERROR, "manual")
    elif cmd == "seat_heater":
        seat = int(data.get("seat", 0))
        level = int(data.get("level", 0))
        seat_names = {0: "Driver", 1: "Passenger", 2: "Rear left", 3: "Rear center", 4: "Rear right"}
        seat_name = seat_names.get(seat, f"Seat {seat}")
        elog(f"Manual: {seat_name} heater level {level}", INFO, "manual")
        ok = await tesla_api.set_seat_heater(seat, level)
        elog(f"Manual: {seat_name} heater set to {level}" if ok else f"Manual: Set seat heater failed", SUCCESS if ok else ERROR, "manual")
    elif cmd == "steering_wheel_heater":
        on = data.get("on", False)
        state_str = "on" if on else "off"
        elog(f"Manual: Steering wheel heater {state_str}", INFO, "manual")
        ok = await tesla_api.set_steering_wheel_heater(on)
        elog(f"Manual: Steering wheel heater {state_str}" if ok else "Manual: Steering wheel heater failed", SUCCESS if ok else ERROR, "manual")
    elif cmd == "defrost":
        on = data.get("on", False)
        state_str = "on" if on else "off"
        elog(f"Manual: Max defrost {state_str}", INFO, "manual")
        ok = await tesla_api.set_preconditioning_max(on)
        elog(f"Manual: Max defrost {state_str}" if ok else "Manual: Max defrost failed", SUCCESS if ok else ERROR, "manual")
    elif cmd == "clear_key_revoked":
        tesla_api.clear_key_revoked()
        return {"ok": True, "command": cmd}
    else:
        return {"error": f"Unknown command: {cmd}"}
    return {"ok": ok, "command": cmd}


@router.get("/vehicle_data")
async def get_full_vehicle_data():
    """Return full vehicle data for the vehicle page."""
    token = get_valid_token()
    if not token:
        return {"connected": False, "data": None}
    data = await tesla_api.get_full_vehicle_data()
    return {"connected": True, "data": data}
