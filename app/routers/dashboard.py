from datetime import date,timedelta
from fastapi import APIRouter,Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database.dependencies import get_db
from app.routers.users import current_user
from app.models.habit import Habit
from app.models.completion import HabitCompletion
from app.models.ai_insight import AIInsight
from app.services.statistics_service import day_stat,period_statistics
from app.services.streak_service import streak_data
from app.services.recommendation_service import generate_all
from app.services.achievement_service import evaluate
router=APIRouter(prefix="/dashboard",tags=["Dashboard"])

@router.get("")
def dashboard(user=Depends(current_user),db:Session=Depends(get_db)):
    habits=db.scalars(select(Habit).where(Habit.user_id==user.id,Habit.active.is_(True),Habit.archived.is_(False))).all()
    today=day_stat(db,habits,date.today())
    week=period_statistics(db,user.id,date.today()-timedelta(days=6),date.today())
    month=period_statistics(db,user.id,date.today()-timedelta(days=29),date.today())
    streaks=[streak_data(db,h) for h in habits]
    overall=max((x["current_streak"] for x in streaks),default=0)
    longest=max((x["longest_streak"] for x in streaks),default=0)
    recs=generate_all(db,user.id)
    evaluate(db,user.id)
    insight=db.scalar(select(AIInsight).where(AIInsight.user_id==user.id).order_by(AIInsight.created_at.desc()))
    recent=db.scalars(select(HabitCompletion).join(Habit).where(Habit.user_id==user.id).order_by(HabitCompletion.completed_at.desc()).limit(10)).all()
    return {"user_name":user.username,"today":date.today(),"today_habits":habits,"completed_habits":today["completed"],
            "remaining_habits":today["planned"]-today["completed"],"completion_percentage":today["completion_percentage"],
            "current_overall_streak":overall,"longest_streak":longest,"weekly_statistics":week,"monthly_statistics":month,
            "recent_activity":[{"habit_id":c.habit_id,"date":c.completion_date,"actual_value":c.actual_value} for c in recent],
            "habit_performance":[{"habit_id":h.id,"name":h.name,"completion_rate":streak_data(db,h)["completion_rate"]} for h in habits],
            "consistency_score":round(sum(streak_data(db,h)["completion_rate"] for h in habits)/len(habits),2) if habits else 0,
            "important_ai_insight":insight.content if insight else None,"top_recommendation":recs[0] if recs else None}
