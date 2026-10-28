from functools import lru_cache
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AI Habit Tracker API"
    environment: str = "development"
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/habit_tracker"
    secret_key: str = "CHANGE_ME_IN_ENV"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    ai_api_key: str = ""
    ai_base_url: str = Field(
        default="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        validation_alias=AliasChoices("AI_BASE_URL", "AI_API_URL"),
    )
    ai_model: str = Field(
        default="gemini-3.6-flash",
        validation_alias=AliasChoices("AI_MODEL"),
    )
    tts_model: str = Field(
        default="gemini-3.8-flash-tts",
        validation_alias=AliasChoices("TTS_MODEL"),
    )

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()