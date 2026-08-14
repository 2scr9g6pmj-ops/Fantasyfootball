from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    sleeper_username: str = "CousinsFF"
    sleeper_season: str | None = None
    sleeper_base_url: str = "https://api.sleeper.app/v1"
    database_url: str = "sqlite:///./data/fantasyfootball.db"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()

