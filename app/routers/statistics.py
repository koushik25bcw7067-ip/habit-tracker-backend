from datetime import date,timedelta
from fastapi import APIRouter,Depends,Query
from sqlalchemy.orm import Session
from app.database.dependencies import get_db
from app.routers.users import current_user
from app.models.habit import Habit
from app.services.statistics_service import day_stat,period_statistics,habit_statistics
router=APIRouter(prefix="/statistics",tags=["Statistics"])

@router.get("/daily")
def daily(day:date=date.today(),user=Depends(current_user),db:Session=Depends(get_db)):
    habits=db.query(Habit).filter(Habit.user_id==user.id).all()
    s=day_stat(db,habits,day);return {**s,"remaining":s["planned"]-s["completed"]}

@router.get("/weekly")
def weekly(end_date:date=date.today(),user=Depends(current_user),db:Session=Depends(get_db)):
    return period_statistics(db,user.id,end_date-timedelta(days=6),end_date)

@router.get("/monthly")
def monthly(year:int|None=None,month:int|None=None,user=Depends(current_user),db:Session=Depends(get_db)):
    import calendar
    today=date.today();y=year or today.year;m=month or today.month
    start=date(y,m,1);end=date(y,m,calendar.monthrange(y,m)[1])
    return period_statistics(db,user.id,start,end)

@router.get("/category")
def category_stats(user=Depends(current_user),db:Session=Depends(get_db)):
    from collections import defaultdict
    habits=db.query(Habit).filter(Habit.user_id==user.id).all(); out=defaultdict(list)
    for h in habits: out[h.category.name if h.category else "Other"].append(h)
    return {k:round(sum(habit_statistics(db,h)["completion_rate"] for h in hs)/len(hs),2) for k,hs in out.items()}

@router.get("/habits/{habit_id}")
def habit_stats(habit_id:int,user=Depends(current_user),db:Session=Depends(get_db)):
    from app.services.habit_service import get_owned_habit
    return habit_statistics(db,get_owned_habit(db,user.id,habit_id))
