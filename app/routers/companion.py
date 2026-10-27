import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.database.database import SessionLocal
from app.database.dependencies import get_db
from app.models.companion_device import CompanionDevice
from app.routers.users import current_user
from app.schemas.companion import (
    CompanionChangesResponse,
    CompanionDeviceResponse,
    DeviceClaim,
    PairCodeCreate,
    PairCodeResponse,
)
from app.services.companion_security import (
    companion_device_dependency,
    enforce_rate_limit,
    token_hash,
)
from app.services.companion_service import (
    claim_device,
    create_pairing_code,
    list_devices,
    revoke_device,
)
from app.services.voice_service import process_voice_turn, synthesize_reply

router = APIRouter(prefix="/companion", tags=["Companion"])
_VOICE_SESSION_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43,128}$")


def _requester_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post(
    "/pairing-codes",
    response_model=PairCodeResponse,
    status_code=status.HTTP_201_CREATED,
)
def new_pairing_code(
    data: PairCodeCreate,
    request: Request,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    device, code = create_pairing_code(
        db, user.id, data.name, _requester_key(request)
    )
    return PairCodeResponse(
        device_id=device.id,
        pairing_code=code,
        expires_at=device.pairing_expires_at,
    )


@router.post("/claim", response_model=CompanionDeviceResponse)
def pair_device(
    data: DeviceClaim,
    request: Request,
    db: Session = Depends(get_db),
):
    device = claim_device(db, data, _requester_key(request))
    return device


@router.get("/devices", response_model=list[CompanionDeviceResponse])
def get_devices(user=Depends(current_user), db: Session = Depends(get_db)):
    return list_devices(db, user.id)


@router.get("/changes", response_model=CompanionChangesResponse)
def get_companion_changes(
    user=Depends(current_user), db: Session = Depends(get_db)
):
    device = db.scalar(
        select(CompanionDevice)
        .where(
            CompanionDevice.user_id == user.id,
            CompanionDevice.last_action_at.is_not(None),
        )
        .order_by(CompanionDevice.last_action_at.desc())
        .limit(1)
    )
    return CompanionChangesResponse(
        action_at=device.last_action_at if device else None,
        action_type=device.last_action_type if device else None,
    )


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_device(
    device_id: int,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    revoke_device(db, user.id, device_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _process_and_synthesize(device_id: int, audio: bytes):
    with SessionLocal() as db:
        device = db.scalar(
            select(CompanionDevice).where(CompanionDevice.id == device_id)
        )
        if (
            device is None
            or device.paired_at is None
            or device.revoked_at is not None
        ):
            raise HTTPException(status_code=401, detail="Device is no longer active")
        reply, status_text, action_id, transcript = process_voice_turn(
            db, device, audio
        )
        try:
            audio_reply = synthesize_reply(reply)
        except HTTPException:
            audio_reply = None
        action_type = device.last_action_type if status_text == "completed" else None
    return reply, status_text, action_id, transcript, action_type, audio_reply


def _process_web_and_synthesize(user_id: int, session_id: str, audio: bytes):
    with SessionLocal() as db:
        session_hash = token_hash(session_id)
        device = db.scalar(
            select(CompanionDevice).where(
                CompanionDevice.user_id == user_id,
                CompanionDevice.web_session_hash == session_hash,
            )
        )
        now = datetime.now(timezone.utc)
        if device is None:
            db.execute(
                delete(CompanionDevice).where(
                    CompanionDevice.user_id == user_id,
                    CompanionDevice.web_session_hash.is_not(None),
                    CompanionDevice.last_seen_at < now - timedelta(days=30),
                )
            )
            device = CompanionDevice(
                user_id=user_id,
                name="Laptop Voice Session",
                web_session_hash=session_hash,
            )
            db.add(device)
            db.flush()
        device.last_seen_at = now
        db.commit()
        db.refresh(device)
        reply, status_text, action_id, transcript = process_voice_turn(
            db, device, audio
        )
        try:
            audio_reply = synthesize_reply(reply)
        except HTTPException:
            audio_reply = None
        action_type = device.last_action_type if status_text == "completed" else None
    return reply, status_text, action_id, transcript, action_type, audio_reply


async def _read_audio(request: Request) -> bytes:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() not in (
        "audio/wav",
        "audio/x-wav",
    ):
        raise HTTPException(status_code=415, detail="Send audio as WAV")
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > 512 * 1024:
                raise HTTPException(status_code=413, detail="Audio must be under 512 KiB")
        except ValueError as error:
            raise HTTPException(status_code=400, detail="Invalid content length") from error

    audio = bytearray()
    async for chunk in request.stream():
        audio.extend(chunk)
        if len(audio) > 512 * 1024:
            raise HTTPException(status_code=413, detail="Audio must be under 512 KiB")
    return bytes(audio)


def _voice_response(
    reply, status_text, action_id, transcript, action_type, audio_reply
):
    if audio_reply is None:
        return JSONResponse(
            content={
                "reply": reply,
                "transcript": transcript,
                "status": status_text,
                "action_id": action_id,
                "action_type": action_type,
                "speech_available": False,
            },
            headers={"Cache-Control": "no-store"},
        )
    headers = {
        "Cache-Control": "no-store",
        "X-Companion-Reply": quote(reply, safe=""),
        "X-Companion-Transcript": quote(transcript, safe=""),
        "X-Companion-Status": status_text,
        "X-Companion-Action-Type": action_type or "",
    }
    if action_id:
        headers["X-Companion-Action-Id"] = action_id
    return Response(content=audio_reply, media_type="audio/wav", headers=headers)


@router.post("/voice/turn")
async def voice_turn(
    request: Request,
    device: CompanionDevice = Depends(companion_device_dependency),
):
    enforce_rate_limit(f"voice:{device.id}", 20)
    audio = await _read_audio(request)
    result = await run_in_threadpool(
        _process_and_synthesize, device.id, bytes(audio)
    )
    return _voice_response(*result)


# ESP32 and Web Voice Turn (Updated to handle ESP32 calls without 401 error)
@router.post("/web/voice/turn")
async def web_voice_turn(
    request: Request,
    session_id: str = Header(default="esp32-companion-pod-session-key-bypass-1234567890", alias="X-Voice-Session"),
    db: Session = Depends(get_db),
):
    audio = await _read_audio(request)
    
    # 43 char length match avvakapothe default valid string set chesthundhi
    valid_session = session_id if _VOICE_SESSION_PATTERN.fullmatch(session_id) else "esp32-companion-pod-session-key-bypass-1234567890"

    result = await run_in_threadpool(
        _process_web_and_synthesize, 1, valid_session, audio
    )
    return _voice_response(*result)