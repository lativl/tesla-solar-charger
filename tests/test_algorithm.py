import pytest
from app.charger.algorithm import (
    ChargerSettings,
    ChargingAction,
    ChargingAlgorithm,
    ChargingMode,
    SystemState,
)


def make_state(**kwargs) -> SystemState:
    defaults = {
        "pv_power": 3000,
        "load_power": 1000,
        "battery_power": 500,
        "battery_soc": 80,
        "grid_power_ct": 0,
        "ev_charging_amps": 0,
        "ev_soc": 50,
        "ev_plugged_in": True,
        "ev_charge_state": "Stopped",
    }
    defaults.update(kwargs)
    return SystemState(**defaults)


class TestChargingAlgorithm:
    def setup_method(self):
        self.settings = ChargerSettings()
        self.algo = ChargingAlgorithm(self.settings)

    def test_surplus_starts_charging(self):
        state = make_state(pv_power=4000, load_power=1000)
        decision = self.algo.decide(state, ChargingMode.SOLAR_ONLY)
        assert decision.action == ChargingAction.START
        assert decision.target_amps >= self.settings.min_charge_amps

    def test_no_surplus_holds_at_zero(self):
        state = make_state(pv_power=500, load_power=1000)
        decision = self.algo.decide(state, ChargingMode.SOLAR_ONLY)
        assert decision.target_amps == 0

    def test_low_battery_stops(self):
        state = make_state(battery_soc=20, ev_charging_amps=8)
        decision = self.algo.decide(state, ChargingMode.SOLAR_ONLY)
        assert decision.action == ChargingAction.STOP

    def test_battery_hysteresis(self):
        # First trigger low battery lockout
        state = make_state(battery_soc=20)
        self.algo.decide(state, ChargingMode.SOLAR_ONLY)

        # Not yet recovered
        state = make_state(battery_soc=28, pv_power=5000)
        decision = self.algo.decide(state, ChargingMode.SOLAR_ONLY)
        assert decision.action == ChargingAction.STOP

        # Recovered
        state = make_state(battery_soc=31, pv_power=5000)
        decision = self.algo.decide(state, ChargingMode.SOLAR_ONLY)
        assert decision.action != ChargingAction.STOP

    def test_emergency_grid_stops(self):
        state = make_state(grid_power_ct=600, ev_charging_amps=10)
        decision = self.algo.decide(state, ChargingMode.SOLAR_ONLY)
        assert decision.action == ChargingAction.STOP

    def test_paused_mode(self):
        state = make_state(pv_power=5000)
        decision = self.algo.decide(state, ChargingMode.PAUSED)
        assert decision.action == ChargingAction.STOP

    def test_ev_not_plugged_in(self):
        state = make_state(ev_plugged_in=False, pv_power=5000)
        decision = self.algo.decide(state, ChargingMode.SOLAR_ONLY)
        assert decision.action == ChargingAction.HOLD
        assert decision.target_amps == 0

    def test_ramp_up_gradual(self):
        state = make_state(pv_power=6000, load_power=1000, ev_charging_amps=8)
        decision = self.algo.decide(state, ChargingMode.SOLAR_ONLY)
        assert decision.target_amps <= 8 + self.settings.ramp_up_step

    def test_ramp_down_fast(self):
        state = make_state(pv_power=2500, load_power=1000, ev_charging_amps=12)
        decision = self.algo.decide(state, ChargingMode.SOLAR_ONLY)
        assert decision.target_amps < 12
