import secrets
import wave
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.database.database import Base
from app.database.dependencies import get_db
from app.main import app
from app.models.achievement import Achievement, UserAchievement
from app.models.category import Category
from app.models.companion_device import CompanionDevice
from app.models.completion import HabitCompletion
from app.models.habit import Habit
from app.models.user import User
from app.models.user_task import UserTask
from app.routers.users import current_user
from app.routers import companion as companion_router
from app.services import voice_service


def make_wav():
    stream = BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 1600)
    return stream.getvalue()


@pytest.fixture
def voice_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        User.__table__,
        UserTask.__table__,
        Category.__table__,
        Habit.__table__,
        HabitCompletion.__table__,
        Achievement.__table__,
        UserAchievement.__table__,
        CompanionDevice.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with testing_session() as db:
        db.add(
            User(
                id=1,
                username="voice-actions-user",
                email="voice-actions@example.com",
                password_hash="not-used",
            )
        )
        db.commit()

    def override_get_db():
        with testing_session() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=1)
    monkeypatch.setattr(companion_router, "SessionLocal", testing_session)
    monkeypatch.setattr(voice_service, "synthesize_reply", lambda _reply: b"fake wav")
    monkeypatch.setattr(companion_router, "synthesize_reply", lambda _reply: b"fake wav")
    with testing_session() as db:
        device = CompanionDevice(
            user_id=1,
            name="Test device",
            credential_hash=secrets.token_hex(32),
            paired_at=datetime.now(timezone.utc),
        )
        db.add(device)
        db.commit()
        db.refresh(device)
        device_id = device.id

    with TestClient(app) as test_client:
        yield test_client, testing_session, device_id
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine, tables=list(reversed(tables)))
    engine.dispose()


def run_intents(testing_session, device_id, monkeypatch, intents):
    results = iter(intents)
    monkeypatch.setattr(
        voice_service, "_gemini_json", lambda _audio, _prompt: next(results)
    )
    replies = []
    for _ in intents:
        with testing_session() as db:
            device = db.get(CompanionDevice, device_id)
            replies.append(voice_service.process_voice_turn(db, device, make_wav()))
    return replies


def test_voice_creates_task_and_updates_its_deadline(
    voice_client, monkeypatch
):
    _, testing_session, device_id = voice_client
    first_due_date = date.today() + timedelta(days=2)
    changed_due_date = date.today() + timedelta(days=5)
    run_intents(
        testing_session,
        device_id,
        monkeypatch,
        [
            {
                "transcript": "Create a task to review notes",
                "intent": "create_task",
                "title": "Review notes",
                "due_date": first_due_date.isoformat(),
            },
            {"transcript": "Yes please", "intent": "confirm"},
        ],
    )
    with testing_session() as db:
        task = db.scalar(select(UserTask).where(UserTask.title == "Review notes"))
        assert task is not None
        assert task.due_date == first_due_date
        task_id = task.id

    run_intents(
        testing_session,
        device_id,
        monkeypatch,
        [
            {
                "transcript": "Move the deadline",
                "intent": "update_task",
                "target_id": task_id,
                "due_date": changed_due_date.isoformat(),
            },
            {"transcript": "Go ahead", "intent": "confirm"},
        ],
    )
    with testing_session() as db:
        task = db.get(UserTask, task_id)
        assert task is not None
        assert task.due_date == changed_due_date


def test_voice_completes_habit_and_weekly_progress_includes_it(
    voice_client, monkeypatch
):
    client, testing_session, device_id = voice_client
    with testing_session() as db:
        habit = Habit(
            user_id=1,
            name="Read",
            start_date=date.today(),
        )
        db.add(habit)
        db.commit()
        db.refresh(habit)
        habit_id = habit.id

    run_intents(
        testing_session,
        device_id,
        monkeypatch,
        [
            {
                "transcript": "Mark my reading habit complete",
                "intent": "complete_habit",
                "target_id": habit_id,
            },
            {"transcript": "Yes, please!", "intent": "confirm"},
        ],
    )
    with testing_session() as db:
        completion = db.scalar(
            select(HabitCompletion).where(
                HabitCompletion.habit_id == habit_id,
                HabitCompletion.completion_date == date.today(),
            )
        )
        assert completion is not None

    weekly = client.get("/statistics/weekly")
    assert weekly.status_code == 200
    assert weekly.json()["total_completions"] == 1
    assert weekly.json()["completion_rate"] == 100.0
    result = run_intents(
        testing_session,
        device_id,
        monkeypatch,
        [{"transcript": "How was my weekly progress?", "intent": "weekly_progress"}],
    )
    assert "1 habits" in result[0][0]
    assert "100.0 percent" in result[0][0]


def test_voice_creates_updates_habit_and_confirms_task_deletion(
    voice_client, monkeypatch
):
    _, testing_session, device_id = voice_client
    run_intents(
        testing_session,
        device_id,
        monkeypatch,
        [
            {
                "transcript": "Create a daily habit to stretch",
                "intent": "create_habit",
                "title": "Stretch",
                "frequency": "daily",
            },
            {"transcript": "Yes", "intent": "confirm"},
        ],
    )
    with testing_session() as db:
        habit = db.scalar(select(Habit).where(Habit.name == "Stretch"))
        assert habit is not None
        habit_id = habit.id
        task = UserTask(user_id=1, title="Remove later")
        db.add(task)
        db.commit()
        db.refresh(task)
        task_id = task.id

    run_intents(
        testing_session,
        device_id,
        monkeypatch,
        [
            {
                "transcript": "Rename my stretching habit",
                "intent": "update_habit",
                "target_id": habit_id,
                "title": "Morning stretch",
            },
            {"transcript": "Yes", "intent": "confirm"},
            {
                "transcript": "Delete the old task",
                "intent": "delete_task",
                "target_id": task_id,
            },
            {"transcript": "No, not now", "intent": "cancel"},
            {
                "transcript": "Delete the old task",
                "intent": "delete_task",
                "target_id": task_id,
            },
            {"transcript": "Yes, do that", "intent": "confirm"},
        ],
    )
    with testing_session() as db:
        habit = db.get(Habit, habit_id)
        task = db.get(UserTask, task_id)
        assert habit is not None
        assert habit.name == "Morning stretch"
        assert task is None


def test_voice_can_read_tasks_and_habits(voice_client, monkeypatch):
    _, testing_session, device_id = voice_client
    with testing_session() as db:
        db.add(UserTask(user_id=1, title="Review notes"))
        db.add(Habit(user_id=1, name="Walk", start_date=date.today()))
        db.commit()

    results = run_intents(
        testing_session,
        device_id,
        monkeypatch,
        [
            {"transcript": "What tasks do I have?", "intent": "list_tasks"},
            {"transcript": "What habits do I have?", "intent": "list_habits"},
        ],
    )
    assert "Review notes" in results[0][0]
    assert "Walk" in results[1][0]


def test_laptop_voice_endpoint_requires_authenticated_session_and_returns_transcript(
    voice_client, monkeypatch
):
    client, testing_session, _ = voice_client
    intents = iter(
        [
            {
                "transcript": "Create a task to review notes",
                "intent": "create_task",
                "title": "Review notes",
            },
            {"transcript": "Yes please", "intent": "confirm"},
        ]
    )
    monkeypatch.setattr(
        voice_service, "_gemini_json", lambda _audio, _prompt: next(intents)
    )
    session_id = secrets.token_urlsafe(32)
    headers = {"X-Voice-Session": session_id, "Content-Type": "audio/wav"}
    first = client.post("/companion/web/voice/turn", headers=headers, content=make_wav())
    assert first.status_code == 200
    assert first.headers["x-companion-transcript"] == (
        "Create%20a%20task%20to%20review%20notes"
    )
    assert first.headers["x-companion-status"] == "confirmation_required"
    assert first.content == b"fake wav"

    second = client.post("/companion/web/voice/turn", headers=headers, content=make_wav())
    assert second.status_code == 200
    assert second.headers["x-companion-transcript"] == "Yes%20please"
    assert second.headers["x-companion-status"] == "completed"
    assert second.headers["x-companion-action-type"] == "create_task"
    with testing_session() as db:
        task = db.scalar(select(UserTask).where(UserTask.title == "Review notes"))
        assert task is not None

    changes = client.get("/companion/changes")
    assert changes.status_code == 200
    assert changes.json()["action_type"] == "create_task"
