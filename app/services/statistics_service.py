from datetime import date, timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.completion import HabitCompletion
from app.models.habit import Habit
from app.utils.helpers import scheduled_on, clamp
from app.services.streak_service import streak_data

def day_stat(db: Session, habits: list[Habit], day: date):
    planned_habits = [h for h in habits if scheduled_on(h, day)]
    ids = [h.id for h in planned_habits]
    completed_ids = set()
    if ids:
        completed_ids = set(db.scalars(select(HabitCompletion.habit_id).where(
            HabitCompletion.habit_id.in_(ids), HabitCompletion.completion_date == day
        )).all())
    planned, completed = len(planned_habits), len(completed_ids)
    missed = planned - completed
    pct = round(completed / planned * 100, 2) if planned else 100.0
    return {"date": day, "planned": planned, "completed": completed, "missed": missed, "completion_percentage": pct}

def consistency_score(rate: float, recent_rate: float, streak: int, missed: int, goal: float | None):
    streak_factor = min(streak / 14, 1) * 100
    missed_factor = max(0, 100 - min(missed * 5, 100))
    goal_factor = 100 if goal is None else clamp(goal)
    return round(clamp(0.35*rate + 0.25*recent_rate + 0.15*streak_factor + 0.10*missed_factor + 0.15*goal_factor), 2)

def habit_statistics(db: Session, habit: Habit, end: date | None = None):
    end = end or date.today()
    start = max(habit.start_date, end - timedelta(days=29))
    history = []
    for d in _daterange(start, end):
        history.append(day_stat(db, [habit], d))
    weekly = history[-7:]
    streak = streak_data(db, habit, start, end)
    recent_rate = round(sum(x["completion_percentage"] for x in weekly) / len(weekly), 2) if weekly else 0
    goal = habit.target_value
    goal_achievement = None
    if goal and goal > 0:
        vals = db.scalars(select(HabitCompletion.actual_value).where(
            HabitCompletion.habit_id == habit.id,
            HabitCompletion.completion_date >= start,
            HabitCompletion.completion_date <= end,
            HabitCompletion.actual_value.is_not(None)
        )).all()
        goal_achievement = round(min(100, (sum(vals) / (goal * max(1, streak["total_completions"]))) * 100), 2) if vals else 0
    score = consistency_score(streak["completion_rate"], recent_rate, streak["current_streak"], streak["missed_days"], goal_achievement)
    return {**streak, "habit_id": habit.id, "weekly_trend": weekly, "monthly_trend": history, "consistency_score": score, "goal_achievement": goal_achievement}

def period_statistics(db: Session, user_id: int, start: date, end: date):
    habits = db.scalars(select(Habit).where(Habit.user_id == user_id)).all()
    daily = [day_stat(db, habits, d) for d in _daterange(start, end)]
    total_planned = sum(x["planned"] for x in daily)
    total_completed = sum(x["completed"] for x in daily)
    total_missed = sum(x["missed"] for x in daily)
    rates = [x["completion_percentage"] for x in daily if x["planned"]]
    per_habit = []
    longest = 0
    for h in habits:
        s = habit_statistics(db, h, end)
        per_habit.append({"habit_id": h.id, "name": h.name, "completion_rate": s["completion_rate"], "streak": s["current_streak"]})
        longest = max(longest, s["longest_streak"])
    best = max(per_habit, key=lambda x: x["completion_rate"], default=None)
    worst = min(per_habit, key=lambda x: x["completion_rate"], default=None)
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - (end-start)
    prev_daily = [day_stat(db, habits, d) for d in _daterange(previous_start, previous_end)]
    prev_rate = sum(x["completed"] for x in prev_daily) / sum(x["planned"] for x in prev_daily) * 100 if sum(x["planned"] for x in prev_daily) else 0
    current_rate = total_completed / total_planned * 100 if total_planned else 0
    return {
        "start_date": start, "end_date": end, "total_completions": total_completed,
        "total_missed": total_missed, "completion_rate": round(current_rate,2),
        "best_habit": best, "worst_habit": worst, "longest_streak": longest,
        "average_completion_rate": round(sum(rates)/len(rates),2) if rates else 0,
        "trend_vs_previous_period": round(current_rate-prev_rate,2), "daily": daily
    }

def _daterange(start, end):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)
