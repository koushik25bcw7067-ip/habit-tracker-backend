from sqlalchemy import select
from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.models.habit import Habit
from app.models.category import Category
from app.schemas.habit import HabitCreate, HabitUpdate

def get_owned_habit(db: Session, user_id: int, habit_id: int) -> Habit:
    habit = db.scalar(select(Habit).where(Habit.id == habit_id, Habit.user_id == user_id))
    if not habit:
        raise HTTPException(status_code=404, detail="Habit not found")
    return habit

def validate_category(db, user_id, category_id):
    if category_id is None:
        return
    category = db.scalar(select(Category).where(Category.id == category_id, (Category.user_id == user_id) | (Category.user_id.is_(None))))
    if not category:
        raise HTTPException(status_code=400, detail="Invalid category")

def create(db: Session, user_id: int, data: HabitCreate):
    if data.frequency.value == "custom" and not data.target_days:
        raise HTTPException(status_code=400, detail="Custom frequency requires target_days")
    if any(d < 0 or d > 6 for d in data.target_days):
        raise HTTPException(status_code=400, detail="target_days must use 0=Monday through 6=Sunday")
    validate_category(db, user_id, data.category_id)
    habit = Habit(user_id=user_id, **data.model_dump())
    db.add(habit); db.commit(); db.refresh(habit)
    return habit

def update(db: Session, user_id: int, habit_id: int, data: HabitUpdate):
    habit = get_owned_habit(db, user_id, habit_id)
    payload = data.model_dump(exclude_unset=True)
    if "category_id" in payload:
        validate_category(db, user_id, payload["category_id"])
    for k,v in payload.items(): setattr(habit,k,v)
    db.commit(); db.refresh(habit)
    return habit
