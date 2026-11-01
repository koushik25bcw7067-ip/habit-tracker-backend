from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.database import Base
from app.database.dependencies import get_db
from app.main import app
from app.models.ai_conversation import AIConversation, AIConversationMessage
from app.models.companion_device import CompanionDevice
from app.models.user import User
from app.models.user_theme import UserTheme
from app.routers.users import current_user
from app.services.ai_service import AIService


@pytest.fixture
def conversation_client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        User.__table__,
        CompanionDevice.__table__,
        AIConversation.__table__,
        AIConversationMessage.__table__,
        UserTheme.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with testing_session() as db:
        db.add_all(
            [
                User(
                    id=1,
                    username="conversation-one",
                    email="conversation-one@example.com",
                    password_hash="not-used",
                ),
                User(
                    id=2,
                    username="conversation-two",
                    email="conversation-two@example.com",
                    password_hash="not-used",
                ),
            ]
        )
        db.commit()

    def override_get_db():
        with testing_session() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=1)
    monkeypatch.setattr(
        AIService,
        "chat",
        lambda *_args, **_kwargs: {
            "reply": "Hello from the server-side model.",
            "source_status": "gemini",
        },
    )
    with TestClient(app) as client:
        yield client, testing_session
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine, tables=list(reversed(tables)))
    engine.dispose()


def test_chat_is_persisted_and_conversations_are_user_scoped(conversation_client):
    client, testing_session = conversation_client
    created = client.post("/ai/conversations", json={"title": "New chat"})
    assert created.status_code == 201
    conversation_id = created.json()["id"]

    response = client.post(
        "/ai/chat",
        json={
            "conversation_id": conversation_id,
            "messages": [{"role": "user", "content": "Help me focus today"}],
        },
    )
    assert response.status_code == 200
    assert response.json()["conversation_id"] == conversation_id
    assert response.json()["reply"] == "Hello from the server-side model."

    history = client.get(f"/ai/conversations/{conversation_id}/messages")
    assert [(item["role"], item["content"]) for item in history.json()] == [
        ("user", "Help me focus today"),
        ("assistant", "Hello from the server-side model."),
    ]
    listed = client.get("/ai/conversations")
    assert listed.status_code == 200
    assert listed.json()[0]["title"] == "Help me focus today"

    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=2)
    assert client.get("/ai/conversations").json() == []
    assert client.get(f"/ai/conversations/{conversation_id}/messages").status_code == 404
    assert client.delete(f"/ai/conversations/{conversation_id}").status_code == 404

    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=1)
    assert client.delete(f"/ai/conversations/{conversation_id}").status_code == 204
    with testing_session() as db:
        assert db.query(AIConversation).count() == 0
        assert db.query(AIConversationMessage).count() == 0


def test_chat_provider_failure_does_not_save_a_fake_turn(
    conversation_client, monkeypatch
):
    from fastapi import HTTPException

    client, _ = conversation_client
    conversation_id = client.post("/ai/conversations", json={}).json()["id"]

    def fail_chat(*_args, **_kwargs):
        raise HTTPException(status_code=502, detail="Gemini unavailable")

    monkeypatch.setattr(AIService, "chat", fail_chat)
    response = client.post(
        "/ai/chat",
        json={
            "conversation_id": conversation_id,
            "messages": [{"role": "user", "content": "Is the model available?"}],
        },
    )
    assert response.status_code == 502
    assert client.get(f"/ai/conversations/{conversation_id}/messages").json() == []
