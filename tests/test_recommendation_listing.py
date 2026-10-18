from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database.database import Base
from app.models.recommendation import Recommendation, RecommendationStatus, RecommendationType
from app.models.user import User
from app.services.recommendation_service import latest_recommendations


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[User.__table__, Recommendation.__table__])
    with Session(engine) as session:
        session.add(
            User(
                id=1,
                username="recommendation-list-user",
                email="recommendation-list-user@example.com",
                password_hash="not-used",
            )
        )
        session.commit()
        yield session
    Base.metadata.drop_all(bind=engine, tables=[Recommendation.__table__, User.__table__])
    engine.dispose()


def add_recommendation(db, created_at, *, user_id=1, habit_id=None, rtype=None, title):
    recommendation = Recommendation(
        user_id=user_id,
        habit_id=habit_id,
        type=rtype or RecommendationType.HABIT_OVERLOAD,
        priority="MEDIUM",
        confidence="MEDIUM",
        title=title,
        reason="Account data",
        expected_benefit="Improve consistency",
        action="Review your routine",
        created_at=created_at,
    )
    db.add(recommendation)
    db.commit()
    return recommendation


def test_listing_returns_latest_recommendation_per_type_and_habit(db):
    now = datetime.now(timezone.utc)
    duplicate_records = [
        add_recommendation(db, now - timedelta(minutes=offset), title=f"Overload {offset}")
        for offset in range(4)
    ]
    other = add_recommendation(
        db,
        now,
        habit_id=8,
        rtype=RecommendationType.HABIT_RECOVERY,
        title="Routine recovery",
    )

    listed = latest_recommendations(db, user_id=1)

    assert [item.id for item in listed] == [other.id, duplicate_records[0].id]
    assert listed[1].status == RecommendationStatus.PENDING


def test_listing_keeps_users_recommendations_separate(db):
    now = datetime.now(timezone.utc)
    own = add_recommendation(db, now, title="My recommendation")
    add_recommendation(db, now, user_id=2, title="Another user's recommendation")

    listed = latest_recommendations(db, user_id=1)

    assert [item.id for item in listed] == [own.id]
