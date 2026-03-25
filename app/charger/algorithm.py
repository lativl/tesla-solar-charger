import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class ChargingMode(str, Enum):
    SOLAR_ONLY = "solar_only"
    SCHEDULE = "schedule"
    MANUAL = "manual"
    PAUSED = "paused"


class ChargingAction(str, Enum):
    HOLD = "hold"
    INCREASE = "increase"
    DECREASE = "decrease"
    START = "start"
    STOP = "stop"


@dataclass
class ChargerSettings:
    min_battery_soc: int = 25
    battery_soc_hysteresis: int = 5
    max_grid_import_w: int = 200
    emergency_grid_limit_w: int = 500
    min_charge_amps: int = 6
    max_charge_amps: int = 16
    ramp_up_step: int = 2
    ramp_down_step: int = 4
    charger_phases: int = 1
    charger_voltage: int = 230
    battery_protection_buffer_w: int = 200
    battery_discharge_threshold_w: int = 500
    battery_penalty_delay_s: int = 30     # seconds above threshold before penalty applies
    battery_recovery_delay_s: int = 60    # seconds below threshold before penalty clears
    grid_penalty_delay_s: int = 30        # seconds above threshold before penalty applies
    grid_recovery_delay_s: int = 60       # seconds below threshold before penalty clears


@dataclass
class SystemState:
    pv_power: int = 0
    load_power: int = 0
    battery_power: int = 0  # negative = discharging
    battery_soc: int = 0
    grid_power_ct: int = 0  # positive = importing
    ev_charging_amps: int = 0
    ev_soc: int = 0
    ev_plugged_in: bool = False
    ev_charge_state: str = "unknown"


@dataclass
class Decision:
    action: ChargingAction
    target_amps: int
    reason: str
    available_power_w: int = 0


TICK_INTERVAL_S = 10  # control loop interval


class ChargingAlgorithm:
    def __init__(self, settings: ChargerSettings | None = None):
        self.settings = settings or ChargerSettings()
        self._battery_low_lockout = False
        # Time-based penalty tracking (in consecutive ticks)
        self._battery_exceed_ticks: int = 0
        self._battery_recover_ticks: int = 0
        self._battery_penalty_active: bool = False
        self._grid_exceed_ticks: int = 0
        self._grid_recover_ticks: int = 0
        self._grid_penalty_active: bool = False

    def decide(self, state: SystemState, mode: ChargingMode) -> Decision:
        s = self.settings

        if mode == ChargingMode.PAUSED:
            return Decision(ChargingAction.STOP, 0, "Charging paused by user")

        if mode == ChargingMode.MANUAL:
            return Decision(ChargingAction.HOLD, state.ev_charging_amps, "Manual mode")

        if not state.ev_plugged_in:
            return Decision(ChargingAction.HOLD, 0, "EV not plugged in")

        # Battery protection with hysteresis
        if state.battery_soc < s.min_battery_soc:
            self._battery_low_lockout = True
            return Decision(ChargingAction.STOP, 0,
                            f"Battery SoC {state.battery_soc}% below minimum {s.min_battery_soc}%")

        if self._battery_low_lockout:
            if state.battery_soc >= s.min_battery_soc + s.battery_soc_hysteresis:
                self._battery_low_lockout = False
                logger.info("Battery SoC recovered, clearing lockout")
            else:
                return Decision(ChargingAction.STOP, 0,
                                f"Battery recovering: {state.battery_soc}% "
                                f"(need {s.min_battery_soc + s.battery_soc_hysteresis}%)")

        # Emergency grid protection
        if state.grid_power_ct > s.emergency_grid_limit_w:
            return Decision(ChargingAction.STOP, 0,
                            f"Emergency: grid import {state.grid_power_ct}W exceeds {s.emergency_grid_limit_w}W")

        # Calculate current EV draw in watts
        ev_draw_w = state.ev_charging_amps * s.charger_voltage * s.charger_phases

        # Solar surplus = PV - (house load minus what EV is already using) - buffer
        house_load = max(0, state.load_power - ev_draw_w)
        solar_surplus = state.pv_power - house_load - s.battery_protection_buffer_w

        # Battery discharge penalty with time-based filter
        battery_exceeding = state.battery_power < -s.battery_discharge_threshold_w
        if battery_exceeding:
            self._battery_exceed_ticks += 1
            self._battery_recover_ticks = 0
            ticks_needed = max(1, s.battery_penalty_delay_s // TICK_INTERVAL_S)
            if self._battery_exceed_ticks >= ticks_needed:
                self._battery_penalty_active = True
        else:
            self._battery_exceed_ticks = 0
            if self._battery_penalty_active:
                self._battery_recover_ticks += 1
                ticks_needed = max(1, s.battery_recovery_delay_s // TICK_INTERVAL_S)
                if self._battery_recover_ticks >= ticks_needed:
                    self._battery_penalty_active = False
                    self._battery_recover_ticks = 0

        if self._battery_penalty_active and battery_exceeding:
            discharge_penalty = abs(state.battery_power) - s.battery_discharge_threshold_w
            solar_surplus -= discharge_penalty

        # Grid import penalty with time-based filter
        grid_exceeding = state.grid_power_ct > s.max_grid_import_w
        if grid_exceeding:
            self._grid_exceed_ticks += 1
            self._grid_recover_ticks = 0
            ticks_needed = max(1, s.grid_penalty_delay_s // TICK_INTERVAL_S)
            if self._grid_exceed_ticks >= ticks_needed:
                self._grid_penalty_active = True
        else:
            self._grid_exceed_ticks = 0
            if self._grid_penalty_active:
                self._grid_recover_ticks += 1
                ticks_needed = max(1, s.grid_recovery_delay_s // TICK_INTERVAL_S)
                if self._grid_recover_ticks >= ticks_needed:
                    self._grid_penalty_active = False
                    self._grid_recover_ticks = 0

        if self._grid_penalty_active and grid_exceeding:
            grid_penalty = state.grid_power_ct - s.max_grid_import_w
            solar_surplus -= grid_penalty

        available_w = max(0, solar_surplus)

        # Convert to amps
        target_amps = int(available_w / (s.charger_voltage * s.charger_phases))
        target_amps = min(target_amps, s.max_charge_amps)

        # Below minimum — stop
        if target_amps < s.min_charge_amps:
            if state.ev_charging_amps > 0:
                return Decision(ChargingAction.STOP, 0,
                                f"Available {available_w}W ({target_amps}A) below minimum {s.min_charge_amps}A",
                                available_w)
            return Decision(ChargingAction.HOLD, 0,
                            f"Insufficient surplus: {available_w}W ({target_amps}A)",
                            available_w)

        current_amps = state.ev_charging_amps

        # Not currently charging — start
        if current_amps == 0:
            start_amps = min(target_amps, s.min_charge_amps + s.ramp_up_step)
            return Decision(ChargingAction.START, start_amps,
                            f"Starting at {start_amps}A (surplus {available_w}W)",
                            available_w)

        # Ramp up (gradual)
        if target_amps > current_amps:
            new_amps = min(current_amps + s.ramp_up_step, target_amps)
            return Decision(ChargingAction.INCREASE, new_amps,
                            f"Ramping up {current_amps}A → {new_amps}A (surplus {available_w}W)",
                            available_w)

        # Ramp down (faster for safety)
        if target_amps < current_amps:
            new_amps = max(current_amps - s.ramp_down_step, target_amps)
            if new_amps < s.min_charge_amps:
                return Decision(ChargingAction.STOP, 0,
                                f"Ramping down to {new_amps}A below minimum — stopping",
                                available_w)
            return Decision(ChargingAction.DECREASE, new_amps,
                            f"Ramping down {current_amps}A → {new_amps}A (surplus {available_w}W)",
                            available_w)

        # Hold
        return Decision(ChargingAction.HOLD, current_amps,
                        f"Holding at {current_amps}A (surplus {available_w}W)",
                        available_w)
