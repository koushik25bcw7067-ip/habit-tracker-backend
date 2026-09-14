from sqlalchemy import select
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from app.models.user import User
from app.utils.security import hash_password, verify_password

def register(db: Session, username: str, email: str, password: str) -> User:
    if db.scalar(select(User).where(User.email == email.lower())):
        raise HTTPException(status_code=409, detail="Email already registered")
    if db.scalar(select(User).where(User.username == username)):
        raise HTTPException(status_code=409, detail="Username already taken")
    user = User(username=username, email=email.lower(), password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

def authenticate(db: Session, email: str, password: str) -> User:
    user = db.scalar(select(User).where(User.email == email.lower()))
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    return user
