from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,  # so `export FMCSA_WEB_KEY=` does not override .env
    )

    database_url: str = "sqlite:///./data/loads.db"
    api_key: str = "sk-testkey"  # override with API_KEY in production
    redis_url: str = "redis://localhost:6379/0"
    fmcsa_web_key: str = ""  # set FMCSA_WEB_KEY in .env (QCMobile Web Key from mobile.fmcsa.dot.gov)
    # Browser URL for the Streamlit UI (dev: http://127.0.0.1:8501 ; docker+nginx: https://localhost/dashboard/)
    dashboard_entry_url: str = "http://127.0.0.1:8501"

    @field_validator("api_key", "fmcsa_web_key", mode="before")
    @classmethod
    def strip_outer_whitespace(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip()
        return v


settings = Settings()
