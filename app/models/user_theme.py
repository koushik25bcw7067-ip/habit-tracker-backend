from sqlalchemy import ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.database import Base


class UserTheme(Base):
    __tablename__ = "user_themes"
    __table_args__ = (UniqueConstraint("user_id", name="uq_user_themes_user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    preset_key: Mapped[str | None] = mapped_column(String(40), nullable=True)
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
