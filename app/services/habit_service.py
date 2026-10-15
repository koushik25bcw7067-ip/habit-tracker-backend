from sqlalchemy import select
from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.models.habit import Habit
from app.models.category import Category
from app.models.completion import HabitCompletion
from app.schemas.completion import CompletionCreate
from app.schemas.habit import HabitCreate, HabitUpdate
from app.services.achievement_service import evaluate
from app.utils.helpers import scheduled_on

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

def create(db: Session, user_id: int, data: HabitCreate, commit: bool = True):
    if data.frequency.value == "custom" and not data.target_days:
        raise HTTPException(status_code=400, detail="Custom frequency requires target_days")
    if any(d < 0 or d > 6 for d in data.target_days):
        raise HTTPException(status_code=400, detail="target_days must use 0=Monday through 6=Sunday")
    validate_category(db, user_id, data.category_id)
    habit = Habit(user_id=user_id, **data.model_dump())
    db.add(habit)
    if commit:
        db.commit()
        db.refresh(habit)
    else:
        db.flush()
    return habit

def update(
    db: Session,
    user_id: int,
    habit_id: int,
    data: HabitUpdate,
    commit: bool = True,
):
    habit = get_owned_habit(db, user_id, habit_id)
    payload = data.model_dump(exclude_unset=True)
    if "category_id" in payload:
        validate_category(db, user_id, payload["category_id"])
    frequency = payload.get("frequency", habit.frequency)
    target_days = payload.get("target_days", habit.target_days)
    if frequency is None:
        raise HTTPException(status_code=400, detail="frequency cannot be blank")
    if target_days is None:
        if frequency.value == "custom":
            raise HTTPException(status_code=400, detail="Custom frequency requires target_days")
        target_days = []
        payload["target_days"] = target_days
    if frequency.value == "custom" and not target_days:
        raise HTTPException(status_code=400, detail="Custom frequency requires target_days")
    if any(day < 0 or day > 6 for day in target_days):
        raise HTTPException(status_code=400, detail="target_days must use 0=Monday through 6=Sunday")
    for k,v in payload.items(): setattr(habit,k,v)
    if commit:
        db.commit()
        db.refresh(habit)
    else:
        db.flush()
    return habit

def complete(
    db: Session,
    user_id: int,
    habit_id: int,
    data: CompletionCreate,
    commit: bool = True,
):
    habit = get_owned_habit(db, user_id, habit_id)
    if not habit.active or habit.archived:
        raise HTTPException(400, "Habit is not active")
    if not scheduled_on(habit, data.completion_date):
        raise HTTPException(400, "Habit is not scheduled for that date")
    existing = db.scalar(
        select(HabitCompletion).where(
            HabitCompletion.habit_id == habit_id,
            HabitCompletion.completion_date == data.completion_date,
        )
    )
    if existing:
        raise HTTPException(409, "Habit is already completed for this date")
    completion = HabitCompletion(habit_id=habit_id, **data.model_dump())
    db.add(completion)
    if commit:
        db.commit()
        db.refresh(completion)
    else:
        db.flush()
    evaluate(db, user_id, commit=commit)
    return completion
