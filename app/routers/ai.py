from fastapi import APIRouter,Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database.dependencies import get_db
from app.routers.users import current_user
from app.models.ai_insight import AIInsight
from app.services.ai_service import AIService
from app.schemas.ai import AIAnalyzeRequest,NewHabitSuggestionRequest,ChatRequest,ChatResponse
router=APIRouter(prefix="/ai",tags=["AI"])

@router.get("/insights")
def insights(user=Depends(current_user),db:Session=Depends(get_db)):
    return db.scalars(select(AIInsight).where(AIInsight.user_id==user.id).order_by(AIInsight.created_at.desc()).limit(20)).all()

@router.post("/analyze")
def analyze(data:AIAnalyzeRequest,user=Depends(current_user),db:Session=Depends(get_db)):
    svc=AIService(db,user.id);content=svc.analyze(data.days);item=svc.save_insight("analysis",content);return item

@router.get("/weekly-report")
def weekly_report(user=Depends(current_user),db:Session=Depends(get_db)):
    return AIService(db,user.id).report(7)

@router.get("/monthly-report")
def monthly_report(user=Depends(current_user),db:Session=Depends(get_db)):
    return AIService(db,user.id).report(30)

@router.post("/suggest-habits")
def suggest(data:NewHabitSuggestionRequest,user=Depends(current_user),db:Session=Depends(get_db)):
    return {"suggestions":AIService(db,user.id).suggest_habits(data.goal),"created":False}

@router.post("/chat",response_model=ChatResponse)
def chat(data:ChatRequest,user=Depends(current_user),db:Session=Depends(get_db)):
    return AIService(db,user.id).chat(data.messages)
