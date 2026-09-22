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
    yahoo_redirect_uri: str = "http://127.0.0.1:8000/api/yahoo/callback"
    yahoo_token_encryption_key: str | None = None
    yahoo_fantasy_base_url: str = "https://fantasysports.yahooapis.com/fantasy/v2"
    yahoo_auth_url: str = "https://api.login.yahoo.com/oauth2/request_auth"
    yahoo_token_url: str = "https://api.login.yahoo.com/oauth2/get_token"
    app_access_username: str | None = None
    app_access_password: str | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def effective_yahoo_redirect_uri(self) -> str:
        render_url = os.getenv("RENDER_EXTERNAL_URL")
        if render_url:
            return f"{render_url.rstrip('/')}/api/yahoo/callback"
        return self.yahoo_redirect_uri


@lru_cache
def get_settings() -> Settings:
    return Settings()

