from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))
    start_time: Mapped[str] = mapped_column(String(5))  # "HH:MM"
    end_time: Mapped[str] = mapped_column(String(5))
    target_soc: Mapped[int] = mapped_column(Integer, default=80)
    allow_grid: Mapped[bool] = mapped_column(Boolean, default=False)
    max_grid_amps: Mapped[int] = mapped_column(Integer, default=16)
    days_of_week: Mapped[str] = mapped_column(String(50), default="*")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ChargingSession(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    energy_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    solar_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    grid_kwh: Mapped[float] = mapped_column(Float, default=0.0)
    start_soc: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_soc: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avg_amps: Mapped[float] = mapped_column(Float, default=0.0)


class Metric(Base):
    __tablename__ = "metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    pv_power: Mapped[int] = mapped_column(Integer, default=0)
    battery_power: Mapped[int] = mapped_column(Integer, default=0)
    battery_soc: Mapped[int] = mapped_column(Integer, default=0)
    grid_power: Mapped[int] = mapped_column(Integer, default=0)
    load_power: Mapped[int] = mapped_column(Integer, default=0)
    ev_charging_amps: Mapped[float] = mapped_column(Float, default=0.0)
    ev_soc: Mapped[int | None] = mapped_column(Integer, nullable=True)
    solar_lux: Mapped[float | None] = mapped_column(Float, nullable=True)


class LuxPvBucket(Base):
    __tablename__ = "lux_pv_buckets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lux_bucket: Mapped[int] = mapped_column(Integer, index=True, unique=True)
    pv_power_max: Mapped[int] = mapped_column(Integer, default=0)
    pv_power_p90: Mapped[int] = mapped_column(Integer, default=0)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    settings_json: Mapped[str] = mapped_column(Text, default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TeslaToken(Base):
    __tablename__ = "tesla_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    access_token: Mapped[str] = mapped_column(Text)
    refresh_token: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
