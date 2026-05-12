from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/loads.db"
    api_key: str = "sk-testkey"  # override with API_KEY in production


settings = Settings()
