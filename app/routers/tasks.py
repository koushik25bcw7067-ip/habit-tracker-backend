from datetime import date

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.database.dependencies import get_db
from app.models.user_task import UserTask
from app.routers.users import current_user
from app.schemas.task import TaskCreate, TaskResponse, TaskUpdate
from app.services import task_service


router = APIRouter(prefix="/tasks", tags=["Tasks"])


def _user_task(db: Session, user_id: int, task_id: int) -> UserTask:
    return task_service.get_owned_task(db, user_id, task_id)


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
    return task_service.create(db, user.id, data)


@router.post("/{task_id}/complete", response_model=TaskResponse)
def complete_task(
    task_id: int,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    return task_service.complete(db, user.id, task_id)


@router.put("/{task_id}", response_model=TaskResponse)
def update_task(
    task_id: int,
    data: TaskUpdate,
    user=Depends(current_user),
    db: Session = Depends(get_db),
):
    return task_service.update(db, user.id, task_id, data)


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
    task_service.delete(db, user.id, task_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
