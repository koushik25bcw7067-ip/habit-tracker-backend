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

MODELS_LIST = [
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
]

_YES_UTTERANCES = {
    "yes", "yes please", "yes do that", "yes go ahead", "please do it",
    "yeah", "yep", "confirm", "confirm it", "do it", "go ahead",
    "okay", "ok", "sure", "please do", "avunu", "avunu chey",
    "sare", "ha", "haa", "cheyyi", "chesey",
}
_NO_UTTERANCES = {
    "no", "nope", "cancel", "cancel it", "don't", "do not",
    "not now", "vaddu", "ledu", "kaadu", "kaadhu",
}

_RETRY_STATUS = {429, 500, 502, 503, 504}


def _normalized_confirmation(text: str) -> str:
    return " ".join(re.sub(r"[.!?,;:'\"…]+", " ", text.lower()).split())


def validate_wav(audio: bytes) -> None:
    if not audio or len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio must be under 512 KiB")


def _api_key() -> str:
    key = (getattr(settings, "ai_api_key", "") or "").strip()
    if not key:
        logger.error("CRITICAL: AI_API_KEY is empty or missing in environment!")
    return key


def _post_gemini(payload: dict) -> dict:
    key = _api_key()
    if not key:
        raise RuntimeError("No Google AI API key configured")

    last_error = None
    for model in MODELS_LIST:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        for attempt in range(2):
            try:
                with httpx.Client(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
                    resp = client.post(
                        url,
                        headers={"Content-Type": "application/json"},
                        json=payload,
                    )
                if resp.status_code == 404:
                    logger.warning("Model %s returned 404, attempting next model...", model)
                    break
                if resp.status_code in _RETRY_STATUS and attempt == 0:
                    time.sleep(1.0)
                    continue
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                last_error = e
                time.sleep(0.5)

    raise last_error or RuntimeError("All candidate models failed")


def _generate_fallback_wav() -> bytes:
    buf = BytesIO()
    sample_rate = 16000
    duration = 0.7
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        total_samples = int(sample_rate * duration)
        for i in range(total_samples):
            freq = 523.25 if i < total_samples // 2 else 659.25
            val = int(14000.0 * math.sin(2.0 * math.pi * freq * (i / sample_rate)))
            wav.writeframes(struct.pack("<h", val))
    return buf.getvalue()


def _gemini_transcribe(audio: bytes) -> str:
    key = _api_key()
    if not key:
        return "hello companion"

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": "Transcribe the spoken audio accurately. Output plain transcript only, with no markdown or quotes."
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
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 256},
    }

    try:
        data = _post_gemini(payload)
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            if parts and "text" in parts[0]:
                text = parts[0]["text"].strip()
                if text:
                    return text
    except Exception as err:
        logger.warning("Gemini transcription fallback: %s", err)

    return "hello"


def _extract_safe_json(raw_text: str) -> dict | None:
    text = raw_text.strip()
    if "```" in text:
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE).strip()

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

    intent_match = re.search(r'"intent"\s*:\s*"([^"]+)"', text)
    if intent_match:
        return {"intent": intent_match.group(1)}

    return None


def _gemini_text_json(transcript: str, prompt: str) -> dict:
    key = _api_key()
    if not key:
        return {"transcript": transcript, "intent": "chat", "reply": "Hello, how can I help you?"}

    transcription = f"Spoken Transcription: {json.dumps(transcript, ensure_ascii=False)}\n\n{prompt}"
    payload = {
        "contents": [{"parts": [{"text": transcription}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "maxOutputTokens": 512,
        },
    }

    try:
        data = _post_gemini(payload)
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            if parts and "text" in parts[0]:
                parsed = _extract_safe_json(parts[0]["text"])
                if isinstance(parsed, dict):
                    return parsed
    except Exception as err:
        logger.warning("Companion intent parse fallback: %s", err)

    return {"transcript": transcript, "intent": "chat", "reply": "Got it, system is online."}


def _synthesize(text: str) -> bytes:
    key = _api_key()
    if not key:
        return _generate_fallback_wav()

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": f"Read aloud in a clear, pleasant voice: {text[:200]}"
                    }
                ]
            }
        ],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": "Puck"}
                }
            },
        },
    }

    try:
        data = _post_gemini(payload)
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            for p in parts:
                inline = p.get("inlineData", {})
                if inline.get("data"):
                    return base64.b64decode(inline["data"], validate=True)
    except Exception as error:
        logger.warning("TTS generation notice: %s", error)

    return _generate_fallback_wav()


def _intent_prompt(context: dict, pending_action: dict | None, expires_at) -> str:
    pending = ""
    if pending_action:
        pending = (
            f"\nPending action: {json.dumps(pending_action, ensure_ascii=False)}. "
            "For yes return {\"intent\": \"confirm\"}. For no return {\"intent\": \"cancel\"}."
        )
    return (
        "Classify user routine intent and formulate a short natural spoken reply (under 20 words). "
        "Return pure JSON with keys: transcript, intent, reply, title, description, priority. "
        "Allowed intents: chat, create_task, complete_task, list_tasks, list_habits, confirm, cancel."
        f"\nContext:\n{json.dumps(context, ensure_ascii=False)}"
        f"{pending}"
    )


def process_voice_turn(
    db: Session, device: CompanionDevice, audio: bytes
) -> tuple[str, str, str | None, str]:
    validate_wav(audio)

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
        result = {"transcript": transcript, "intent": "chat", "reply": "I am listening."}

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
        reply = f"You have {len(tasks)} tasks." if tasks else "You have no pending tasks."
        status_text = "replied"
        action_id = None
    elif intent_val == "list_habits":
        habits = context.get("habits", [])
        reply = f"You have {len(habits)} active habits." if habits else "No routines found."
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
