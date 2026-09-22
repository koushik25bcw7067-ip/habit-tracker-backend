from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.models.day_plan import DayPlan, DayPlanItem
from app.schemas.day_plan import DayPlanCreate, DayPlanResponse
from app.utils.dependencies import get_current_user


router = APIRouter(prefix="/day-plans", tags=["Day Planner"])


@router.post(
    "",
    response_model=DayPlanResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_day_plan(
    data: DayPlanCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    existing = (
        db.query(DayPlan)
        .filter(
            DayPlan.user_id == current_user.id,
            DayPlan.plan_date == data.plan_date,
        )
        .first()
    )

    if existing:
        raise HTTPException(
            status_code=400,
            detail="A plan already exists for this date.",
        )

    plan = DayPlan(
        user_id=current_user.id,
        plan_date=data.plan_date,
        title=data.title,
        main_goal=data.main_goal,
    )

    db.add(plan)
    db.flush()

    for item in data.items:
        plan_item = DayPlanItem(
            day_plan_id=plan.id,
            time=item.time,
            title=item.title,
            type=item.type,
            priority=item.priority,
            reminder=item.reminder,
            completed=False,
        )
        db.add(plan_item)

    db.commit()
    db.refresh(plan)

    return plan


@router.get(
    "/{plan_date}",
    response_model=DayPlanResponse,
)
def get_day_plan(
    plan_date: date,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    plan = (
        db.query(DayPlan)
        .filter(
            DayPlan.user_id == current_user.id,
            DayPlan.plan_date == plan_date,
        )
        .first()
    )

    if not plan:
        raise HTTPException(
            status_code=404,
            detail="No plan found for this date.",
        )

    return plan


@router.delete(
    "/{plan_date}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_day_plan(
    plan_date: date,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    plan = (
        db.query(DayPlan)
        .filter(
            DayPlan.user_id == current_user.id,
            DayPlan.plan_date == plan_date,
        )
        .first()
    )

    if not plan:
        raise HTTPException(
            status_code=404,
            detail="No plan found for this date.",
        )

    db.delete(plan)
    db.commit()