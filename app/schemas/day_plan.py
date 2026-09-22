from datetime import date, time as dt_time
from enum import Enum

from pydantic import BaseModel, ConfigDict


class DayPlanItemType(str, Enum):
    TASK = "task"
    HABIT = "habit"
    PERSONAL = "personal"


class DayPlanPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DayPlanItemCreate(BaseModel):
    time: dt_time | None = None
    title: str
    type: DayPlanItemType
    priority: DayPlanPriority
    reminder: bool = False


class DayPlanCreate(BaseModel):
    plan_date: date
    title: str
    main_goal: str | None = None
    items: list[DayPlanItemCreate] = []


class DayPlanItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    time: dt_time | None
    title: str
    type: DayPlanItemType
    priority: DayPlanPriority
    reminder: bool
    completed: bool


class DayPlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    plan_date: date
    title: str
    main_goal: str | None
    items: list[DayPlanItemResponse]