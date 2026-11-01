from functools import lru_cache
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AI Habit Tracker API"
    environment: str = "development"
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/habit_tracker"
    secret_key: str = "b4c9e8f17a3d2e5b6c8f0a1e3d5b7c9f8a0e2d4b6c8f1a3e5d7b9c0e2f4a6b8d"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    ai_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("AI_API_KEY"),
    )
    ai_model: str = Field(
        default="gemini-3.5-flash-lite",
        validation_alias=AliasChoices("AI_MODEL"),
    )
    tts_model: str = Field(
        default="gemini-2.5-flash-preview-tts",
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
