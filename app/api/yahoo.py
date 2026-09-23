from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import DraftEvent, DraftSession, YahooConnection
from app.services.draft_engine import initial_state
from app.services.yahoo_client import (YahooAPIError, YahooClient, decrypt_tokens, encrypt_tokens,
                                        require_yahoo_settings, token_expiry, verify_state)

router = APIRouter(prefix="/api/yahoo", tags=["Yahoo"])


class LeagueMapping(BaseModel):
    league_id: str
    team_id: str


class ConnectRequest(BaseModel):
    leagues: list[LeagueMapping]


def _connection(db: Session, connection_id: str) -> YahooConnection:
    row = db.get(YahooConnection, connection_id)
    if not row: raise HTTPException(404, "Yahoo connection not found")
    return row


def connection_view(row: YahooConnection) -> dict:
    return {"id": row.id, "status": row.status, "leagues": row.league_mappings, "last_error": row.last_error,
            "connected": row.status == "connected", "updated_at": row.updated_at}


async def access_token(row: YahooConnection, db: Session, client: YahooClient) -> str:
    settings = get_settings(); require_yahoo_settings(settings)
    if not row.encrypted_tokens: raise YahooAPIError("Yahoo connection has no credentials")
    tokens = decrypt_tokens(row.encrypted_tokens, settings.yahoo_token_encryption_key)
    if not row.token_expires_at or row.token_expires_at <= __import__("datetime").datetime.utcnow():
        refreshed = await client.refresh(tokens["refresh_token"])
        if "refresh_token" not in refreshed: refreshed["refresh_token"] = tokens["refresh_token"]
        row.encrypted_tokens = encrypt_tokens(refreshed, settings.yahoo_token_encryption_key)
        row.token_expires_at = token_expiry(refreshed); db.commit(); tokens = refreshed
    return tokens["access_token"]


@router.post("/connect")
def connect(body: ConnectRequest, db: Session = Depends(get_db)):
    settings = get_settings()
    try: require_yahoo_settings(settings)
    except YahooAPIError as exc: raise HTTPException(503, str(exc)) from exc
    row = YahooConnection(id=uuid4().hex, league_mappings=[x.model_dump() for x in body.leagues], status="pending")
    db.add(row); db.commit()
    return {**connection_view(row), "authorization_url": YahooClient(settings).authorization_url(row.id)}


@router.get("/callback")
async def callback(code: str | None = None, state: str | None = None, error: str | None = None, db: Session = Depends(get_db)):
    settings = get_settings()
    if error: return RedirectResponse(f"/draft?yahoo_error={error}")
    if not code or not state: raise HTTPException(400, "Yahoo callback is missing code or state")
    try: connection_id = verify_state(state, settings.yahoo_client_secret or "")
    except YahooAPIError as exc: raise HTTPException(400, str(exc)) from exc
    row = _connection(db, connection_id); client = YahooClient(settings)
    try:
        tokens = await client.exchange_code(code)
        leagues = await client.leagues(tokens["access_token"])
        by_id = {str(x["league_id"]): x for x in leagues}
        mappings = [{**mapping, **({"league_key": by_id[str(mapping["league_id"])]["league_key"], "name": by_id[str(mapping["league_id"])].get("name")} if str(mapping["league_id"]) in by_id else {})} for mapping in row.league_mappings]
        row.yahoo_guid = tokens.get("xoauth_yahoo_guid"); row.encrypted_tokens = encrypt_tokens(tokens, settings.yahoo_token_encryption_key)
        row.token_expires_at = token_expiry(tokens); row.league_mappings = mappings; row.status = "connected"; row.last_error = None; db.commit()
    except (YahooAPIError, KeyError) as exc:
        row.status = "error"; row.last_error = str(exc); db.commit()
        return RedirectResponse(f"/draft?yahoo_connection={row.id}&yahoo_error=connection_failed")
    finally: await client.close()
    return RedirectResponse(f"/draft?yahoo_connection={row.id}")


@router.get("/connections/{connection_id}")
def status(connection_id: str, db: Session = Depends(get_db)): return connection_view(_connection(db, connection_id))


@router.post("/connections/{connection_id}/leagues/{league_id}/draft")
async def import_draft(connection_id: str, league_id: str, db: Session = Depends(get_db)):
    connection = _connection(db, connection_id); mapping = next((x for x in connection.league_mappings if str(x["league_id"]) == league_id), None)
    if not mapping or not mapping.get("league_key"): raise HTTPException(404, "Connected Yahoo league was not found")
    client = YahooClient(get_settings())
    try:
        token = await access_token(connection, db, client)
        bundle = await client.league_bundle(mapping["league_key"], token)
        players = await client.available_players(mapping["league_key"], token)
    except YahooAPIError as exc: raise HTTPException(502, str(exc)) from exc
    finally: await client.close()
    teams = {str(t["team_id"]): [] for t in bundle["teams"]}
    state = initial_state({"players": players, "teams": teams, "settings": {"teams": bundle["num_teams"], "roster_positions": bundle["roster_positions"], "scoring": bundle["scoring"]},
                           "current_pick": 1, "current_round": 1, "platform_connection_id": connection.id, "yahoo_league_key": mapping["league_key"]})
    session = DraftSession(id=uuid4().hex, league_id=league_id, platform="yahoo", platform_draft_id=mapping["league_key"], user_team_id=str(mapping["team_id"]), base_state=state)
    db.add(session); db.flush()
    for pick in sorted(bundle["picks"], key=lambda x: x["pick"]):
        team_id = str(pick["team_key"]).rsplit(".t.", 1)[-1]
        db.add(DraftEvent(session_id=session.id, sequence=pick["pick"], event_type="pick", payload={"player_id": pick["player_key"], "team_id": team_id, "pick": pick["pick"], "round": pick["round"]}, source="yahoo"))
    session.cursor = len(bundle["picks"]); db.commit()
    return {"session_id": session.id, "league": bundle, "draft_url": f"/draft?session={session.id}"}
