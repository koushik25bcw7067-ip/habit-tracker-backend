from datetime import date, datetime, timedelta, time
from collections import Counter
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.habit import Habit, Frequency, Priority
from app.models.completion import HabitCompletion
from app.models.recommendation import Recommendation, RecommendationType
from app.services.statistics_service import habit_statistics, period_statistics
from app.utils.helpers import scheduled_on

def generate_habit_recommendations(db: Session, user_id: int, habit: Habit):
    s = habit_statistics(db, habit)
    recs = []
    if s["total_completions"] < 7:
        return recs
    if s["completion_rate"] >= 90:
        recs.append(_rec(db, user_id, habit, RecommendationType.TARGET_ADJUSTMENT, "LOW", "MEDIUM",
            "Your habit may be ready for a slightly higher target",
            f"{habit.name} is at {s['completion_rate']}% completion over the measured period.",
            "A small target increase may provide additional challenge.", "Consider increasing the target slightly."))
    elif s["completion_rate"] < 40:
        recs.append(_rec(db, user_id, habit, RecommendationType.HABIT_SIMPLIFICATION, "HIGH", "HIGH",
            "Simplify this habit",
            f"{habit.name} is at {s['completion_rate']}% completion.",
            "A smaller target may be easier to maintain.", "Consider reducing the target or splitting the habit into smaller steps."))
    if s["current_streak"] == 0 and s["longest_streak"] >= 7:
        recs.append(_rec(db, user_id, habit, RecommendationType.HABIT_RECOVERY, "MEDIUM", "HIGH",
            "Restart normally",
            f"You previously maintained a {s['longest_streak']}-day streak.",
            "A normal restart avoids compensating for missed days.", "Resume the normal target rather than trying to compensate."))
    return recs

def generate_all(db, user_id):
    habits = db.scalars(select(Habit).where(Habit.user_id == user_id, Habit.active.is_(True), Habit.archived.is_(False))).all()
    out=[]
    for h in habits: out.extend(generate_habit_recommendations(db,user_id,h))
    # overload is data-driven: no arbitrary hard maximum
    recent_start = date.today() - timedelta(days=13)
    old_start = date.today() - timedelta(days=27)
    recent = period_statistics(db,user_id,recent_start,date.today())
    old = period_statistics(db,user_id,old_start,date.today()-timedelta(days=14))
    if len(habits) >= 2 and recent["completion_rate"] + 10 < old["completion_rate"]:
        out.append(_rec(db,user_id,None,RecommendationType.HABIT_OVERLOAD,"MEDIUM","MEDIUM",
            "Your active habit load may be affecting consistency",
            f"You have {len(habits)} active habits and completion fell from {old['completion_rate']}% to {recent['completion_rate']}%.",
            "Focusing on high-priority habits may improve consistency.",
            "Review your highest-priority habits before adding more."))
    return out

def _rec(db,user_id,habit,rtype,priority,confidence,title,reason,benefit,action):
    r=Recommendation(user_id=user_id,habit_id=habit.id if habit else None,type=rtype,priority=priority,
                     confidence=confidence,title=title,reason=reason,expected_benefit=benefit,action=action)
    db.add(r); db.commit(); db.refresh(r); return r

def best_time(db, habit):
    rows=db.scalars(select(HabitCompletion).where(HabitCompletion.habit_id==habit.id).order_by(HabitCompletion.completion_date)).all()
    if len(rows)<7: return None
    # completed_at is the best available historical completion-time signal
    buckets=Counter(r.completed_at.hour for r in rows)
    hour,count=buckets.most_common(1)[0]
    return {"start_hour":hour, "end_hour":(hour+2)%24, "sample_size":len(rows), "confidence":"MEDIUM" if len(rows)>=14 else "LOW"}

def weekly_plan(db,user_id):
    stats=period_statistics(db,user_id,date.today()-timedelta(days=6),date.today())
    return {"overall_rate":stats["completion_rate"],"focus":stats["best_habit"],"improvement":stats["worst_habit"],
            "plan":["Protect time for the highest-priority habit.","Keep targets unchanged unless data supports an adjustment.","Review missed days at the end of the week."]}

def _to_dict(r): return {"id":r.id,"habit_id":r.habit_id,"type":r.type,"priority":r.priority,"confidence":r.confidence,
"title":r.title,"reason":r.reason,"expected_benefit":r.expected_benefit,"action":r.action,"status":r.status}
