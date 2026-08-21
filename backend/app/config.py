"""Settings loaded from environment / .env. Nothing else reads os.environ."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./mealmind.db"

    # LLM arrangement (OpenAI-compatible chat completions; Grok or OpenAI).
    llm_api_key: str = ""
    llm_base_url: str = "https://api.x.ai/v1"
    llm_model: str = "grok-3-mini"
    # Free tiers cap requests PER MODEL PER DAY (Gemini: 20/day). When the
    # primary is exhausted these are tried in order before giving up.
    llm_fallback_models: str = ""       # comma-separated

    # QuickCommerce live prices.
    quickcommerce_api_key: str = ""
    quickcommerce_base_url: str = "https://api.quickcommerce.example/v1"

    default_budget_rs: float = 1500.0
    default_family_size: int = 4

    # JWT signing secret — MUST be overridden in .env for anything shared
    jwt_secret: str = "dev-secret-change-me"

    # id of the seeded demo account (owns the demo pantry)
    demo_user_id: int = 1


@lru_cache
def get_settings() -> Settings:
    return Settings()
