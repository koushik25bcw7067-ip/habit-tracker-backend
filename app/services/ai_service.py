import json
import httpx
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.ai_insight import AIInsight
from app.models.habit import Habit
from app.services.statistics_service import period_statistics, habit_statistics


class AIService:
    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id

        # Read API configuration from .env / settings.
        self.api_key = (
            getattr(settings, "ai_api_key", "")
            or getattr(settings, "AI_API_KEY", "")
            or ""
        ).strip()

        # Gemini OpenAI-compatible endpoint.
        self.base_url = (
            getattr(settings, "ai_api_url", "")
            or getattr(settings, "AI_API_URL", "")
            or "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        ).rstrip("/")

        self.endpoint = self.base_url

        # Gemini model.
        self.model = (
            getattr(settings, "ai_model", "")
            or getattr(settings, "AI_MODEL", "")
            or "gemini-2.5-flash"
        ).strip()

    def verified_context(self, days=30):
        end = date.today()
        start = end - timedelta(days=days - 1)

        period = period_statistics(
            self.db,
            self.user_id,
            start,
            end,
        )

        habits = self.db.scalars(
            select(Habit).where(
                Habit.user_id == self.user_id
            )
        ).all()

        habit_stats = [
            habit_statistics(self.db, h, end)
            for h in habits
        ]

        return {
            "period": period,
            "habits": habit_stats,
        }

    def _fallback(self, context, reason):
        p = context.get("period", {})

        completion = p.get("completion_rate", 0)
        trend = p.get("trend_vs_previous_period", 0)

        habits = context.get("habits", [])

        strong = [
            h.get("habit_id")
            for h in habits
            if h.get("completion_rate", 0) >= 80
        ]

        weak = [
            h.get("habit_id")
            for h in habits
            if h.get("completion_rate", 0) < 50
        ]

        return {
            "performance_analysis": (
                f"Verified completion rate is {completion}%."
            ),
            "strong_habits": strong,
            "weak_habits": weak,
            "patterns": [
                f"Trend versus previous period: "
                f"{trend} percentage points."
            ],
            "recommendations": [
                "Focus on completing your top priority habit consistently."
            ],
            "source_status": reason,
        }

    def _chat_fallback(self, messages, context, reason):
        p = context.get("period", {})

        completion = p.get("completion_rate", 0)
        streak = p.get("longest_streak", 0)
        trend = p.get("trend_vs_previous_period", 0)

        last = ""

        if messages:
            last_msg = messages[-1]

            if isinstance(last_msg, dict):
                last = last_msg.get("content", "")
            else:
                last = getattr(last_msg, "content", "")

            last = str(last).lower()

        if "missed" in last or "skip" in last:
            reply = (
                f"Missing a day happens — your verified completion "
                f"rate is {completion}% over the last 30 days. "
                f"Don't worry about the gap; focus on today's target."
            )

        elif (
            "consisten" in last
            or "worse" in last
            or "trend" in last
        ):
            reply = (
                f"Your completion rate changed by {trend} "
                f"percentage points compared to the last period. "
                f"Try reducing your daily load to one core habit "
                f"for the next few days."
            )

        else:
            reply = (
                f"Your verified stats show a {completion}% "
                f"completion rate over the last 30 days, "
                f"with a top streak of {streak} days. "
                f"What habit do you want to work on today?"
            )

        return {
            "reply": reply,
            "source_status": reason,
        }

    def _build_system_prompt(self, context):
        return (
            "You are a friendly personal AI companion inside a habit "
            "tracking and productivity application. "

            "Speak naturally, warmly, and casually. "
            "Do not sound robotic or overly formal. "

            "You can communicate naturally in English, Telugu, "
            "Romanized Telugu, and Telugu-English mixed language. "

            "Mirror the user's language when appropriate. "

            "For simple greetings and casual conversation, give a "
            "natural conversational response. Do not unnecessarily "
            "dump habit statistics when the user is simply saying hi. "

            "Maintain conversation context and understand follow-up "
            "references such as 'it', 'that', 'why', 'what next', "
            "and 'what after that'. "

            "When discussing habits, tasks, or statistics, use ONLY "
            "the verified data supplied below. "

            "Never invent statistics, streaks, tasks, deadlines, "
            "habits, calendar events, or other user information. "

            "The backend is the source of truth for numerical data. "

            "Be concise for simple questions and more detailed when "
            "the user asks for an explanation or plan. "

            "VERIFIED USER DATA:\n"
            f"{json.dumps(context, default=str)}"
        )

    def chat(self, messages):
        context = self.verified_context(30)

        if not self.api_key:
            return self._chat_fallback(
                messages,
                context,
                "ai_not_configured",
            )

        system_prompt = self._build_system_prompt(context)

        formatted_messages = [
            {
                "role": "system",
                "content": system_prompt,
            }
        ]

        for m in messages:
            if isinstance(m, dict):
                role = m.get("role", "user")
                content = m.get("content", "")
            else:
                role = getattr(m, "role", "user")
                content = getattr(m, "content", "")

            formatted_messages.append(
                {
                    "role": role,
                    "content": str(content),
                }
            )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model,
            "messages": formatted_messages,
            "temperature": 0.5,
        }

        try:
            with httpx.Client(timeout=30) as client:
                response = client.post(
                    self.endpoint,
                    headers=headers,
                    json=payload,
                )

                if response.status_code != 200:
                    print(
                        f"DEBUG GEMINI RESPONSE "
                        f"[{response.status_code}]: "
                        f"{response.text}"
                    )

                response.raise_for_status()

                data = response.json()

                reply = data["choices"][0]["message"]["content"]

                return {
                    "reply": reply,
                    "source_status": "ai",
                }

        except Exception as e:
            print(
                f"DEBUG GEMINI AI ERROR: "
                f"{type(e).__name__} - {e}"
            )

            return self._chat_fallback(
                messages,
                context,
                "ai_unavailable",
            )

    def analyze(self, days=30):
        context = self.verified_context(days)

        if not self.api_key:
            return self._fallback(
                context,
                "ai_not_configured",
            )

        prompt = (
            "Analyze these verified habit statistics. "
            "Do not invent numbers. "
            "Return valid JSON with keys: "
            "performance_analysis, strong_habits, weak_habits, "
            "patterns, recommendations. "
            f"VERIFIED DATA: "
            f"{json.dumps(context, default=str)}"
        )

        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }

            body = {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a habit analytics assistant. "
                            "Return only valid JSON."
                        ),
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                "temperature": 0.2,
            }

            with httpx.Client(timeout=30) as client:
                response = client.post(
                    self.endpoint,
                    headers=headers,
                    json=body,
                )

                if response.status_code != 200:
                    print(
                        f"DEBUG GEMINI ANALYZE RESPONSE "
                        f"[{response.status_code}]: "
                        f"{response.text}"
                    )

                response.raise_for_status()

                text = response.json()[
                    "choices"
                ][0]["message"]["content"]

            return json.loads(text)

        except Exception as e:
            print(
                f"DEBUG GEMINI ANALYZE ERROR: "
                f"{type(e).__name__} - {e}"
            )

            return self._fallback(
                context,
                "ai_unavailable",
            )

    def suggest_habits(self, goal):
        fallback_item = [
            {
                "name": "Define one small daily action",
                "description": goal,
                "frequency": "daily",
                "target_value": 1,
                "category": "Productivity",
                "reason": "Start with a manageable routine.",
            }
        ]

        if not self.api_key:
            return fallback_item

        prompt = (
            f"Suggest 3 measurable habits for this goal: "
            f"'{goal}'. "
            "Return a clean JSON array of objects with keys: "
            "name, description, frequency, target_value, "
            "category, reason."
        )

        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }

            body = {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": "Return only a valid JSON array.",
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                "temperature": 0.4,
            }

            with httpx.Client(timeout=30) as client:
                response = client.post(
                    self.endpoint,
                    headers=headers,
                    json=body,
                )

                if response.status_code != 200:
                    print(
                        f"DEBUG GEMINI SUGGEST RESPONSE "
                        f"[{response.status_code}]: "
                        f"{response.text}"
                    )

                response.raise_for_status()

                text = response.json()[
                    "choices"
                ][0]["message"]["content"]

            return json.loads(text)

        except Exception as e:
            print(
                f"DEBUG GEMINI SUGGEST ERROR: "
                f"{type(e).__name__} - {e}"
            )

            return fallback_item

    def save_insight(self, insight_type, content):
        item = AIInsight(
            user_id=self.user_id,
            insight_type=insight_type,
            content=content,
        )

        self.db.add(item)
        self.db.commit()
        self.db.refresh(item)

        return item

    def report(self, days):
        context = self.analyze(days)

        return {
            "overall_score": context.get(
                "performance_analysis"
            ),
            "summary": context.get(
                "performance_analysis"
            ),
            "wins": context.get(
                "strong_habits",
                [],
            ),
            "struggles": context.get(
                "weak_habits",
                [],
            ),
            "patterns": context.get(
                "patterns",
                [],
            ),
            "recommendations": context.get(
                "recommendations",
                [],
            ),
            "next_week_plan": [
                "Focus on your primary habit.",
                "Keep daily targets manageable.",
            ],
            "source_status": context.get(
                "source_status",
                "ai",
            ),
        }