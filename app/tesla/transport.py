"""Abstract interface for Tesla vehicle communication.

Both FleetApiTransport (existing TeslaAPI) and BleTransport (ESPHome BLE)
implement this interface. The worker and API routes use the active transport
without caring which channel is underneath.
"""

from abc import ABC, abstractmethod

from app.tesla.models import VehicleState


class TeslaTransport(ABC):

    # --- Core methods (both transports must implement) ---

    @abstractmethod
    async def get_vehicle_data(self) -> VehicleState: ...

    @abstractmethod
    async def start_charging(self) -> bool: ...

    @abstractmethod
    async def stop_charging(self) -> bool: ...

    @abstractmethod
    async def set_charging_amps(self, amps: int) -> bool: ...

    @abstractmethod
    async def wake_up(self) -> bool: ...

    @abstractmethod
    async def wake_and_wait(self, max_wait: int = 30) -> bool: ...

    @property
    @abstractmethod
    def last_state(self) -> VehicleState: ...

    @property
    @abstractmethod
    def key_revoked(self) -> bool: ...

    @abstractmethod
    def clear_key_revoked(self): ...

    @property
    def supports_multi_command(self) -> bool:
        """True if transport can send set_amps + charge_start in the same tick.
        Fleet API needs a two-tick split due to proxy recreation limits.
        BLE has no such constraint.
        """
        return False

    @property
    def reachable(self) -> bool:
        """Whether the transport is currently reachable."""
        return True

    # --- Extended commands (optional — Fleet API implements, BLE skips) ---

    async def get_full_vehicle_data(self) -> dict | None:
        return None

    async def set_charge_limit(self, percent: int) -> bool:
        raise NotImplementedError

    async def charge_port_door_open(self) -> bool:
        raise NotImplementedError

    async def charge_port_door_close(self) -> bool:
        raise NotImplementedError

    async def door_lock(self) -> bool:
        raise NotImplementedError

    async def door_unlock(self) -> bool:
        raise NotImplementedError

    async def climate_start(self) -> bool:
        raise NotImplementedError

    async def climate_stop(self) -> bool:
        raise NotImplementedError

    async def set_temps(self, driver: float, passenger: float) -> bool:
        raise NotImplementedError

    async def actuate_trunk(self, which: str) -> bool:
        raise NotImplementedError

    async def flash_lights(self) -> bool:
        raise NotImplementedError

    async def honk_horn(self) -> bool:
        raise NotImplementedError

    async def set_sentry_mode(self, on: bool) -> bool:
        raise NotImplementedError

    async def window_control(self, command: str) -> bool:
        raise NotImplementedError

    async def set_seat_heater(self, seat: int, level: int) -> bool:
        raise NotImplementedError

    async def set_steering_wheel_heater(self, on: bool) -> bool:
        raise NotImplementedError

    async def set_preconditioning_max(self, on: bool) -> bool:
        raise NotImplementedError
