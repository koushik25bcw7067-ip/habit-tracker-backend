from datetime import date, datetime, time, timezone
from enum import Enum
from sqlalchemy import Boolean, Date, DateTime, Enum as SAEnum, ForeignKey, Integer, String, Text, Time, Float, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database.database import Base

class Frequency(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    CUSTOM = "custom"

class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

class Habit(Base):
    __tablename__ = "habits"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    frequency: Mapped[Frequency] = mapped_column(SAEnum(Frequency, name="frequency_enum"), default=Frequency.DAILY, nullable=False)
    target_days: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    reminder_time: Mapped[time | None] = mapped_column(Time)
    start_date: Mapped[date] = mapped_column(Date, default=date.today, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)
    priority: Mapped[Priority] = mapped_column(SAEnum(Priority, name="priority_enum"), default=Priority.MEDIUM, nullable=False)
    color: Mapped[str | None] = mapped_column(String(20))
    icon: Mapped[str | None] = mapped_column(String(50))
    target_value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(30))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    user = relationship("User", back_populates="habits")
    category = relationship("Category", back_populates="habits")
    completions = relationship("HabitCompletion", back_populates="habit", cascade="all, delete-orphan")
    goals = relationship("HabitGoal", back_populates="habit", cascade="all, delete-orphan")
    reminders = relationship("Reminder", back_populates="habit", cascade="all, delete-orphan")
