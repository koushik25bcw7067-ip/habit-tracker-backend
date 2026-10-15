from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.database import Base


class CompanionDevice(Base):
    __tablename__ = "companion_devices"
    __table_args__ = (
        Index("ix_companion_devices_user_id", "user_id"),
        Index("ix_companion_devices_pairing_code_hash", "pairing_code_hash"),
        Index(
            "uq_companion_devices_user_web_session_hash",
            "user_id",
            "web_session_hash",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    pairing_code_hash: Mapped[str | None] = mapped_column(String(64))
    pairing_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    credential_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    paired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pending_action: Mapped[dict | None] = mapped_column(JSON)
    pending_action_id: Mapped[str | None] = mapped_column(String(64))
    pending_action_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    web_session_hash: Mapped[str | None] = mapped_column(String(64))
    last_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_action_type: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
