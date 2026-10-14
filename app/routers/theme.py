import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database.dependencies import get_db
from app.routers.users import current_user
from app.models.user_theme import UserTheme
from app.services.theme_service import (
    PRESETS, generate_custom, sanitize_theme, to_public, normalize_query,
)

router = APIRouter(prefix="/theme", tags=["Theme"])

# Existing endpoints (unchanged paths/semantics):
#   GET  /theme/presets      GET /theme      PUT /theme/preset      POST /theme/custom (generate + save)
# Added (for "live preview, then Apply"):
#   POST /theme/custom/preview  generate only, nothing is saved
#   PUT  /theme/custom          save a previewed custom theme (re-validated server side)


class PresetIn(BaseModel):
    preset_key: str


class CustomIn(BaseModel):
    query: str = Field(min_length=2, max_length=200)


class CustomSaveIn(BaseModel):
    theme: dict
    query: str | None = Field(default=None, max_length=200)


# Generating calls a paid LLM: keep a small per-user budget (in-memory; use Redis if you run many workers).
_hits: dict[int, deque] = defaultdict(deque)
_lock = Lock()
_LIMIT, _WINDOW = 10, 60.0


def _throttle(user_id: int) -> None:
    now = time.monotonic()
    with _lock:
        q = _hits[user_id]
        while q and now - q[0] > _WINDOW:
            q.popleft()
        if len(q) >= _LIMIT:
            raise HTTPException(429, "Too many theme requests. Try again in a minute.")
        q.append(now)


def _get_row(db: Session, user_id: int) -> UserTheme | None:
    return db.scalar(select(UserTheme).where(UserTheme.user_id == user_id))


def _save(db: Session, user_id: int, preset_key: str | None, data: dict | None) -> UserTheme:
    t = _get_row(db, user_id)
    if not t:
        t = UserTheme(user_id=user_id)
        db.add(t)
    t.preset_key = preset_key
    t.data = data
    db.commit()
    db.refresh(t)
    return t


@router.get("/presets")
def list_presets():
    return PRESETS


@router.get("")
def get_theme(user=Depends(current_user), db: Session = Depends(get_db)):
    t = _get_row(db, user.id)
    if not t:
        return None
    if t.preset_key:
        preset = PRESETS.get(t.preset_key)
        return {"preset_key": t.preset_key, **preset} if preset else None
    try:
        # also upgrades rows saved by the first (v1) version of this feature
        return {"preset_key": None, **to_public(sanitize_theme(t.data or {}))}
    except ValueError:
        return None


@router.put("/preset")
def set_preset(data: PresetIn, user=Depends(current_user), db: Session = Depends(get_db)):
    if data.preset_key not in PRESETS:
        raise HTTPException(404, "Unknown preset")
    t = _save(db, user.id, data.preset_key, None)
    return {"preset_key": t.preset_key, **PRESETS[t.preset_key]}


@router.post("/custom/preview")
def preview_custom(data: CustomIn, user=Depends(current_user)):
    _throttle(user.id)
    return {"preset_key": None, **generate_custom(normalize_query(data.query))}


@router.post("/custom")
def set_custom(data: CustomIn, user=Depends(current_user), db: Session = Depends(get_db)):
    _throttle(user.id)
    generated = generate_custom(normalize_query(data.query))
    _save(db, user.id, None, generated)
    return {"preset_key": None, **generated}


@router.put("/custom")
def save_custom(data: CustomSaveIn, user=Depends(current_user), db: Session = Depends(get_db)):
    try:
        clean = sanitize_theme(data.theme, normalize_query(data.query) if data.query else None)
    except ValueError as exc:
        raise HTTPException(422, f"Invalid theme: {exc}") from exc
    public = to_public(clean)
    _save(db, user.id, None, public)
    return {"preset_key": None, **public}
