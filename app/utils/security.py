from datetime import datetime, timedelta, timezone
from typing import Any
import hashlib
import secrets
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import HTTPException, status
from app.config import settings

ph = PasswordHasher()

def hash_password(password: str) -> str:
    return ph.hash(password)

def verify_password(password: str, password_hash: str) -> bool:
    try:
        return ph.verify(password_hash, password)
    except VerifyMismatchError:
        return False

def create_access_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "type": "access", "iat": now, "exp": now + timedelta(minutes=settings.access_token_expire_minutes)}
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")

def create_refresh_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "type": "refresh", "jti": secrets.token_hex(16), "iat": now, "exp": now + timedelta(days=settings.refresh_token_expire_days)}
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")

def decode_token(token: str, expected_type: str = "access") -> dict[str, Any]:
    try:
        if token.lower().startswith("bearer "):
            token = token.split(" ", 1)[1].strip()
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        if payload.get("type") != expected_type or not payload.get("sub"):
            raise ValueError
        return payload
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"DEBUG ERROR: {type(exc).__name__} - {str(exc)}") from exc

def token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
