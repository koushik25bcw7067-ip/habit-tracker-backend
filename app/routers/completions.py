from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database.dependencies import get_db
from app.models.completion import HabitCompletion
from app.schemas.completion import CompletionCreate,CompletionResponse
from app.routers.users import current_user
from app.services.habit_service import get_owned_habit
from app.services.achievement_service import evaluate
router=APIRouter(prefix="/habits",tags=["Completion"])

@router.post("/{habit_id}/complete",response_model=CompletionResponse,status_code=201)
def complete(habit_id:int,data:CompletionCreate,user=Depends(current_user),db:Session=Depends(get_db)):
    h=get_owned_habit(db,user.id,habit_id)
    if not h.active or h.archived: raise HTTPException(400,"Habit is not active")
    from app.utils.helpers import scheduled_on
    if not scheduled_on(h,data.completion_date): raise HTTPException(400,"Habit is not scheduled for that date")
    if db.scalar(select(HabitCompletion).where(HabitCompletion.habit_id==habit_id,HabitCompletion.completion_date==data.completion_date)):
        raise HTTPException(409,"Habit is already completed for this date")
    c=HabitCompletion(habit_id=habit_id,**data.model_dump());db.add(c);db.commit();db.refresh(c)
    evaluate(db,user.id)
    return c

@router.delete("/{habit_id}/complete/{completion_date}")
def undo(habit_id:int,completion_date:date,user=Depends(current_user),db:Session=Depends(get_db)):
    get_owned_habit(db,user.id,habit_id)
    c=db.scalar(select(HabitCompletion).where(HabitCompletion.habit_id==habit_id,HabitCompletion.completion_date==completion_date))
    if not c: raise HTTPException(404,"Completion not found")
    db.delete(c);db.commit();return {"message":"Completion removed"}
