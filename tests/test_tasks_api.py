from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.database import Base
from app.database.dependencies import get_db
from app.main import app
from app.models.user import User
from app.models.user_task import UserTask
from app.routers.users import current_user


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[User.__table__, UserTask.__table__])
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with testing_session() as db:
        db.add(
            User(
                id=1,
                username="task-user",
                email="task-user@example.com",
                password_hash="not-used",
            )
        )
        db.commit()

    def override_get_db():
        with testing_session() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=1)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine, tables=[UserTask.__table__, User.__table__])
    engine.dispose()


def test_task_crud_endpoints(client):
    created = client.post(
        "/tasks",
        json={
            "title": "Submit assignment",
            "description": "Finish the report",
            "due_date": date.today().isoformat(),
            "priority": "high",
            "reminder_enabled": True,
        },
    )
    assert created.status_code == 201
    task = created.json()
    assert task["status"] == "pending"
    assert task["priority"] == "high"
    assert client.get("/tasks").json() == [task]

    task_id = task["id"]
    completed = client.post(f"/tasks/{task_id}/complete")
    assert completed.status_code == 200
    assert completed.json()["status"] == "done"

    reopened = client.post(f"/tasks/{task_id}/reopen")
    assert reopened.status_code == 200
    assert reopened.json()["status"] == "pending"

    deleted = client.delete(f"/tasks/{task_id}")
    assert deleted.status_code == 204
    assert client.get("/tasks").json() == []


def test_today_tasks_only_returns_pending_overdue_and_due_today(client):
    response = client.post(
        "/tasks",
        json={"title": "Overdue", "due_date": (date.today() - timedelta(days=1)).isoformat()},
    )
    overdue = response.json()
    client.post(
        "/tasks",
        json={"title": "Due today", "due_date": date.today().isoformat()},
    )
    client.post(
        "/tasks",
        json={"title": "Future", "due_date": (date.today() + timedelta(days=1)).isoformat()},
    )
    client.post(f"/tasks/{overdue['id']}/complete")

    today = client.get("/tasks/today")
    assert today.status_code == 200
    assert [task["title"] for task in today.json()["overdue"]] == []
    assert [task["title"] for task in today.json()["due_today"]] == ["Due today"]


def test_tasks_are_user_scoped(client):
    created = client.post("/tasks", json={"title": "Private task"})
    task_id = created.json()["id"]
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=2)

    assert client.get("/tasks").json() == []
    assert client.post(f"/tasks/{task_id}/complete").status_code == 404
    assert client.delete(f"/tasks/{task_id}").status_code == 404
