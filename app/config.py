import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # MQTT
    MQTT_HOST: str = os.getenv("MQTT_HOST", "")
    MQTT_PORT: int = int(os.getenv("MQTT_PORT", "1883"))
    MQTT_TOPIC_PREFIX: str = os.getenv("MQTT_TOPIC_PREFIX", "solar_assistant")

    # Redis
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6380")

    # Tesla
    TESLA_CLIENT_ID: str = os.getenv("TESLA_CLIENT_ID", "")
    TESLA_CLIENT_SECRET: str = os.getenv("TESLA_CLIENT_SECRET", "")
    TESLA_REDIRECT_URI: str = os.getenv(
        "TESLA_REDIRECT_URI", ""
    )
    TESLA_AUDIENCE: str = os.getenv(
        "TESLA_AUDIENCE", "https://fleet-api.prd.eu.vn.cloud.tesla.com"
    )

    # Tesla HTTP Proxy (for vehicle commands)
    TESLA_PROXY_URL: str = os.getenv("TESLA_PROXY_URL", "https://localhost:4443")

    # Home Assistant
    HA_URL: str = os.getenv("HA_URL", "")
    HA_TOKEN: str = os.getenv("HA_TOKEN", "")

    # App
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me")
    APP_PORT: int = int(os.getenv("APP_PORT", "5050"))
    TZ: str = os.getenv("TZ", "Europe/Kyiv")
    DB_PATH: str = os.getenv("DB_PATH", "data/tesla_charger.db")


settings = Settings()
