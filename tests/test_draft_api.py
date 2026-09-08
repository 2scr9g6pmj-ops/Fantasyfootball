from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app


def test_draft_events_undo_redo_and_branch():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            created = client.post("/api/drafts", json={
                "user_team_id": "1", "settings": {"teams": 2, "roster_positions": ["RB"]},
                "players": [{"player_id": "p1", "name": "One", "position": "RB", "projection": 10, "adp": 1}]
            }).json()
            sid = created["id"]
            picked = client.post(f"/api/drafts/{sid}/events", json={"type": "pick", "payload": {"player_id": "p1", "team_id": "1", "pick": 1}}).json()
            assert picked["state"]["current_pick"] == 2 and not picked["recommendations"]
            undone = client.post(f"/api/drafts/{sid}/undo").json()
            assert undone["can_redo"] and undone["recommendations"][0]["player_id"] == "p1"
            redone = client.post(f"/api/drafts/{sid}/redo").json()
            assert redone["roster"]["player_ids"] == ["p1"]
            client.post(f"/api/drafts/{sid}/undo")
            branched = client.post(f"/api/drafts/{sid}/events", json={"type": "pool", "payload": {"remove": ["p1"]}}).json()
            assert not branched["can_redo"] and branched["event_count"] == 1
    finally:
        app.dependency_overrides.clear()
        db.close()
