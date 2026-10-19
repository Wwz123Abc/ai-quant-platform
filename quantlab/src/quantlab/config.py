"""Settings, read from QUANTLAB_* environment variables and an optional .env file in the working directory."""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QUANTLAB_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    data_dir: Path = Path("data")          # SQLite databases: prices cache, paper accounts
    reports_dir: Path = Path("reports")    # charts and CSV files written by the command line
    host: str = "127.0.0.1"                # the API listens on this machine only unless changed
    port: int = 8000
    api_token: str | None = None           # when unset, one is generated once and kept in data_dir/api_token
    cors_origins: list[str] = []           # e.g. ["http://localhost:5173"] for the web dev server
    log_level: str = "INFO"
    log_format: Literal["text", "json"] = "text"
    cache_max_age: int = 6 * 3600          # seconds before a cached symbol is downloaded again
    http_timeout: float = 20.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
