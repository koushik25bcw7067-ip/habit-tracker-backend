from typing import Literal
from pydantic import BaseModel, Field

class AIAnalyzeRequest(BaseModel):
    days: int = Field(default=30, ge=7, le=365)

class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)

class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)

class ChatResponse(BaseModel):
    reply: str
    source_status: str

class NewHabitSuggestionRequest(BaseModel):
    goal: str = Field(min_length=3, max_length=500)

class AIInsightResponse(BaseModel):
    id: int
    insight_type: str
    content: dict
    created_at: object
    class Config:
        from_attributes = True
