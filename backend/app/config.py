from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./kamion.db"
    media_dir: str = "./media"

    # "mock" needs no key and runs simple offline heuristics — good for local dev
    # and demoing the pipeline shape. "anthropic" calls a real multimodal model
    # and is what should be used for the actual judged demo.
    vision_adapter: str = "mock"
    anthropic_api_key: str | None = None
    vision_model: str = "claude-sonnet-5"

    max_upload_mb: int = 25

    # pricing
    min_comparables: int = 5
    max_comparables: int = 10
    conformal_alpha: float = 0.1  # -> nominal 90% interval


@lru_cache
def get_settings() -> Settings:
    return Settings()
