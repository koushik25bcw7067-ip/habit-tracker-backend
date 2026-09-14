# AI Habit Tracker Backend

A modular FastAPI + PostgreSQL backend for an AI-powered habit tracker.

## Architecture

Frontend → FastAPI routers → authentication/dependencies → services → SQLAlchemy/PostgreSQL → verified statistics → rule-based recommendations → optional AI interpretation.

The backend is the source of truth for numerical statistics. AI receives verified statistics and explains them; it does not replace the statistics engine.

## Features

- JWT access/refresh authentication with Argon2 password hashing
- User isolation and protected routes
- Habit CRUD, archive/restore, activate/deactivate
- Daily, weekly, custom-day scheduling
- Completion/progress records with duplicate prevention
- Streaks, consistency score, calendar and dashboard
- Category statistics
- Reminders with provider-neutral data model
- Achievements and XP-ready schema
- Rule-based personalized recommendations
- Recommendation accept/reject history
- Optional OpenAI-compatible AI provider
- AI analysis, weekly/monthly reports and new-habit suggestions
- Swagger/OpenAPI
- Alembic migrations
- Seed data and tests

## Folder structure

```text
ai-habit-tracker-backend/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── database/
│   ├── models/
│   ├── schemas/
│   ├── routers/
│   ├── services/
│   └── utils/
├── alembic/
│   └── versions/0001_initial.py
├── tests/
├── .env.example
├── .gitignore
├── alembic.ini
├── requirements.txt
├── seed.py
└── README.md
```

## PostgreSQL setup

Create a database named `habit_tracker`, then copy `.env.example` to `.env` and update credentials.

Example SQL:

```sql
CREATE DATABASE habit_tracker;
```

## Installation

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

macOS/Linux:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Migrations

```bash
alembic upgrade head
```

## Seed data

```bash
python seed.py
```

Demo account:

```text
demo@example.com
DemoPass123!
```

## Run

```bash
uvicorn app.main:app --reload
```

Swagger: `http://127.0.0.1:8000/docs`

## AI configuration

AI is optional. The application works without it.

Set:

```env
AI_API_KEY=your_key
AI_API_URL=https://api.openai.com/v1/chat/completions
AI_MODEL=gpt-4o-mini
```

The AI service sends only calculated habit statistics and related non-authentication data. It never receives passwords, JWTs or API keys.

If AI is unavailable, habit tracking, statistics and rule-based recommendations continue working.

## Consistency score

This is an application metric, not a scientifically validated measure:

```text
score =
35% completion rate
25% recent performance
15% streak factor
10% missed-day factor
15% goal achievement
```

Each component is normalized to 0–100. Streak factor reaches 100 at 14 consecutive scheduled completions. Missed-day factor starts at 100 and decreases by 5 points per missed scheduled day, with a floor of 0.

## Important scheduling rule

`target_days` uses Python weekday numbering:

```text
0 Monday
1 Tuesday
2 Wednesday
3 Thursday
4 Friday
5 Saturday
6 Sunday
```

Daily habits are expected every day in their active date range. Weekly habits repeat on the weekday of their start date. Custom habits repeat on the listed target days. Unscheduled days are never counted as missed.

## Example requests

Register:

```http
POST /auth/register
Content-Type: application/json

{
  "username": "sanju",
  "email": "sanju@example.com",
  "password": "StrongPass123!"
}
```

Login:

```http
POST /auth/login
Content-Type: application/json

{
  "email": "sanju@example.com",
  "password": "StrongPass123!"
}
```

Create habit:

```http
POST /habits
Authorization: Bearer ACCESS_TOKEN
Content-Type: application/json

{
  "name": "Study DSA",
  "description": "Practice DSA for two hours",
  "frequency": "daily",
  "target_days": [],
  "start_date": "2026-09-10",
  "priority": "high",
  "target_value": 120,
  "unit": "minutes",
  "active": true
}
```

Complete habit:

```http
POST /habits/1/complete
Authorization: Bearer ACCESS_TOKEN
Content-Type: application/json

{
  "completion_date": "2026-09-10",
  "actual_value": 90,
  "notes": "Solved arrays and stacks"
}
```

Statistics:

```http
GET /statistics/weekly
Authorization: Bearer ACCESS_TOKEN
```

AI recommendation/report:

```http
GET /ai/weekly-report
Authorization: Bearer ACCESS_TOKEN
```

## Frontend integration

1. Login and store the access token securely on the client.
2. Send `Authorization: Bearer <access_token>` on protected requests.
3. On HTTP 401, use `/auth/refresh` with the refresh token.
4. Use `/dashboard` for the main dashboard to minimize round trips.
5. Use `/statistics/*` for chart data.
6. Use `/calendar` for calendar heatmaps.
7. Use `/recommendations/*` for recommendation cards.
8. Use `/ai/*` for AI analysis and reports.
9. Never put PostgreSQL credentials or AI API keys in frontend code.
