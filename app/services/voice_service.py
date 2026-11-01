
import base64
import json
import logging
import math
import re
import struct
import time
import wave

from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Dict, Optional, Tuple

import httpx
from fastapi import HTTPException
from pydantic import ValidationError
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
from app.services.conversation_service import (
    append_turn,
    get_or_create_device_conversation,
    get_recent_context,
)
from app.services.statistics_service import period_statistics


logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 512 * 1024
MAX_AUDIO_SECONDS = 15

# Normal Gemini model for transcription and intent processing.
ACTIVE_MODEL = "gemini-3.5-flash-lite"

# Dedicated Gemini text-to-speech model.
TTS_MODEL = "gemini-2.5-flash-preview-tts"

# These are only used for normal text/transcription requests.
_RETRY_STATUS = {429, 500, 502, 503, 504}

_YES_UTTERANCES = {
    "yes", "yes please", "yes do that", "yes go ahead",
    "please do it", "yeah", "yep", "confirm", "confirm it",
    "do it", "go ahead", "okay", "ok", "sure", "please do",
    "avunu", "avunu chey", "sare", "ha", "haa",
    "cheyyi", "chesey", "అవును", "సరే", "చేయి",
}

_NO_UTTERANCES = {
    "no", "nope", "cancel", "cancel it", "don't", "do not",
    "not now", "vaddu", "ledu", "kaadu", "kaadhu",
    "లేదు", "వద్దు", "ఆపు",
}


# ============================================================
# GENERAL HELPERS
# ============================================================

def _normalized_confirmation(text: str) -> str:
    cleaned = re.sub(r"""[.!?,;:'"…]+""", " ", text.lower())
    return " ".join(cleaned.split())


def validate_wav(audio: bytes) -> None:
    if not audio or len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Audio must be under 512 KiB",
        )

    try:
        with wave.open(BytesIO(audio), "rb") as wav:
            valid_format = (
                wav.getnchannels() == 1
                and wav.getsampwidth() == 2
                and wav.getframerate() == 16000
                and wav.getcomptype() == "NONE"
            )
            frame_count = wav.getnframes()
    except (wave.Error, EOFError) as error:
        raise HTTPException(status_code=422, detail="Invalid WAV audio.") from error

    if not valid_format:
        raise HTTPException(
            status_code=422,
            detail="WAV audio must be uncompressed 16-bit mono at 16 kHz.",
        )
    if frame_count > MAX_AUDIO_SECONDS * 16000:
        raise HTTPException(
            status_code=422,
            detail=f"Audio must be {MAX_AUDIO_SECONDS} seconds or shorter.",
        )


def _api_key() -> str:
    return (
        getattr(settings, "ai_api_key", "") or ""
    ).strip()


def _sanitize_model(model: str) -> str:
    if not model:
        return ACTIVE_MODEL

    return model.strip().removeprefix("models/")


def _get_gemini_endpoint(
    model_override: Optional[str] = None,
) -> str:
    raw_model = (
        model_override
        or getattr(settings, "ai_model", "")
        or ACTIVE_MODEL
    )

    model = _sanitize_model(raw_model)

    return (
        "https://generativelanguage.googleapis.com/"
        f"v1beta/models/{model}:generateContent"
    )


# ============================================================
# GEMINI REQUEST WITH RETRY
# Used for transcription and text/intent requests only.
# ============================================================

def _post_with_retry(
    endpoint: str,
    payload: dict,
    attempts: int = 2,
) -> httpx.Response:

    if not endpoint.startswith(("http://", "https://")):
        endpoint = f"https://{endpoint.lstrip('/')}"

    last_error = None

    if not _api_key():
        logger.error("Gemini API key is not configured")
        raise HTTPException(status_code=503, detail="Gemini API is not configured.")

    for attempt in range(attempts):
        try:
            response = httpx.post(
                endpoint,
                timeout=httpx.Timeout(30.0, connect=10.0),
                headers={"x-goog-api-key": _api_key()},
                json=payload,
            )

            response_status = getattr(response, "status_code", 200)
            if response_status in _RETRY_STATUS and attempt < attempts - 1:
                time.sleep(1.0 * (attempt + 1))
                continue

            response.raise_for_status()
            return response

        except httpx.HTTPStatusError as error:
            last_error = error
            logger.error(
                "Gemini voice request failed: model=%s status=%s",
                endpoint.rsplit("/models/", 1)[-1].split(":", 1)[0],
                error.response.status_code,
            )
            break
        except (httpx.TimeoutException, httpx.TransportError) as error:
            last_error = error
            if attempt < attempts - 1:
                time.sleep(1.0 * (attempt + 1))

    if isinstance(last_error, httpx.HTTPStatusError):
        response = last_error.response
        try:
            provider_message = response.json().get("error", {}).get("message", "")
        except (ValueError, AttributeError):
            provider_message = ""
        detail = f"Gemini request failed (HTTP {response.status_code})"
        if provider_message:
            detail += f": {provider_message[:250]}"
        raise HTTPException(status_code=502, detail=detail) from last_error

    logger.error(
        "Could not reach Gemini voice API: error=%s",
        type(last_error).__name__ if last_error else "unknown",
    )
    raise HTTPException(
        status_code=502,
        detail="Could not reach Gemini.",
    ) from last_error


# ============================================================
# FALLBACK AUDIO
# Plays a short tone if Gemini TTS fails.
# ============================================================

def _generate_fallback_wav() -> bytes:
    buffer = BytesIO()
    sample_rate = 16000
    duration = 0.7

    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)

        total_samples = int(sample_rate * duration)

        for i in range(total_samples):
            frequency = (
                523.25
                if i < total_samples // 2
                else 659.25
            )

            value = int(
                10000.0
                * math.sin(
                    2.0 * math.pi * frequency * (i / sample_rate)
                )
            )

            wav.writeframes(struct.pack("<h", value))

    return buffer.getvalue()


# ============================================================
# CONVERT GEMINI PCM RESPONSE INTO WAV
# Gemini TTS commonly returns raw PCM audio.
# ============================================================

def _pcm_to_wav(
    pcm_data: bytes,
    sample_rate: int = 24000,
    channels: int = 1,
) -> bytes:

    if not pcm_data:
        raise ValueError("Empty PCM audio received")

    # 16-bit PCM must contain complete samples.
    if len(pcm_data) % 2 != 0:
        pcm_data = pcm_data[:-1]

    if not pcm_data:
        raise ValueError("Invalid PCM audio length")

    buffer = BytesIO()

    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm_data)

    return buffer.getvalue()


def _ensure_wav(
    audio_data: bytes,
    mime_type: str = "",
) -> bytes:

    if not audio_data:
        raise ValueError("Gemini returned empty audio")

    # Already a WAV file.
    if audio_data[:4] == b"RIFF" and audio_data[8:12] == b"WAVE":
        logger.info("Gemini returned an existing WAV file")
        return audio_data

    # Gemini raw PCM response.
    sample_rate = 24000

    rate_match = re.search(
        r"rate\s*=\s*(\d+)",
        mime_type or "",
        re.IGNORECASE,
    )

    if rate_match:
        sample_rate = int(rate_match.group(1))

    logger.info(
        "Wrapping raw PCM as WAV. MIME=%s, sample_rate=%s, bytes=%s",
        mime_type,
        sample_rate,
        len(audio_data),
    )

    return _pcm_to_wav(
        audio_data,
        sample_rate=sample_rate,
        channels=1,
    )


# ============================================================
# GEMINI SPEECH TRANSCRIPTION
# ============================================================

def _gemini_transcribe(audio: bytes) -> str:

    key = _api_key()

    if not key:
        logger.error("Gemini API key is not configured")
        raise HTTPException(status_code=503, detail="Gemini API is not configured.")

    endpoint = _get_gemini_endpoint()

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": (
                            "Transcribe the spoken audio accurately. "
                            "Preserve Telugu script and code-switching. "
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

    response = _post_with_retry(endpoint, payload)
    data = response.json()
    candidates = data.get("candidates", [])
    if candidates:
        parts = candidates[0].get("content", {}).get("parts", [])
        for part in parts:
            transcript = part.get("text", "").strip()
            if transcript:
                return transcript

    logger.error("Gemini returned no transcription text")
    raise HTTPException(status_code=422, detail="No speech was recognized.")


# ============================================================
# SAFE JSON PARSER
# ============================================================

def _extract_safe_json(
    raw_text: str,
) -> Optional[Dict]:

    text = raw_text.strip()

    if text.startswith("```"):
        text = re.sub(
            r"^```(?:json)?\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"\s*```$",
            "",
            text,
        ).strip()

    try:
        result = json.loads(text)

        if isinstance(result, dict):
            return result

    except json.JSONDecodeError:
        pass

    match = re.search(
        r"\{.*\}",
        text,
        re.DOTALL,
    )

    if match:
        try:
            result = json.loads(match.group(0))

            if isinstance(result, dict):
                return result

        except json.JSONDecodeError:
            pass

    intent_match = re.search(
        r'"intent"\s*:\s*"([^"]+)"',
        text,
    )

    if intent_match:
        return {
            "intent": intent_match.group(1)
        }

    return None


# ============================================================
# GEMINI INTENT / CHAT RESPONSE
# ============================================================

def _gemini_text_json(
    transcript: str,
    prompt: str,
) -> dict:

    key = _api_key()

    if not key:
        logger.error("Gemini API key is not configured")
        raise HTTPException(status_code=503, detail="Gemini API is not configured.")

    endpoint = _get_gemini_endpoint()

    full_prompt = (
        f"Spoken Transcription: "
        f"{json.dumps(transcript, ensure_ascii=False)}"
        f"\n\n{prompt}"
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": full_prompt
                    }
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "maxOutputTokens": 512,
            "temperature": 0.2,
        },
    }

    response = _post_with_retry(endpoint, payload)
    data = response.json()
    candidates = data.get("candidates", [])
    if candidates:
        parts = candidates[0].get("content", {}).get("parts", [])
        for part in parts:
            if "text" in part:
                parsed = _extract_safe_json(part["text"])
                if isinstance(parsed, dict):
                    return parsed

    logger.error("Gemini returned no valid voice intent response")
    raise HTTPException(
        status_code=502,
        detail="Gemini returned an invalid voice response.",
    )


# ============================================================
# GEMINI TEXT TO SPEECH
# IMPORTANT:
# Uses the dedicated TTS model directly.
# Does NOT use normal Gemini model fallbacks.
# ============================================================

def _synthesize(text: str) -> bytes:

    key = _api_key()

    if not key:
        logger.error("AI_API_KEY missing. Using fallback audio.")
        return _generate_fallback_wav()

    if not text or not text.strip():
        logger.warning("Empty TTS text. Using fallback audio.")
        return _generate_fallback_wav()

    model = TTS_MODEL

    logger.info(
        "Using Gemini TTS model: %s",
        model,
    )

    url = (
        "https://generativelanguage.googleapis.com/"
        f"v1beta/models/{model}:generateContent"
    )

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": (
                            "Say the following text naturally and clearly. "
                            "Do not add extra words: "
                            f"{text[:200]}"
                        )
                    }
                ]
            }
        ],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": "Puck"
                    }
                }
            },
        },
    }

    try:
        with httpx.Client(
            timeout=httpx.Timeout(60.0, connect=10.0)
        ) as client:

            response = client.post(
                url,
                headers={
                    "x-goog-api-key": key,
                    "Content-Type": "application/json",
                },
                json=payload,
            )

        # Do not silently try non-TTS models.
        if response.status_code != 200:
            logger.error(
                "Gemini TTS HTTP %s: %s",
                response.status_code,
                response.text[:2000],
            )

            response.raise_for_status()

        data = response.json()

        candidates = data.get("candidates", [])

        for candidate in candidates:
            parts = (
                candidate
                .get("content", {})
                .get("parts", [])
            )

            for part in parts:
                inline = (
                    part.get("inlineData")
                    or part.get("inline_data")
                    or {}
                )

                encoded_audio = inline.get("data")

                if encoded_audio:
                    audio_bytes = base64.b64decode(
                        encoded_audio
                    )

                    mime_type = (
                        inline.get("mimeType")
                        or inline.get("mime_type")
                        or ""
                    )

                    wav_audio = _ensure_wav(
                        audio_bytes,
                        mime_type,
                    )

                    logger.info(
                        "Gemini TTS success. WAV bytes: %s",
                        len(wav_audio),
                    )

                    return wav_audio

        logger.error(
            "Gemini TTS response contained no audio: %s",
            json.dumps(data)[:2000],
        )

    except Exception as error:
        logger.exception(
            "Gemini TTS generation failed: %s",
            error,
        )

    logger.warning(
        "Using fallback WAV because Gemini TTS failed."
    )

    return _generate_fallback_wav()


# ============================================================
# INTENT PROMPT
# ============================================================

def _intent_prompt(
    context: dict,
    pending_action: Optional[Dict],
    expires_at,
    recent_messages: Optional[list[dict[str, str]]] = None,
) -> str:

    pending = ""

    if pending_action:
        pending = (
            "\nPending action awaiting confirmation: "
            f"{json.dumps(pending_action, ensure_ascii=False)}. "
            'For yes return {"intent": "confirm"}. '
            'For no return {"intent": "cancel"}.'
        )

    return (
        "Classify the user's task or habit intent and formulate a short "
        "natural spoken reply under 20 words. Return only a JSON object "
        "with transcript, intent, reply, and any relevant "
        "VoiceIntent fields: target_id, title, description, due_date, "
        "priority, frequency, target_days, start_date, end_date, "
        "category_id, target_value, and unit. Allowed intents are chat, "
        "create_task, update_task, complete_task, delete_task, "
        "complete_habit, create_habit, update_habit, list_tasks, "
        "list_habits, weekly_progress, confirm, cancel, and unclear. "
        "Never claim an action has been executed; actions require explicit "
        "confirmation. "

        f"\nUser Data Context:\n"
        f"{json.dumps(context, ensure_ascii=False)}"
        f"\nRecent Conversation:\n"
        f"{json.dumps(recent_messages or [], ensure_ascii=False)}"
        f"{pending}"
    )


# ============================================================
# PENDING ACTION EXPIRY
# ============================================================

def _is_pending_expired(
    device: CompanionDevice,
) -> bool:

    expiry = device.pending_action_expires_at

    if expiry is not None and expiry.tzinfo is None:
        expiry = expiry.replace(
            tzinfo=timezone.utc
        )

    return bool(
        not device.pending_action
        or not expiry
        or expiry <= datetime.now(timezone.utc)
    )


# ============================================================
# PROCESS COMPLETE VOICE TURN
# ============================================================

def process_voice_turn(
    db: Session,
    device: CompanionDevice,
    audio: bytes,
) -> Tuple[str, str, Optional[str], str]:

    validate_wav(audio)

    if _is_pending_expired(device):
        device.pending_action = None
        device.pending_action_id = None
        device.pending_action_expires_at = None

    try:
        context = get_voice_context(
            db,
            device.user_id,
        )

    except Exception as error:
        logger.exception(
            "Could not load voice context: %s",
            error,
        )

        context = {
            "tasks": [],
            "habits": [],
        }

    conversation = get_or_create_device_conversation(db, device)
    recent_messages = get_recent_context(db, conversation.id)
    prompt = _intent_prompt(
        context,
        device.pending_action,
        device.pending_action_expires_at,
        recent_messages,
    )

    transcript = _gemini_transcribe(audio)

    logger.info("Voice transcription completed: characters=%d", len(transcript))

    result = _gemini_text_json(
        transcript,
        prompt,
    )

    if not isinstance(result, dict):
        raise HTTPException(
            status_code=502,
            detail="Gemini returned an invalid voice response.",
        )
    result["transcript"] = transcript
    try:
        intent = VoiceIntent.model_validate(result)
    except ValidationError as error:
        logger.warning("Gemini voice response did not match VoiceIntent")
        raise HTTPException(
            status_code=502,
            detail="Gemini returned an invalid voice response.",
        ) from error

    if device.pending_action:
        action_id = device.pending_action_id
        utterance = _normalized_confirmation(
            transcript
        )

        if (
            intent.intent == "confirm"
            and utterance in _YES_UTTERANCES
        ):
            reply = execute_pending_action(db, device)
            status_text = "completed"

        elif (
            intent.intent == "cancel"
            and utterance in _NO_UTTERANCES
        ):
            clear_pending_action(db, device)

            reply = "Cancelled that action."
            status_text = "cancelled"

        else:
            reply = (
                "Please say yes to confirm or no to cancel."
            )
            status_text = "confirmation_required"

    elif intent.intent == "chat":
        reply = (intent.reply or "I am listening.").strip()
        status_text = "replied"
        action_id = None

    elif intent.intent == "list_tasks":
        tasks = context.get("tasks", [])
        titles = [item["title"] for item in tasks[:5]]
        reply = (
            f"You have {len(tasks)} tasks: {', '.join(titles)}."
            if tasks
            else "You have no pending tasks."
        )
        status_text = "replied"
        action_id = None

    elif intent.intent == "list_habits":
        habits = context.get("habits", [])
        names = [item["name"] for item in habits[:5]]
        reply = (
            f"You have {len(habits)} active routines: {', '.join(names)}."
            if habits
            else "No routines found."
        )
        status_text = "replied"
        action_id = None

    elif intent.intent == "weekly_progress":
        today = datetime.now().date()
        start = today - timedelta(days=today.weekday())
        stats = period_statistics(db, device.user_id, start, today)
        count = stats["total_completions"]
        rate = stats["completion_rate"]
        label = "routine" if count == 1 else "routines"
        reply = (
            f"This week, you completed {count} {label} with a "
            f"{rate:.1f} percent completion rate."
        )
        status_text = "replied"
        action_id = None

    else:
        pending_action, reply = prepare_action(db, device.user_id, intent)
        if pending_action is None:
            status_text = "replied"
            action_id = None
        else:
            action_id = store_pending_action(db, device, pending_action)
            status_text = "confirmation_required"

    append_turn(db, conversation, transcript, reply[:200])
    return (
        reply[:200],
        status_text,
        action_id,
        transcript,
    )


# ============================================================
# PUBLIC TTS FUNCTION
# Called by the voice API route.
# ============================================================

def synthesize_reply(reply: str) -> bytes:

    try:
        audio = _synthesize(reply)

        if (
            audio
            and audio[:4] == b"RIFF"
            and audio[8:12] == b"WAVE"
        ):
            return audio

        logger.error(
            "TTS returned invalid WAV. Using fallback."
        )

    except Exception as error:
        logger.exception(
            "synthesize_reply failed: %s",
            error,
        )

    return _generate_fallback_wav()
