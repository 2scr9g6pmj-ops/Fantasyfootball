from functools import lru_cache
import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    sleeper_username: str = "CousinsFF"
    sleeper_season: str | None = None
    sleeper_base_url: str = "https://api.sleeper.app/v1"
    database_url: str = "sqlite:///./data/fantasyfootball.db"
    yahoo_client_id: str | None = None
    yahoo_client_secret: str | None = None
    yahoo_redirect_uri: str = "https://localhost:8000/api/platforms/yahoo/callback"
    app_encryption_key: str | None = None
    app_access_username: str | None = None
    app_access_password: str | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def effective_yahoo_redirect_uri(self) -> str:
        render_url = os.getenv("RENDER_EXTERNAL_URL")
        if render_url:
            return f"{render_url.rstrip('/')}/api/platforms/yahoo/callback"
        return self.yahoo_redirect_uri


@lru_cache
def get_settings() -> Settings:
    return Settings()
