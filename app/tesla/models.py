from dataclasses import asdict, dataclass


@dataclass
class VehicleState:
    vin: str = ""
    name: str = ""
    state: str = "unknown"  # online, asleep, offline
    battery_level: int = 0
    charge_state: str = "unknown"  # Charging, Stopped, Disconnected, Complete
    charging_amps: int = 0
    charge_amps_request: int = 0
    charge_limit_soc: int = 80
    charger_voltage: int = 0
    charger_power: float = 0.0
    time_to_full: float = 0.0
    is_plugged_in: bool = False


def vehicle_state_to_dict(state: VehicleState) -> dict:
    return asdict(state)
