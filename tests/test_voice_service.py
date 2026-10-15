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
        monkeypatch.setattr(
            voice_service, "_gemini_json", lambda _audio, _prompt: next(intents)
        )
        reply, status_text, action_id, _ = voice_service.process_voice_turn(
            db, device, make_wav()
        )
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
