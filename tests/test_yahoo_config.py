from fastapi.testclient import TestClient

from app.main import app


def test_yahoo_is_disabled_until_credentials_are_configured():
    with TestClient(app) as client:
        assert client.get("/api/platforms/yahoo/status").json()["configured"] is False
        response = client.get("/api/platforms/yahoo/connect", follow_redirects=False)
        assert response.status_code == 503
        assert "not configured" in response.json()["detail"]
