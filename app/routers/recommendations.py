from datetime import date,timedelta
from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database.dependencies import get_db
from app.routers.users import current_user
from app.models.recommendation import Recommendation,RecommendationStatus
from app.models.habit import Habit
from app.services.recommendation_service import generate_all,generate_habit_recommendations,best_time,weekly_plan
from app.services.statistics_service import period_statistics
router=APIRouter(prefix="/recommendations",tags=["Recommendations"])

@router.get("")
def all_recommendations(user=Depends(current_user),db:Session=Depends(get_db)):
    generate_all(db,user.id)
    return db.scalars(select(Recommendation).where(Recommendation.user_id==user.id).order_by(Recommendation.created_at.desc()).limit(50)).all()

@router.get("/habits/{habit_id}")
def habit_recommendations(habit_id:int,user=Depends(current_user),db:Session=Depends(get_db)):
    h=db.scalar(select(Habit).where(Habit.id==habit_id,Habit.user_id==user.id))
    if not h: raise HTTPException(404,"Habit not found")
    generate_habit_recommendations(db,user.id,h)
    return db.scalars(select(Recommendation).where(Recommendation.user_id==user.id,Recommendation.habit_id==habit_id).order_by(Recommendation.created_at.desc()).limit(20)).all()

@router.get("/weekly")
def weekly(user=Depends(current_user),db:Session=Depends(get_db)): return weekly_plan(db,user.id)

@router.get("/monthly")
def monthly(user=Depends(current_user),db:Session=Depends(get_db)):
    return period_statistics(db,user.id,date.today()-timedelta(days=29),date.today())

@router.get("/schedule")
def schedule(user=Depends(current_user),db:Session=Depends(get_db)):
    habits=db.scalars(select(Habit).where(Habit.user_id==user.id,Habit.active.is_(True))).all()
    return [{"habit_id":h.id,"name":h.name,"best_time":best_time(db,h)} for h in habits]

@router.post("/{recommendation_id}/accept")
def accept(recommendation_id:int,user=Depends(current_user),db:Session=Depends(get_db)):
    r=db.scalar(select(Recommendation).where(Recommendation.id==recommendation_id,Recommendation.user_id==user.id))
    if not r: raise HTTPException(404,"Recommendation not found")
    r.status=RecommendationStatus.ACCEPTED;db.commit();return r

@router.post("/{recommendation_id}/reject")
def reject(recommendation_id:int,user=Depends(current_user),db:Session=Depends(get_db)):
    r=db.scalar(select(Recommendation).where(Recommendation.id==recommendation_id,Recommendation.user_id==user.id))
    if not r: raise HTTPException(404,"Recommendation not found")
    r.status=RecommendationStatus.REJECTED;db.commit();return r
