from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.models.user_task import UserTask
from app.routers.users import current_user
from app.schemas.task import TaskCreate, TaskResponse


router = APIRouter(prefix="/tasks", tags=["Tasks"])


def _user_task(db: Session, user_id: int, task_id: int) -> UserTask:
    task = db.scalar(
        select(UserTask).where(UserTask.id == task_id, UserTask.user_id == user_id)
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("", response_model=list[TaskResponse])
def list_tasks(user=Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(
        select(UserTask)
        .where(UserTask.user_id == user.id)
        .order_by(
            case((UserTask.due_date.is_(None), 1), else_=0),
            UserTask.due_date,
            UserTask.id,
        )
    ).all()


@router.get("/today")
def list_today_tasks(user=Depends(current_user), db: Session = Depends(get_db)):
    today = date.today()
    pending = db.scalars(
        select(UserTask)
        .where(
            UserTask.user_id == user.id,
            UserTask.status == "pending",
            UserTask.due_date <= today,
        )
        .order_by(UserTask.due_date, UserTask.id)
    ).all()
    return {
        "overdue": [
            TaskResponse.model_validate(task)
            for task in pending
            if task.due_date < today
        ],
        "due_today": [
            TaskResponse.model_validate(task)
            for task in pending
            if task.due_date == today
        ],
    }


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
def create_task(
    data: TaskCreate,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    task = UserTask(user_id=user.id, **data.model_dump())
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.post("/{task_id}/complete", response_model=TaskResponse)
def complete_task(
    task_id: int,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    task = _user_task(db, user.id, task_id)
    task.status = "done"
    db.commit()
    db.refresh(task)
    return task


@router.post("/{task_id}/reopen", response_model=TaskResponse)
def reopen_task(
    task_id: int,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    task = _user_task(db, user.id, task_id)
    task.status = "pending"
    db.commit()
    db.refresh(task)
    return task


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: int,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    task = _user_task(db, user.id, task_id)
    db.delete(task)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
