from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.achievement import Achievement, UserAchievement
from app.models.habit import Habit
from app.models.completion import HabitCompletion
from app.services.streak_service import streak_data

DEFAULTS = [
    ("STREAK_7","7 Day Streak","Maintain a 7-day streak.",50,"7-day"),
    ("STREAK_30","30 Day Streak","Maintain a 30-day streak.",200,"30-day"),
    ("COMPLETED_100","100 Habits Completed","Complete 100 scheduled habit days.",300,"100"),
    ("CONSISTENCY_MASTER","Consistency Master","Reach a 90% measured completion rate.",250,"master"),
]

def ensure_defaults(db):
    for code,name,desc,xp,badge in DEFAULTS:
        if not db.scalar(select(Achievement).where(Achievement.code==code)):
            db.add(Achievement(code=code,name=name,description=desc,xp=xp,badge=badge))
    db.commit()

def evaluate(db,user_id):
    ensure_defaults(db)
    habits=db.scalars(select(Habit).where(Habit.user_id==user_id)).all()
    total=db.scalar(select(HabitCompletion.id).join(Habit).where(Habit.user_id==user_id).count()) if False else len(db.scalars(select(HabitCompletion.id).join(Habit).where(Habit.user_id==user_id)).all())
    max_streak=max([streak_data(db,h)["longest_streak"] for h in habits],default=0)
    rates=[streak_data(db,h)["completion_rate"] for h in habits]
    for a in db.scalars(select(Achievement)).all():
        ok=(a.code=="STREAK_7" and max_streak>=7) or (a.code=="STREAK_30" and max_streak>=30) or (a.code=="COMPLETED_100" and total>=100) or (a.code=="CONSISTENCY_MASTER" and rates and sum(rates)/len(rates)>=90)
        exists=db.scalar(select(UserAchievement).where(UserAchievement.user_id==user_id,UserAchievement.achievement_id==a.id))
        if ok and not exists:
            db.add(UserAchievement(user_id=user_id,achievement_id=a.id,unlocked_at=datetime.now(timezone.utc)))
    db.commit()
