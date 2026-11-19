"""Time ranges for local listening statistics."""

from __future__ import annotations

from datetime import datetime, time, timedelta


STAT_PERIODS = (
    ("today", "Today"),
    ("week", "This week"),
    ("month", "This month"),
    ("year", "This year"),
    ("all_time", "All time"),
)


def period_start(period: str, now: datetime | None = None):
    if period not in {key for key, _ in STAT_PERIODS}:
        raise ValueError(f"Unknown listening statistics period: {period}")
    if period == "all_time":
        return None

    current = now or datetime.now().astimezone()
    current_day = current.date()
    if period == "today":
        start_day = current_day
    elif period == "week":
        start_day = current_day - timedelta(days=current_day.weekday())
    elif period == "month":
        start_day = current_day.replace(day=1)
    else:
        start_day = current_day.replace(month=1, day=1)
    return datetime.combine(start_day, time.min, tzinfo=current.tzinfo).isoformat(
        timespec="seconds"
    )
