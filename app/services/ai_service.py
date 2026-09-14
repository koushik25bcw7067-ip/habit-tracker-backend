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
        self.db, self.user_id = db, user_id

    def verified_context(self, days=30):
        end=date.today(); start=end-timedelta(days=days-1)
        period=period_statistics(self.db,self.user_id,start,end)
        habits=self.db.scalars(select(Habit).where(Habit.user_id==self.user_id)).all()
        habit_stats=[habit_statistics(self.db,h,end) for h in habits]
        return {"period":period,"habits":habit_stats}

    def analyze(self, days=30):
        context=self.verified_context(days)
        if not settings.ai_api_key:
            return self._fallback(context, "ai_not_configured")
        prompt = (
            "Analyze the verified habit-tracker statistics below. Do not calculate or invent numbers. "
            "Use only supplied values. Return JSON with keys performance_analysis,strong_habits,weak_habits,"
            "patterns,recommendations. Recommendations must be actionable and non-judgmental. "
            f"VERIFIED DATA: {json.dumps(context, default=str)}"
        )
        try:
            body={"model":settings.ai_model,"messages":[{"role":"system","content":"You are a habit analytics assistant. Backend statistics are the source of truth."},{"role":"user","content":prompt}],"temperature":0.2}
            with httpx.Client(timeout=30) as client:
                r=client.post(settings.ai_api_url,headers={"Authorization":f"Bearer {settings.ai_api_key}","Content-Type":"application/json"},json=body)
                r.raise_for_status()
                text=r.json()["choices"][0]["message"]["content"]
            parsed=json.loads(text)
        except Exception:
            return self._fallback(context, "ai_unavailable")
        return parsed

    def _fallback(self, context, reason):
        p=context["period"]
        rates=[h["completion_rate"] for h in context["habits"]]
        strong=[h["habit_id"] for h in context["habits"] if h["completion_rate"]>=80]
        weak=[h["habit_id"] for h in context["habits"] if h["completion_rate"]<50]
        return {"performance_analysis":f"Verified completion rate is {p['completion_rate']}%.",
                "strong_habits":strong,"weak_habits":weak,
                "patterns":[f"Trend versus previous period: {p['trend_vs_previous_period']} percentage points."],
                "recommendations":["Review weak habits and consider simplifying targets when completion remains low."],
                "source_status":reason}

    def save_insight(self, insight_type, content):
        item=AIInsight(user_id=self.user_id,insight_type=insight_type,content=content)
        self.db.add(item); self.db.commit(); self.db.refresh(item); return item

    def suggest_habits(self, goal):
        if not settings.ai_api_key:
            return [{"name":"Define one small daily action","description":f"A simple action aligned with: {goal}","frequency":"daily","target_value":1,"category":"Productivity","reason":"Start with a measurable, manageable behavior."}]
        prompt=f"Suggest 3 measurable habits for this goal: {goal}. Return JSON array with name,description,frequency,target_value,category,reason. Do not create habits."
        try:
            with httpx.Client(timeout=30) as client:
                r=client.post(settings.ai_api_url,headers={"Authorization":f"Bearer {settings.ai_api_key}"},json={"model":settings.ai_model,"messages":[{"role":"user","content":prompt}],"temperature":0.4})
                r.raise_for_status()
                return json.loads(r.json()["choices"][0]["message"]["content"])
        except Exception:
            return [{"name":"Start with one small daily action","description":goal,"frequency":"daily","target_value":1,"category":"Other","reason":"AI provider unavailable; this is a safe fallback suggestion."}]

    def chat(self, messages):
        """Multi-turn companion chat. `messages` is a list of ChatMessage (role, content) —
        the full session history is sent by the client each call; nothing is persisted server-side.
        Verified backend statistics are injected as system context so the model never has to
        guess or invent numbers about the user's habits."""
        context = self.verified_context(30)
        system_prompt = (
            "You are a supportive, encouraging personal productivity assistant inside a student "
            "habit-tracking app. Be warm, concise, and practical. Base any claim about the user's "
            "habits, streaks, or completion rates ONLY on the VERIFIED DATA supplied below — never "
            "invent or estimate numbers. If the data doesn't cover what's asked, say so plainly. "
            f"VERIFIED DATA: {json.dumps(context, default=str)}"
        )
        if not settings.ai_api_key:
            return self._chat_fallback(messages, context, "ai_not_configured")
        try:
            body_messages = [{"role": "system", "content": system_prompt}]
            body_messages += [{"role": m.role, "content": m.content} for m in messages]
            with httpx.Client(timeout=30) as client:
                r = client.post(
                    settings.ai_api_url,
                    headers={"Authorization": f"Bearer {settings.ai_api_key}", "Content-Type": "application/json"},
                    json={"model": settings.ai_model, "messages": body_messages, "temperature": 0.5},
                )
                r.raise_for_status()
                reply = r.json()["choices"][0]["message"]["content"]
            return {"reply": reply, "source_status": "ai"}
        except Exception:
            return self._chat_fallback(messages, context, "ai_unavailable")

    def _chat_fallback(self, messages, context, reason):
        p = context["period"]
        last = messages[-1].content.lower() if messages else ""
        if "missed" in last or "skip" in last:
            reply = (
                f"Missing a day happens — your verified completion rate is {p['completion_rate']}% "
                f"over the last 30 days, so one gap won't undo that. The best move is just to complete "
                f"today's habits on schedule and let the streak logic pick back up naturally."
            )
        elif "consisten" in last or "worse" in last or "wors" in last:
            reply = (
                f"Looking at your verified stats, your completion rate moved {p['trend_vs_previous_period']} "
                f"percentage points versus the previous period. If that's trending down, try protecting time "
                f"for just your highest-priority habit for a few days before adding anything else back."
            )
        else:
            reply = (
                f"Based on your verified stats: {p['completion_rate']}% completion over the last 30 days, "
                f"with a longest streak of {p['longest_streak']} days. What would you like to focus on?"
            )
        return {"reply": reply, "source_status": reason}

    def report(self, days):
        context=self.analyze(days)
        return {"overall_score":context.get("performance_analysis"),"summary":context.get("performance_analysis"),
                "wins":context.get("strong_habits",[]),"struggles":context.get("weak_habits",[]),
                "patterns":context.get("patterns",[]),"recommendations":context.get("recommendations",[]),
                "next_week_plan":["Focus on the highest-priority habit.","Keep measurable targets realistic."],"source_status":context.get("source_status","ai")}
