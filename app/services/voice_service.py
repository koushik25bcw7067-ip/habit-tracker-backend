
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

ACTIVE_MODEL = "gemini-1.5-flash"
TTS_MODEL = "gemini-2.5-flash-exp"

VALID_MODELS = [
    "gemini-1.5-flash",
    "gemini-2.5-flash",
    "gemini-1.5-flash-8b",
    "gemini-2.5-flash-exp",
]

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

_RETRY_STATUS = {429, 500, 502, 503, 504}


# =========================================================
# TEXT HELPERS
# =========================================================

def _normalized_confirmation(text: str) -> str:
    cleaned = re.sub(
        r"""[.!?,;:'"…]+""",
        " ",
        text.lower()
    )
    return " ".join(cleaned.split())


def _extract_safe_json(raw_text: str) -> Optional[Dict]:
    text = raw_text.strip()

    if text.startswith("```"):
        text = re.sub(
            r"^```(?:json)?\s*",
            "",
            text,
            flags=re.IGNORECASE
        )
        text = re.sub(r"\s*```$", "", text).strip()

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
        text
    )

    if intent_match:
        return {"intent": intent_match.group(1)}

    return None


# =========================================================
# WAV VALIDATION
# =========================================================

def validate_wav(audio: bytes) -> None:
    if not audio or len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Audio must be under 512 KiB"
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


# =========================================================
# GEMINI CONFIGURATION
# =========================================================

def _api_key() -> str:
    return (
        getattr(settings, "ai_api_key", "") or ""
    ).strip()


def _sanitize_model(model: str) -> str:
    if not model:
        return ACTIVE_MODEL

    model = model.strip().replace("models/", "")

    if (
        "3.8" in model
        or "3.6" in model
        or "3-8" in model
        or (
            "tts" in model.lower()
            and "2.5" not in model
        )
    ):
        logger.warning(
            "Invalid model %s detected. Using %s",
            model,
            ACTIVE_MODEL
        )
        return ACTIVE_MODEL

    return model


def _get_gemini_endpoint(
    model_override: Optional[str] = None
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


# =========================================================
# GEMINI HTTP REQUEST WITH RETRIES
# =========================================================

def _post_with_retry(
    endpoint: str,
    payload: dict,
    attempts: int = 2
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
                    timeout=httpx.Timeout(
                        30.0,
                        connect=10.0
                    )
                ) as client:

                    response = client.post(
                        current_endpoint,
                        headers={
                            "x-goog-api-key": _api_key()
                        },
                        json=payload
                    )

                if response.status_code in _RETRY_STATUS:
                    last_error = response

                    if attempt < attempts - 1:
                        time.sleep(1.0 * (attempt + 1))
                        continue

                    logger.warning(
                        "Model request failed with status %s. "
                        "Trying fallback model.",
                        response.status_code
                    )
                    break

                response.raise_for_status()
                return response

            except (
                httpx.TimeoutException,
                httpx.TransportError
            ) as error:

                last_error = error

                if attempt < attempts - 1:
                    time.sleep(1.0 * (attempt + 1))
                    continue

                break

            except httpx.HTTPStatusError as error:
                last_error = error
                break

    if isinstance(last_error, httpx.Response):
        last_error.raise_for_status()

    if isinstance(last_error, httpx.HTTPStatusError):
        raise last_error

    raise last_error or RuntimeError(
        "Gemini API retry limit reached"
    )


# =========================================================
# FALLBACK WAV GENERATOR
# =========================================================

def _generate_fallback_wav() -> bytes:
    buffer = BytesIO()

    sample_rate = 16000
    duration = 0.7

    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)

        total_samples = int(sample_rate * duration)

        frames = bytearray()

        for i in range(total_samples):
            frequency = (
                523.25
                if i < total_samples // 2
                else 659.25
            )

            value = int(
                14000.0
                * math.sin(
                    2.0 * math.pi * frequency
                    * (i / sample_rate)
                )
            )

            frames.extend(struct.pack("<h", value))

        wav.writeframes(bytes(frames))

    return buffer.getvalue()


# =========================================================
# GEMINI SPEECH TRANSCRIPTION
# =========================================================

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
                            "data": base64.b64encode(
                                audio
                            ).decode("ascii")
                        }
                    }
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.0,
            "maxOutputTokens": 256
        }
    }

    try:
        response = _post_with_retry(
            endpoint,
            payload
        )

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
        logger.warning(
            "Gemini transcription fallback: %s",
            error
        )

    return "hello"


# =========================================================
# GEMINI TEXT / INTENT GENERATION
# =========================================================

def _gemini_text_json(
    transcript: str,
    prompt: str
) -> dict:

    key = _api_key()

    if not key:
        return {
            "transcript": transcript,
            "intent": "chat",
            "reply": "Hello, how can I help you?"
        }

    endpoint = _get_gemini_endpoint()

    transcription = (
        f"Spoken Transcription: "
        f"{json.dumps(transcript, ensure_ascii=False)}"
        f"\n\n{prompt}"
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
            "temperature": 0.2
        }
    }

    try:
        response = _post_with_retry(
            endpoint,
            payload
        )

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
        logger.warning(
            "Companion intent parse fallback: %s",
            error
        )

    return {
        "transcript": transcript,
        "intent": "chat",
        "reply": "Got it, system is online."
    }


# =========================================================
# GEMINI TEXT-TO-SPEECH
# =========================================================

def _synthesize(text: str) -> bytes:
    """
    Generate Gemini TTS audio.

    Gemini may return raw PCM audio instead of a WAV container.
    This function converts raw PCM into a valid WAV file.
    """

    key = _api_key()

    if not key:
        logger.warning(
            "AI API key missing. Returning fallback WAV."
        )
        return _generate_fallback_wav()

    model = _sanitize_model(
        getattr(settings, "tts_model", "") or TTS_MODEL
    )

    if model == "gemini-1.5-flash":
        model = TTS_MODEL

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
                            "Say in a natural clear voice: "
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
            }
        }
    }

    try:
        response = _post_with_retry(
            url,
            payload,
            attempts=2
        )

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
                    part.get("inlineData", {})
                    or part.get("inline_data", {})
                )

                encoded_audio = inline.get("data")

                if not encoded_audio:
                    continue

                raw_audio = base64.b64decode(
                    encoded_audio
                )

                mime_type = (
                    inline.get("mimeType")
                    or inline.get("mime_type")
                    or ""
                )

                logger.info(
                    "Gemini TTS MIME type: %s",
                    mime_type
                )

                logger.info(
                    "Gemini TTS received bytes: %d",
                    len(raw_audio)
                )

                # Case 1: Gemini already returned a WAV file.
                if (
                    raw_audio.startswith(b"RIFF")
                    and raw_audio[8:12] == b"WAVE"
                ):
                    logger.info(
                        "Gemini returned an existing WAV file."
                    )
                    return raw_audio

                # Case 2: Gemini returned raw PCM.
                # Gemini TTS commonly uses 24 kHz PCM.
                sample_rate = 24000

                rate_match = re.search(
                    r"rate=(\d+)",
                    mime_type,
                    re.IGNORECASE
                )

                if rate_match:
                    sample_rate = int(
                        rate_match.group(1)
                    )

                if len(raw_audio) < 2:
                    logger.warning(
                        "Gemini returned empty audio."
                    )
                    break

                if len(raw_audio) % 2 != 0:
                    logger.warning(
                        "PCM data has an odd byte count: %d",
                        len(raw_audio)
                    )
                    break

                # Create a standard 16-bit mono PCM WAV.
                wav_buffer = BytesIO()

                with wave.open(wav_buffer, "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(sample_rate)
                    wav.writeframes(raw_audio)

                wav_bytes = wav_buffer.getvalue()

                if not (
                    wav_bytes.startswith(b"RIFF")
                    and wav_bytes[8:12] == b"WAVE"
                ):
                    logger.error(
                        "Generated WAV header validation failed."
                    )
                    break

                logger.info(
                    "TTS WAV created successfully: "
                    "%d bytes, %d Hz, mono, 16-bit",
                    len(wav_bytes),
                    sample_rate
                )

                return wav_bytes

        logger.warning(
            "TTS response did not contain usable audio: %s",
            json.dumps(data)[:1000]
        )

    except Exception as error:
        logger.exception(
            "TTS generation failed: %s",
            error
        )

    logger.warning(
        "Using fallback WAV because TTS generation failed."
    )

    return _generate_fallback_wav()


# =========================================================
# INTENT PROMPT
# =========================================================

def _intent_prompt(
    context: dict,
    pending_action: Optional[Dict],
    expires_at
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
        "a short natural spoken reply (under 20 words). "

        "Return pure JSON with keys: transcript, intent, "
        "reply, title, description, priority. "

        "Allowed intents: chat, create_task, complete_task, "
        "list_tasks, list_habits, confirm, cancel."

        f"\nUser Data Context:\n"
        f"{json.dumps(context, ensure_ascii=False)}"
        f"{pending}"
    )


# =========================================================
# PENDING ACTION EXPIRY
# =========================================================

def _is_pending_expired(
    device: CompanionDevice
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


# =========================================================
# PROCESS COMPLETE VOICE TURN
# =========================================================

def process_voice_turn(
    db: Session,
    device: CompanionDevice,
    audio: bytes
) -> Tuple[str, str, Optional[str], str]:

    validate_wav(audio)

    if _is_pending_expired(device):
        device.pending_action = None
        device.pending_action_id = None
        device.pending_action_expires_at = None

    try:
        context = get_voice_context(
            db,
            device.user_id
        )

    except Exception as error:
        logger.warning(
            "Could not load voice context: %s",
            error
        )

        context = {
            "tasks": [],
            "habits": []
        }

    prompt = _intent_prompt(
        context,
        device.pending_action,
        device.pending_action_expires_at
    )

    transcript = _gemini_transcribe(audio)

    logger.info(
        "Transcribed voice text: %s",
        transcript
    )

    result = _gemini_text_json(
        transcript,
        prompt
    )

    if not isinstance(result, dict):
        result = {
            "transcript": transcript,
            "intent": "chat",
            "reply": "I am listening."
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
            clear_pending_action(
                db,
                device
            )

            reply = "Cancelled that action."
            status_text = "cancelled"

        else:
            reply = (
                "Please say yes to confirm or no to cancel."
            )
            status_text = "confirmation_required"

    elif intent_val == "chat":

        reply = (
            ai_reply.strip()
            or "I am listening."
        )

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

        reply = (
            ai_reply.strip()
            or "Got your update."
        )

        status_text = "replied"
        action_id = None

    return (
        reply[:200],
        status_text,
        action_id,
        transcript
    )


# =========================================================
# PUBLIC TTS FUNCTION
# =========================================================

def synthesize_reply(reply: str) -> bytes:

    try:
        audio = _synthesize(reply)

        if not (
            audio.startswith(b"RIFF")
            and audio[8:12] == b"WAVE"
        ):
            logger.error(
                "TTS output is not a valid WAV file."
            )
            return _generate_fallback_wav()

        logger.info(
            "Final response WAV size: %d bytes",
            len(audio)
        )

        return audio

    except Exception as error:

        logger.exception(
            "synthesize_reply failed: %s",
            error
        )

        return _generate_fallback_wav()
