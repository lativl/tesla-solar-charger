import logging
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx

from app.config import settings
from app.database import SessionLocal
from app.models import TeslaToken

logger = logging.getLogger(__name__)

TESLA_AUTH_URL = "https://auth.tesla.com/oauth2/v3"
TESLA_TOKEN_URL = f"{TESLA_AUTH_URL}/token"
TESLA_AUTHORIZE_URL = f"{TESLA_AUTH_URL}/authorize"

SCOPES = "openid offline_access vehicle_device_data vehicle_cmds vehicle_charging_cmds"

# The redirect URI registered in the Tesla Developer portal (HA's redirect)
HA_REDIRECT_URI = "https://my.home-assistant.io/redirect/oauth"


def get_authorize_url(state: str = "tesla-solar-charger") -> str:
    params = {
        "response_type": "code",
        "client_id": settings.TESLA_CLIENT_ID,
        "redirect_uri": HA_REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
    }
    return f"{TESLA_AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            TESLA_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": settings.TESLA_CLIENT_ID,
                "client_secret": settings.TESLA_CLIENT_SECRET,
                "code": code,
                "redirect_uri": HA_REDIRECT_URI,
                "audience": settings.TESLA_AUDIENCE,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    _store_tokens(data)
    return data


async def refresh_access_token() -> str | None:
    db = SessionLocal()
    try:
        token = db.query(TeslaToken).first()
        if not token:
            return None

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                TESLA_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": settings.TESLA_CLIENT_ID,
                    "refresh_token": token.refresh_token,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        _store_tokens(data)
        return data["access_token"]
    except Exception as e:
        logger.error(f"Token refresh failed: {e}")
        return None
    finally:
        db.close()


def get_valid_token() -> str | None:
    db = SessionLocal()
    try:
        token = db.query(TeslaToken).first()
        if not token:
            return None
        if token.expires_at < datetime.utcnow():
            return None  # caller should refresh
        return token.access_token
    finally:
        db.close()


def _store_tokens(data: dict):
    db = SessionLocal()
    try:
        token = db.query(TeslaToken).first()
        expires_at = datetime.utcnow() + timedelta(seconds=data.get("expires_in", 3600))
        if token:
            token.access_token = data["access_token"]
            token.refresh_token = data.get("refresh_token", token.refresh_token)
            token.expires_at = expires_at
        else:
            token = TeslaToken(
                access_token=data["access_token"],
                refresh_token=data.get("refresh_token", ""),
                expires_at=expires_at,
            )
            db.add(token)
        db.commit()
    finally:
        db.close()
