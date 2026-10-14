from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    due_date: date | None = None
    priority: Literal["low", "medium", "high"] = "medium"
    reminder_enabled: bool = False


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str | None
    due_date: date | None
    priority: Literal["low", "medium", "high"]
    reminder_enabled: bool
    status: Literal["pending", "done"]
    created_at: datetime
    updated_at: datetime
