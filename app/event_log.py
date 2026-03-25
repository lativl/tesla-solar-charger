"""In-memory event log for workflow events visible to the user."""

import threading
from collections import deque
from datetime import datetime

# Ring buffer — keeps the last 500 events
_MAX_EVENTS = 500
_events: deque[dict] = deque(maxlen=_MAX_EVENTS)
_lock = threading.Lock()

# Event levels
INFO = "info"
WARN = "warn"
ERROR = "error"
SUCCESS = "success"


def log(message: str, level: str = INFO, category: str = "system"):
    """Add an event to the log.

    Categories: system, algorithm, tesla, mqtt, schedule, manual
    """
    event = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "level": level,
        "category": category,
        "message": message,
    }
    with _lock:
        _events.append(event)


def get_events(limit: int = 200, category: str | None = None) -> list[dict]:
    """Return recent events, newest first."""
    with _lock:
        items = list(_events)
    items.reverse()
    if category:
        items = [e for e in items if e["category"] == category]
    return items[:limit]


def clear():
    with _lock:
        _events.clear()
