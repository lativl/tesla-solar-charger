"""Learns the relationship between solar lux and maximum PV power output.

Uses a bucketed envelope model: groups lux readings into 1000-lux-wide buckets,
tracks the P90 PV power from uncurtailed observations, and predicts max PV
for a given lux value via interpolation.
"""

import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import LuxPvBucket

logger = logging.getLogger(__name__)

BUCKET_WIDTH = 1000
MIN_SAMPLES = 10
MIN_BUCKETS_FOR_MODEL = 3
REFRESH_INTERVAL_S = 3600  # 1 hour


def _lux_to_bucket(lux: float) -> int:
    return int(round(lux / BUCKET_WIDTH)) * BUCKET_WIDTH


class LuxPvModel:
    def __init__(self):
        # lux_bucket -> (p90, max, count)
        self._buckets: dict[int, tuple[int, int, int]] = {}
        self._last_refresh: float = 0
        self._model_ready: bool = False
        self._total_samples: int = 0
        self._last_refreshed_at: datetime | None = None

    @property
    def ready(self) -> bool:
        return self._model_ready

    def refresh_if_needed(self):
        now = time.monotonic()
        if now - self._last_refresh < REFRESH_INTERVAL_S:
            return
        self._last_refresh = now
        try:
            self._refresh()
        except Exception as e:
            logger.warning(f"Lux model refresh failed: {e}")

    def _refresh(self):
        """Recompute buckets from metrics table."""
        db = SessionLocal()
        try:
            # Read window_days from settings
            from app.models import Setting
            row = db.query(Setting).get("lux_model_window_days")
            window_days = int(row.value) if row else 30
            self._aggregate(db, window_days)
            self._load_buckets(db)
        finally:
            db.close()

    def _aggregate(self, db: Session, window_days: int = 30):
        """Query uncurtailed metrics and compute P90/max per lux bucket."""
        since = datetime.utcnow() - timedelta(days=window_days)

        # Fetch uncurtailed data points
        rows = db.execute(text("""
            SELECT solar_lux, pv_power
            FROM metrics
            WHERE timestamp >= :since
              AND solar_lux IS NOT NULL
              AND solar_lux > 500
              AND pv_power > 200
              AND (
                  load_power > 0.7 * pv_power
                  OR battery_power > 300
                  OR grid_power < -50
              )
              AND NOT (
                  battery_soc >= 98
                  AND battery_power <= 0
                  AND load_power < 0.5 * pv_power
              )
        """), {"since": since}).fetchall()

        if not rows:
            logger.info("Lux model: no uncurtailed data points found")
            return

        # Group by bucket
        buckets: dict[int, list[int]] = {}
        for lux, pv in rows:
            b = _lux_to_bucket(lux)
            buckets.setdefault(b, []).append(pv)

        # Compute P90 and max, upsert into table
        now = datetime.utcnow()
        total = 0

        # Clear old buckets
        db.execute(text("DELETE FROM lux_pv_buckets"))

        for bucket_lux, pv_values in sorted(buckets.items()):
            pv_values.sort()
            count = len(pv_values)
            total += count
            pv_max = pv_values[-1]
            # P90: value at 90th percentile index
            p90_idx = int(count * 0.9)
            pv_p90 = pv_values[min(p90_idx, count - 1)]

            db.add(LuxPvBucket(
                lux_bucket=bucket_lux,
                pv_power_max=pv_max,
                pv_power_p90=pv_p90,
                sample_count=count,
                updated_at=now,
            ))

        db.commit()
        logger.info(f"Lux model refreshed: {len(buckets)} buckets, {total} samples")

    def _load_buckets(self, db: Session):
        """Load computed buckets into memory."""
        rows = db.query(LuxPvBucket).order_by(LuxPvBucket.lux_bucket).all()
        self._buckets = {}
        self._total_samples = 0
        for r in rows:
            self._buckets[r.lux_bucket] = (r.pv_power_p90, r.pv_power_max, r.sample_count)
            self._total_samples += r.sample_count

        confident = sum(1 for _, _, c in self._buckets.values() if c >= MIN_SAMPLES)
        self._model_ready = confident >= MIN_BUCKETS_FOR_MODEL
        self._last_refreshed_at = datetime.utcnow()

    def predict_max_pv(self, lux: float | None) -> int | None:
        """Predict max PV power for given lux. Returns None if insufficient data."""
        if lux is None or not self._model_ready:
            return None

        bucket = _lux_to_bucket(lux)

        # Exact bucket match with enough samples
        if bucket in self._buckets:
            p90, _, count = self._buckets[bucket]
            if count >= MIN_SAMPLES:
                return p90

        # Interpolate between nearest populated buckets
        sorted_buckets = sorted(
            ((b, p90, cnt) for b, (p90, _, cnt) in self._buckets.items() if cnt >= MIN_SAMPLES)
        )
        if len(sorted_buckets) < 2:
            return sorted_buckets[0][1] if sorted_buckets else None

        # Find lower and upper neighbors
        lower = None
        upper = None
        for b, p90, _ in sorted_buckets:
            if b <= bucket:
                lower = (b, p90)
            elif upper is None:
                upper = (b, p90)

        if lower and upper:
            # Linear interpolation
            span = upper[0] - lower[0]
            if span == 0:
                return lower[1]
            t = (bucket - lower[0]) / span
            return int(lower[1] + t * (upper[1] - lower[1]))
        elif lower:
            return lower[1]  # extrapolate flat from highest bucket
        elif upper:
            return upper[1]  # extrapolate flat from lowest bucket

        return None

    def get_curtailment_headroom(self, lux: float | None, current_pv: int) -> int:
        """Return estimated watts of untapped PV potential."""
        predicted = self.predict_max_pv(lux)
        if predicted is None:
            return 0
        return max(0, predicted - current_pv)

    def get_model_data(self) -> dict:
        """Return model data for API/dashboard."""
        buckets = []
        for b in sorted(self._buckets.keys()):
            p90, pv_max, count = self._buckets[b]
            buckets.append({
                "lux": b,
                "pv_max": pv_max,
                "pv_p90": p90,
                "samples": count,
            })
        return {
            "buckets": buckets,
            "total_samples": self._total_samples,
            "last_refreshed": self._last_refreshed_at.isoformat() if self._last_refreshed_at else None,
            "model_ready": self._model_ready,
        }


# Module-level singleton
lux_pv_model = LuxPvModel()
