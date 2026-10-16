from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PairCodeCreate(BaseModel):
    name: str = Field(default="Habit Companion", min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Device name cannot be blank")
        return value


class PairCodeResponse(BaseModel):
    device_id: int
    pairing_code: str
    expires_at: datetime


class DeviceClaim(BaseModel):
    pairing_code: str = Field(pattern=r"^[A-F0-9]{12}$")
    credential: str = Field(pattern=r"^[A-Za-z0-9_-]{43,128}$")


class CompanionDeviceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    paired_at: datetime | None
    last_seen_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class VoiceIntent(BaseModel):
    transcript: str = Field(min_length=1, max_length=1000)
    intent: Literal[
        "chat",
        "create_task",
        "update_task",
        "complete_task",
        "delete_task",
        "complete_habit",
        "create_habit",
        "update_habit",
        "list_tasks",
        "list_habits",
        "weekly_progress",
        "confirm",
        "cancel",
        "unclear",
    ]
    target_id: int | None = Field(default=None, gt=0)
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    due_date: date | None = None
    priority: Literal["low", "medium", "high"] | None = None
    frequency: Literal["daily", "weekly", "custom"] | None = None
    target_days: list[int] | None = None
    start_date: date | None = None
    end_date: date | None = None
    category_id: int | None = Field(default=None, gt=0)
    target_value: float | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, max_length=30)

    @model_validator(mode="after")
    def validate_target_days(self):
        if self.target_days is not None and any(
            day < 0 or day > 6 for day in self.target_days
        ):
            raise ValueError("target_days must use 0=Monday through 6=Sunday")
        return self


class CompanionChangesResponse(BaseModel):
    action_at: datetime | None
    action_type: str | None
