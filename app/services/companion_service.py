import secrets
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.category import Category
from app.models.companion_device import CompanionDevice
from app.models.completion import HabitCompletion
from app.models.habit import Habit
from app.models.user_task import UserTask
from app.schemas.companion import DeviceClaim, VoiceIntent
from app.schemas.completion import CompletionCreate
from app.schemas.habit import HabitCreate, HabitUpdate
from app.schemas.task import TaskCreate, TaskUpdate
from app.services.companion_security import (
    constant_time_hash_matches,
    enforce_rate_limit,
    token_hash,
)
from app.services.habit_service import (
    create as create_habit,
    complete as complete_habit,
    get_owned_habit,
    update as update_habit,
)
from app.services.task_service import (
    complete as complete_task,
    create as create_task,
    delete as delete_task,
    update as update_task,
)
from app.utils.helpers import scheduled_on

PAIRING_LIFETIME = timedelta(minutes=5)
ACTION_LIFETIME = timedelta(seconds=60)


def create_pairing_code(
    db: Session, user_id: int, name: str, requester_key: str
) -> tuple[CompanionDevice, str]:
    enforce_rate_limit(f"pairing-user:{user_id}", 3)
    enforce_rate_limit(f"pairing-ip:{requester_key}", 10)
    now = datetime.now(timezone.utc)
    for pending in db.scalars(
        select(CompanionDevice).where(
            CompanionDevice.user_id == user_id,
            CompanionDevice.paired_at.is_(None),
            CompanionDevice.revoked_at.is_(None),
        )
    ):
        pending.pairing_expires_at = now

    code = secrets.token_hex(6).upper()
    device = CompanionDevice(
        user_id=user_id,
        name=name.strip(),
        pairing_code_hash=token_hash(code),
        pairing_expires_at=now + PAIRING_LIFETIME,
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    return device, code


def claim_device(db: Session, data: DeviceClaim, requester_key: str) -> CompanionDevice:
    enforce_rate_limit(f"claim-ip:{requester_key}", 20)
    now = datetime.now(timezone.utc)
    code_hash = token_hash(data.pairing_code)
    candidates = db.scalars(
        select(CompanionDevice).where(
            CompanionDevice.pairing_code_hash == code_hash,
            CompanionDevice.revoked_at.is_(None),
        )
    ).all()
    device = next(
        (
            item
            for item in candidates
            if item.pairing_expires_at
            and (
                item.pairing_expires_at.replace(tzinfo=timezone.utc)
                if item.pairing_expires_at.tzinfo is None
                else item.pairing_expires_at
            )
            > now
            and constant_time_hash_matches(
                data.pairing_code, item.pairing_code_hash or ""
            )
        ),
        None,
    )
    if device is None:
        raise HTTPException(status_code=401, detail="Pairing code is invalid or expired")

    digest = token_hash(data.credential)
    if device.paired_at is not None:
        if not device.credential_hash or not constant_time_hash_matches(
            data.credential, device.credential_hash
        ):
            raise HTTPException(status_code=409, detail="Device has already been paired")
        return device

    device.credential_hash = digest
    device.paired_at = now
    db.commit()
    db.refresh(device)
    return device


def list_devices(db: Session, user_id: int) -> list[CompanionDevice]:
    return db.scalars(
        select(CompanionDevice)
        .where(
            CompanionDevice.user_id == user_id,
            CompanionDevice.credential_hash.is_not(None),
        )
        .order_by(CompanionDevice.created_at.desc())
    ).all()


def revoke_device(db: Session, user_id: int, device_id: int) -> None:
    device = db.scalar(
        select(CompanionDevice).where(
            CompanionDevice.id == device_id,
            CompanionDevice.user_id == user_id,
        )
    )
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    device.revoked_at = datetime.now(timezone.utc)
    device.pending_action = None
    device.pending_action_id = None
    device.pending_action_expires_at = None
    db.commit()


def get_voice_context(db: Session, user_id: int) -> dict:
    tasks = db.scalars(
        select(UserTask)
        .where(UserTask.user_id == user_id)
        .order_by(UserTask.due_date, UserTask.id)
        .limit(50)
    ).all()
    habits = db.scalars(
        select(Habit)
        .where(
            Habit.user_id == user_id,
            Habit.active.is_(True),
            Habit.archived.is_(False),
        )
        .order_by(Habit.id)
        .limit(50)
    ).all()
    categories = db.scalars(
        select(Category)
        .where((Category.user_id == user_id) | (Category.user_id.is_(None)))
        .order_by(Category.id)
        .limit(50)
    ).all()
    return {
        "today": date.today().isoformat(),
        "tasks": [
            {
                "id": task.id,
                "title": task.title,
                "due_date": task.due_date.isoformat() if task.due_date else None,
                "priority": task.priority,
                "status": task.status,
            }
            for task in tasks
        ],
        "habits": [{"id": habit.id, "name": habit.name} for habit in habits],
        "categories": [
            {"id": category.id, "name": category.name} for category in categories
        ],
    }


def prepare_action(
    db: Session, user_id: int, intent: VoiceIntent
) -> tuple[dict | None, str]:
    if intent.intent == "create_task":
        title = (intent.title or "").strip()
        if not title:
            return None, "What should I name the task?"
        action = {
            "type": "create_task",
            "title": title,
            "description": intent.description,
            "due_date": intent.due_date.isoformat() if intent.due_date else None,
            "priority": intent.priority or "medium",
        }
        preview = f"Should I create the task “{title}”?"
        if intent.due_date:
            preview = (
                f"Should I create “{title}”, due {intent.due_date.isoformat()}?"
            )
        return action, preview

    if intent.intent in ("complete_task", "update_task"):
        if intent.target_id is None:
            return None, "Which task do you mean? Please say its name."
        task = db.scalar(
            select(UserTask).where(
                UserTask.id == intent.target_id,
                UserTask.user_id == user_id,
            )
        )
        if task is None:
            return None, "I couldn't find that task in your account."
        if intent.intent == "complete_task":
            if task.status == "done":
                return None, f"“{task.title}” is already complete."
            return (
                {"type": "complete_task", "task_id": task.id},
                f"Should I mark “{task.title}” complete?",
            )
        updates = {}
        if intent.title is not None:
            title = intent.title.strip()
            if not title:
                return None, "A task title cannot be blank."
            updates["title"] = title
        if intent.description is not None:
            updates["description"] = intent.description
        if intent.due_date is not None:
            updates["due_date"] = intent.due_date.isoformat()
        if intent.priority is not None:
            updates["priority"] = intent.priority
        if not updates:
            return None, "What would you like me to change about that task?"
        return (
            {"type": "update_task", "task_id": task.id, "updates": updates},
            f"Should I update “{task.title}”?",
        )

    if intent.intent == "delete_task":
        if intent.target_id is None:
            return None, "Which task do you mean? Please say its name."
        task = db.scalar(
            select(UserTask).where(
                UserTask.id == intent.target_id,
                UserTask.user_id == user_id,
            )
        )
        if task is None:
            return None, "I couldn't find that task in your account."
        return (
            {"type": "delete_task", "task_id": task.id},
            f"Should I permanently delete “{task.title}”?",
        )

    if intent.intent == "complete_habit":
        if intent.target_id is None:
            return None, "Which routine do you mean? Please say its name."
        try:
            habit = get_owned_habit(db, user_id, intent.target_id)
        except HTTPException as error:
            if error.status_code == 404:
                return None, "I couldn't find that routine in your account."
            raise
        if not habit.active or habit.archived:
            return None, "That routine is not active."
        if not scheduled_on(habit, date.today()):
            return None, "That routine is not scheduled for today."
        completed = db.scalar(
            select(HabitCompletion).where(
                HabitCompletion.habit_id == habit.id,
                HabitCompletion.completion_date == date.today(),
            )
        )
        if completed:
            return None, f"“{habit.name}” is already complete for today."
        return (
            {"type": "complete_habit", "habit_id": habit.id},
            f"Should I mark routine “{habit.name}” complete for today?",
        )

    if intent.intent == "create_habit":
        name = (intent.title or "").strip()
        if not name:
            return None, "What should I name the routine?"
        if len(name) > 120:
            return None, "Routine names can be at most 120 characters."
        if intent.frequency == "custom" and not intent.target_days:
            return None, "Which weekdays should I schedule it for?"
        action = {
            "type": "create_habit",
            "name": name,
            "description": intent.description,
            "frequency": intent.frequency or "daily",
            "target_days": intent.target_days or [],
            "start_date": (intent.start_date or date.today()).isoformat(),
            "end_date": intent.end_date.isoformat() if intent.end_date else None,
            "priority": intent.priority or "medium",
            "category_id": intent.category_id,
            "target_value": intent.target_value,
            "unit": intent.unit,
        }
        return action, f"Should I create the routine “{name}”?"

    if intent.intent == "update_habit":
        if intent.target_id is None:
            return None, "Which routine do you mean? Please say its name."
        try:
            habit = get_owned_habit(db, user_id, intent.target_id)
        except HTTPException as error:
            if error.status_code == 404:
                return None, "I couldn't find that routine in your account."
            raise
        updates = {}
        if intent.title is not None:
            name = intent.title.strip()
            if not name:
                return None, "A routine name cannot be blank."
            if len(name) > 120:
                return None, "Routine names can be at most 120 characters."
            updates["name"] = name
        for field in (
            "description",
            "frequency",
            "target_days",
            "start_date",
            "end_date",
            "priority",
            "category_id",
            "target_value",
            "unit",
        ):
            value = getattr(intent, field)
            if value is not None:
                updates[field] = value.isoformat() if isinstance(value, date) else value
        if not updates:
            return None, "What would you like me to change about that routine?"
        return (
            {"type": "update_habit", "habit_id": habit.id, "updates": updates},
            f"Should I update the routine “{habit.name}”?",
        )

    return None, "I can help with tasks and routines. What would you like to do?"


def store_pending_action(
    db: Session, device: CompanionDevice, action: dict
) -> str:
    now = datetime.now(timezone.utc)
    action_id = secrets.token_urlsafe(18)
    device.pending_action = action
    device.pending_action_id = action_id
    device.pending_action_expires_at = now + ACTION_LIFETIME
    db.commit()
    return action_id


def clear_pending_action(db: Session, device: CompanionDevice) -> None:
    device.pending_action = None
    device.pending_action_id = None
    device.pending_action_expires_at = None
    db.commit()


def execute_pending_action(
    db: Session, device: CompanionDevice
) -> str:
    action = device.pending_action
    if not action:
        raise HTTPException(status_code=409, detail="No pending action")
    user_id = device.user_id
    action_type = action.get("type")

    if action_type == "create_task":
        task = create_task(
            db,
            user_id,
            TaskCreate(
                title=action["title"],
                description=action.get("description"),
                due_date=action.get("due_date"),
                priority=action["priority"],
                reminder_enabled=False,
            ),
            commit=False,
        )
        reply = f"Created task: {task.title}."
    elif action_type == "complete_task":
        task = complete_task(
            db, user_id, action["task_id"], commit=False
        )
        reply = f"Done. I marked “{task.title}” complete."
    elif action_type == "delete_task":
        task = delete_task(db, user_id, action["task_id"], commit=False)
        title = task.title
        reply = f"Deleted task: {title}."
    elif action_type == "update_task":
        updates = action.get("updates", {})
        task = update_task(
            db,
            user_id,
            action["task_id"],
            TaskUpdate.model_validate(updates),
            commit=False,
        )
        reply = f"Updated task: {task.title}."
    elif action_type == "complete_habit":
        habit_id = action["habit_id"]
        habit = get_owned_habit(db, user_id, habit_id)
        complete_habit(
            db,
            user_id,
            habit_id,
            CompletionCreate(
                completion_date=date.today(),
                notes="Completed using the voice companion",
            ),
            commit=False,
        )
        reply = f"Done. I marked routine “{habit.name}” complete for today."
    elif action_type == "create_habit":
        data = HabitCreate.model_validate(
            {
                "name": action["name"],
                "description": action.get("description"),
                "frequency": action["frequency"],
                "target_days": action["target_days"],
                "start_date": action["start_date"],
                "end_date": action.get("end_date"),
                "priority": action["priority"],
                "category_id": action.get("category_id"),
                "target_value": action.get("target_value"),
                "unit": action.get("unit"),
            }
        )
        habit = create_habit(db, user_id, data, commit=False)
        reply = f"Created routine: {habit.name}."
    elif action_type == "update_habit":
        habit = get_owned_habit(db, user_id, action["habit_id"])
        updates = action.get("updates", {})
        data = HabitUpdate.model_validate(updates)
        updated = update_habit(
            db, user_id, habit.id, data, commit=False
        )
        reply = f"Updated routine: {updated.name}."
    else:
        raise HTTPException(status_code=409, detail="Pending action is invalid")

    device.pending_action = None
    device.pending_action_id = None
    device.pending_action_expires_at = None
    device.last_action_at = datetime.now(timezone.utc)
    device.last_action_type = action_type
    db.commit()
    return reply
