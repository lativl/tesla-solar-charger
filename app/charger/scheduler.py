import logging
from datetime import datetime

from app.database import SessionLocal
from app.models import Schedule

logger = logging.getLogger(__name__)


class ScheduleManager:
    def get_active_schedule(self) -> Schedule | None:
        db = SessionLocal()
        try:
            now = datetime.now()
            current_time = now.strftime("%H:%M")
            current_day = now.strftime("%a").lower()

            schedules = db.query(Schedule).filter(Schedule.enabled.is_(True)).all()

            for sched in schedules:
                if sched.days_of_week != "*":
                    days = [d.strip().lower() for d in sched.days_of_week.split(",")]
                    if current_day not in days:
                        continue

                if sched.start_time <= current_time <= sched.end_time:
                    return sched

                # Handle overnight schedules (e.g., 23:00 - 06:00)
                if sched.start_time > sched.end_time:
                    if current_time >= sched.start_time or current_time <= sched.end_time:
                        return sched

            return None
        finally:
            db.close()

    def calculate_required_amps(
        self,
        schedule: Schedule,
        current_ev_soc: int,
        charger_voltage: int = 230,
        charger_phases: int = 1,
    ) -> int | None:
        """Calculate amps needed to reach target SoC by end time.
        Returns None if no forced charging needed (solar can handle it)."""
        if current_ev_soc >= schedule.target_soc:
            return None

        now = datetime.now()
        end_h, end_m = map(int, schedule.end_time.split(":"))
        end_time = now.replace(hour=end_h, minute=end_m, second=0)
        if end_time <= now:
            end_time = end_time.replace(day=end_time.day + 1)

        remaining_hours = (end_time - now).total_seconds() / 3600
        if remaining_hours <= 0:
            return None

        # Rough estimate: Tesla battery ~75kWh, each % ≈ 0.75kWh
        soc_needed = schedule.target_soc - current_ev_soc
        energy_needed_kwh = soc_needed * 0.75

        power_needed_w = (energy_needed_kwh / remaining_hours) * 1000
        amps_needed = int(power_needed_w / (charger_voltage * charger_phases))

        return max(1, amps_needed)


schedule_manager = ScheduleManager()
