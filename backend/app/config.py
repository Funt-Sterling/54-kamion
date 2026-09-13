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
    # Organization-scoped API keys must name the workspace to bill/run in;
    # workspace-scoped keys don't need this and can leave it empty.
    anthropic_workspace_id: str | None = None
    vision_model: str = "claude-sonnet-5"
    # A photo upload waits on this call, so it must be bounded: on timeout
    # the media item is kept and marked vision_status="failed" rather than
    # hanging the request or inventing evidence.
    vision_timeout_seconds: float = 45.0
    vision_max_retries: int = 1

    max_upload_mb: int = 25

    # pricing
    min_comparables: int = 5
    max_comparables: int = 10
    conformal_alpha: float = 0.1  # -> nominal 90% interval


@lru_cache
def get_settings() -> Settings:
    return Settings()
