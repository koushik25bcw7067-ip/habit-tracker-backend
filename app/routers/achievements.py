from fastapi import APIRouter,Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database.dependencies import get_db
from app.routers.users import current_user
from app.models.achievement import Achievement,UserAchievement
from app.services.achievement_service import ensure_defaults,evaluate
router=APIRouter(prefix="/achievements",tags=["Achievements"])

@router.get("")
def achievements(user=Depends(current_user),db:Session=Depends(get_db)):
    ensure_defaults(db);return db.scalars(select(Achievement)).all()

@router.get("/user")
def user_achievements(user=Depends(current_user),db:Session=Depends(get_db)):
    evaluate(db,user.id)
    return db.scalars(select(UserAchievement).where(UserAchievement.user_id==user.id)).all()
