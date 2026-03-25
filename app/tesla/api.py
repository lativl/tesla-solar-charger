import asyncio
import logging
import time
from dataclasses import dataclass

import httpx

from app.config import settings
from app.event_log import log as elog, INFO, WARN, ERROR, SUCCESS
from app.tesla.auth import get_valid_token, refresh_access_token

logger = logging.getLogger(__name__)

FLEET_API_BASE = settings.TESLA_AUDIENCE
PROXY_BASE = settings.TESLA_PROXY_URL


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


class TeslaAPI:
    def __init__(self):
        self._vehicle_id: str | None = None
        self._vin: str | None = None
        self._last_state: VehicleState = VehicleState()
        self._last_known_online: bool = False
        self._last_command_success: float = 0  # monotonic time of last successful proxy command
        self._last_proxy_recreate: float = 0   # monotonic time of last proxy recreation
        self._consecutive_proxy_failures: int = 0
        self._key_revoked: bool = False  # set True after repeated fresh-proxy failures

    async def _get_token(self) -> str | None:
        token = get_valid_token()
        if not token:
            token = await refresh_access_token()
        return token

    async def _request(self, method: str, path: str, use_proxy: bool = False, **kwargs) -> dict | None:
        """Send request to Tesla API.
        use_proxy=True routes through tesla-http-proxy (for commands that need signing).
        """
        token = await self._get_token()
        if not token:
            logger.warning("No valid Tesla token available")
            return None

        base = PROXY_BASE if use_proxy else FLEET_API_BASE
        url = f"{base}{path}"

        # Proxy uses self-signed cert — skip verification
        verify = not use_proxy

        async with httpx.AsyncClient(verify=verify) as client:
            resp = await client.request(
                method,
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
                **kwargs,
            )
            if resp.status_code == 401:
                token = await refresh_access_token()
                if not token:
                    return None
                resp = await client.request(
                    method,
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=30,
                    **kwargs,
                )
            resp.raise_for_status()
            return resp.json()

    async def get_vehicles(self) -> list[dict]:
        data = await self._request("GET", "/api/1/vehicles")
        if data and "response" in data:
            return data["response"]
        return []

    async def _ensure_vehicle_info(self) -> bool:
        """Ensure we have both vehicle_id (for data reads) and VIN (for proxy commands)."""
        if self._vehicle_id and self._vin:
            return True
        vehicles = await self.get_vehicles()
        if vehicles:
            self._vehicle_id = str(vehicles[0]["id"])
            self._vin = vehicles[0].get("vin", "")
            return True
        return False

    async def wake_up(self) -> bool:
        if not await self._ensure_vehicle_info():
            return False
        try:
            await self._request("POST", f"/api/1/vehicles/{self._vehicle_id}/wake_up")
            return True
        except Exception as e:
            logger.error(f"Wake up failed: {e}")
            return False

    async def get_vehicle_data(self) -> VehicleState:
        if not await self._ensure_vehicle_info():
            return self._last_state

        try:
            data = await self._request(
                "GET",
                f"/api/1/vehicles/{self._vehicle_id}/vehicle_data",
                params={"endpoints": "charge_state;vehicle_state"},
            )
            if not data or "response" not in data:
                return self._last_state

            r = data["response"]
            cs = r.get("charge_state", {})

            self._last_state = VehicleState(
                vin=r.get("vin", ""),
                name=r.get("vehicle_state", {}).get("vehicle_name", "Tesla"),
                state=r.get("state", "unknown"),
                battery_level=cs.get("battery_level", 0),
                charge_state=cs.get("charging_state", "unknown"),
                charging_amps=cs.get("charger_actual_current", 0),
                charge_amps_request=cs.get("charge_amps", 0),
                charge_limit_soc=cs.get("charge_limit_soc", 80),
                charger_voltage=cs.get("charger_voltage", 0),
                charger_power=cs.get("charger_power", 0.0),
                time_to_full=cs.get("time_to_full_charge", 0.0),
                is_plugged_in=cs.get("charge_port_door_open", False)
                and cs.get("charging_state", "") != "Disconnected",
            )
            if not self._vin:
                self._vin = self._last_state.vin
            return self._last_state
        except httpx.HTTPStatusError as e:
            # 408 = vehicle asleep/unavailable — mark state and proxy as dirty
            if e.response.status_code in (408, 503):
                logger.info(f"Vehicle data request returned {e.response.status_code} — vehicle likely asleep")
                self._last_state.state = "asleep"
                self._last_known_online = False
                self._proxy_session_dirty = True
            else:
                logger.error(f"Failed to get vehicle data: {e}")
            return self._last_state
        except Exception as e:
            logger.error(f"Failed to get vehicle data: {e}")
            return self._last_state

    async def get_full_vehicle_data(self) -> dict | None:
        """Fetch all available vehicle data categories."""
        if not await self._ensure_vehicle_info():
            return None
        try:
            data = await self._request(
                "GET",
                f"/api/1/vehicles/{self._vehicle_id}/vehicle_data",
                params={"endpoints": "charge_state;vehicle_state;climate_state;drive_state;vehicle_config;gui_settings"},
            )
            if data and "response" in data:
                return data["response"]
            return None
        except Exception as e:
            logger.error(f"Failed to get full vehicle data: {e}")
            return None

    async def set_charging_amps(self, amps: int) -> bool:
        ok = await self._send_command("set_charging_amps", json={"charging_amps": amps})
        if ok:
            logger.info(f"Set charging amps to {amps}A")
        return ok

    async def set_charge_limit(self, percent: int) -> bool:
        return await self._send_command("set_charge_limit", json={"percent": percent})

    async def charge_port_door_open(self) -> bool:
        return await self._send_command("charge_port_door_open")

    async def charge_port_door_close(self) -> bool:
        return await self._send_command("charge_port_door_close")

    async def door_lock(self) -> bool:
        return await self._send_command("door_lock")

    async def door_unlock(self) -> bool:
        return await self._send_command("door_unlock")

    async def climate_start(self) -> bool:
        return await self._send_command("auto_conditioning_start")

    async def climate_stop(self) -> bool:
        return await self._send_command("auto_conditioning_stop")

    async def set_temps(self, driver: float, passenger: float) -> bool:
        return await self._send_command("set_temps", json={"driver_temp": driver, "passenger_temp": passenger})

    async def actuate_trunk(self, which: str) -> bool:
        return await self._send_command("actuate_trunk", json={"which_trunk": which})

    async def flash_lights(self) -> bool:
        return await self._send_command("flash_lights")

    async def honk_horn(self) -> bool:
        return await self._send_command("honk_horn")

    async def set_sentry_mode(self, on: bool) -> bool:
        return await self._send_command("set_sentry_mode", json={"on": on})

    async def window_control(self, command: str) -> bool:
        # lat/lon required by API but values don't matter for vent/close
        return await self._send_command("window_control", json={"command": command, "lat": 0, "lon": 0})

    async def set_seat_heater(self, seat: int, level: int) -> bool:
        return await self._send_command("remote_seat_heater_request", json={"heater": seat, "level": level})

    async def set_steering_wheel_heater(self, on: bool) -> bool:
        return await self._send_command("remote_steering_wheel_heater_request", json={"on": on})

    async def set_preconditioning_max(self, on: bool) -> bool:
        return await self._send_command("set_preconditioning_max", json={"on": on})

    async def _recreate_proxy(self) -> bool:
        """Recreate tesla-http-proxy container to guarantee a clean session cache.

        CRITICAL: docker restart does NOT clear tmpfs, and the archive API
        overwrite is unreliable. The ONLY proven way to clear the proxy's
        stale session cache (.tesla-cache.json on tmpfs) is full container
        recreation: stop → remove → create → start. This guarantees fresh
        tmpfs, preventing SIGNEDMESSAGE_INFORMATION_FAULT_IV errors that
        cause the car to revoke our key.
        """
        CONTAINER = "tesla-http-proxy"
        try:
            transport = httpx.AsyncHTTPTransport(uds="/var/run/docker.sock")
            async with httpx.AsyncClient(
                transport=transport, base_url="http://localhost"
            ) as client:
                # Step 1: Inspect current container to capture its full config
                resp = await client.get(f"/v1.45/containers/{CONTAINER}/json", timeout=10)
                if resp.status_code != 200:
                    logger.error(f"Cannot inspect proxy container: HTTP {resp.status_code}")
                    return False
                info = resp.json()

                config = info["Config"]
                host_config = info["HostConfig"]

                # Step 2: Stop the container
                resp = await client.post(
                    f"/v1.45/containers/{CONTAINER}/stop",
                    params={"t": "3"}, timeout=30,
                )
                if resp.status_code not in (200, 204, 304):  # 304 = already stopped
                    logger.warning(f"Stop proxy returned HTTP {resp.status_code}")

                # Step 3: Remove the container (this clears tmpfs!)
                resp = await client.delete(
                    f"/v1.45/containers/{CONTAINER}",
                    params={"force": "true"}, timeout=10,
                )
                if resp.status_code not in (200, 204):
                    logger.error(f"Remove proxy failed: HTTP {resp.status_code}")
                    return False
                logger.info("Removed proxy container (tmpfs cleared)")

                # Step 4: Create a new container with the same config
                create_body = {
                    "Image": config["Image"],
                    "Env": config.get("Env", []),
                    "Cmd": config.get("Cmd"),
                    "Entrypoint": config.get("Entrypoint"),
                    "HostConfig": {
                        "NetworkMode": host_config.get("NetworkMode", "host"),
                        "Binds": host_config.get("Binds", []),
                        "Tmpfs": host_config.get("Tmpfs", {}),
                        "RestartPolicy": host_config.get("RestartPolicy", {"Name": "unless-stopped"}),
                    },
                }

                resp = await client.post(
                    "/v1.45/containers/create",
                    params={"name": CONTAINER},
                    json=create_body,
                    timeout=10,
                )
                if resp.status_code not in (200, 201):
                    logger.error(f"Create proxy failed: HTTP {resp.status_code} — {resp.text}")
                    return False
                new_id = resp.json().get("Id", "")[:12]
                logger.info(f"Created new proxy container: {new_id}")

                # Step 5: Start the new container
                resp = await client.post(
                    f"/v1.45/containers/{CONTAINER}/start", timeout=10,
                )
                if resp.status_code not in (200, 204):
                    logger.error(f"Start proxy failed: HTTP {resp.status_code}")
                    return False

                logger.info("Proxy container recreated with fresh tmpfs")
                elog("Proxy recreated — fresh session cache", INFO, "tesla")
                await asyncio.sleep(3)  # Wait for proxy to be ready
                return True

        except Exception as e:
            logger.error(f"Failed to recreate proxy: {e}")
            elog(f"Proxy recreate failed: {e}", WARN, "tesla")
            return False

    async def _restart_proxy(self) -> bool:
        """Restart proxy by fully recreating the container (clears tmpfs)."""
        return await self._recreate_proxy()

    async def _wake_and_wait(self, max_wait: int = 30) -> bool:
        """Wake the vehicle and wait until it's online and ready for commands."""
        logger.info("Waking vehicle...")
        elog("Waking vehicle...", INFO, "tesla")
        await self.wake_up()
        for _ in range(max_wait // 3):
            await asyncio.sleep(3)
            state = await self.get_vehicle_data()
            if state.state == "online":
                logger.info("Vehicle is online")
                elog("Vehicle is online", SUCCESS, "tesla")
                self._last_known_online = True
                # Don't restart proxy here — _ensure_fresh_proxy handles it
                return True
        logger.warning("Vehicle did not wake up in time")
        elog("Vehicle did not wake up in time", WARN, "tesla")
        return False

    async def _check_vehicle_state(self) -> str:
        """Check vehicle state via the lightweight vehicles list endpoint.

        This does NOT require the car to be awake — it queries Tesla's cloud
        for the last known state. Returns 'online', 'asleep', 'offline', or 'unknown'.
        """
        try:
            vehicles = await self.get_vehicles()
            if vehicles:
                state = vehicles[0].get("state", "unknown")
                if state == "online":
                    self._last_known_online = True
                else:
                    self._last_known_online = False
                return state
        except Exception as e:
            logger.warning(f"Failed to check vehicle state: {e}")
        return "unknown"

    async def _ensure_online(self) -> bool:
        """Ensure the vehicle is online before sending proxy commands."""
        if self._key_revoked:
            logger.warning("Key is revoked — skipping command")
            return False

        state = await self._check_vehicle_state()
        if state != "online":
            logger.info(f"Vehicle state is '{state}' — waking before sending any proxy command")
            elog(f"Vehicle is {state} — waking first (pre-command safety)", INFO, "tesla")
            if not await self._wake_and_wait():
                return False
        return True

    async def _send_command(self, command: str, **kwargs) -> bool:
        """Send a command via proxy, ensuring vehicle is online first.

        Strategy: send command directly (proxy keeps its own session cache).
        Only recreate proxy on 500 error, with cooldown to avoid the IV-reset
        problem that causes key revocation. Never recreate preemptively.
        """
        if not await self._ensure_vehicle_info():
            return False

        if not await self._ensure_online():
            logger.error(f"Cannot send {command} — vehicle not online or key revoked")
            elog(f"Cannot send {command} — vehicle not online or key revoked", ERROR, "tesla")
            return False

        # Try sending the command
        try:
            await self._request(
                "POST",
                f"/api/1/vehicles/{self._vin}/command/{command}",
                use_proxy=True,
                **kwargs,
            )
            self._last_command_success = time.monotonic()
            self._consecutive_proxy_failures = 0
            return True
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 500:
                logger.error(f"Command {command} failed: {e}")
                elog(f"Command {command} failed: {e}", ERROR, "tesla")
                return False

            # 500 from proxy — possible stale session. Apply cooldown before recreating.
            self._consecutive_proxy_failures += 1
            now = time.monotonic()
            since_last_recreate = now - self._last_proxy_recreate
            since_last_success = now - self._last_command_success

            # If last command succeeded recently, DON'T recreate — the session is still valid.
            # The 500 might be a transient issue. Just fail and let next tick retry naturally.
            if since_last_success < 120:
                logger.warning(f"Command {command} got 500 but last success was {since_last_success:.0f}s ago — NOT recreating proxy (session likely valid)")
                elog(f"Command {command} failed (transient) — will retry", WARN, "tesla")
                return False

            # If we already recreated recently, don't do it again (cooldown)
            if since_last_recreate < 300:
                logger.warning(f"Command {command} got 500, proxy was recreated {since_last_recreate:.0f}s ago — waiting for cooldown")
                # If we've failed multiple times with fresh proxy, key is probably revoked
                if self._consecutive_proxy_failures >= 3:
                    self._key_revoked = True
                    logger.error(f"Key appears revoked — {self._consecutive_proxy_failures} consecutive failures after proxy recreate")
                    elog("Key appears revoked — stopping all proxy commands. Re-add key via Tesla app.", ERROR, "tesla")
                return False

            # Recreate proxy and retry once
            logger.info(f"Command {command} got 500, last success was {since_last_success:.0f}s ago — recreating proxy")
            elog(f"Proxy session error on {command} — recreating proxy", WARN, "tesla")
            if not await self._restart_proxy():
                return False
            self._last_proxy_recreate = time.monotonic()

            try:
                await self._request(
                    "POST",
                    f"/api/1/vehicles/{self._vin}/command/{command}",
                    use_proxy=True,
                    **kwargs,
                )
                self._last_command_success = time.monotonic()
                self._consecutive_proxy_failures = 0
                elog(f"Command {command} succeeded after proxy recreate", SUCCESS, "tesla")
                return True
            except Exception as e2:
                self._consecutive_proxy_failures += 1
                logger.error(f"Command {command} failed after proxy recreate: {e2}")
                elog(f"Command {command} failed after proxy recreate", ERROR, "tesla")
                if self._consecutive_proxy_failures >= 3:
                    self._key_revoked = True
                    logger.error("Key appears revoked — stopping all proxy commands")
                    elog("Key appears revoked — stopping all proxy commands. Re-add key via Tesla app.", ERROR, "tesla")
                return False
        except Exception as e:
            logger.error(f"Command {command} failed: {e}")
            return False

    async def start_charging(self) -> bool:
        ok = await self._send_command("charge_start")
        if ok:
            logger.info("Charging started")
        return ok

    async def stop_charging(self) -> bool:
        ok = await self._send_command("charge_stop")
        if ok:
            logger.info("Charging stopped")
        return ok

    def clear_key_revoked(self):
        """Call after user re-adds the key via Tesla app."""
        self._key_revoked = False
        self._consecutive_proxy_failures = 0
        logger.info("Key revoked flag cleared — proxy commands re-enabled")
        elog("Key revoked flag cleared — commands re-enabled", SUCCESS, "tesla")

    @property
    def key_revoked(self) -> bool:
        return self._key_revoked

    @property
    def last_state(self) -> VehicleState:
        return self._last_state


# Singleton
tesla_api = TeslaAPI()
