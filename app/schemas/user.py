from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Mapped, mapped_column, relationship

class UserUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=50)
    email: EmailStr | None = None
