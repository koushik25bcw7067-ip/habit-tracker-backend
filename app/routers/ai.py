from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.routers.users import current_user
from app.models.ai_insight import AIInsight
from app.models.user_theme import UserTheme
from app.services.theme_service import PRESETS, persona_for_prompt
from app.services.ai_service import AIService
from app.schemas.ai import (
    AIAnalyzeRequest,
    NewHabitSuggestionRequest,
    ChatRequest,
    ChatResponse,
    CustomThemeRequest,
    CustomThemeResponse,
)
from app.schemas.ai_conversation import (
    ConversationCreate,
    ConversationMessageResponse,
    ConversationResponse,
)
from app.models.ai_conversation import AIConversation, AIConversationMessage
from app.services.conversation_service import (
    append_turn,
    create_conversation,
    get_owned_conversation,
)

router = APIRouter(prefix="/ai", tags=["AI"])


@router.get("/conversations", response_model=list[ConversationResponse])
def list_conversations(
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    return db.scalars(
        select(AIConversation)
        .where(AIConversation.user_id == user.id)
        .order_by(AIConversation.updated_at.desc(), AIConversation.id.desc())
        .limit(100)
    ).all()


@router.post(
    "/conversations",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
def new_conversation(
    data: ConversationCreate,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    return create_conversation(db, user.id, data.title)


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=list[ConversationMessageResponse],
)
def conversation_messages(
    conversation_id: int,
    limit: int = Query(default=200, ge=1, le=500),
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    conversation = get_owned_conversation(db, user.id, conversation_id)
    messages = db.scalars(
        select(AIConversationMessage)
        .where(AIConversationMessage.conversation_id == conversation.id)
        .order_by(
            AIConversationMessage.created_at.desc(),
            AIConversationMessage.id.desc(),
        )
        .limit(limit)
    ).all()
    return list(reversed(messages))


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_conversation(
    conversation_id: int,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    conversation = get_owned_conversation(db, user.id, conversation_id)
    db.delete(conversation)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/insights")
def insights(
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    return db.scalars(
        select(AIInsight)
        .where(AIInsight.user_id == user.id)
        .order_by(AIInsight.created_at.desc())
        .limit(20)
    ).all()


@router.post("/analyze")
def analyze(
    data: AIAnalyzeRequest,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    svc = AIService(db, user.id)
    content = svc.analyze(data.days)
    item = svc.save_insight("analysis", content)
    return item


@router.get("/weekly-report")
def weekly_report(
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    return AIService(db, user.id).report(7)


@router.get("/monthly-report")
def monthly_report(
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    return AIService(db, user.id).report(30)


@router.post("/suggest-habits")
def suggest(
    data: NewHabitSuggestionRequest,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    return {
        "suggestions": AIService(
            db, user.id
        ).suggest_habits(data.goal),
        "created": False,
    }


@router.post("/chat", response_model=ChatResponse)
def chat(
    data: ChatRequest,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    row = db.scalar(select(UserTheme).where(UserTheme.user_id == user.id))
    theme = (
        PRESETS.get(row.preset_key)
        if row and row.preset_key
        else (row.data if row else None)
    )
    if data.conversation_id is not None:
        conversation = get_owned_conversation(
            db, user.id, data.conversation_id
        )
    else:
        conversation = None
    result = AIService(db, user.id).chat(
        data.messages,
        persona=persona_for_prompt(theme),
    )
    if conversation is not None:
        latest_user_message = data.messages[-1].content
        append_turn(
            db,
            conversation,
            latest_user_message,
            result["reply"],
        )
        result["conversation_id"] = conversation.id
    return result


@router.post(
    "/generate-theme",
    response_model=CustomThemeResponse,
)
def create_theme(
    req: CustomThemeRequest,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    service = AIService(db, user.id)
    theme_data = service.generate_custom_theme(req.theme_name)
    return theme_data