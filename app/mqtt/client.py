import json
import logging
import threading

import paho.mqtt.client as mqtt
import redis

from app.config import settings
from app.event_log import log as elog, INFO, WARN, ERROR, SUCCESS

logger = logging.getLogger(__name__)


class MQTTClient:
    def __init__(self):
        self.redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id="tesla-solar-charger"
        )
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect
        self._thread: threading.Thread | None = None

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            topic = f"{settings.MQTT_TOPIC_PREFIX}/#"
            client.subscribe(topic)
            logger.info(f"MQTT connected, subscribed to {topic}")
            elog(f"MQTT connected to {settings.MQTT_HOST}", SUCCESS, "mqtt")
        else:
            logger.error(f"MQTT connection failed with code {rc}")
            elog(f"MQTT connection failed (code {rc})", ERROR, "mqtt")

    def _on_message(self, client, userdata, msg):
        try:
            topic = msg.topic
            value = msg.payload.decode("utf-8").strip()
            # Store under normalized key: strip prefix, replace / with :
            key = topic.replace(f"{settings.MQTT_TOPIC_PREFIX}/", "", 1)
            redis_key = f"mqtt:{key.replace('/', ':')}"
            self.redis.set(redis_key, value, ex=60)  # expire after 60s
        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}")

    def _on_disconnect(self, client, userdata, flags, rc, properties=None):
        logger.warning(f"MQTT disconnected (rc={rc}), will auto-reconnect")

    def start(self):
        self.client.connect(settings.MQTT_HOST, settings.MQTT_PORT, keepalive=60)
        self._thread = threading.Thread(target=self.client.loop_forever, daemon=True)
        self._thread.start()
        logger.info(f"MQTT client started, connecting to {settings.MQTT_HOST}:{settings.MQTT_PORT}")

    def stop(self):
        self.client.disconnect()
        if self._thread:
            self._thread.join(timeout=5)

    def get(self, key: str) -> str | None:
        """Get a cached MQTT value. Key format: inverter_1:pv_power
        Automatically appends :state since Solar Assistant topics end with /state"""
        val = self.redis.get(f"mqtt:{key}")
        if val is None:
            val = self.redis.get(f"mqtt:{key}:state")
        return val

    def get_float(self, key: str, default: float = 0.0) -> float:
        val = self.get(key)
        if val is None:
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    def get_int(self, key: str, default: int = 0) -> int:
        val = self.get(key)
        if val is None:
            return default
        try:
            return int(float(val))
        except (ValueError, TypeError):
            return default

    def get_solar_status(self) -> dict:
        """Get complete solar system status for the dashboard."""
        return {
            "pv": {
                "power": self.get_int("inverter_1:pv_power"),
                "power_1": self.get_int("inverter_1:pv_power_1"),
                "power_2": self.get_int("inverter_1:pv_power_2"),
                "voltage_1": self.get_float("inverter_1:pv_voltage_1"),
                "voltage_2": self.get_float("inverter_1:pv_voltage_2"),
                "current_1": self.get_float("inverter_1:pv_current_1"),
                "current_2": self.get_float("inverter_1:pv_current_2"),
            },
            "battery": {
                "power": self.get_int("total:battery_power"),
                "soc": self.get_int("total:battery_state_of_charge"),
                "voltage": self.get_float("inverter_1:battery_voltage"),
                "current": self.get_float("inverter_1:battery_current"),
                "temperature": self.get_float("total:battery_temperature"),
                "energy_in": self.get_float("total:battery_energy_in"),
                "energy_out": self.get_float("total:battery_energy_out"),
                "banks": [self._get_battery(i) for i in range(1, 5)],
            },
            "grid": {
                "power": self.get_int("inverter_1:grid_power"),
                "power_ct": self.get_int("inverter_1:grid_power_ct"),
                "voltage": self.get_float("inverter_1:grid_voltage"),
                "frequency": self.get_float("inverter_1:grid_frequency"),
                "energy_in": self.get_float("total:grid_energy_in"),
                "energy_out": self.get_float("total:grid_energy_out"),
            },
            "load": {
                "power": self.get_int("inverter_1:load_power"),
                "essential": self.get_int("inverter_1:load_power_essential"),
                "non_essential": self.get_int("inverter_1:load_power_non-essential"),
            },
            "inverter": {
                "temperature": self.get_float("inverter_1:temperature"),
                "load_percentage": self.get_int("inverter_1:load_percentage"),
                "device_mode": self.get("inverter_1:device_mode") or "Unknown",
                "ac_output_voltage": self.get_float("inverter_1:ac_output_voltage"),
                "ac_output_frequency": self.get_float("inverter_1:ac_output_frequency"),
            },
        }

    def _get_battery(self, n: int) -> dict:
        return {
            "id": n,
            "soc": self.get_int(f"battery_{n}:state_of_charge"),
            "voltage": self.get_float(f"battery_{n}:voltage"),
            "current": self.get_float(f"battery_{n}:current"),
            "power": self.get_int(f"battery_{n}:power"),
            "temperature": self.get_float(f"battery_{n}:temperature"),
            "cycles": self.get_int(f"battery_{n}:cycles"),
            "cell_highest": self.get_float(f"battery_{n}:cell_voltage_-_highest"),
            "cell_lowest": self.get_float(f"battery_{n}:cell_voltage_-_lowest"),
        }


# Singleton
mqtt_client = MQTTClient()
