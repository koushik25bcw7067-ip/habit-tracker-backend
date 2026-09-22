from datetime import date, datetime, time, timezone
from enum import Enum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base


class DayPlanItemType(str, Enum):
    TASK = "task"
    HABIT = "habit"
    PERSONAL = "personal"


class DayPlanPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DayPlan(Base):
    __tablename__ = "day_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    plan_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(
        String(150),
        default="My Day Plan",
        nullable=False,
    )

    main_goal: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    user = relationship(
        "User",
        back_populates="day_plans",
    )

    items = relationship(
        "DayPlanItem",
        back_populates="day_plan",
        cascade="all, delete-orphan",
        order_by="DayPlanItem.time",
    )


class DayPlanItem(Base):
    __tablename__ = "day_plan_items"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    day_plan_id: Mapped[int] = mapped_column(
        ForeignKey("day_plans.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    time: Mapped[time | None] = mapped_column(
        Time,
        nullable=True,
    )

    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )

    type: Mapped[DayPlanItemType] = mapped_column(
        SAEnum(
            DayPlanItemType,
            name="day_plan_item_type_enum",
        ),
        default=DayPlanItemType.TASK,
        nullable=False,
    )

    priority: Mapped[DayPlanPriority] = mapped_column(
        SAEnum(
            DayPlanPriority,
            name="day_plan_priority_enum",
        ),
        default=DayPlanPriority.MEDIUM,
        nullable=False,
    )

    reminder: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    completed: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    day_plan = relationship(
        "DayPlan",
        back_populates="items",
    )