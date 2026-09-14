from datetime import date, time
from pydantic import BaseModel, Field, ConfigDict
from app.models.habit import Frequency, Priority

class HabitCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    category_id: int | None = None
    frequency: Frequency = Frequency.DAILY
    target_days: list[int] = Field(default_factory=list)
    reminder_time: time | None = None
    start_date: date = Field(default_factory=date.today)
    end_date: date | None = None
    priority: Priority = Priority.MEDIUM
    color: str | None = Field(default=None, max_length=20)
    icon: str | None = Field(default=None, max_length=50)
    target_value: float | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, max_length=30)
    active: bool = True

class HabitUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    category_id: int | None = None
    frequency: Frequency | None = None
    target_days: list[int] | None = None
    reminder_time: time | None = None
    start_date: date | None = None
    end_date: date | None = None
    priority: Priority | None = None
    color: str | None = None
    icon: str | None = None
    target_value: float | None = Field(default=None, ge=0)
    unit: str | None = None
    active: bool | None = None

class HabitResponse(HabitCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int
    archived: bool
    created_at: object
    updated_at: object
