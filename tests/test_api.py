from fastapi.testclient import TestClient

from app.main import app, home


def test_health_and_home():
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/").status_code == 200
        assert client.get("/docs").status_code == 200


def test_home_csv_parser_keeps_javascript_newline_escapes():
    html = home()
    assert "c==='\\n'" in html
    assert "c==='\\r'" in html
    assert "c==='\n'" not in html
