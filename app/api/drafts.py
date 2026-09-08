from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.database import get_db
from app.models import DraftEvent, DraftSession
from app.services.draft_engine import apply_event, initial_state, recommendations, roster_summary
from app.services.sleeper_client import SleeperAPIError, SleeperClient
from app.services.yahoo_client import YahooAPIError, YahooClient

router = APIRouter(prefix="/api/drafts", tags=["draft room"])


class CreateDraft(BaseModel):
    league_id: str | None = None
    platform: str = Field("manual", pattern="^(manual|sleeper|yahoo|espn)$")
    platform_draft_id: str | None = None
    user_team_id: str
    players: list[dict] = []
    teams: dict[str, list[str]] = {}
    settings: dict = {}
    current_pick: int = 1
    current_round: int = 1


class EventInput(BaseModel):
    type: str = Field(pattern="^(pick|keeper|set_clock|set_roster|pool|player_override|settings_override)$")
    payload: dict
    source: str = "manual"


def _session(db: Session, session_id: str) -> DraftSession:
    row = db.scalar(select(DraftSession).where(DraftSession.id == session_id).options(selectinload(DraftSession.events)).execution_options(populate_existing=True))
    if not row: raise HTTPException(404, "Draft session not found")
    return row


def _view(row: DraftSession) -> dict:
    state = initial_state(row.base_state)
    applied = [e for e in row.events if e.sequence <= row.cursor]
    for event in applied: state = apply_event(state, event.event_type, event.payload)
    return {"id": row.id, "platform": row.platform, "platform_draft_id": row.platform_draft_id,
            "cursor": row.cursor, "event_count": len(row.events), "can_undo": row.cursor > 0,
            "can_redo": row.cursor < len(row.events), "state": state,
            "roster": roster_summary(state, row.user_team_id),
            "recommendations": recommendations(state, row.user_team_id)[:30],
            "history": [{"sequence": e.sequence, "type": e.event_type, "payload": e.payload, "source": e.source, "applied": e.sequence <= row.cursor} for e in row.events]}


@router.post("")
def create_draft(body: CreateDraft, db: Session = Depends(get_db)):
    data = body.model_dump(exclude={"league_id", "platform", "platform_draft_id", "user_team_id"})
    row = DraftSession(id=uuid4().hex, league_id=body.league_id, platform=body.platform,
                       platform_draft_id=body.platform_draft_id, user_team_id=body.user_team_id,
                       base_state=initial_state(data))
    db.add(row); db.commit()
    return _view(_session(db, row.id))


@router.get("/{session_id}")
def get_draft(session_id: str, db: Session = Depends(get_db)): return _view(_session(db, session_id))


@router.post("/{session_id}/events")
def add_event(session_id: str, body: EventInput, db: Session = Depends(get_db)):
    row = _session(db, session_id)
    if row.cursor < len(row.events):
        db.execute(delete(DraftEvent).where(DraftEvent.session_id == row.id, DraftEvent.sequence > row.cursor))
    row.cursor += 1
    db.add(DraftEvent(session_id=row.id, sequence=row.cursor, event_type=body.type, payload=body.payload, source=body.source))
    db.commit(); return _view(_session(db, session_id))


@router.post("/{session_id}/undo")
def undo(session_id: str, db: Session = Depends(get_db)):
    row = _session(db, session_id); row.cursor = max(0, row.cursor - 1); db.commit(); return _view(_session(db, session_id))


@router.post("/{session_id}/redo")
def redo(session_id: str, db: Session = Depends(get_db)):
    row = _session(db, session_id); row.cursor = min(len(row.events), row.cursor + 1); db.commit(); return _view(_session(db, session_id))


@router.post("/{session_id}/sync")
async def sync_platform(session_id: str, db: Session = Depends(get_db)):
    row = _session(db, session_id)
    if not row.platform_draft_id: raise HTTPException(400, "Draft session has no platform draft ID")
    if row.platform == "sleeper":
        client = SleeperClient(get_settings().sleeper_base_url)
        try: picks = [{"player_id": str(p["player_id"]), "team_id": str(p.get("roster_id") or p.get("picked_by")), "pick": int(p["pick_no"]), "source": "sleeper"} for p in await client.get_draft_picks(row.platform_draft_id)]
        except SleeperAPIError as exc: raise HTTPException(502, str(exc)) from exc
        finally: await client.close()
    elif row.platform == "yahoo":
        from app.api.yahoo import _connection, access_token
        connection_id = row.base_state.get("platform_connection_id")
        if not connection_id: raise HTTPException(400, "Yahoo draft session is not linked to a connection")
        client = YahooClient(get_settings())
        try:
            bundle = await client.league_bundle(row.platform_draft_id, await access_token(_connection(db, connection_id), db, client))
            picks = [{"player_id": p["player_key"], "team_id": str(p["team_key"]).rsplit(".t.", 1)[-1], "pick": p["pick"], "round": p["round"], "source": "yahoo"} for p in bundle["picks"]]
        except YahooAPIError as exc: raise HTTPException(502, str(exc)) from exc
        finally: await client.close()
    else: raise HTTPException(400, f"Automatic sync is not available for {row.platform}")
    known = {(str(e.payload.get("player_id")), e.payload.get("pick")) for e in row.events}
    for p in picks:
        payload = {k: v for k, v in p.items() if k != "source"}
        if (str(payload["player_id"]), payload["pick"]) not in known:
            row.cursor += 1; db.add(DraftEvent(session_id=row.id, sequence=row.cursor, event_type="pick", payload=payload, source=p["source"]))
    db.commit(); return _view(_session(db, session_id))
