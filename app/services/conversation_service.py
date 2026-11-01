from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_conversation import AIConversation, AIConversationMessage
from app.models.companion_device import CompanionDevice


def create_conversation(
    db: Session,
    user_id: int,
    title: str = "New chat",
    source: str = "web",
    device_id: int | None = None,
) -> AIConversation:
    conversation = AIConversation(
        user_id=user_id,
        title=title.strip() or "New chat",
        source=source,
        device_id=device_id,
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def get_owned_conversation(
    db: Session, user_id: int, conversation_id: int
) -> AIConversation:
    conversation = db.scalar(
        select(AIConversation).where(
            AIConversation.id == conversation_id,
            AIConversation.user_id == user_id,
        )
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


def append_turn(
    db: Session,
    conversation: AIConversation,
    user_text: str,
    assistant_text: str,
) -> None:
    now = datetime.now(timezone.utc)
    is_first_turn = db.scalar(
        select(AIConversationMessage.id)
        .where(AIConversationMessage.conversation_id == conversation.id)
        .limit(1)
    ) is None
    if is_first_turn:
        conversation.title = user_text.strip().replace("\n", " ")[:120] or "New chat"
    db.add_all(
        [
            AIConversationMessage(
                conversation_id=conversation.id,
                role="user",
                content=user_text,
                created_at=now,
            ),
            AIConversationMessage(
                conversation_id=conversation.id,
                role="assistant",
                content=assistant_text,
                created_at=now,
            ),
        ]
    )
    conversation.updated_at = now
    db.commit()


def get_or_create_device_conversation(
    db: Session, device: CompanionDevice
) -> AIConversation:
    conversation = db.scalar(
        select(AIConversation).where(AIConversation.device_id == device.id)
    )
    if conversation is None:
        source = "browser_voice" if device.web_session_hash else "hardware"
        conversation = create_conversation(
            db,
            user_id=device.user_id,
            title=device.name,
            source=source,
            device_id=device.id,
        )
    return conversation


def get_recent_context(
    db: Session, conversation_id: int, limit: int = 12
) -> list[dict[str, str]]:
    messages = db.scalars(
        select(AIConversationMessage)
        .where(AIConversationMessage.conversation_id == conversation_id)
        .order_by(AIConversationMessage.created_at.desc(), AIConversationMessage.id.desc())
        .limit(limit)
    ).all()
    messages.reverse()
    return [{"role": message.role, "content": message.content} for message in messages]
