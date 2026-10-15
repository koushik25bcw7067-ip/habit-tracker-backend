import hashlib
import hmac
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.models.companion_device import CompanionDevice


_bearer = HTTPBearer(auto_error=False)
_rate_lock = threading.Lock()
_rate_events: dict[str, deque[float]] = defaultdict(deque)
_RATE_WINDOW_SECONDS = 60


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def enforce_rate_limit(key: str, limit: int) -> None:
    now = time.monotonic()
    with _rate_lock:
        if len(_rate_events) > 4096:
            stale_keys = [
                event_key
                for event_key, events in _rate_events.items()
                if not events or now - events[-1] >= _RATE_WINDOW_SECONDS
            ]
            for event_key in stale_keys:
                _rate_events.pop(event_key, None)
        events = _rate_events[key]
        while events and now - events[0] >= _RATE_WINDOW_SECONDS:
            events.popleft()
        if len(events) >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please try again shortly.",
            )
        events.append(now)


def companion_device_dependency(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> CompanionDevice:
    host = request.client.host if request.client else "unknown"
    enforce_rate_limit(f"device-auth-ip:{host}", 60)
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Device authentication required")
    digest = token_hash(credentials.credentials)
    device = db.scalar(
        select(CompanionDevice).where(CompanionDevice.credential_hash == digest)
    )
    if device is None or device.paired_at is None or device.revoked_at is not None:
        raise HTTPException(status_code=401, detail="Invalid device credential")
    enforce_rate_limit(f"device:{device.id}", 30)
    device.last_seen_at = datetime.now(timezone.utc)
    db.commit()
    return device


def constant_time_hash_matches(raw: str, expected_hash: str) -> bool:
    return hmac.compare_digest(token_hash(raw), expected_hash)
