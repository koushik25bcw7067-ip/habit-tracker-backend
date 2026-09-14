from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database.dependencies import get_db
from app.models.goal import HabitGoal
from app.models.habit import Habit
from app.routers.users import current_user

router = APIRouter(prefix="/goals", tags=["Goals"])

class GoalIn(BaseModel):
    habit_id: int
    target_value: float = Field(gt=0)
    unit: str | None = Field(default=None, max_length=30)
    active: bool = True

@router.get("")
def list_goals(user=Depends(current_user), db: Session=Depends(get_db)):
    return db.scalars(select(HabitGoal).where(HabitGoal.user_id == user.id)).all()

@router.post("", status_code=201)
def create_goal(data: GoalIn, user=Depends(current_user), db: Session=Depends(get_db)):
    habit = db.scalar(select(Habit).where(Habit.id == data.habit_id, Habit.user_id == user.id))
    if not habit:
        raise HTTPException(404, "Habit not found")
    if db.scalar(select(HabitGoal).where(HabitGoal.habit_id == data.habit_id)):
        raise HTTPException(409, "A goal already exists for this habit")
    goal = HabitGoal(user_id=user.id, **data.model_dump())
    db.add(goal); db.commit(); db.refresh(goal)
    return goal

@router.put("/{goal_id}")
def update_goal(goal_id: int, data: GoalIn, user=Depends(current_user), db: Session=Depends(get_db)):
    goal = db.scalar(select(HabitGoal).where(HabitGoal.id == goal_id, HabitGoal.user_id == user.id))
    if not goal:
        raise HTTPException(404, "Goal not found")
    habit = db.scalar(select(Habit).where(Habit.id == data.habit_id, Habit.user_id == user.id))
    if not habit:
        raise HTTPException(404, "Habit not found")
    for k, v in data.model_dump().items():
        setattr(goal, k, v)
    db.commit(); db.refresh(goal)
    return goal

@router.delete("/{goal_id}")
def delete_goal(goal_id: int, user=Depends(current_user), db: Session=Depends(get_db)):
    goal = db.scalar(select(HabitGoal).where(HabitGoal.id == goal_id, HabitGoal.user_id == user.id))
    if not goal:
        raise HTTPException(404, "Goal not found")
    db.delete(goal); db.commit()
    return {"message": "Goal deleted"}
