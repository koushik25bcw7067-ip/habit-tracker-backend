from datetime import date,timedelta,time,datetime,timezone
from app.database.database import SessionLocal
from app.models import User,Category,Habit,HabitCompletion,HabitGoal
from app.models.habit import Frequency,Priority
from app.models.achievement import Achievement
from app.services.auth_service import register
from app.services.achievement_service import ensure_defaults

def main():
    db=SessionLocal()
    try:
        user=db.query(User).filter(User.email=="demo@example.com").first()
        if not user: user=register(db,"demo","demo@example.com","DemoPass123!")
        names=["Health","Fitness","Study","Productivity","Personal Development","Sleep","Finance","Mental Wellness","Other"]
        cats={}
        for n in names:
            c=db.query(Category).filter(Category.name==n,Category.user_id.is_(None)).first()
            if not c: c=Category(name=n,is_default=True);db.add(c);db.flush()
            cats[n]=c
        habits=[
            Habit(user_id=user.id,name="Drink Water",description="Drink enough water",category_id=cats["Health"].id,frequency=Frequency.DAILY,target_days=[],start_date=date.today()-timedelta(days=30),priority=Priority.HIGH,target_value=2,unit="litres",reminder_time=time(9)),
            Habit(user_id=user.id,name="Study DSA",description="Practice DSA",category_id=cats["Study"].id,frequency=Frequency.DAILY,target_days=[],start_date=date.today()-timedelta(days=30),priority=Priority.HIGH,target_value=120,unit="minutes",reminder_time=time(19)),
            Habit(user_id=user.id,name="Read",description="Read 10 pages",category_id=cats["Personal Development"].id,frequency=Frequency.CUSTOM,target_days=[0,2,4],start_date=date.today()-timedelta(days=30),priority=Priority.MEDIUM,target_value=10,unit="pages")
        ]
        for h in habits: db.add(h)
        db.commit()
        for h in habits:
            db.refresh(h)
            for i in range(30):
                d=date.today()-timedelta(days=i)
                if h.frequency==Frequency.CUSTOM and d.weekday() not in h.target_days: continue
                if i%5 != 0:
                    db.add(HabitCompletion(habit_id=h.id,completion_date=d,actual_value=h.target_value,completed_at=datetime.combine(d,time(19),timezone.utc)))
        db.commit();ensure_defaults(db)
        print("Seed complete. Demo login: demo@example.com / DemoPass123!")
    finally: db.close()
if __name__=="__main__": main()
