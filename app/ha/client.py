import logging
import time

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class HAClient:
    """Read sensor values from Home Assistant REST API."""

    def __init__(self):
        self._base_url = settings.HA_URL.rstrip("/")
        self._token = settings.HA_TOKEN
        self._cache: dict[str, tuple[float, float]] = {}  # sensor_id -> (value, timestamp)
        self._cache_ttl = 30  # seconds

    @property
    def configured(self) -> bool:
        return bool(self._base_url and self._token)

    async def get_sensor(self, sensor_id: str) -> float | None:
        """Fetch a sensor value from HA. Returns None on failure. Cached for 30s."""
        if not self.configured:
            return None

        now = time.monotonic()
        if sensor_id in self._cache:
            value, ts = self._cache[sensor_id]
            if now - ts < self._cache_ttl:
                return value

        try:
            async with httpx.AsyncClient(verify=False) as client:
                resp = await client.get(
                    f"{self._base_url}/api/states/{sensor_id}",
                    headers={"Authorization": f"Bearer {self._token}"},
                    timeout=10,
                )
                if resp.status_code != 200:
                    logger.warning(f"HA sensor {sensor_id}: HTTP {resp.status_code}")
                    return self._cache.get(sensor_id, (None,))[0]

                data = resp.json()
                state = data.get("state")
                if state in ("unavailable", "unknown", None):
                    return self._cache.get(sensor_id, (None,))[0]

                value = float(state)
                self._cache[sensor_id] = (value, now)
                return value
        except (ValueError, TypeError):
            logger.warning(f"HA sensor {sensor_id}: non-numeric state '{state}'")
            return None
        except Exception as e:
            logger.warning(f"HA sensor {sensor_id} fetch failed: {e}")
            return self._cache.get(sensor_id, (None,))[0]

    async def get_solar_lux(self) -> float | None:
        """Get solar lux from Ecowitt sensor."""
        return await self.get_sensor("sensor.ecowitt_solar_lux")


ha_client = HAClient()
