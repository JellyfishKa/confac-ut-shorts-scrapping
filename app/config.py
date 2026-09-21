from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    youtube_api_key: str = ""
    download_dir: Path = Path("downloads")
    max_results: int = 50

    default_max_age_hours: int = 168
    default_min_views: int = 10_000
    default_min_views_per_hour: float = 500.0
    default_min_like_rate: float = 0.02
    default_max_duration_seconds: int = 180

    weight_views_per_hour: float = 0.55
    weight_like_rate: float = 0.30
    weight_comment_rate: float = 0.15

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
