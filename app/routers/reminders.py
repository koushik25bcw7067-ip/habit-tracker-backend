from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database.dependencies import get_db
from app.routers.users import current_user
from app.models.reminder import Reminder
from app.models.habit import Habit
from pydantic import BaseModel,Field
from datetime import time
router=APIRouter(prefix="/reminders",tags=["Reminders"])

class ReminderIn(BaseModel):
    habit_id:int
    enabled:bool=True
    reminder_time:time
    reminder_days:list[int]=Field(default_factory=list)

@router.get("")
def list_reminders(user=Depends(current_user),db:Session=Depends(get_db)):
    return db.scalars(select(Reminder).where(Reminder.user_id==user.id)).all()

@router.post("",status_code=201)
def create_reminder(data:ReminderIn,user=Depends(current_user),db:Session=Depends(get_db)):
    h=db.scalar(select(Habit).where(Habit.id==data.habit_id,Habit.user_id==user.id))
    if not h: raise HTTPException(404,"Habit not found")
    r=Reminder(user_id=user.id,**data.model_dump());db.add(r);db.commit();db.refresh(r);return r

@router.put("/{reminder_id}")
def update_reminder(reminder_id:int,data:ReminderIn,user=Depends(current_user),db:Session=Depends(get_db)):
    r=db.scalar(select(Reminder).where(Reminder.id==reminder_id,Reminder.user_id==user.id))
    if not r: raise HTTPException(404,"Reminder not found")
    for k,v in data.model_dump().items(): setattr(r,k,v)
    r.user_id=user.id;db.commit();db.refresh(r);return r

@router.delete("/{reminder_id}")
def delete_reminder(reminder_id:int,user=Depends(current_user),db:Session=Depends(get_db)):
    r=db.scalar(select(Reminder).where(Reminder.id==reminder_id,Reminder.user_id==user.id))
    if not r: raise HTTPException(404,"Reminder not found")
    db.delete(r);db.commit();return {"message":"Reminder deleted"}
