import json
import httpx
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.ai_insight import AIInsight
from app.models.habit import Habit
from app.services.statistics_service import (
    period_statistics,
    habit_statistics,
)


class AIService:
    def __init__(self, db: Session, user_id: int):
        self.db = db
        self.user_id = user_id

        self.api_key = (
            getattr(settings, "ai_api_key", "")
            or getattr(settings, "AI_API_KEY", "")
            or ""
        ).strip()

        self.base_url = (
            getattr(settings, "ai_base_url", "")
            or getattr(settings, "ai_api_url", "")
            or getattr(settings, "AI_API_URL", "")
            or "https://generativelanguage.googleapis.com/v1beta/chat/completions"
        ).rstrip("/")

        self.endpoint = self.base_url

        self.model = (
            getattr(settings, "ai_model", "")
            or getattr(settings, "AI_MODEL", "")
            or "gemini-2.5-flash"
        ).strip()

    def verified_context(self, days=30):
        end = date.today()
        start = end - timedelta(days=days - 1)

        period = period_statistics(
            self.db, self.user_id, start, end
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
                "Focus on completing your top priority routine consistently."
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
                f"Try reducing your daily load to one core routine "
                f"for the next few days."
            )
        else:
            reply = (
                f"Your verified stats show a {completion}% "
                f"completion rate over the last 30 days, "
                f"with a top streak of {streak} days. "
                f"What routine do you want to work on today?"
            )

        return {
            "reply": reply,
            "source_status": reason,
        }

    def _build_system_prompt(self, context, persona=""):
        return (
            "You are a friendly personal AI companion inside a routine "
            "tracking and productivity application. "
            "Speak naturally, warmly, and casually. "
            "Do not sound robotic or overly formal. "
            "You can communicate naturally in English, Telugu, "
            "Romanized Telugu, and Telugu-English mixed language. "
            "Mirror the user's language when appropriate. "
            "For simple greetings and casual conversation, give a "
            "natural conversational response. Do not unnecessarily "
            "dump routine statistics when the user is simply saying hi. "
            "Maintain conversation context and understand follow-up "
            "references such as 'it', 'that', 'why', 'what next', "
            "and 'what after that'. "
            "Call recurring tracked items routines. When discussing routines, tasks, or statistics, use ONLY "
            "the verified data supplied below. "
            "Never invent statistics, streaks, tasks, deadlines, "
            "routines, calendar events, or other user information. "
            "The backend is the source of truth for numerical data. "
            "Be concise for simple questions and more detailed when "
            "the user asks for an explanation or plan. "
            + (f"WORLD PERSONA (style only; never changes facts): {persona}\n" if persona else "")
            + "VERIFIED USER DATA:\n"
            + f"{json.dumps(context, default=str)}"
        )

    def _request_ai(self, messages, temperature=0.5):
        """Send a request through the existing Gemini-compatible API."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        body = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }

        with httpx.Client(timeout=30) as client:
            response = client.post(
                self.endpoint,
                headers=headers,
                json=body,
            )
            response.raise_for_status()

        data = response.json()
        return data["choices"][0]["message"]["content"]

    @staticmethod
    def _parse_json(text):
        """Parse JSON even if the AI wraps it in a code block."""
        clean_text = text.strip()

        if clean_text.startswith("```"):
            lines = clean_text.splitlines()

            if lines and lines[0].startswith("```"):
                lines = lines[1:]

            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]

            clean_text = "\n".join(lines).strip()

        return json.loads(clean_text)

    def generate_custom_theme(self, theme_name: str):
        """Generate a theme palette and companion dialogues."""
        theme_name = theme_name.strip()

        if not theme_name:
            raise ValueError("Theme name cannot be empty.")

        # Safe default if AI is not configured or unavailable.
        fallback_theme = {
            "name": theme_name,
            "palette": {
                "bg": "#0f172a",
                "card": "rgba(30, 41, 59, 0.7)",
                "primary": "#e11d48",
                "accent": "#f59e0b",
                "text": "#f8fafc",
                "border": "rgba(255, 255, 255, 0.1)",
            },
            "font_family": "Inter, sans-serif",
            "doll_emoji": "🗡️",
            "dialogues": [
                "Mowaa, mission start cheyyi!",
                "Pending tasks ni victory ga marchudham!",
                "Oka task complete cheyyi; next mission ready!",
            ],
        }

        if not self.api_key:
            return fallback_theme

        prompt = f"""
Create a polished, professional website theme and AI companion
dialogues for the theme: {json.dumps(theme_name)}.

Return ONLY valid JSON with exactly these keys:
{{
  "name": "{theme_name}",
  "palette": {{
    "bg": "#0f172a",
    "card": "rgba(30, 41, 59, 0.7)",
    "primary": "#e11d48",
    "accent": "#f59e0b",
    "text": "#f8fafc",
    "border": "rgba(255, 255, 255, 0.1)"
  }},
  "font_family": "Inter, sans-serif",
  "doll_emoji": "🗡️",
  "dialogues": [
    "Mowaa, mission start cheyyi!",
    "Pending tasks ni victory ga marchudham!",
    "Oka task complete cheyyi; next mission ready!"
  ]
}}

Requirements:
- Provide all six palette keys shown above.
- Use readable colors with good contrast.
- Use a suitable font-family CSS value.
- Provide a suitable single emoji for the companion.
- Provide 3 to 5 short, witty companion dialogues.
- Dialogues should use Romanized Telugu or Telugu-English.
- If the theme is Attack on Titan, use a survey-corps-inspired
  military adventure atmosphere and original dialogue.
- Do not include Markdown or explanations outside the JSON.
"""

        try:
            raw_text = self._request_ai(
                [
                    {
                        "role": "system",
                        "content": (
                            "You generate website themes. "
                            "Return only valid JSON without Markdown."
                        ),
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                temperature=0.4,
            )

            theme = self._parse_json(raw_text)

            # Validate and complete the expected structure.
            required_palette = [
                "bg", "card", "primary",
                "accent", "text", "border",
            ]

            if not isinstance(theme, dict):
                return fallback_theme

            palette = theme.get("palette")
            dialogues = theme.get("dialogues")

            if not isinstance(palette, dict):
                return fallback_theme

            if not all(
                isinstance(palette.get(key), str)
                for key in required_palette
            ):
                return fallback_theme

            if not isinstance(dialogues, list) or not all(
                isinstance(item, str) for item in dialogues
            ):
                return fallback_theme

            if not isinstance(theme.get("font_family"), str):
                return fallback_theme

            if not isinstance(theme.get("doll_emoji"), str):
                return fallback_theme

            theme["name"] = theme_name
            return theme

        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            print(f"DEBUG CUSTOM THEME ERROR: {type(e).__name__} - {e}")
            return fallback_theme
        except Exception as e:
            print(f"DEBUG CUSTOM THEME ERROR: {type(e).__name__} - {e}")
            return fallback_theme

    def chat(self, messages, persona=""):
        context = self.verified_context(30)

        if not self.api_key:
            return self._chat_fallback(
                messages, context, "ai_not_configured"
            )

        system_prompt = self._build_system_prompt(context, persona)

        formatted_messages = [
            {"role": "system", "content": system_prompt}
        ]

        for m in messages:
            if isinstance(m, dict):
                role = m.get("role", "user")
                content = m.get("content", "")
            else:
                role = getattr(m, "role", "user")
                content = getattr(m, "content", "")

            formatted_messages.append(
                {"role": role, "content": str(content)}
            )

        try:
            reply = self._request_ai(
                formatted_messages,
                temperature=0.5,
            )

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
                messages, context, "ai_unavailable"
            )

    def analyze(self, days=30):
        context = self.verified_context(days)

        if not self.api_key:
            return self._fallback(
                context, "ai_not_configured"
            )

        prompt = (
            "Analyze these verified routine statistics. "
            "Do not invent numbers. "
            "Return valid JSON with keys: "
            "performance_analysis, strong_habits, weak_habits, "
            "patterns, recommendations. "
            f"VERIFIED DATA: {json.dumps(context, default=str)}"
        )

        try:
            text = self._request_ai(
                [
                    {
                        "role": "system",
                        "content": (
                            "You are a routine analytics assistant. "
                            "Return only valid JSON."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
            )

            result = self._parse_json(text)

            if not isinstance(result, dict):
                raise ValueError("AI analysis must be a JSON object.")

            result.setdefault("source_status", "ai")
            return result

        except Exception as e:
            print(
                f"DEBUG GEMINI ANALYZE ERROR: "
                f"{type(e).__name__} - {e}"
            )
            return self._fallback(
                context, "ai_unavailable"
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
            f"Suggest 3 measurable routines for this goal: "
            f"'{goal}'. "
            "Return a clean JSON array of objects with keys: "
            "name, description, frequency, target_value, "
            "category, reason."
        )

        try:
            text = self._request_ai(
                [
                    {
                        "role": "system",
                        "content": "Return only a valid JSON array.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.4,
            )

            result = self._parse_json(text)

            if not isinstance(result, list):
                raise ValueError("Routine suggestions must be a JSON list.")

            return result

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
            "overall_score": context.get("performance_analysis"),
            "summary": context.get("performance_analysis"),
            "wins": context.get("strong_habits", []),
            "struggles": context.get("weak_habits", []),
            "patterns": context.get("patterns", []),
            "recommendations": context.get("recommendations", []),
            "next_week_plan": [
                "Focus on your primary routine.",
                "Keep daily targets manageable.",
            ],
            "source_status": context.get("source_status", "ai"),
        }