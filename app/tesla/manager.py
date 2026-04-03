"""Transport manager — routes Tesla commands through the active channel.

Holds references to both FleetApiTransport (TeslaAPI) and BleTransport.
The active channel is persisted in the settings DB so it survives restarts.
BLE host and entity map are read from DB settings, not from .env.
"""

import logging

from app.tesla.transport import TeslaTransport

logger = logging.getLogger(__name__)


class TransportManager:
    def __init__(self):
        self._fleet = None   # TeslaAPI instance (always created)
        self._ble = None     # BleTransport instance (created only if configured)
        self._active_channel: str = "fleet_api"

    def initialize(self):
        """Create transports. Call once at app startup after DB is ready."""
        from app.tesla.api import TeslaAPI
        self._fleet = TeslaAPI()

        # Read BLE host from DB settings
        from app.database import SessionLocal
        from app.models import Setting
        db = SessionLocal()
        try:
            def get(key):
                row = db.query(Setting).get(key)
                return row.value if row else ""

            channel = get("tesla_channel") or "fleet_api"
            self._active_channel = channel

            ble_host = get("ble_host")
            if ble_host:
                from app.tesla.ble import BleTransport
                entity_map = {}
                for k in [
                    "battery_level", "charging_current", "charging_state",
                    "charge_limit_soc", "charger_voltage", "charger_power",
                    "charging_switch", "charging_amps", "charge_limit", "wake_button",
                ]:
                    v = get(f"ble_entity_{k}")
                    if v:
                        entity_map[k] = v
                api_key = get("ble_api_key")
                self._ble = BleTransport(host=ble_host, api_key=api_key,
                                         entity_map=entity_map or None)
                logger.info(f"BLE transport initialized: {ble_host}")
            else:
                logger.info("BLE transport not configured (no ble_host in settings)")
        finally:
            db.close()

        logger.info(f"Active channel: {self._active_channel}")

    def reinitialize_ble(self):
        """Recreate BLE transport from current DB settings. Call after BLE config changes."""
        from app.database import SessionLocal
        from app.models import Setting
        db = SessionLocal()
        try:
            def get(key):
                row = db.query(Setting).get(key)
                return row.value if row else ""

            ble_host = get("ble_host")
            if ble_host:
                from app.tesla.ble import BleTransport
                entity_map = {}
                for k in [
                    "battery_level", "charging_current", "charging_state",
                    "charge_limit_soc", "charger_voltage", "charger_power",
                    "charging_switch", "charging_amps", "charge_limit", "wake_button",
                ]:
                    v = get(f"ble_entity_{k}")
                    if v:
                        entity_map[k] = v
                api_key = get("ble_api_key")
                self._ble = BleTransport(host=ble_host, api_key=api_key,
                                         entity_map=entity_map or None)
                logger.info(f"BLE transport reinitialized: {ble_host}")
            else:
                self._ble = None
        finally:
            db.close()

    @property
    def active(self) -> TeslaTransport:
        if self._active_channel == "ble" and self._ble is not None:
            return self._ble
        return self._fleet

    @property
    def active_channel(self) -> str:
        return self._active_channel

    @property
    def fleet(self):
        return self._fleet

    @property
    def ble(self):
        return self._ble

    def set_channel(self, channel: str):
        if channel not in ("fleet_api", "ble"):
            raise ValueError(f"Unknown channel: {channel}")
        if channel == "ble" and self._ble is None:
            raise ValueError("BLE transport not configured — set ble_host in settings first")
        self._active_channel = channel
        # Persist to DB
        from app.database import SessionLocal
        from app.models import Setting
        from datetime import datetime
        db = SessionLocal()
        try:
            row = db.query(Setting).get("tesla_channel")
            if row:
                row.value = channel
                row.updated_at = datetime.utcnow()
            else:
                db.add(Setting(key="tesla_channel", value=channel))
            db.commit()
        finally:
            db.close()
        logger.info(f"Active channel switched to: {channel}")

    def get_status(self) -> dict:
        from app.tesla.auth import get_valid_token
        has_token = bool(get_valid_token())
        return {
            "active_channel": self._active_channel,
            "fleet_api": {
                "available": self._fleet is not None,
                "has_token": has_token,
                "key_revoked": self._fleet.key_revoked if self._fleet else False,
            },
            "ble": {
                "available": self._ble is not None,
                "reachable": self._ble.reachable if self._ble else False,
            },
        }


# Module-level singleton
transport_manager = TransportManager()
