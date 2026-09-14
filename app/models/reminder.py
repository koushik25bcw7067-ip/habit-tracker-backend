from datetime import datetime, time, timezone
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, Time
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database.database import Base

class Reminder(Base):
    __tablename__ = "reminders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    habit_id: Mapped[int] = mapped_column(ForeignKey("habits.id", ondelete="CASCADE"), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reminder_time: Mapped[time] = mapped_column(Time, nullable=False)
    reminder_days: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    user = relationship("User", back_populates="reminders")
    habit = relationship("Habit", back_populates="reminders")
