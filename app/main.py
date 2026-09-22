from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings

from app.routers import (
    auth,
    users,
    habits,
    completions,
    statistics,
    dashboard,
    calendar,
    recommendations,
    achievements,
    reminders,
    ai,
    categories,
    goals,
)

from app.models import (
    User,
    Habit,
    HabitCompletion,
    HabitGoal,
    Category,
    Reminder,
    Achievement,
    UserAchievement,
    Recommendation,
    AIInsight,
)

from app.routers.day_plans import router as day_plans_router


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="Modular AI-powered habit tracker backend.",
)


# CORS
cors_origins = list(settings.cors_origin_list)

if "https://habit-tracker-app-green-chi.vercel.app" not in cors_origins:
    cors_origins.append("https://habit-tracker-app-green-chi.vercel.app")

if "http://localhost:5173" not in cors_origins:
    cors_origins.append("http://localhost:5173")


app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Routers
for router in [
    auth.router,
    users.router,
    habits.router,
    completions.router,
    statistics.router,
    dashboard.router,
    calendar.router,
    recommendations.router,
    achievements.router,
    reminders.router,
    ai.router,
    categories.router,
    goals.router,
]:
    app.include_router(router)


# Day Planner router
app.include_router(day_plans_router)


@app.get("/", tags=["System"])
def root():
    return {
        "name": settings.app_name,
        "status": "ok",
        "docs": "/docs",
    }


@app.get("/health", tags=["System"])
def health():
    return {"status": "healthy"}