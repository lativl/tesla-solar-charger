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
    ramp_up_delay_s: int = 120           # seconds to hold after increasing before next change
    ramp_down_delay_s: int = 60          # seconds to hold after decreasing before next change
    tesla_poll_interval_s: int = 300     # seconds between Tesla API polls
    speculative_start_soc: int = 80      # battery SoC above which speculative start is allowed
    speculative_min_pv_w: int = 500      # minimum PV production to attempt speculative start
    high_soc_threshold: int = 95         # battery SoC above which slight discharge is tolerated
    high_soc_discharge_allowance_w: int = 150  # allowed battery discharge (W) when SoC above threshold
    battery_charge_threshold_w: int = 200  # hold EV amps if battery charging below this W and SoC < high_soc_threshold
    lux_stop_threshold: int = 200        # below this lux, stop trying solar (overcast/dark)
    lux_conservative_threshold: int = 5000  # below this lux, reduce speculative aggressiveness
    lux_aggressive_threshold: int = 20000   # above this lux, full confidence in solar
    lux_model_curtailment_factor: float = 0.7  # fraction of predicted headroom added to surplus


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
    solar_lux: float | None = None  # from HA Ecowitt sensor, None = unavailable
    predicted_max_pv: int | None = None  # from lux model, None = insufficient data


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
        # Stabilization: hold after amps change to avoid oscillation
        self._stabilize_ticks_remaining: int = 0
        # Stop cooldown: block speculative start for N ticks after stopping
        self._stop_cooldown_ticks: int = 0

    def decide(self, state: SystemState, mode: ChargingMode) -> Decision:
        s = self.settings

        if self._stop_cooldown_ticks > 0:
            self._stop_cooldown_ticks -= 1

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

        # Solar surplus calculation:
        # house_load = what the house uses excluding EV
        # battery_available = power currently going INTO batteries (positive = charging)
        #   that could be redirected to EV if needed
        # This solves PV curtailment: when inverter throttles PV because batteries are
        # full and load is low, battery_available captures the "hidden" solar capacity.
        house_load = max(0, state.load_power - ev_draw_w)
        battery_available = max(0, state.battery_power)  # only count charging, not discharging
        solar_surplus = state.pv_power + battery_available - house_load - s.battery_protection_buffer_w

        # When battery is nearly full, tolerate slight discharge to keep EV charging stable
        if state.battery_soc >= s.high_soc_threshold:
            solar_surplus += s.high_soc_discharge_allowance_w

        # Lux model: add predicted curtailment headroom (conservative fraction)
        if state.predicted_max_pv is not None and s.lux_model_curtailment_factor > 0:
            curtailment_headroom = max(0, state.predicted_max_pv - state.pv_power)
            solar_surplus += int(curtailment_headroom * s.lux_model_curtailment_factor)

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

        # Lux-based solar confidence
        lux = state.solar_lux
        lux_info = f", lux {int(lux)}" if lux is not None else ""

        # Below minimum — check for speculative start or stop
        if target_amps < s.min_charge_amps:
            # Lux-based early exit: if lux is available and very low, don't bother
            # trying solar — it's overcast or dark, no point waking the car or speculating
            if lux is not None and lux < s.lux_stop_threshold:
                if state.ev_charging_amps > 0:
                    return Decision(ChargingAction.STOP, 0,
                                    f"Low light ({int(lux)} lux < {s.lux_stop_threshold}) — stopping",
                                    available_w)
                return Decision(ChargingAction.HOLD, 0,
                                f"Low light ({int(lux)} lux) — no solar expected",
                                available_w)

            # Speculative start: when battery SoC is high and there's PV production,
            # the inverter may be curtailing solar (MPPT throttling). Try starting at
            # min amps — if the inverter can ramp up PV, the next cycle will see more
            # surplus and keep charging. If not, the algorithm will stop next cycle.
            #
            # Lux modulates aggressiveness:
            #  - lux unavailable (None): use default thresholds
            #  - lux < conservative (5000): skip speculative start (cloudy)
            #  - lux >= conservative: allow speculative start
            #  - lux >= aggressive (20000): lower SoC requirement by 10% (bright sun)
            speculative_allowed = True
            speculative_soc = s.speculative_start_soc
            if lux is not None:
                if lux < s.lux_conservative_threshold:
                    speculative_allowed = False
                elif lux >= s.lux_aggressive_threshold:
                    speculative_soc = max(50, s.speculative_start_soc - 10)

            # Lux model can also trigger speculative start when predicted PV is high
            pv_condition = state.pv_power >= s.speculative_min_pv_w
            predicted_info = ""
            if not pv_condition and state.predicted_max_pv is not None:
                if state.predicted_max_pv >= s.speculative_min_pv_w * 2:
                    pv_condition = True
                    predicted_info = f", predicted PV {state.predicted_max_pv}W"

            if (speculative_allowed
                    and state.ev_charging_amps == 0
                    and self._stop_cooldown_ticks == 0
                    and state.battery_soc >= speculative_soc
                    and pv_condition
                    and state.grid_power_ct <= s.max_grid_import_w):
                return Decision(ChargingAction.START, s.min_charge_amps,
                                f"Speculative start at {s.min_charge_amps}A "
                                f"(battery {state.battery_soc}% ≥ {speculative_soc}%, "
                                f"PV {state.pv_power}W, surplus only {available_w}W{lux_info}{predicted_info})",
                                available_w)
            if state.ev_charging_amps > 0:
                self._stop_cooldown_ticks = max(1, s.ramp_down_delay_s // TICK_INTERVAL_S)
                return Decision(ChargingAction.STOP, 0,
                                f"Available {available_w}W ({target_amps}A) below minimum {s.min_charge_amps}A{lux_info}",
                                available_w)
            return Decision(ChargingAction.HOLD, 0,
                            f"Insufficient surplus: {available_w}W ({target_amps}A){lux_info}",
                            available_w)

        current_amps = state.ev_charging_amps

        # Not currently charging — start
        if current_amps == 0:
            start_amps = min(target_amps, s.min_charge_amps + s.ramp_up_step)
            self._stabilize_ticks_remaining = max(1, s.ramp_up_delay_s // TICK_INTERVAL_S)
            return Decision(ChargingAction.START, start_amps,
                            f"Starting at {start_amps}A (surplus {available_w}W)",
                            available_w)

        # Stabilization: after a recent amps change, hold to avoid oscillation
        # (emergency stop and stop-below-minimum bypass this)
        if self._stabilize_ticks_remaining > 0:
            self._stabilize_ticks_remaining -= 1
            return Decision(ChargingAction.HOLD, current_amps,
                            f"Holding at {current_amps}A (stabilizing {self._stabilize_ticks_remaining * TICK_INTERVAL_S}s left, surplus {available_w}W)",
                            available_w)

        # Battery weak-charge hold: keep amps steady when battery is just trickle-charging
        # and SoC hasn't reached the high-SoC threshold yet
        if (
            state.battery_soc < s.high_soc_threshold
            and 0 <= state.battery_power <= s.battery_charge_threshold_w
        ):
            return Decision(
                ChargingAction.HOLD,
                current_amps,
                f"Holding at {current_amps}A — battery charging weakly "
                f"({state.battery_power}W ≤ {s.battery_charge_threshold_w}W, "
                f"SoC {state.battery_soc}% < {s.high_soc_threshold}%)",
                available_w,
            )

        # Ramp up (gradual)
        if target_amps > current_amps:
            new_amps = min(current_amps + s.ramp_up_step, target_amps)
            self._stabilize_ticks_remaining = max(1, s.ramp_up_delay_s // TICK_INTERVAL_S)
            return Decision(ChargingAction.INCREASE, new_amps,
                            f"Ramping up {current_amps}A → {new_amps}A (surplus {available_w}W)",
                            available_w)

        # Ramp down (faster for safety)
        if target_amps < current_amps:
            new_amps = max(current_amps - s.ramp_down_step, target_amps)
            if new_amps < s.min_charge_amps:
                self._stop_cooldown_ticks = max(1, s.ramp_down_delay_s // TICK_INTERVAL_S)
                return Decision(ChargingAction.STOP, 0,
                                f"Ramping down to {new_amps}A below minimum — stopping",
                                available_w)
            self._stabilize_ticks_remaining = max(1, s.ramp_down_delay_s // TICK_INTERVAL_S)
            return Decision(ChargingAction.DECREASE, new_amps,
                            f"Ramping down {current_amps}A → {new_amps}A (surplus {available_w}W)",
                            available_w)

        # Hold
        return Decision(ChargingAction.HOLD, current_amps,
                        f"Holding at {current_amps}A (surplus {available_w}W)",
                        available_w)
