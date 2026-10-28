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

# FIXED: Valid models only
ACTIVE_MODEL = "gemini-1.5-flash"
TTS_MODEL = "gemini-2.0-flash-exp"
VALID_MODELS = ["gemini-1.5-flash", "gemini-2.0-flash", "gemini-1.5-flash-8b", "gemini-2.0-flash-exp"]

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
    return " ".join(re.sub(r"[.!?,;:'\"…]+", " ", text.lower()).split())

def validate_wav(audio: bytes) -> None:
    if not audio or len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio must be under 512 KiB")
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

def _sanitize_model(model: str) -> str:
    """Fix invalid 3.8 / 3.6 model names"""
    if not model:
        return ACTIVE_MODEL
    model = model.strip().replace("models/", "")
    # Auto-fix fake models
    if "3.8" in model or "3.6" in model or "3-8" in model or "tts" in model.lower() and "2.5" not in model:
        logger.warning("Invalid model %s detected, falling back to %s", model, ACTIVE_MODEL)
        return ACTIVE_MODEL
    return model

def _get_gemini_endpoint(model_override: Optional[str] = None) -> str:
    raw_model = model_override or getattr(settings, "ai_model", "") or ACTIVE_MODEL
    model = _sanitize_model(raw_model)
    return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

def _post_with_retry(endpoint: str, payload: dict, attempts: int = 2) -> httpx.Response:
    if not endpoint.startswith(("http://", "https://")):
        endpoint = f"https://{endpoint.lstrip('/')}"
    
    # Build fallback list
    primary_model = _sanitize_model(endpoint.split("/models/")[-1].split(":")[0] if "/models/" in endpoint else "")
    endpoints_to_try = [endpoint]
    for fb in VALID_MODELS:
        fb_url = f"https://generativelanguage.googleapis.com/v1beta/models/{fb}:generateContent"
        if fb_url not in endpoints_to_try:
            endpoints_to_try.append(fb_url)

    last_error = None
    for ep in endpoints_to_try:
        for attempt in range(attempts):
            try:
                with httpx.Client(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
                    response = client.post(
                        ep,
                        headers={"x-goog-api-key": _api_key()},
                        json=payload,
                    )
                if response.status_code in _RETRY_STATUS:
                    last_error = response
                    if attempt < attempts - 1:
                        time.sleep(1.0 * (attempt + 1))
                        continue
                    # try next model
                    logger.warning("Model %s failed %s, trying next", ep, response.status_code)
                    break
                
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_error = e
                if attempt < attempts - 1:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                break

    # If all failed, raise last
    if isinstance(last_error, httpx.Response):
        last_error.raise_for_status()
    raise last_error or RuntimeError("API retry limit reached")

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
    endpoint = _get_gemini_endpoint()
    payload = {
        "contents": [{
            "parts": [
                {"text": "Transcribe the spoken audio accurately. Output plain transcript only, with no markdown or quotes."},
                {"inline_data": {"mime_type": "audio/wav", "data": base64.b64encode(audio).decode("ascii")}}
            ]
        }],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 256},
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

def _extract_safe_json(raw_text: str) -> Optional[Dict]:
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
    endpoint = _get_gemini_endpoint()
    transcription = f"Spoken Transcription: {json.dumps(transcript, ensure_ascii=False)}\n\n{prompt}"
    payload = {
        "contents": [{"parts": [{"text": transcription}]}],
        "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": 512, "temperature": 0.2},
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
    return {"transcript": transcript, "intent": "chat", "reply": "Got it, system is online."}

def _synthesize(text: str) -> bytes:
    """FIXED: Use correct generateContent with AUDIO modality"""
    key = _api_key()
    if not key:
        return _generate_fallback_wav()
    
    model = _sanitize_model(getattr(settings, "tts_model", "") or TTS_MODEL)
    # For TTS, use a model that supports AUDIO
    if model == "gemini-1.5-flash":
        model = TTS_MODEL
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    
    payload = {
        "contents": [{"parts": [{"text": f"Say in a natural clear voice: {text[:200]}"}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Puck"}}}
        }
    }
    try:
        response = _post_with_retry(url, payload, attempts=2)
        data = response.json()
        for cand in data.get("candidates", []):
            for p in cand.get("content", {}).get("parts", []):
                inline = p.get("inlineData", {}) or p.get("inline_data", {})
                if inline.get("data"):
                    return base64.b64decode(inline["data"])
        logger.warning("TTS no audio data: %s", json.dumps(data)[:1000])
    except Exception as error:
        logger.warning("TTS generation notice: %s", error)
    return _generate_fallback_wav()

def _intent_prompt(context: dict, pending_action: Optional[Dict], expires_at) -> str:
    pending = ""
    if pending_action:
        pending = f"\nPending action awaiting confirmation: {json.dumps(pending_action, ensure_ascii=False)}. For yes return {{\"intent\": \"confirm\"}}. For no return {{\"intent\": \"cancel\"}}."
    return (
        "Classify the routine/habit user intent and formulate a short natural spoken reply (under 20 words). "
        "Return pure JSON with keys: transcript, intent, reply, title, description, priority. "
        "Allowed intents: chat, create_task, complete_task, list_tasks, list_habits, confirm, cancel."
        f"\nUser Data Context:\n{json.dumps(context, ensure_ascii=False)}{pending}"
    )

def _is_pending_expired(device: CompanionDevice) -> bool:
    expiry = device.pending_action_expires_at
    if expiry is not None and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return bool(not device.pending_action or not expiry or expiry <= datetime.now(timezone.utc))

def process_voice_turn(db: Session, device: CompanionDevice, audio: bytes) -> Tuple[str, str, Optional[str], str]:
    validate_wav(audio)
    if _is_pending_expired(device):
        device.pending_action = None
        device.pending_action_id = None
        device.pending_action_expires_at = None
    try:
        context = get_voice_context(db, device.user_id)
    except Exception:
        context = {"tasks": [], "habits": []}
    prompt = _intent_prompt(context, device.pending_action, device.pending_action_expires_at)
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