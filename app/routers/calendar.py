from datetime import date
import calendar as cal
from fastapi import APIRouter,Depends,Query
from sqlalchemy.orm import Session
from app.database.dependencies import get_db
from app.routers.users import current_user
from app.models.habit import Habit
from app.services.statistics_service import day_stat
router=APIRouter(prefix="/calendar",tags=["Calendar"])

@router.get("")
def calendar_view(year:int,month:int,user=Depends(current_user),db:Session=Depends(get_db)):
    habits=db.query(Habit).filter(Habit.user_id==user.id).all()
    days=cal.monthrange(year,month)[1]
    return {"year":year,"month":month,"days":[day_stat(db,habits,date(year,month,d)) for d in range(1,days+1)]}
