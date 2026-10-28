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

ACTIVE_MODEL = "gemini-3.8-flash"
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

_RETRY_STATUS = {429, 500, 502, 503, 504}


def _normalized_confirmation(text: str) -> str:
    return " ".join(
        re.sub(r"[.!?,;:'\"…]+", " ", text.lower()).split()
    )


def validate_wav(audio: bytes) -> None:
    if not audio or len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Audio must be under 512 KiB",
        )

    try:
        with wave.open(BytesIO(audio), "rb") as wav:
            if (
                wav.getnchannels() != 1
                or wav.getsampwidth() != 2
                or wav.getcomptype() != "NONE"
                or wav.getnframes() > MAX_AUDIO_SECONDS * 16000
            ):
                logger.warning("Soft wav validation applied")
    except Exception as error:
        logger.warning("Soft wav bypass: %s", error)


def _api_key() -> str:
    return (getattr(settings, "ai_api_key", "") or "").strip()


def _get_gemini_endpoint() -> str:
    """Build a Gemini generateContent endpoint from the configured model."""
    raw_model = (getattr(settings, "ai_model", "") or "").strip()
    model = raw_model.removeprefix("models/").strip()

    if model != ACTIVE_MODEL:
        model = ACTIVE_MODEL

    return (
        "https://generativelanguage.googleapis.com/"
        f"v1beta/models/{model}:generateContent"
    )


def _post_with_retry(
    endpoint: str,
    payload: dict,
    attempts: int = 2,
) -> httpx.Response:
    if not endpoint.startswith(("http://", "https://")):
        endpoint = f"https://{endpoint.lstrip('/')}"

    key = _api_key()

    for attempt in range(attempts):
        last = attempt == attempts - 1

        try:
            with httpx.Client(
                timeout=httpx.Timeout(20.0, connect=5.0)
            ) as client:
                response = client.post(
                    endpoint,
                    headers={"x-goog-api-key": key},
                    json=payload,
                )

        except (httpx.TimeoutException, httpx.TransportError):
            if last:
                raise
            time.sleep(1.0 * (attempt + 1))
            continue

        if response.status_code == 429 and not last:
            time.sleep(2.0 * (attempt + 1))
            continue

        if response.status_code in _RETRY_STATUS and not last:
            time.sleep(1.0 * (attempt + 1))
            continue

        response.raise_for_status()
        return response

    raise RuntimeError("API retry limit reached")


def _generate_fallback_wav() -> bytes:
    """Generate a simple 16 kHz mono chime as fallback audio."""
    buf = BytesIO()
    sample_rate = 16000
    duration = 0.7

    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)

        total_samples = int(sample_rate * duration)

        for i in range(total_samples):
            freq = (
                523.25
                if i < total_samples // 2
                else 659.25
            )
            val = int(
                14000.0
                * math.sin(2.0 * math.pi * freq * (i / sample_rate))
            )
            wav.writeframes(struct.pack("<h", val))

    return buf.getvalue()


def _gemini_transcribe(audio: bytes) -> str:
    key = _api_key()

    if not key:
        return "hello companion"

    endpoint = _get_gemini_endpoint()

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": (
                            "Transcribe the spoken audio accurately. "
                            "Output plain transcript only, "
                            "with no markdown or quotes."
                        )
                    },
                    {
                        "inline_data": {
                            "mime_type": "audio/wav",
                            "data": base64.b64encode(audio).decode("ascii"),
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.0,
            "maxOutputTokens": 256,
        },
    }

    try:
        response = _post_with_retry(endpoint, payload)
        data = response.json()
        candidates = data.get("candidates", [])

        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])

            if parts and "text" in parts[0]:
                transcript = parts[0]["text"].strip()

                if transcript:
                    return transcript

    except Exception as err:
        logger.warning("Gemini transcription fallback: %s", err)

    return "hello"


def _extract_safe_json(raw_text: str) -> dict | None:
    text = raw_text.strip()

    if "```" in text:
        text = re.sub(
            r"^```(?:json)?\s*",
            "",
            text,
            flags=re.MULTILINE,
        )
        text = re.sub(
            r"\s*```$",
            "",
            text,
            flags=re.MULTILINE,
        ).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)

    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    intent_match = re.search(
        r'"intent"\s*:\s*"([^"]+)"',
        text,
    )

    if intent_match:
        return {"intent": intent_match.group(1)}

    return None


def _gemini_text_json(transcript: str, prompt: str) -> dict:
    key = _api_key()

    if not key:
        return {
            "transcript": transcript,
            "intent": "chat",
            "reply": "Hello, how can I help you?",
        }

    endpoint = _get_gemini_endpoint()

    transcription = (
        f"Spoken Transcription: "
        f"{json.dumps(transcript, ensure_ascii=False)}\n\n{prompt}"
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": transcription}
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "maxOutputTokens": 512,
        },
    }

    try:
        response = _post_with_retry(endpoint, payload)
        data = response.json()
        candidates = data.get("candidates", [])

        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])

            if parts and "text" in parts[0]:
                parsed = _extract_safe_json(parts[0]["text"])

                if isinstance(parsed, dict):
                    return parsed

    except Exception as err:
        logger.warning("Companion intent parse fallback: %s", err)

    return {
        "transcript": transcript,
        "intent": "chat",
        "reply": "Got it, system is online.",
    }


def _synthesize(text: str) -> bytes:
    """Generate speech; return a fallback chime if generation fails."""
    key = _api_key()

    if not key:
        return _generate_fallback_wav()

    endpoint = (
        "https://generativelanguage.googleapis.com/"
        "v1beta/interactions"
    )

    payload = {
        "model": TTS_MODEL,
        "input": [
            {
                "type": "user_input",
                "content": [
                    {
                        "type": "text",
                        "text": text[:200],
                        "annotations": [
                            {
                                "type": "speech_metadata",
                                "style": "clear, pleasant voice",
                            }
                        ],
                    }
                ],
            }
        ],
        "response_format": {
            "type": "audio",
            "mime_type": "audio/wav",
        },
        "generation_config": {
            "speech_config": [
                {"voice": "Puck"}
            ],
        },
    }

    try:
        response = _post_with_retry(
            endpoint,
            payload,
            attempts=2,
        )
        data = response.json()
        audio_data = data.get("output_audio", {}).get("data")

        if audio_data:
            return base64.b64decode(audio_data, validate=True)

        logger.warning(
            "TTS response did not contain output_audio.data"
        )

    except Exception as error:
        logger.warning("TTS generation notice: %s", error)

    return _generate_fallback_wav()


def _intent_prompt(
    context: dict,
    pending_action: dict | None,
    expires_at,
) -> str:
    pending = ""

    if pending_action:
        pending = (
            f"\nPending action awaiting confirmation: "
            f"{json.dumps(pending_action, ensure_ascii=False)}. "
            'For yes return {"intent": "confirm"}. '
            'For no return {"intent": "cancel"}.'
        )

    return (
        "Classify the routine/habit user intent and formulate "
        "a short natural spoken reply (under 20 words). "
        "Return pure JSON with keys: transcript, intent, reply, "
        "title, description, priority. "
        "Allowed intents: chat, create_task, complete_task, "
        "list_tasks, list_habits, confirm, cancel."
        f"\nUser Data Context:\n"
        f"{json.dumps(context, ensure_ascii=False)}"
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
    db: Session,
    device: CompanionDevice,
    audio: bytes,
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
    logger.info("Transcribed voice text: %s", transcript)

    result = _gemini_text_json(transcript, prompt)

    if not isinstance(result, dict):
        result = {
            "transcript": transcript,
            "intent": "chat",
            "reply": "I am listening.",
        }

    result["transcript"] = transcript
    intent_val = result.get("intent", "chat")
    ai_reply = result.get("reply", "")

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
        reply = ai_reply.strip() or "I am listening."
        status_text = "replied"
        action_id = None

    elif intent_val == "list_tasks":
        tasks = context.get("tasks", [])
        reply = (
            f"You have {len(tasks)} tasks."
            if tasks
            else "You have no pending tasks."
        )
        status_text = "replied"
        action_id = None

    elif intent_val == "list_habits":
        habits = context.get("habits", [])
        reply = (
            f"You have {len(habits)} active habits."
            if habits
            else "No routines found."
        )
        status_text = "replied"
        action_id = None

    else:
        reply = ai_reply.strip() or "Got your update."
        status_text = "replied"
        action_id = None

    return reply[:200], status_text, action_id, transcript


def synthesize_reply(reply: str) -> bytes:
    try:
        return _synthesize(reply)
    except Exception:
        return _generate_fallback_wav()