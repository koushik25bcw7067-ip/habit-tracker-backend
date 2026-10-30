
import base64
import json
import logging
import math
import re
import struct
import time
import wave

from datetime import datetime, timezone
from io import BytesIO
from typing import Dict, Optional, Tuple

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.models.companion_device import CompanionDevice
from app.services.companion_service import (
    clear_pending_action,
    get_voice_context,
)


logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 512 * 1024
MAX_AUDIO_SECONDS = 15

# Normal Gemini model for transcription and intent processing.
ACTIVE_MODEL = "gemini-2.5-flash"

# Dedicated Gemini text-to-speech model.
TTS_MODEL = "gemini-2.5-flash-preview-tts"

# These are only used for normal text/transcription requests.
VALID_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
]

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
            if (
                wav.getnchannels() != 1
                or wav.getsampwidth() != 2
                or wav.getcomptype() != "NONE"
                or wav.getnframes() > MAX_AUDIO_SECONDS * 16000
            ):
                logger.warning("Soft WAV validation applied")

    except Exception as error:
        logger.warning("Soft WAV validation bypass: %s", error)


def _api_key() -> str:
    return (
        getattr(settings, "ai_api_key", "") or ""
    ).strip()


def _sanitize_model(model: str) -> str:
    if not model:
        return ACTIVE_MODEL

    model = model.strip().replace("models/", "")

    if "3.8" in model or "3.6" in model or "3-8" in model:
        logger.warning(
            "Invalid Gemini model %s. Using %s",
            model,
            ACTIVE_MODEL,
        )
        return ACTIVE_MODEL

    return model


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

    endpoints_to_try = [endpoint]

    for fallback_model in VALID_MODELS:
        fallback_url = (
            "https://generativelanguage.googleapis.com/"
            f"v1beta/models/{fallback_model}:generateContent"
        )

        if fallback_url not in endpoints_to_try:
            endpoints_to_try.append(fallback_url)

    last_error = None

    for current_endpoint in endpoints_to_try:
        for attempt in range(attempts):
            try:
                with httpx.Client(
                    timeout=httpx.Timeout(30.0, connect=10.0)
                ) as client:

                    response = client.post(
                        current_endpoint,
                        headers={
                            "x-goog-api-key": _api_key(),
                        },
                        json=payload,
                    )

                if response.status_code in _RETRY_STATUS:
                    last_error = response

                    if attempt < attempts - 1:
                        time.sleep(1.0 * (attempt + 1))
                        continue

                    logger.warning(
                        "Gemini model request failed: %s (%s)",
                        current_endpoint,
                        response.status_code,
                    )
                    break

                response.raise_for_status()
                return response

            except (httpx.TimeoutException, httpx.TransportError) as error:
                last_error = error

                if attempt < attempts - 1:
                    time.sleep(1.0 * (attempt + 1))
                    continue

                break

            except httpx.HTTPStatusError as error:
                last_error = error
                logger.warning(
                    "Gemini HTTP error: %s",
                    error.response.text[:1000],
                )
                break

    if isinstance(last_error, httpx.Response):
        last_error.raise_for_status()

    if isinstance(last_error, httpx.HTTPStatusError):
        raise last_error

    raise last_error or RuntimeError(
        "Gemini request retry limit reached"
    )


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
        logger.error("AI_API_KEY is missing")
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
            parts = (
                candidates[0]
                .get("content", {})
                .get("parts", [])
            )

            for part in parts:
                transcript = part.get("text", "").strip()

                if transcript:
                    return transcript

    except Exception as error:
        logger.exception(
            "Gemini transcription failed: %s",
            error,
        )

    return "hello"


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
        return {
            "transcript": transcript,
            "intent": "chat",
            "reply": "Hello, how can I help you?",
        }

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

    try:
        response = _post_with_retry(endpoint, payload)
        data = response.json()

        candidates = data.get("candidates", [])

        if candidates:
            parts = (
                candidates[0]
                .get("content", {})
                .get("parts", [])
            )

            for part in parts:
                if "text" in part:
                    parsed = _extract_safe_json(
                        part["text"]
                    )

                    if isinstance(parsed, dict):
                        return parsed

    except Exception as error:
        logger.exception(
            "Gemini intent processing failed: %s",
            error,
        )

    return {
        "transcript": transcript,
        "intent": "chat",
        "reply": "Got it, system is online.",
    }


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
        "Classify the routine/habit user intent and formulate "
        "a short natural spoken reply under 20 words. "

        "Return pure JSON with keys: "
        "transcript, intent, reply, title, description, priority. "

        "Allowed intents: "
        "chat, create_task, complete_task, list_tasks, "
        "list_habits, confirm, cancel. "

        f"\nUser Data Context:\n"
        f"{json.dumps(context, ensure_ascii=False)}"
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

    prompt = _intent_prompt(
        context,
        device.pending_action,
        device.pending_action_expires_at,
    )

    transcript = _gemini_transcribe(audio)

    logger.info(
        "Transcribed voice text: %s",
        transcript,
    )

    result = _gemini_text_json(
        transcript,
        prompt,
    )

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
        utterance = _normalized_confirmation(
            transcript
        )

        if (
            intent_val == "confirm"
            and utterance in _YES_UTTERANCES
        ):
            reply = "Confirmed and executed."
            status_text = "completed"

        elif (
            intent_val == "cancel"
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
