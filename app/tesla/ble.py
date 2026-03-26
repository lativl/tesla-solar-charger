"""ESPHome Tesla BLE transport.

Communicates with the Tesla via an ESPHome ESP32 device using BLE.
The ESPHome device exposes a REST API (native web server) at a local IP.

No rate limits, no proxy recreation, <1s response times, no internet required.
"""

import asyncio
import logging

import httpx

from app.event_log import log as elog, INFO, WARN, ERROR, SUCCESS
from app.tesla.models import VehicleState
from app.tesla.transport import TeslaTransport

logger = logging.getLogger(__name__)

# Core entities used by get_vehicle_data() (charger algorithm)
DEFAULT_ENTITY_MAP = {
    "battery_level":      "sensor/battery",
    "charging_current":   "sensor/charger_current",
    "charging_state":     "text_sensor/charging",
    "charge_limit_soc":   "number/charging_limit",
    "charger_voltage":    "sensor/charger_voltage",
    "charger_power":      "sensor/charger_power",
    "charging_switch":    "switch/charger",
    "charging_amps":      "number/charging_amps",
    "charge_limit":       "number/charging_limit",
    "wake_button":        "button/wake_up",
    "time_to_full":       "sensor/time_to_full",
    # Extended sensors (used by get_full_vehicle_data)
    "charging_rate":      "sensor/charging_rate",
    "energy_added":       "sensor/energy_added",
    "outside_temp":       "sensor/outside_temperature",
    "range":              "sensor/range",
    "tpms_fl":            "sensor/tpms_front_left",
    "tpms_fr":            "sensor/tpms_front_right",
    "tpms_rl":            "sensor/tpms_rear_left",
    "tpms_rr":            "sensor/tpms_rear_right",
    # Binary sensors
    "asleep":             "binary_sensor/asleep",
    "parking_brake":      "binary_sensor/parking_brake",
    "charger_plugged":    "binary_sensor/charger",
    # Climate
    "climate":            "climate/climate",
    # Switches
    "sentry_mode":        "switch/sentry_mode",
    "heated_steering":    "switch/heated_steering",
    # Covers
    "charge_port_door":   "cover/charge_port_door",
    "trunk":              "cover/trunk",
    "frunk":              "cover/frunk",
    "windows":            "cover/windows",
    # Locks
    "charge_port_latch":  "lock/charge_port_latch",
    "doors_lock":         "lock/doors",
    # Buttons
    "flash_lights":       "button/flash_lights",
    "sound_horn":         "button/sound_horn",
    "force_update":       "button/force_data_update",
}

BAR_TO_PSI = 14.5038

# IEC 61851 codes (some ESPHome setups) → Tesla-style states
IEC_TO_CHARGE_STATE = {
    "A": "Disconnected",
    "B": "Stopped",
    "C": "Charging",
    "D": "Charging",
    "E": "Stopped",
    "F": "Stopped",
}

# Tesla-style states returned directly by some ESPHome setups (uppercase for comparison)
TESLA_CHARGE_STATES = {"CHARGING", "STOPPED", "DISCONNECTED", "COMPLETE", "NOPOWER"}


class BleTransport(TeslaTransport):
    """Communicates with Tesla via ESPHome BLE bridge device."""

    def __init__(self, host: str, api_key: str = "", entity_map: dict | None = None):
        self._host = host.rstrip("/")
        self._api_key = api_key
        self._entity_map = {**DEFAULT_ENTITY_MAP, **(entity_map or {})}
        self._last_state: VehicleState = VehicleState()
        self._reachable: bool = False
        self._current_amps: int = 0  # tracked for 5A stepping quirk

    def _url(self, entity_path: str) -> str:
        return f"http://{self._host}/{entity_path}"

    def _headers(self) -> dict:
        if self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        return {}

    async def _get(self, entity_key: str) -> dict | None:
        path = self._entity_map.get(entity_key, entity_key)
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(self._url(path), headers=self._headers(), timeout=5)
                if resp.status_code == 200:
                    return resp.json()
                logger.warning(f"BLE GET {path}: HTTP {resp.status_code}")
                return None
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            logger.warning(f"BLE GET {path}: {e}")
            return None

    async def _post(self, entity_path: str, params: dict | None = None) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    self._url(entity_path),
                    headers=self._headers(),
                    params=params,
                    timeout=10,
                )
                if resp.status_code == 200:
                    return True
                logger.warning(f"BLE POST {entity_path}: HTTP {resp.status_code}")
                return False
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            logger.warning(f"BLE POST {entity_path}: {e}")
            return False

    async def get_vehicle_data(self) -> VehicleState:
        """Fetch vehicle state from ESPHome sensor endpoints in parallel."""
        keys = ["battery_level", "charging_current", "charging_state",
                "charge_limit_soc", "charger_voltage", "charger_power", "time_to_full"]

        results = await asyncio.gather(
            *[self._get(k) for k in keys],
            return_exceptions=True,
        )
        data = {k: (r if isinstance(r, dict) else None) for k, r in zip(keys, results)}

        if all(v is None for v in data.values()):
            self._reachable = False
            logger.warning("BLE: all sensor reads failed — device unreachable")
            elog("BLE device unreachable — using last known state", WARN, "tesla")
            return self._last_state

        self._reachable = True

        def val(key, default=0):
            d = data.get(key)
            return d["value"] if d and "value" in d else default

        def state_str(key, default=""):
            d = data.get(key)
            return d["state"] if d and "state" in d else default

        battery_level = int(val("battery_level", 0))
        charging_amps = int(val("charging_current", 0))
        try:
            charge_limit_soc = int(float(val("charge_limit_soc", 80)))
        except (ValueError, TypeError):
            charge_limit_soc = 80
        charger_voltage = int(val("charger_voltage", 0))
        charger_power = float(val("charger_power", 0.0))
        try:
            time_to_full = float(val("time_to_full", 0)) / 60.0  # minutes → hours
        except (ValueError, TypeError):
            time_to_full = self._last_state.time_to_full

        # Handle both IEC 61851 codes (single letter A-F) and Tesla-style states
        # returned directly by some ESPHome setups.
        raw_state = state_str("charging_state", "").strip()
        raw_upper = raw_state.upper()
        if raw_upper in IEC_TO_CHARGE_STATE:
            charge_state = IEC_TO_CHARGE_STATE[raw_upper]
            is_plugged_in = raw_upper not in ("A", "")
        elif raw_upper in TESLA_CHARGE_STATES:
            # Device returns Tesla-style state directly (e.g. "Charging", "Stopped")
            charge_state = raw_state  # preserve original capitalisation
            is_plugged_in = raw_upper not in ("disconnected", "")
        else:
            charge_state = "Stopped"
            is_plugged_in = False

        self._current_amps = charging_amps

        self._last_state = VehicleState(
            vin=self._last_state.vin,
            name=self._last_state.name or "Tesla (BLE)",
            state="online",  # BLE device is always online if reachable
            battery_level=battery_level,
            charge_state=charge_state,
            charging_amps=charging_amps,
            charge_amps_request=self._last_state.charge_amps_request,
            charge_limit_soc=charge_limit_soc,
            charger_voltage=charger_voltage,
            charger_power=charger_power,
            time_to_full=time_to_full,
            is_plugged_in=is_plugged_in,
        )
        return self._last_state

    async def start_charging(self) -> bool:
        path = self._entity_map["charging_switch"] + "/turn_on"
        ok = await self._post(path)
        if ok:
            elog("BLE: charge started", SUCCESS, "tesla")
        else:
            elog("BLE: charge start failed", ERROR, "tesla")
        return ok

    async def stop_charging(self) -> bool:
        path = self._entity_map["charging_switch"] + "/turn_off"
        ok = await self._post(path)
        if ok:
            elog("BLE: charge stopped", SUCCESS, "tesla")
        else:
            elog("BLE: charge stop failed", ERROR, "tesla")
        return ok

    async def set_charging_amps(self, amps: int) -> bool:
        """Set charging amps via ESPHome number entity.

        Tesla firmware quirk: stepping below 5A from above (or vice versa)
        requires an intermediate step through 5A with a short pause.
        """
        path = self._entity_map["charging_amps"] + "/set"
        current = self._current_amps

        needs_step = (current > 5 and amps < 5) or (current < 5 and amps > 5)
        if needs_step and current != 5:
            logger.info(f"BLE: stepping through 5A ({current}A → 5A → {amps}A)")
            if not await self._post(path, params={"value": 5}):
                return False
            await asyncio.sleep(0.5)

        ok = await self._post(path, params={"value": amps})
        if ok:
            self._current_amps = amps
            elog(f"BLE: charging amps set to {amps}A", INFO, "tesla")
        else:
            elog(f"BLE: set amps to {amps}A failed", ERROR, "tesla")
        return ok

    async def wake_up(self) -> bool:
        path = self._entity_map["wake_button"] + "/press"
        ok = await self._post(path)
        if ok:
            elog("BLE: wake command sent", INFO, "tesla")
        return ok

    async def wake_and_wait(self, max_wait: int = 30) -> bool:
        """Wake via BLE and verify by polling get_vehicle_data."""
        ok = await self.wake_up()
        if not ok:
            return False
        # BLE is direct — just verify we can still read data
        for _ in range(3):
            await asyncio.sleep(2)
            state = await self.get_vehicle_data()
            if self._reachable:
                elog("BLE: vehicle responsive after wake", SUCCESS, "tesla")
                return True
        return self._reachable

    async def set_charge_limit(self, percent: int) -> bool:
        path = self._entity_map["charge_limit"] + "/set"
        ok = await self._post(path, params={"value": percent})
        if ok:
            elog(f"BLE: charge limit set to {percent}%", INFO, "tesla")
        return ok

    async def get_full_vehicle_data(self) -> dict | None:
        """Fetch all vehicle data and return in Fleet-API-compatible format."""
        keys = [
            "battery_level", "charging_current", "charging_state", "charge_limit_soc",
            "charger_voltage", "charger_power", "time_to_full", "charging_rate",
            "energy_added", "outside_temp", "range", "tpms_fl", "tpms_fr",
            "tpms_rl", "tpms_rr", "asleep", "parking_brake", "charger_plugged",
            "climate", "sentry_mode", "heated_steering", "charge_port_door",
            "charge_port_latch", "doors_lock", "trunk", "frunk", "windows",
        ]

        results = await asyncio.gather(
            *[self._get(k) for k in keys],
            return_exceptions=True,
        )
        data = {k: (r if isinstance(r, dict) else None) for k, r in zip(keys, results)}

        if all(v is None for v in data.values()):
            return None

        def val(key, default=0):
            d = data.get(key)
            return d["value"] if d and "value" in d else default

        def state_str(key, default=""):
            d = data.get(key)
            return d["state"] if d and "state" in d else default

        def bool_val(key, default=False):
            d = data.get(key)
            return d["value"] if d and "value" in d else default

        # Charge state
        raw_state = state_str("charging_state", "").strip()
        raw_upper = raw_state.upper()
        if raw_upper in IEC_TO_CHARGE_STATE:
            charging_state = IEC_TO_CHARGE_STATE[raw_upper]
        elif raw_upper in TESLA_CHARGE_STATES:
            charging_state = raw_state
        else:
            charging_state = "Stopped"

        # Climate entity has different structure
        climate = data.get("climate") or {}
        is_climate_on = climate.get("mode", "OFF") != "OFF"
        inside_temp = _safe_float(climate.get("current_temperature"))
        target_temp = _safe_float(climate.get("target_temperature"))

        # Covers/locks
        cp_door = data.get("charge_port_door") or {}
        cp_latch = data.get("charge_port_latch") or {}
        doors = data.get("doors_lock") or {}
        trunk_d = data.get("trunk") or {}
        frunk_d = data.get("frunk") or {}
        windows_d = data.get("windows") or {}
        sentry_d = data.get("sentry_mode") or {}
        steering_d = data.get("heated_steering") or {}

        trunk_open = trunk_d.get("state", "CLOSED") == "OPEN"
        frunk_open = frunk_d.get("state", "CLOSED") == "OPEN"
        windows_open = windows_d.get("state", "CLOSED") == "OPEN"

        return {
            "state": "asleep" if bool_val("asleep") else "online",
            "charge_state": {
                "battery_level": int(val("battery_level", 0)),
                "charge_limit_soc": int(float(val("charge_limit_soc", 80))),
                "charging_state": charging_state,
                "charge_rate": float(val("charging_rate", 0)),
                "charger_power": float(val("charger_power", 0)),
                "time_to_full_charge": float(val("time_to_full", 0)) / 60.0,
                "battery_range": float(val("range", 0)),
                "charge_port_door_open": cp_door.get("state", "CLOSED") == "OPEN",
                "charge_port_latch": "Engaged" if cp_latch.get("state") == "LOCKED" else "",
                "charger_actual_current": int(val("charging_current", 0)),
                "charger_voltage": int(val("charger_voltage", 0)),
                "charge_energy_added": float(val("energy_added", 0)),
            },
            "climate_state": {
                "outside_temp": _safe_float(val("outside_temp")) if data.get("outside_temp") else None,
                "inside_temp": inside_temp,
                "driver_temp_setting": target_temp,
                "is_climate_on": is_climate_on,
                "fan_status": None,
                "steering_wheel_heater": steering_d.get("value", False),
            },
            "vehicle_state": {
                "tpms_pressure_fl": float(val("tpms_fl", 0)) * BAR_TO_PSI if data.get("tpms_fl") else None,
                "tpms_pressure_fr": float(val("tpms_fr", 0)) * BAR_TO_PSI if data.get("tpms_fr") else None,
                "tpms_pressure_rl": float(val("tpms_rl", 0)) * BAR_TO_PSI if data.get("tpms_rl") else None,
                "tpms_pressure_rr": float(val("tpms_rr", 0)) * BAR_TO_PSI if data.get("tpms_rr") else None,
                "sentry_mode": sentry_d.get("value", False),
                "locked": doors.get("state", "LOCKED") == "LOCKED",
                "ft": 1 if frunk_open else 0,
                "rt": 1 if trunk_open else 0,
                "fd_window": 1 if windows_open else 0,
                "fp_window": 1 if windows_open else 0,
                "rd_window": 1 if windows_open else 0,
                "rp_window": 1 if windows_open else 0,
            },
            "drive_state": {},
            "vehicle_config": {},
            "gui_settings": {},
        }

    # --- BLE commands ---

    async def charge_port_door_open(self) -> bool:
        ok = await self._post(self._entity_map["charge_port_door"] + "/open")
        if ok:
            elog("BLE: charge port opened", INFO, "tesla")
        return ok

    async def charge_port_door_close(self) -> bool:
        ok = await self._post(self._entity_map["charge_port_door"] + "/close")
        if ok:
            elog("BLE: charge port closed", INFO, "tesla")
        return ok

    async def door_lock(self) -> bool:
        ok = await self._post(self._entity_map["doors_lock"] + "/lock")
        if ok:
            elog("BLE: doors locked", INFO, "tesla")
        return ok

    async def door_unlock(self) -> bool:
        ok = await self._post(self._entity_map["doors_lock"] + "/unlock")
        if ok:
            elog("BLE: doors unlocked", INFO, "tesla")
        return ok

    async def climate_start(self) -> bool:
        ok = await self._post(self._entity_map["climate"] + "/set", params={"mode": "auto"})
        if ok:
            elog("BLE: climate started", INFO, "tesla")
        return ok

    async def climate_stop(self) -> bool:
        ok = await self._post(self._entity_map["climate"] + "/set", params={"mode": "off"})
        if ok:
            elog("BLE: climate stopped", INFO, "tesla")
        return ok

    async def set_temps(self, driver: float, passenger: float) -> bool:
        ok = await self._post(
            self._entity_map["climate"] + "/set",
            params={"target_temperature": driver},
        )
        if ok:
            elog(f"BLE: climate temp set to {driver}°C", INFO, "tesla")
        return ok

    async def actuate_trunk(self, which: str) -> bool:
        if which == "front":
            ok = await self._post(self._entity_map["frunk"] + "/open")
            if ok:
                elog("BLE: frunk opened", INFO, "tesla")
        else:
            ok = await self._post(self._entity_map["trunk"] + "/toggle")
            if ok:
                elog("BLE: trunk toggled", INFO, "tesla")
        return ok

    async def flash_lights(self) -> bool:
        ok = await self._post(self._entity_map["flash_lights"] + "/press")
        if ok:
            elog("BLE: lights flashed", INFO, "tesla")
        return ok

    async def honk_horn(self) -> bool:
        ok = await self._post(self._entity_map["sound_horn"] + "/press")
        if ok:
            elog("BLE: horn honked", INFO, "tesla")
        return ok

    async def set_sentry_mode(self, on: bool) -> bool:
        action = "turn_on" if on else "turn_off"
        ok = await self._post(self._entity_map["sentry_mode"] + "/" + action)
        if ok:
            elog(f"BLE: sentry mode {'on' if on else 'off'}", INFO, "tesla")
        return ok

    async def window_control(self, command: str) -> bool:
        action = "open" if command == "vent" else "close"
        ok = await self._post(self._entity_map["windows"] + "/" + action)
        if ok:
            elog(f"BLE: windows {command}", INFO, "tesla")
        return ok

    async def set_steering_wheel_heater(self, on: bool) -> bool:
        action = "turn_on" if on else "turn_off"
        ok = await self._post(self._entity_map["heated_steering"] + "/" + action)
        if ok:
            elog(f"BLE: steering heater {'on' if on else 'off'}", INFO, "tesla")
        return ok

    @property
    def last_state(self) -> VehicleState:
        return self._last_state

    @property
    def key_revoked(self) -> bool:
        return False  # BLE has no IV counter / key revocation issue

    def clear_key_revoked(self):
        pass  # no-op for BLE

    @property
    def supports_multi_command(self) -> bool:
        return True  # no proxy recreation needed — both amps + start can run same tick

    @property
    def reachable(self) -> bool:
        return self._reachable


def _safe_float(v, default=None):
    """Convert to float, returning default on failure."""
    try:
        return float(v)
    except (ValueError, TypeError):
        return default
