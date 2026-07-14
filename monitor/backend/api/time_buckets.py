import calendar
from datetime import datetime, timedelta
from typing import Optional


def hourly_buckets(day: Optional[str] = None) -> list[datetime]:
    if day:
        start = datetime.strptime(day, "%Y-%m-%d")
        now = datetime.utcnow()
        last_hour = now.hour if start.date() == now.date() else 23
        return [start.replace(hour=h) for h in range(last_hour + 1)]
    now = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    return [now - timedelta(hours=h) for h in range(11, -1, -1)]


def daily_buckets(month: Optional[str] = None) -> list[str]:
    now = datetime.utcnow()
    month = month or now.strftime("%Y-%m")
    year, mon = int(month[:4]), int(month[5:7])
    _, days_in_month = calendar.monthrange(year, mon)
    is_current_month = (year, mon) == (now.year, now.month)
    last_day = now.day if is_current_month else days_in_month
    return [f"{month}-{d:02d}" for d in range(1, last_day + 1)]
