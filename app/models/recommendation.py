from datetime import datetime, timezone
from enum import Enum
from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Integer, String, Text, JSON, Float
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database.database import Base

class RecommendationStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"

class RecommendationType(str, Enum):
    TARGET_ADJUSTMENT = "TARGET_ADJUSTMENT"
    REMINDER_TIME = "REMINDER_TIME"
    SCHEDULE_CHANGE = "SCHEDULE_CHANGE"
    HABIT_PRIORITY = "HABIT_PRIORITY"
    HABIT_SIMPLIFICATION = "HABIT_SIMPLIFICATION"
    HABIT_RECOVERY = "HABIT_RECOVERY"
    NEW_HABIT = "NEW_HABIT"
    HABIT_OVERLOAD = "HABIT_OVERLOAD"
    CATEGORY_FOCUS = "CATEGORY_FOCUS"
    WEEKLY_PLAN = "WEEKLY_PLAN"
    MONTHLY_PLAN = "MONTHLY_PLAN"

class Recommendation(Base):
    __tablename__ = "recommendations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    habit_id: Mapped[int | None] = mapped_column(ForeignKey("habits.id", ondelete="CASCADE"), nullable=True)
    type: Mapped[RecommendationType] = mapped_column(SAEnum(RecommendationType, name="recommendation_type_enum"), nullable=False)
    priority: Mapped[str] = mapped_column(String(10), nullable=False)
    confidence: Mapped[str] = mapped_column(String(10), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    expected_benefit: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[RecommendationStatus] = mapped_column(SAEnum(RecommendationStatus, name="recommendation_status_enum"), default=RecommendationStatus.PENDING, nullable=False)
    baseline_rate: Mapped[float | None] = mapped_column(Float)
    outcome_rate: Mapped[float | None] = mapped_column(Float)
    outcome_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    user = relationship("User", back_populates="recommendations")
