from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database.dependencies import get_db
from app.models.habit import Habit, Frequency, Priority
from app.schemas.habit import HabitCreate,HabitUpdate,HabitResponse
from app.routers.users import current_user
from app.services.habit_service import create,update,get_owned_habit
router=APIRouter(prefix="/habits",tags=["Habits"])

@router.post("",response_model=HabitResponse,status_code=201)
def create_habit(data:HabitCreate,user=Depends(current_user),db:Session=Depends(get_db)): return create(db,user.id,data)

@router.get("",response_model=list[HabitResponse])
def list_habits(search:str|None=None,category_id:int|None=None,active:bool|None=None,frequency:Frequency|None=None,priority:Priority|None=None,user=Depends(current_user),db:Session=Depends(get_db)):
    q=select(Habit).where(Habit.user_id==user.id)
    if search:q=q.where(Habit.name.ilike(f"%{search}%"))
    if category_id is not None:q=q.where(Habit.category_id==category_id)
    if active is not None:q=q.where(Habit.active==active)
    if frequency:q=q.where(Habit.frequency==frequency)
    if priority:q=q.where(Habit.priority==priority)
    return db.scalars(q.order_by(Habit.created_at.desc())).all()

@router.get("/{habit_id}",response_model=HabitResponse)
def get_habit(habit_id:int,user=Depends(current_user),db:Session=Depends(get_db)): return get_owned_habit(db,user.id,habit_id)

@router.put("/{habit_id}",response_model=HabitResponse)
def put_habit(habit_id:int,data:HabitUpdate,user=Depends(current_user),db:Session=Depends(get_db)): return update(db,user.id,habit_id,data)

@router.delete("/{habit_id}")
def delete_habit(habit_id:int,user=Depends(current_user),db:Session=Depends(get_db)):
    h=get_owned_habit(db,user.id,habit_id);db.delete(h);db.commit();return {"message":"Habit deleted"}

@router.post("/{habit_id}/archive",response_model=HabitResponse)
def archive(habit_id:int,user=Depends(current_user),db:Session=Depends(get_db)):
    h=get_owned_habit(db,user.id,habit_id);h.archived=True;h.active=False;db.commit();db.refresh(h);return h

@router.post("/{habit_id}/restore",response_model=HabitResponse)
def restore(habit_id:int,user=Depends(current_user),db:Session=Depends(get_db)):
    h=get_owned_habit(db,user.id,habit_id);h.archived=False;h.active=True;db.commit();db.refresh(h);return h

@router.post("/{habit_id}/activate",response_model=HabitResponse)
def activate(habit_id:int,user=Depends(current_user),db:Session=Depends(get_db)):
    h=get_owned_habit(db,user.id,habit_id);h.active=True;db.commit();db.refresh(h);return h

@router.post("/{habit_id}/deactivate",response_model=HabitResponse)
def deactivate(habit_id:int,user=Depends(current_user),db:Session=Depends(get_db)):
    h=get_owned_habit(db,user.id,habit_id);h.active=False;db.commit();db.refresh(h);return h
