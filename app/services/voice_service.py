import base64
import json
import logging
import re
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
    "yes",
    "yes please",
    "yes do that",
    "yes go ahead",
    "please do it",
    "yeah",
    "yep",
    "confirm",
    "confirm it",
    "do it",
    "go ahead",
    "okay",
    "ok",
    "sure",
    "please do",
    "avunu",
    "avunu chey",
    "sare",
    "ha",
    "haa",
    "cheyyi",
    "chesey",
    "అవును",
    "సరే",
    "చేయి",
}
_NO_UTTERANCES = {
    "no",
    "nope",
    "cancel",
    "cancel it",
    "don't",
    "do not",
    "not now",
    "vaddu",
    "ledu",
    "kaadu",
    "kaadhu",
    "లేదు",
    "వద్దు",
    "ఆపు",
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
                raise HTTPException(
                    status_code=422,
                    detail="Audio must be mono 16-bit PCM at 16 kHz and at most 15 seconds",
                )
    except (wave.Error, EOFError) as error:
        raise HTTPException(status_code=422, detail="Invalid WAV audio") from error


def _api_key() -> str:
    key = (getattr(settings, "ai_api_key", "") or "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="Voice AI is not configured")
    return key


def _gemini_json(audio: bytes, prompt: str) -> dict:
    model = getattr(settings, "ai_model", "gemini-3.8-flash")
    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
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
            "responseMimeType": "application/json",
            "temperature": 0.1,
            "maxOutputTokens": 500,
        },
    }
    try:
        response = httpx.post(
            endpoint,
            headers={"x-goog-api-key": _api_key()},
            json=payload,
            timeout=httpx.Timeout(45.0, connect=5.0),
        )
        response.raise_for_status()
        text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except HTTPException:
        raise
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
        logger.warning("Companion audio analysis failed: %s", type(error).__name__)
        raise HTTPException(
            status_code=502, detail="The voice service could not understand that audio"
        ) from error


def _synthesize(text: str) -> bytes:
    endpoint = "https://generativelanguage.googleapis.com/v1beta/interactions"
    payload = {
        "model": TTS_MODEL,
        "input": [
            {
                "type": "user_input",
                "content": [
                    {
                        "type": "text",
                        "text": text[:500],
                        "annotations": [
                            {
                                "type": "speech_metadata",
                                "style": "warm, clear, conversational",
                            }
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
        response = httpx.post(
            endpoint,
            headers={"x-goog-api-key": _api_key()},
            json=payload,
            timeout=httpx.Timeout(45.0, connect=5.0),
        )
        response.raise_for_status()
        body = response.json()
        for step in reversed(body.get("steps", [])):
            if step.get("type") != "model_output":
                continue
            for item in reversed(step.get("content", [])):
                if item.get("type") == "audio" and item.get("data"):
                    return base64.b64decode(item["data"], validate=True)
        raise ValueError("No audio was returned")
    except HTTPException:
        raise
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
        logger.warning("Companion speech synthesis failed: %s", type(error).__name__)
        raise HTTPException(
            status_code=502, detail="The voice service could not generate speech"
        ) from error


def _intent_prompt(context: dict, pending_action: dict | None, expires_at) -> str:
    pending = ""
    if pending_action:
        pending = (
            f"\nA write action is awaiting a direct yes/no confirmation until "
            f"{expires_at.isoformat() if expires_at else 'soon'}: "
            f"{json.dumps(pending_action, ensure_ascii=False)}. "
            "For a clear yes, set intent=confirm; for a clear no, set intent=cancel. "
            "For anything else set intent=unclear; do not replace the pending action."
        )
    return (
        "Interpret the attached short voice recording for a habit-tracker assistant. "
        "Return only a JSON object with keys transcript, intent, target_id, title, "
        "description, due_date, priority, frequency, target_days, start_date, "
        "end_date, category_id, target_value, unit. Supported intents: chat, "
        "create_task, update_task, complete_task, delete_task, complete_habit, "
        "create_habit, update_habit, list_tasks, list_habits, weekly_progress, "
        "confirm, cancel, unclear. "
        "Never claim an action has been performed. Do not invent IDs; target_id must "
        "be an ID from the supplied lists, and category_id must be an ID from the "
        "supplied categories. For updates include only fields the user "
        "explicitly asked to change. Convert relative task due dates using the "
        "provided current date and ISO YYYY-MM-DD. For a new habit use daily "
        "frequency unless the user clearly asks for a different schedule; for "
        "custom frequency provide weekday numbers with Monday=0 and Sunday=6. "
        "For habit updates use only fields explicitly requested. If the user asks "
        "to see or hear their tasks, habits, or weekly progress, use the matching "
        "read-only intent. If the user speaks Telugu or "
        "mixed Telugu-English, understand it and preserve the natural language in "
        "transcript. If uncertain, use unclear. "
        f"\nCurrent date and the user's available items:\n"
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
    db: Session, device: CompanionDevice, audio: bytes
) -> tuple[str, str, str | None, str]:
    validate_wav(audio)
    if _is_pending_expired(device):
        device.pending_action = None
        device.pending_action_id = None
        device.pending_action_expires_at = None

    context = get_voice_context(db, device.user_id)
    result = _gemini_json(
        audio,
        _intent_prompt(
            context,
            device.pending_action,
            device.pending_action_expires_at,
        ),
    )
    try:
        intent = VoiceIntent.model_validate(result)
    except ValidationError as error:
        logger.warning("Companion returned an invalid voice intent")
        raise HTTPException(
            status_code=502, detail="The voice service returned an invalid response"
        ) from error

    transcript = intent.transcript.strip()
    if device.pending_action:
        action_id = device.pending_action_id
        utterance = _normalized_confirmation(transcript)
        if intent.intent == "confirm" and utterance in _YES_UTTERANCES:
            device = db.scalar(
                select(CompanionDevice)
                .where(CompanionDevice.id == device.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if (
                device is None
                or device.pending_action_id != action_id
                or _is_pending_expired(device)
            ):
                reply = "That action is no longer pending."
                status_text = "replied"
                action_id = None
            else:
                reply = execute_pending_action(db, device)
                status_text = "completed"
        elif intent.intent == "cancel" and utterance in _NO_UTTERANCES:
            clear_pending_action(db, device)
            reply = "Okay, I cancelled that action."
            status_text = "cancelled"
        else:
            reply = "Please say yes to confirm or no to cancel the pending action."
            status_text = "confirmation_required"
    elif intent.intent == "chat":
        chat_result = AIService(db, device.user_id).chat(
            [{"role": "user", "content": transcript}]
        )
        reply = str(chat_result.get("reply", "")).strip()
        if not reply:
            raise HTTPException(status_code=502, detail="The assistant returned no reply")
        reply = reply[:500]
        status_text = "replied"
        action_id = None
    elif intent.intent in (
        "create_task",
        "update_task",
        "complete_task",
        "delete_task",
        "complete_habit",
        "create_habit",
        "update_habit",
    ):
        action, reply = prepare_action(db, device.user_id, intent)
        if action is None:
            status_text = "clarification"
            action_id = None
        else:
            action_id = store_pending_action(db, device, action)
            status_text = "confirmation_required"
    elif intent.intent == "list_tasks":
        tasks = context["tasks"]
        if not tasks:
            reply = "You don't have any tasks."
        else:
            titles = ", ".join(
                f'{task["title"]} ({task["status"]})' for task in tasks[:5]
            )
            remaining = len(tasks) - min(len(tasks), 5)
            reply = f"Your tasks are: {titles}."
            if remaining:
                reply += f" And {remaining} more."
        status_text = "replied"
        action_id = None
    elif intent.intent == "list_habits":
        habits = context["habits"]
        if not habits:
            reply = "You have no active habits."
        else:
            reply = "Your active habits are: " + ", ".join(
                habit["name"] for habit in habits[:5]
            ) + "."
            remaining = len(habits) - min(len(habits), 5)
            if remaining:
                reply += f" And {remaining} more."
        status_text = "replied"
        action_id = None
    elif intent.intent == "weekly_progress":
        end_date = date.today()
        weekly = period_statistics(
            db, device.user_id, end_date - timedelta(days=6), end_date
        )
        reply = (
            f"This week, you completed {weekly['total_completions']} habits "
            f"with a {weekly['completion_rate']} percent completion rate."
        )
        status_text = "replied"
        action_id = None
    elif intent.intent == "cancel":
        reply = "There is no pending action to cancel."
        status_text = "replied"
        action_id = None
    else:
        reply = "Sorry, I didn't catch that. Please try again."
        status_text = "clarification"
        action_id = None

    return reply, status_text, action_id, transcript


def synthesize_reply(reply: str) -> bytes:
    return _synthesize(reply)
