from datetime import date
from pydantic import BaseModel, Field, ConfigDict

class CompletionCreate(BaseModel):
    completion_date: date
    actual_value: float | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=1000)

class CompletionResponse(CompletionCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    habit_id: int
    completed_at: object
