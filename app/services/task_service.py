from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user_task import UserTask
from app.schemas.task import TaskCreate, TaskUpdate


def get_owned_task(db: Session, user_id: int, task_id: int) -> UserTask:
    task = db.scalar(
        select(UserTask).where(
            UserTask.id == task_id,
            UserTask.user_id == user_id,
        )
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


def create(
    db: Session, user_id: int, data: TaskCreate, commit: bool = True
) -> UserTask:
    task = UserTask(user_id=user_id, **data.model_dump())
    db.add(task)
    if commit:
        db.commit()
        db.refresh(task)
    else:
        db.flush()
    return task


def update(
    db: Session,
    user_id: int,
    task_id: int,
    data: TaskUpdate,
    commit: bool = True,
) -> UserTask:
    task = get_owned_task(db, user_id, task_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(task, field, value)
    if commit:
        db.commit()
        db.refresh(task)
    else:
        db.flush()
    return task


def complete(
    db: Session, user_id: int, task_id: int, commit: bool = True
) -> UserTask:
    task = get_owned_task(db, user_id, task_id)
    task.status = "done"
    if commit:
        db.commit()
        db.refresh(task)
    else:
        db.flush()
    return task


def delete(
    db: Session, user_id: int, task_id: int, commit: bool = True
) -> UserTask:
    task = get_owned_task(db, user_id, task_id)
    db.delete(task)
    if commit:
        db.commit()
    else:
        db.flush()
    return task
