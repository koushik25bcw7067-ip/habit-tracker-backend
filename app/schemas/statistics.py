from datetime import date
from pydantic import BaseModel

class DayStat(BaseModel):
    date: date
    planned: int
    completed: int
    missed: int
    completion_percentage: float

class DailyStats(DayStat):
    remaining: int

class HabitStats(BaseModel):
    habit_id: int
    current_streak: int
    longest_streak: int
    total_completions: int
    missed_days: int
    completion_rate: float
    weekly_trend: list[DayStat]
    monthly_trend: list[DayStat]
    consistency_score: float
    goal_achievement: float | None

class PeriodStats(BaseModel):
    start_date: date
    end_date: date
    total_completions: int
    total_missed: int
    completion_rate: float
    best_habit: dict | None
    worst_habit: dict | None
    longest_streak: int
    average_completion_rate: float
    trend_vs_previous_period: float
    daily: list[DayStat]
