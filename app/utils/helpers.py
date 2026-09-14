from datetime import date, datetime, time, timedelta
from app.models.habit import Frequency

def scheduled_on(habit, day: date) -> bool:
    if not habit.active or habit.archived:
        return False
    if day < habit.start_date:
        return False
    if habit.end_date and day > habit.end_date:
        return False
    if habit.frequency == Frequency.DAILY:
        return True
    if habit.frequency == Frequency.WEEKLY:
        return day.weekday() == habit.start_date.weekday()
    return day.weekday() in (habit.target_days or [])

def daterange(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)

def clamp(value: float, low=0.0, high=100.0) -> float:
    return max(low, min(high, value))

def combine_date_time(d: date, t: time | None) -> datetime | None:
    return datetime.combine(d, t) if t else None
