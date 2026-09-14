from datetime import date, timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.completion import HabitCompletion
from app.utils.helpers import scheduled_on

def streak_data(db: Session, habit, start: date | None = None, end: date | None = None):
    start = start or habit.start_date
    end = end or date.today()
    rows = db.scalars(select(HabitCompletion).where(
        HabitCompletion.habit_id == habit.id,
        HabitCompletion.completion_date >= start,
        HabitCompletion.completion_date <= end
    )).all()
    completed = {r.completion_date for r in rows}
    planned_days = [d for d in _daterange(start, end) if scheduled_on(habit, d)]
    current = 0
    d = end
    while d >= start and scheduled_on(habit, d):
        if d in completed:
            current += 1
        else:
            break
        d -= timedelta(days=1)
        while d >= start and not scheduled_on(habit, d):
            d -= timedelta(days=1)
    longest = 0
    run = 0
    for d in planned_days:
        if d in completed:
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    total = len(completed.intersection(set(planned_days)))
    missed = max(0, len(planned_days) - total)
    rate = round(total / len(planned_days) * 100, 2) if planned_days else 0.0
    return {"current_streak": current, "longest_streak": longest, "total_completions": total, "missed_days": missed, "completion_rate": rate}

def _daterange(start, end):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)
