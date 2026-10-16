import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database.database import Base
from app.models.recommendation import Recommendation, RecommendationStatus, RecommendationType
from app.models.user import User
from app.services.recommendation_service import _rec


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
                username="recommendation-user",
                email="recommendation-user@example.com",
                password_hash="not-used",
            )
        )
        session.commit()
        yield session
    Base.metadata.drop_all(bind=engine, tables=[Recommendation.__table__, User.__table__])
    engine.dispose()


def generate(db, reason, title="Review routine"):
    return _rec(
        db,
        user_id=1,
        habit=None,
        rtype=RecommendationType.HABIT_OVERLOAD,
        priority="MEDIUM",
        confidence="MEDIUM",
        title=title,
        reason=reason,
        benefit="Improve consistency.",
        action="Review your routine load.",
    )


def test_pending_recommendation_is_updated_instead_of_duplicated(db):
    original = generate(db, "Initial account data.")

    updated = generate(db, "Updated account data.", title="Review current routine load")

    assert updated.id == original.id
    assert updated.reason == "Updated account data."
    assert updated.title == "Review current routine load"
    assert db.scalar(select(func.count()).select_from(Recommendation)) == 1


def test_existing_accepted_recommendation_history_is_preserved(db):
    original = generate(db, "Initial account data.")
    original.status = RecommendationStatus.ACCEPTED
    db.commit()

    repeated = generate(db, "New account data.")

    assert repeated.id == original.id
    assert repeated.status == RecommendationStatus.ACCEPTED
    assert repeated.reason == "Initial account data."
    assert db.scalar(select(func.count()).select_from(Recommendation)) == 1
