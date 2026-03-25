from dataclasses import asdict

from app.tesla.api import VehicleState


def vehicle_state_to_dict(state: VehicleState) -> dict:
    return asdict(state)
