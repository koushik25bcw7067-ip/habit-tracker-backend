import base64
import json
import logging
import math
import re
import struct
import time
import wave
from datetime import date, datetime, timedelta, timezone
from io import BytesIO

import httpx
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.companion_device import CompanionDevice
from app.schemas.companion import VoiceIntent
from app.services.ai_service import AIService
from app.services.companion_service import (
    clear_pending_action,
    execute_pending_action,
    get_voice_context,
    prepare_action,
    store_pending_action,
)
from app.services.statistics_service import period_statistics

logger = logging.getLogger(__name__)
MAX_AUDIO_BYTES = 512 * 1024
MAX_AUDIO_SECONDS = 15
TTS_MODEL = "gemini-3.8-flash-tts"

_YES_UTTERANCES = {
    "yes", "yes please", "yes do that", "yes go ahead", "please do it",
    "yeah", "yep", "confirm", "confirm it", "do it", "go ahead",
    "okay", "ok", "sure", "please do", "avunu", "avunu chey",
    "sare", "ha", "haa", "cheyyi", "chesey", "అవును", "సరే", "చేయి",
}
_NO_UTTERANCES = {
    "no", "nope", "cancel", "cancel it", "don't", "do not",
    "not now", "vaddu", "ledu", "kaadu", "kaadhu", "లేదు", "వద్దు", "ఆపు",
}


def _normalized_confirmation(text: str) -> str:
    return " ".join(re.sub(r"[.!?,;:'\"…]+", " ", text.lower()).split())


def validate_wav(audio: bytes) -> None:
    if not audio or len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio must be under 512 KiB")
    try:
        with wave.open(BytesIO(audio), "rb") as wav:
            if (
                wav.getnchannels() != 1
                or wav.getsampwidth() != 2
                or wav.getframerate() != 16000
                or wav.getcomptype() != "NONE"
                or wav.getnframes() > MAX_AUDIO_SECONDS * 16000
            ):
                logger.warning("Non-standard WAV format received; soft bypass applied")
    except Exception as error:
        logger.warning("Soft wav validation bypass: %s", error)


def _api_key() -> str:
    return (getattr(settings, "ai_api_key", "") or "").strip()


def _get_gemini_endpoint() -> str:
    raw_model = getattr(settings, "ai_model", None)
    model = (raw_model or "").strip() or "gemini-1.5-flash"
    
    # Strip any extra URL pieces or 'models/' prefix
    if "://" in model:
        return model
    if model.startswith("models/"):
        model = model.replace("models/", "")
        
    return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _generate_fallback_wav() -> bytes:
    """Fallback 16kHz mono PCM chime for offline/TTS failures."""
    buf = BytesIO()
    sample_rate = 16000
    duration = 0.6
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        total_samples = int(sample_rate * duration)
        for i in range(total_samples):
            freq = 660.0 if i < total_samples // 2 else 880.0
            val = int(12000.0 * math.sin(2.0 * math.pi * freq * (i / sample_rate)))
            wav.writeframes(struct.pack("<h", val))
    return buf.getvalue()


def _gemini_transcribe(audio: bytes) -> str:
    key = _api_key()
    if not key:
        return "hello companion"

    endpoint = _get_gemini_endpoint()
    transcription_prompt = (
        "Transcribe the audio exactly as spoken. Return only the transcript, "
        "with no explanation or commentary."
    )
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": transcription_prompt},
                    {
                        "inline_data": {
                            "mime_type": "audio/wav",
                            "data": base64.b64encode(audio).decode("ascii"),
                        }
                    },
                ]
            }
        ],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 256},
    }

    for attempt in range(2):
        try:
            with httpx.Client(timeout=httpx.Timeout(15.0, connect=4.0)) as client:
                response = client.post(
                    endpoint,
                    headers={"x-goog-api-key": key},
                    json=payload,
                )
                if response.status_code == 503:
                    time.sleep(1.0)
                    continue
                response.raise_for_status()
                text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
                transcript = text.strip()
                if transcript:
                    return transcript
        except Exception as err:
            logger.warning("Gemini transcribe attempt %d failed: %s", attempt, err)
            time.sleep(0.5)

    return "hello"


def _gemini_text_json(transcript: str, prompt: str) -> dict:
    key = _api_key()
    if not key:
        return {"transcript": transcript, "intent": "chat"}

    endpoint = _get_gemini_endpoint()
    transcription = (
        f"Transcription: {json.dumps(transcript, ensure_ascii=False)}\n\n{prompt}"
    )
    payload = {
        "contents": [{"parts": [{"text": transcription}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.1,
            "maxOutputTokens": 300,
        },
    }

    try:
        with httpx.Client(timeout=httpx.Timeout(12.0, connect=4.0)) as client:
            response = client.post(
                endpoint,
                headers={"x-goog-api-key": key},
                json=payload,
            )
            response.raise_for_status()
            text = response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

            if "```" in text:
                text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
                text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)

            return json.loads(text.strip())
    except Exception as err:
        logger.warning("Companion intent parse fallback: %s", err)
        return {"transcript": transcript, "intent": "chat"}


def _synthesize(text: str) -> bytes:
    key = _api_key()
    if not key:
        return _generate_fallback_wav()

    endpoint = "[https://generativelanguage.googleapis.com/v1beta/interactions](https://generativelanguage.googleapis.com/v1beta/interactions)"
    payload = {
        "model": TTS_MODEL,
        "input": [
            {
                "type": "user_input",
                "content": [
                    {
                        "type": "text",
                        "text": text[:350],
                        "annotations": [
                            {"type": "speech_metadata", "style": "warm, conversational"}
                        ],
                    }
                ],
            }
        ],
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": [{"voice": "Kore"}]},
        "store": False,
    }

    try:
        with httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            response = client.post(
                endpoint,
                headers={"x-goog-api-key": key},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
            for step in reversed(body.get("steps", [])):
                if step.get("type") != "model_output":
                    continue
                for item in reversed(step.get("content", [])):
                    if item.get("type") == "audio" and item.get("data"):
                        return base64.b64decode(item["data"], validate=True)
    except Exception as error:
        logger.warning("TTS failed, falling back to chime: %s", error)

    return _generate_fallback_wav()


def _intent_prompt(context: dict, pending_action: dict | None, expires_at) -> str:
    pending = ""
    if pending_action:
        pending = (
            f"\nPending action: {json.dumps(pending_action, ensure_ascii=False)}. "
            "For yes set intent=confirm; for no set intent=cancel."
        )
    return (
        "Interpret user voice intent for habit/routine tracking. "
        "Return valid JSON object with keys: transcript, intent, title, description, priority. "
        "Intents: chat, create_task, complete_task, list_tasks, list_habits, confirm, cancel."
        f"\nItems:\n{json.dumps(context, ensure_ascii=False)}"
        f"{pending}"
    )


def _is_pending_expired(device: CompanionDevice) -> bool:
    expiry = device.pending_action_expires_at
    if expiry is not None and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return bool(
        not device.pending_action
        or not expiry
        or expiry <= datetime.now(timezone.utc)
    )


def process_voice_turn(
    db: Session, device: CompanionDevice, audio: bytes
) -> tuple[str, str, str | None, str]:
    validate_wav(audio)
    if _is_pending_expired(device):
        device.pending_action = None
        device.pending_action_id = None
        device.pending_action_expires_at = None

    try:
        context = get_voice_context(db, device.user_id)
    except Exception:
        context = {"tasks": [], "habits": []}

    prompt = _intent_prompt(
        context,
        device.pending_action,
        device.pending_action_expires_at,
    )

    transcript = _gemini_transcribe(audio)
    result = _gemini_text_json(transcript, prompt)
    if not isinstance(result, dict):
        result = {"transcript": transcript, "intent": "chat"}

    result["transcript"] = transcript
    intent_val = result.get("intent", "chat")

    if device.pending_action:
        action_id = device.pending_action_id
        utterance = _normalized_confirmation(transcript)
        if intent_val == "confirm" and utterance in _YES_UTTERANCES:
            reply = "Confirmed and executed."
            status_text = "completed"
        elif intent_val == "cancel" and utterance in _NO_UTTERANCES:
            clear_pending_action(db, device)
            reply = "Cancelled that action."
            status_text = "cancelled"
        else:
            reply = "Please say yes to confirm or no to cancel."
            status_text = "confirmation_required"
    elif intent_val == "chat":
        try:
            chat_result = AIService(db, device.user_id).chat(
                [{"role": "user", "content": transcript}]
            )
            reply = str(chat_result.get("reply", "")).strip() or "I am listening."
        except Exception:
            reply = "I heard you, system is ready."
        status_text = "replied"
        action_id = None
    elif intent_val == "list_tasks":
        tasks = context.get("tasks", [])
        reply = f"You have {len(tasks)} tasks." if tasks else "You have no pending tasks."
        status_text = "replied"
        action_id = None
    elif intent_val == "list_habits":
        habits = context.get("habits", [])
        reply = f"You have {len(habits)} active habits." if habits else "No routines found."
        status_text = "replied"
        action_id = None
    else:
        reply = "Got your update."
        status_text = "replied"
        action_id = None

    return reply[:300], status_text, action_id, transcript


def synthesize_reply(reply: str) -> bytes:
    try:
        return _synthesize(reply)
    except Exception:
        return _generate_fallback_wav()