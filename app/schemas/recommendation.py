from pydantic import BaseModel
from app.models.recommendation import RecommendationStatus, RecommendationType

class RecommendationResponse(BaseModel):
    id: int
    habit_id: int | None
    type: RecommendationType
    priority: str
    confidence: str
    title: str
    reason: str
    expected_benefit: str
    action: str
    status: RecommendationStatus
    baseline_rate: float | None
    outcome_rate: float | None
    class Config:
        from_attributes = True
