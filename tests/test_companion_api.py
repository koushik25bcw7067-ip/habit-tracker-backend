import secrets
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.database import Base
from app.database.dependencies import get_db
from app.main import app
from app.models.companion_device import CompanionDevice
from app.models.user import User
from app.routers.users import current_user
from app.services.companion_security import token_hash


@pytest.fixture
def companion_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        bind=engine, tables=[User.__table__, CompanionDevice.__table__]
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with testing_session() as db:
        db.add(
            User(
                id=1,
                username="companion-user",
                email="companion-user@example.com",
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
        yield test_client, testing_session
    app.dependency_overrides.clear()
    Base.metadata.drop_all(
        bind=engine, tables=[CompanionDevice.__table__, User.__table__]
    )
    engine.dispose()


def test_pairing_issues_one_time_code_and_revocable_device_token(companion_client):
    client, testing_session = companion_client
    pairing = client.post(
        "/companion/pairing-codes",
        json={"name": "Desk Companion"},
    )
    assert pairing.status_code == 201
    pairing_data = pairing.json()
    assert len(pairing_data["pairing_code"]) == 12

    credential = secrets.token_urlsafe(32)
    claimed = client.post(
        "/companion/claim",
        json={
            "pairing_code": pairing_data["pairing_code"],
            "credential": credential,
        },
    )
    assert claimed.status_code == 200
    assert claimed.json()["name"] == "Desk Companion"
    assert "credential_hash" not in claimed.json()

    with testing_session() as db:
        device = db.get(CompanionDevice, pairing_data["device_id"])
        assert device is not None
        assert device.credential_hash == token_hash(credential)
        assert device.credential_hash != credential

    listed = client.get("/companion/devices")
    assert listed.status_code == 200
    assert listed.json()[0]["paired_at"] is not None
    assert "credential_hash" not in listed.json()[0]

    revoked = client.delete(f"/companion/devices/{pairing_data['device_id']}")
    assert revoked.status_code == 204
    unauthorized = client.post(
        "/companion/voice/turn",
        headers={"Authorization": f"Bearer {credential}"},
        content=b"not audio",
    )
    assert unauthorized.status_code == 401


def test_claim_rejects_expired_pairing_code(companion_client):
    client, testing_session = companion_client
    pairing = client.post("/companion/pairing-codes", json={}).json()
    with testing_session() as db:
        device = db.get(CompanionDevice, pairing["device_id"])
        device.pairing_expires_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        db.commit()

    response = client.post(
        "/companion/claim",
        json={
            "pairing_code": pairing["pairing_code"],
            "credential": secrets.token_urlsafe(32),
        },
    )
    assert response.status_code == 401
