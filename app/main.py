from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.routers import auth,users,habits,completions,statistics,dashboard,calendar,recommendations,achievements,reminders,ai,categories,goals
from app.models import User,Habit,HabitCompletion,HabitGoal,Category,Reminder,Achievement,UserAchievement,Recommendation,AIInsight
from app.routers.day_plans import router as day_plans_router

app=FastAPI(title=settings.app_name,version="1.0.0",description="Modular AI-powered habit tracker backend.")
app.add_middleware(CORSMiddleware,allow_origins=settings.cors_origin_list,allow_credentials=True,allow_methods=["*"],allow_headers=["*"])
for router in [auth.router,users.router,habits.router,completions.router,statistics.router,dashboard.router,calendar.router,recommendations.router,achievements.router,reminders.router,ai.router,categories.router,goals.router]:
    app.include_router(router)
    app.include_router(day_plans_router)

@app.get("/",tags=["System"])
def root(): return {"name":settings.app_name,"status":"ok","docs":"/docs"}

@app.get("/health",tags=["System"])
def health(): return {"status":"healthy"}
