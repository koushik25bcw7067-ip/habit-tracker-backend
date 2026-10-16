import wave
from datetime import datetime, timezone
from io import BytesIO

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.database import Base
from app.models.companion_device import CompanionDevice
from app.models.category import Category
from app.models.habit import Habit
from app.models.user import User
from app.models.user_task import UserTask
from app.services import voice_service
from app.services.voice_service import (
    MAX_AUDIO_SECONDS,
    _normalized_confirmation,
    validate_wav,
)


def make_wav(duration_seconds=1):
    stream = BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 16000 * duration_seconds)
    return stream.getvalue()


def test_validate_wav_accepts_expected_push_to_talk_format():
    validate_wav(make_wav())


def test_validate_wav_rejects_stereo_audio():
    stream = BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00\x00\x00" * 100)

    with pytest.raises(HTTPException) as error:
        validate_wav(stream.getvalue())
    assert error.value.status_code == 422


def test_validate_wav_rejects_audio_longer_than_limit():
    with pytest.raises(HTTPException) as error:
        validate_wav(make_wav(MAX_AUDIO_SECONDS + 1))
    assert error.value.status_code == 422


def test_gemini_transcription_sends_audio_and_preserves_telugu_text(monkeypatch):
    monkeypatch.setattr(voice_service.settings, "ai_api_key", "gemini-test-key")
    monkeypatch.setattr(voice_service.settings, "ai_model", "gemini-test-model")
    captured = {}

    class Response:
        def raise_for_status(self):
            pass

        @staticmethod
        def json():
            return {
                "candidates": [
                    {"content": {"parts": [{"text": "నా routines complete చెయ్యి"}]}}
                ]
            }

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr(voice_service.httpx, "post", fake_post)

    transcript = voice_service._gemini_transcribe(make_wav())

    assert transcript == "నా routines complete చెయ్యి"
    assert captured["url"].endswith("/models/gemini-test-model:generateContent")
    assert captured["headers"] == {"x-goog-api-key": "gemini-test-key"}
    parts = captured["json"]["contents"][0]["parts"]
    assert "Telugu script" in parts[0]["text"]
    assert parts[1]["inline_data"]["mime_type"] == "audio/wav"
    assert captured["json"]["generationConfig"]["temperature"] == 0


def test_gemini_transcription_requires_existing_gemini_key(monkeypatch):
    monkeypatch.setattr(voice_service.settings, "ai_api_key", "")

    with pytest.raises(HTTPException) as error:
        voice_service._gemini_transcribe(make_wav())

    assert error.value.status_code == 503


def test_gemini_transcription_surfaces_provider_failures(monkeypatch):
    import httpx

    monkeypatch.setattr(voice_service.settings, "ai_api_key", "gemini-test-key")

    def fail_request(*_args, **_kwargs):
        raise httpx.ConnectError("provider unavailable")

    monkeypatch.setattr(voice_service.httpx, "post", fail_request)

    with pytest.raises(HTTPException) as error:
        voice_service._gemini_transcribe(make_wav())

    assert error.value.status_code == 502
    assert "Could not reach Gemini" in error.value.detail


def test_gemini_transcription_exposes_provider_http_error(monkeypatch):
    import httpx

    monkeypatch.setattr(voice_service.settings, "ai_api_key", "gemini-test-key")
    request = httpx.Request(
        "POST", "https://generativelanguage.googleapis.com/v1beta/test"
    )
    response = httpx.Response(
        404,
        request=request,
        json={"error": {"message": "Model not found: gemini-test-model"}},
    )

    monkeypatch.setattr(
        voice_service.httpx,
        "post",
        lambda *_args, **_kwargs: response.raise_for_status(),
    )

    with pytest.raises(HTTPException) as error:
        voice_service._gemini_transcribe(make_wav())

    assert error.value.status_code == 502
    assert error.value.detail == (
        "Gemini transcription failed (HTTP 404): Model not found: gemini-test-model"
    )


def test_gemini_transcription_limits_provider_error_text(monkeypatch):
    import httpx

    monkeypatch.setattr(voice_service.settings, "ai_api_key", "gemini-test-key")
    request = httpx.Request(
        "POST", "https://generativelanguage.googleapis.com/v1beta/test"
    )
    response = httpx.Response(
        429,
        request=request,
        json={"error": {"message": "quota exceeded " + "x" * 500}},
    )

    monkeypatch.setattr(
        voice_service.httpx,
        "post",
        lambda *_args, **_kwargs: response.raise_for_status(),
    )

    with pytest.raises(HTTPException) as error:
        voice_service._gemini_transcribe(make_wav())

    assert error.value.status_code == 502
    assert len(error.value.detail) < 400


def test_gemini_transcription_rejects_blank_audio_result(monkeypatch):
    monkeypatch.setattr(voice_service.settings, "ai_api_key", "gemini-test-key")

    class Response:
        def raise_for_status(self):
            pass

        @staticmethod
        def json():
            return {"candidates": [{"content": {"parts": [{"text": "  "}]}}]}

    monkeypatch.setattr(
        voice_service.httpx, "post", lambda *_args, **_kwargs: Response()
    )

    with pytest.raises(HTTPException) as error:
        voice_service._gemini_transcribe(make_wav())

    assert error.value.status_code == 422


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("Yes, please!", "yes please"),
        ("అవును.", "అవును"),
        ("No, not now.", "no not now"),
    ],
)
def test_confirmation_utterance_normalization(spoken, expected):
    assert _normalized_confirmation(spoken) == expected


def test_task_write_waits_for_clear_confirmation(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        bind=engine,
        tables=[
            User.__table__,
            Category.__table__,
            Habit.__table__,
            UserTask.__table__,
            CompanionDevice.__table__,
        ],
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with testing_session() as db:
        db.add(
            User(
                id=1,
                username="voice-user",
                email="voice-user@example.com",
                password_hash="not-used",
            )
        )
        db.flush()
        device = CompanionDevice(
            user_id=1,
            name="Test device",
            credential_hash="test-hash",
            paired_at=datetime.now(timezone.utc),
        )
        db.add(device)
        db.commit()
        db.refresh(device)

        intents = iter(
            [
                {
                    "transcript": "Create a task to review notes",
                    "intent": "create_task",
                    "title": "Review notes",
                },
                {
                    "transcript": "Yes, but not that one",
                    "intent": "confirm",
                },
                {"transcript": "Yes, please!", "intent": "confirm"},
            ]
        )
        transcripts = iter(
            [
                "Create a task to review notes",
                "Yes, but not that one",
                "Yes, please!",
            ]
        )
        monkeypatch.setattr(
            voice_service,
            "_gemini_transcribe",
            lambda _audio: next(transcripts),
        )
        monkeypatch.setattr(
            voice_service,
            "_gemini_text_json",
            lambda _transcript, _prompt: {
                **next(intents),
                "transcript": "Intent model output must not replace exact transcription",
            },
        )
        reply, status_text, action_id, transcript = voice_service.process_voice_turn(
            db, device, make_wav()
        )
        assert transcript == "Create a task to review notes"
        assert status_text == "confirmation_required"
        assert action_id
        assert "Review notes" in reply
        assert db.scalar(select(UserTask)) is None

        _, status_text, _, _ = voice_service.process_voice_turn(
            db, device, make_wav()
        )
        assert status_text == "confirmation_required"
        assert db.scalar(select(UserTask)) is None

        reply, status_text, _, _ = voice_service.process_voice_turn(
            db, device, make_wav()
        )
        assert status_text == "completed"
        assert "Review notes" in reply
        task = db.scalar(select(UserTask))
        assert task is not None
        assert task.title == "Review notes"

    engine.dispose()
