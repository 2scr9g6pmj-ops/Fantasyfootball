from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.database import get_db
from app.models import FantasyRoster, League, SyncHistory, User
from app.services.sleeper_client import SleeperClient, SleeperAPIError
from app.services.sync_service import SyncService

router = APIRouter(prefix="/api")


@router.get("/user")
def get_user(db: Session = Depends(get_db)):
    username = get_settings().sleeper_username
    user = db.scalar(select(User).where(User.username.ilike(username)))
    if not user:
        raise HTTPException(404, "User has not been synced. Run POST /api/sync first.")
    return user


@router.get("/leagues")
def get_leagues(season: str | None = None, db: Session = Depends(get_db)):
    query = select(League).order_by(League.name)
    if season:
        query = query.where(League.season == season)
    return list(db.scalars(query))


@router.get("/leagues/{league_id}")
def get_league(league_id: str, db: Session = Depends(get_db)):
    league = db.get(League, league_id)
    if not league:
        raise HTTPException(404, "League not found")
    return league


@router.get("/leagues/{league_id}/roster")
def get_primary_roster(league_id: str, db: Session = Depends(get_db)):
    roster = db.scalar(
        select(FantasyRoster)
        .where(FantasyRoster.league_id == league_id, FantasyRoster.is_primary_user.is_(True))
        .options(selectinload(FantasyRoster.players))
    )
    if not roster:
        raise HTTPException(404, "Primary user's roster not found")
    return roster


@router.post("/sync")
async def sync_all(
    season: str | None = Query(default=None, pattern=r"^\d{4}$"),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    client = SleeperClient(settings.sleeper_base_url)
    try:
        result = await SyncService(db, client).sync(settings.sleeper_username, season or settings.sleeper_season)
        return result
    except SleeperAPIError as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        await client.close()


@router.post("/leagues/{league_id}/sync")
async def sync_league(league_id: str, season: str | None = None, db: Session = Depends(get_db)):
    # Sleeper discovery is user/season scoped; resync it and verify the requested league exists.
    result = await sync_all(season=season, db=db)
    if not db.get(League, league_id):
        raise HTTPException(404, "League was not found in the selected season")
    return result


@router.get("/sync/history")
def get_sync_history(db: Session = Depends(get_db)):
    return list(db.scalars(select(SyncHistory).order_by(SyncHistory.started_at.desc()).limit(20)))

