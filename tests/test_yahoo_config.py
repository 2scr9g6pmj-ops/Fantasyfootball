from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


def test_yahoo_is_disabled_until_credentials_are_configured(monkeypatch):
    monkeypatch.setenv("YAHOO_CLIENT_ID", "")
    monkeypatch.setenv("YAHOO_CLIENT_SECRET", "")
    monkeypatch.setenv("APP_ENCRYPTION_KEY", "")
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            assert client.get("/api/platforms/yahoo/status").json()["configured"] is False
            response = client.get("/api/platforms/yahoo/connect", follow_redirects=False)
            assert response.status_code == 503
            assert "not configured" in response.json()["detail"]
    finally:
        get_settings.cache_clear()
