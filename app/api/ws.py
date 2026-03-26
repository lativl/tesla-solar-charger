import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.charger.worker import get_mode
from app.ha.client import ha_client
from app.mqtt.client import mqtt_client
from app.tesla.manager import transport_manager

router = APIRouter()


@router.websocket("/ws/status")
async def websocket_status(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            solar = mqtt_client.get_solar_status()
            ev = transport_manager.active.last_state
            solar_lux = await ha_client.get_solar_lux() if ha_client.configured else None
            data = {
                "solar": solar,
                "ev": {
                    "name": ev.name,
                    "battery_level": ev.battery_level,
                    "charge_state": ev.charge_state,
                    "charging_amps": ev.charging_amps,
                    "charge_amps_request": ev.charge_amps_request,
                    "charger_power": ev.charger_power,
                    "is_plugged_in": ev.is_plugged_in,
                },
                "mode": get_mode().value,
                "ha": {
                    "solar_lux": solar_lux,
                },
            }
            await websocket.send_text(json.dumps(data))
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
