import asyncio
import logging
import time

import httpx

from app.config import settings
from app.event_log import log as elog, INFO, WARN, ERROR, SUCCESS
from app.tesla.auth import get_valid_token, refresh_access_token
from app.tesla.models import VehicleState
from app.tesla.transport import TeslaTransport

logger = logging.getLogger(__name__)

FLEET_API_BASE = settings.TESLA_AUDIENCE
PROXY_BASE = settings.TESLA_PROXY_URL


class TeslaAPI(TeslaTransport):
    def __init__(self):
        self._vehicle_id: str | None = None
        self._vin: str | None = None
        self._last_state: VehicleState = VehicleState()
        self._last_known_online: bool = False
        self._last_proxy_command: float = 0    # monotonic time of last proxy command (success or fail)
        self._consecutive_proxy_failures: int = 0
        self._key_revoked: bool = False  # set True after repeated fresh-proxy failures
        self._command_lock = asyncio.Lock()  # prevent concurrent proxy recreations
        MIN_COMMAND_INTERVAL = 30  # minimum seconds between proxy commands

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
            # 408 = vehicle asleep/unavailable — mark state but preserve plugged-in info
            if e.response.status_code in (408, 503):
                logger.info(f"Vehicle data request returned {e.response.status_code} — vehicle likely asleep")
                self._last_state.state = "asleep"
                self._last_state.charging_amps = 0
                self._last_state.charger_power = 0.0
                # Keep is_plugged_in and charge_state from last known data
                # so the algorithm knows whether to wake the car for charging
                self._last_known_online = False
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
        """Recreate tesla-http-proxy container via Docker API to guarantee a clean session.

        CRITICAL: docker restart does NOT clear tmpfs. Full container recreation
        (stop → remove → create → start) clears the proxy's session cache
        (.tesla-cache.json on tmpfs), preventing IV counter corruption.

        Copies all config AND labels from the old container so Docker Compose
        still recognizes it on subsequent deploys (no name conflicts).
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
                if resp.status_code not in (200, 204, 304):
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

                # Step 4: Create a new container with the same config + labels + user
                create_body = {
                    "Image": config["Image"],
                    "Env": config.get("Env", []),
                    "Cmd": config.get("Cmd"),
                    "Entrypoint": config.get("Entrypoint"),
                    "User": config.get("User", ""),  # preserve container user (e.g. 65532)
                    "Labels": config.get("Labels", {}),  # preserve Compose labels
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

                # Wait for proxy to accept TLS connections (poll up to 15s)
                for attempt in range(15):
                    await asyncio.sleep(1)
                    try:
                        async with httpx.AsyncClient(verify=False) as test_client:
                            await test_client.get("https://localhost:4443/", timeout=2)
                        logger.info(f"Proxy ready after {attempt + 1}s")
                        return True
                    except Exception:
                        pass  # not ready yet
                logger.warning("Proxy not ready after 15s — proceeding anyway")
                return True

        except Exception as e:
            logger.error(f"Failed to recreate proxy: {e}")
            elog(f"Proxy recreate failed: {e}", WARN, "tesla")
            return False

    async def _restart_proxy(self) -> bool:
        """Restart proxy by fully recreating the container (clears tmpfs)."""
        return await self._recreate_proxy()

    async def wake_and_wait(self, max_wait: int = 30) -> bool:
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
        Also logs sleep/wake transitions.
        """
        try:
            vehicles = await self.get_vehicles()
            if vehicles:
                state = vehicles[0].get("state", "unknown")
                # Log transitions
                if state == "online" and not self._last_known_online:
                    logger.info("Vehicle is now online")
                    elog("Vehicle is now online", SUCCESS, "tesla")
                    self._last_known_online = True
                elif state != "online" and self._last_known_online:
                    logger.info(f"Vehicle went to sleep ({state})")
                    elog(f"Vehicle went to sleep ({state})", INFO, "tesla")
                    self._last_known_online = False
                elif state == "online":
                    self._last_known_online = True
                else:
                    self._last_known_online = False
                self._last_state.state = state
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
            if not await self.wake_and_wait():
                return False
        return True

    async def _send_command(self, command: str, **kwargs) -> bool:
        """Send a command via proxy with fresh session every time.

        CRITICAL: The tesla-http-proxy has an IV counter bug where the counter
        gets corrupted after 2-3 commands on the same session, causing
        IV_SMALLER_THAN_EXPECTED which makes the car revoke our key permanently.

        Prevention: recreate the proxy before EVERY command so each command gets
        a brand new session (IV starts at 0). One command per proxy lifetime.
        Uses asyncio.Lock to prevent concurrent proxy recreations (409 Conflict).
        """
        async with self._command_lock:
            if not await self._ensure_vehicle_info():
                return False

            if not await self._ensure_online():
                logger.error(f"Cannot send {command} — vehicle not online or key revoked")
                elog(f"Cannot send {command} — vehicle not online or key revoked", ERROR, "tesla")
                return False

            # Enforce minimum interval between proxy commands
            now = time.monotonic()
            since_last = now - self._last_proxy_command
            if self._last_proxy_command > 0 and since_last < 30:
                wait = 30 - since_last
                logger.info(f"Waiting {wait:.0f}s before next proxy command (minimum interval)")
                await asyncio.sleep(wait)

            # Recreate proxy for a fresh session (prevents IV counter corruption)
            logger.info(f"Recreating proxy for fresh session before {command}")
            if not await self._restart_proxy():
                logger.error(f"Cannot send {command} — proxy recreate failed")
                elog(f"Proxy recreate failed before {command}", ERROR, "tesla")
                return False

            # Send the command on the fresh proxy
            self._last_proxy_command = time.monotonic()
            try:
                await self._request(
                    "POST",
                    f"/api/1/vehicles/{self._vin}/command/{command}",
                    use_proxy=True,
                    **kwargs,
                )
                self._consecutive_proxy_failures = 0
                return True
            except httpx.HTTPStatusError as e:
                self._consecutive_proxy_failures += 1
                logger.error(f"Command {command} failed on fresh proxy: {e}")
                elog(f"Command {command} failed: {e}", ERROR, "tesla")
                if e.response.status_code == 500 and self._consecutive_proxy_failures >= 2:
                    self._key_revoked = True
                    logger.error("Key appears revoked — 2 consecutive failures on fresh proxy sessions")
                    elog("Key appears revoked — stopping all proxy commands. Re-add key via Tesla app.", ERROR, "tesla")
                return False
            except Exception as e:
                self._consecutive_proxy_failures += 1
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
