from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

UTC = timezone.utc
NY = ZoneInfo("America/New_York")


def iso(value=None):
    return (value or datetime.now(UTC)).astimezone(UTC).isoformat()


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(str(value).replace(",", "").replace("$", ""))
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError):
        return None


def count(value):
    value = number(value)
    return int(value) if value is not None and value.is_integer() else None


def ratio(numerator, denominator):
    return numerator / denominator if numerator is not None and denominator is not None and denominator > 0 else None


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
    temp.replace(path)


def read_json(path, default=None):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else ({} if default is None else default)


def completed_sessions(now=None, count_=6):
    """Exchange holidays/early closes; allow 20 minutes for delayed closing data."""
    import exchange_calendars as xcals
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("An aware observation time is required")
    calendar = xcals.get_calendar("XNYS")
    dates = calendar.sessions_in_range((now - timedelta(days=40)).date(), now.astimezone(NY).date())
    completed = [d.date().isoformat() for d in dates
                 if calendar.session_close(d).to_pydatetime() <= now - timedelta(minutes=20)]
    if len(completed) < count_:
        raise RuntimeError("Insufficient completed exchange sessions")
    return completed[-count_:]
