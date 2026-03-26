import asyncio
import logging
import time
from datetime import datetime

from app.charger.algorithm import (
    ChargerSettings,
    ChargingAction,
    ChargingAlgorithm,
    ChargingMode,
    SystemState,
)
from app.charger.lux_model import lux_pv_model
from app.charger.scheduler import schedule_manager
from app.database import SessionLocal
from app.event_log import log as elog, INFO, WARN, ERROR, SUCCESS
from app.ha.client import ha_client
from app.models import ChargingSession, Metric, Setting
from app.mqtt.client import mqtt_client
from app.tesla.manager import transport_manager

logger = logging.getLogger(__name__)

# Runtime state
_current_mode: ChargingMode = ChargingMode.SOLAR_ONLY
_last_amps_sent: int = -1
_current_session: ChargingSession | None = None
_running: bool = False
_algorithm: ChargingAlgorithm | None = None
_last_tesla_poll: float = 0
_last_logged_action: str = ""
_last_ev_charge_state: str = ""
_last_wake_check: float = 0
_ever_polled_successfully: bool = False
_pending_charge_start: bool = False  # set_amps succeeded, need to send charge_start next tick
_pending_start_amps: int = 0
WAKE_CHECK_INTERVAL = 900  # 15 minutes — how often to wake car to check plug status


def get_mode() -> ChargingMode:
    return _current_mode


def set_mode(mode: ChargingMode):
    global _current_mode
    _current_mode = mode
    logger.info(f"Charging mode set to {mode.value}")
    elog(f"Mode changed to {mode.value.upper()}", INFO, "system")


def _load_settings() -> ChargerSettings:
    db = SessionLocal()
    try:
        cs = ChargerSettings()
        rows = db.query(Setting).all()
        setting_map = {r.key: r.value for r in rows}
        for field_name in vars(cs):
            if field_name in setting_map:
                val = setting_map[field_name]
                current = getattr(cs, field_name)
                if isinstance(current, int):
                    setattr(cs, field_name, int(float(val)))
                elif isinstance(current, float):
                    setattr(cs, field_name, float(val))
        return cs
    finally:
        db.close()


async def _build_system_state() -> SystemState:
    ev = transport_manager.active.last_state
    solar_lux = await ha_client.get_solar_lux() if ha_client.configured else None
    predicted_max_pv = lux_pv_model.predict_max_pv(solar_lux) if solar_lux else None
    return SystemState(
        pv_power=mqtt_client.get_int("inverter_1:pv_power"),
        load_power=mqtt_client.get_int("inverter_1:load_power"),
        battery_power=mqtt_client.get_int("total:battery_power"),
        battery_soc=mqtt_client.get_int("total:battery_state_of_charge"),
        grid_power_ct=mqtt_client.get_int("inverter_1:grid_power_ct"),
        ev_charging_amps=ev.charging_amps,
        ev_soc=ev.battery_level,
        ev_plugged_in=ev.is_plugged_in,
        ev_charge_state=ev.charge_state,
        solar_lux=solar_lux,
        predicted_max_pv=predicted_max_pv,
    )


def _record_metric(state: SystemState, ev_amps: float):
    db = SessionLocal()
    try:
        m = Metric(
            pv_power=state.pv_power,
            battery_power=state.battery_power,
            battery_soc=state.battery_soc,
            grid_power=state.grid_power_ct,
            load_power=state.load_power,
            ev_charging_amps=ev_amps,
            ev_soc=state.ev_soc,
            solar_lux=state.solar_lux,
        )
        db.add(m)
        db.commit()
    finally:
        db.close()


async def _control_loop_tick():
    global _last_amps_sent, _current_session, _current_mode, _algorithm, _last_tesla_poll, _last_logged_action, _last_ev_charge_state, _last_wake_check, _ever_polled_successfully, _pending_charge_start, _pending_start_amps

    charger_settings = _load_settings()
    lux_pv_model.refresh_if_needed()

    # Reuse algorithm instance to preserve state (e.g. battery_low_lockout)
    if _algorithm is None:
        _algorithm = ChargingAlgorithm(charger_settings)
    else:
        _algorithm.settings = charger_settings

    # Rate-limited Tesla polling
    now = time.monotonic()
    tesla = transport_manager.active
    if now - _last_tesla_poll >= charger_settings.tesla_poll_interval_s:
        try:
            await tesla.get_vehicle_data()
            _last_tesla_poll = now
            ev_after = tesla.last_state
            if ev_after.state == "online" and ev_after.battery_level > 0:
                _ever_polled_successfully = True
        except Exception as e:
            logger.warning(f"Tesla poll failed: {e}")

    state = await _build_system_state()
    mode = _current_mode

    # Wake-to-check: if car is asleep and there's enough solar surplus,
    # wake the car to refresh its plug-in status.
    # Option B: if last known state was plugged in, wake immediately (it's likely still plugged in)
    # Option A fallback: if we never got real data, wake periodically to discover plug state
    if (state.ev_charge_state in ("unknown", "asleep") or tesla.last_state.state == "asleep") \
            and mode in (ChargingMode.SOLAR_ONLY, ChargingMode.SCHEDULE) \
            and not tesla.key_revoked:
        # Calculate rough surplus to see if waking is worthwhile
        rough_surplus = state.pv_power - state.load_power - charger_settings.battery_protection_buffer_w
        min_charge_w = charger_settings.min_charge_amps * charger_settings.charger_voltage * charger_settings.charger_phases
        has_surplus = rough_surplus >= min_charge_w

        should_wake = False
        if has_surplus and tesla.last_state.is_plugged_in and (now - _last_wake_check >= WAKE_CHECK_INTERVAL):
            # Option B: car was plugged in before sleeping — wake to start charging
            should_wake = True
            wake_reason = "plugged in before sleep + solar surplus available"
        elif has_surplus and not _ever_polled_successfully and (now - _last_wake_check >= WAKE_CHECK_INTERVAL):
            # Option A fallback: never got real data, check periodically
            should_wake = True
            wake_reason = "unknown plug state + solar surplus — periodic check"
        elif has_surplus and not tesla.last_state.is_plugged_in and (now - _last_wake_check >= WAKE_CHECK_INTERVAL):
            # Option A fallback: was not plugged in last we knew, but check periodically
            should_wake = True
            wake_reason = "solar surplus — periodic plug check"

        if should_wake:
            _last_wake_check = now
            logger.info(f"Waking car to check state: {wake_reason}")
            elog(f"Waking car: {wake_reason}", INFO, "tesla")
            if await tesla.wake_and_wait():
                await tesla.get_vehicle_data()
                _last_tesla_poll = now
                _ever_polled_successfully = True
                state = await _build_system_state()  # rebuild with fresh data

    # Detect external charging state changes (started/stopped from Tesla app or car)
    if _last_ev_charge_state and state.ev_charge_state != _last_ev_charge_state:
        if state.ev_charge_state == "Charging" and _last_ev_charge_state != "Charging":
            elog(f"Charging started externally ({state.ev_charging_amps}A)", INFO, "tesla")
            _last_amps_sent = state.ev_charging_amps  # sync with actual state
        elif _last_ev_charge_state == "Charging" and state.ev_charge_state != "Charging":
            elog(f"Charging stopped externally (was {_last_amps_sent}A)", INFO, "tesla")
            _last_amps_sent = 0
    _last_ev_charge_state = state.ev_charge_state

    # Sync _last_amps_sent if car is charging but we didn't initiate it
    if state.ev_charge_state == "Charging" and state.ev_charging_amps > 0 and _last_amps_sent <= 0:
        _last_amps_sent = state.ev_charging_amps

    # Check for active schedule
    active_schedule = schedule_manager.get_active_schedule()
    if active_schedule and mode == ChargingMode.SOLAR_ONLY:
        mode = ChargingMode.SCHEDULE

    decision = _algorithm.decide(state, mode)

    # Schedule override: if schedule requires grid charging
    if active_schedule and active_schedule.allow_grid and mode == ChargingMode.SCHEDULE:
        required_amps = schedule_manager.calculate_required_amps(
            active_schedule,
            state.ev_soc,
            charger_settings.charger_voltage,
            charger_settings.charger_phases,
        )
        if required_amps and required_amps > decision.target_amps:
            grid_amps = min(required_amps, active_schedule.max_grid_amps, charger_settings.max_charge_amps)
            decision.target_amps = grid_amps
            decision.action = ChargingAction.START if state.ev_charging_amps == 0 else ChargingAction.INCREASE
            decision.reason = (f"Schedule '{active_schedule.name}': need {required_amps}A "
                               f"to reach {active_schedule.target_soc}% by {active_schedule.end_time}")

    logger.info(f"Decision: {decision.action.value} → {decision.target_amps}A | {decision.reason}")

    # Handle pending charge_start from previous tick (START is split into 2 ticks:
    # tick 1 = set_amps, tick 2 = charge_start — one command per proxy session to
    # prevent the tesla-http-proxy IV counter corruption bug.
    # BLE skips this via supports_multi_command=True)
    if _pending_charge_start:
        _pending_charge_start = False
        if decision.action != ChargingAction.STOP:  # don't start if algorithm now says stop
            elog(f"Sending charge_start ({_pending_start_amps}A)", INFO, "tesla")
            ok = await tesla.start_charging()
            if ok:
                _start_session(state.ev_soc)
                elog(f"Charging started at {_pending_start_amps}A", SUCCESS, "tesla")
            else:
                elog("charge_start failed — will retry", WARN, "tesla")
        return  # only one command per tick

    # Execute decision (only send command if amps actually changed)
    if decision.target_amps != _last_amps_sent:
        # Only log on meaningful state changes, not repeated holds
        action_key = f"{decision.action.value}:{decision.target_amps}"
        if action_key != _last_logged_action:
            elog(f"Algorithm: {decision.reason}", INFO, "algorithm")
            _last_logged_action = action_key

        if decision.action == ChargingAction.STOP:
            elog("Sending stop charging command", INFO, "tesla")
            ok = await tesla.stop_charging()
            if ok:
                _last_amps_sent = 0
                _end_session(state.ev_soc)
                elog("Charging stopped", SUCCESS, "tesla")
            else:
                # Mark as sent anyway so we don't hammer BLE on every tick.
                # If the car is actually still charging, the next data poll will
                # show ev_charging_amps > 0 and re-sync _last_amps_sent, which
                # will trigger a fresh stop attempt after the next poll interval.
                _last_amps_sent = 0
                logger.warning("Stop command failed (BLE disconnected?) — will retry after next poll")
                elog("Stop command failed — will retry after next poll", WARN, "tesla")
        elif decision.action in (ChargingAction.START, ChargingAction.INCREASE, ChargingAction.DECREASE):
            elog(f"Setting charging to {decision.target_amps}A", INFO, "tesla")
            ok = await tesla.set_charging_amps(decision.target_amps)
            if ok:
                _last_amps_sent = decision.target_amps
                if decision.action == ChargingAction.START:
                    if tesla.supports_multi_command:
                        # BLE: send charge_start in the same tick
                        ok2 = await tesla.start_charging()
                        if ok2:
                            _start_session(state.ev_soc)
                            elog(f"Charging started at {decision.target_amps}A", SUCCESS, "tesla")
                        else:
                            elog("charge_start failed — will retry", WARN, "tesla")
                    else:
                        # Fleet API: queue charge_start for next tick (IV counter bug)
                        _pending_charge_start = True
                        _pending_start_amps = decision.target_amps
                        elog(f"Amps set to {decision.target_amps}A — charge_start queued for next tick", INFO, "tesla")
                elif decision.action == ChargingAction.INCREASE:
                    elog(f"Amps increased to {decision.target_amps}A", SUCCESS, "algorithm")
                elif decision.action == ChargingAction.DECREASE:
                    elog(f"Amps decreased to {decision.target_amps}A", WARN, "algorithm")
            else:
                logger.warning(f"Set amps command failed — will retry next tick")
                elog(f"Set amps command failed — will retry", WARN, "tesla")
        elif decision.action == ChargingAction.HOLD:
            # No command needed, just sync state to avoid re-entering this block
            _last_amps_sent = decision.target_amps

    # Record metrics every 30s (every 3rd tick at 10s interval)
    _record_metric(state, decision.target_amps)


def _start_session(start_soc: int):
    global _current_session
    if _current_session:
        return
    db = SessionLocal()
    try:
        _current_session = ChargingSession(started_at=datetime.utcnow(), start_soc=start_soc)
        db.add(_current_session)
        db.commit()
        db.refresh(_current_session)
    finally:
        db.close()


def _end_session(end_soc: int):
    global _current_session
    if not _current_session:
        return
    db = SessionLocal()
    try:
        session = db.query(ChargingSession).get(_current_session.id)
        if session:
            session.ended_at = datetime.utcnow()
            session.end_soc = end_soc
            db.commit()
        _current_session = None
    finally:
        db.close()


async def run_worker():
    global _running
    _running = True
    logger.info("Charging worker started (10s loop)")
    elog("Charging worker started", SUCCESS, "system")

    while _running:
        try:
            await _control_loop_tick()
        except Exception as e:
            logger.error(f"Control loop error: {e}", exc_info=True)
            elog(f"Control loop error: {e}", ERROR, "system")
        await asyncio.sleep(10)


def stop_worker():
    global _running
    _running = False
