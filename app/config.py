from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    youtube_api_key: str = ""
    gemini_api_key: str = ""
    vk_access_token: str = ""
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    tiktok_research_token: str = ""
    instagram_access_token: str = ""
    instagram_user_id: str = ""
    download_dir: Path = Path("downloads")
    data_dir: Path = Path("data")
    max_results: int = 50

    default_max_age_hours: int = 168
    default_min_views: int = 1_000
    default_min_views_per_hour: float = 0.0
    default_min_like_rate: float = 0.0
    default_max_duration_seconds: int = 180

    weight_views_per_hour: float = 0.55
    weight_like_rate: float = 0.30
    weight_comment_rate: float = 0.15

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
