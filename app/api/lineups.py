from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import get_settings
from app.models import FantasyRoster, League, NFLPlayer, RosterPlayer, WeeklyPlayerProjection
from app.services.lineup_optimizer import Candidate, optimize
from app.services.projection_provider import SleeperProjectionProvider
from app.services.recommendation_engine import decision_breakdown
from app.services.scoring_engine import fantasy_points

router = APIRouter(prefix="/api")


def _load_or_fetch_projections(
    league: League, week: int, db: Session, refresh: bool = False
) -> tuple[dict[str, dict[str, float]], str]:
    query = select(WeeklyPlayerProjection).where(
        WeeklyPlayerProjection.league_id == league.league_id,
        WeeklyPlayerProjection.week == week,
    )
    rows = list(db.scalars(query))
    if rows and not refresh:
        return {
            row.data.get("player_id"): row.data.get("stats", {}) for row in rows
        }, rows[0].data.get("source", "stored")

    provider = SleeperProjectionProvider(get_settings().sleeper_projection_base_url)
    try:
        projections = provider.projections(week, league.season)
    finally:
        provider.close()
    if refresh:
        db.query(WeeklyPlayerProjection).filter_by(
            league_id=league.league_id, week=week
        ).delete()
    for player_id, stats in projections.items():
        db.add(WeeklyPlayerProjection(
            league_id=league.league_id,
            week=week,
            data={"player_id": player_id, "stats": stats, "source": "sleeper"},
        ))
    db.commit()
    return projections, "sleeper"


@router.get("/leagues/{league_id}/recommendations/{week}")
def recommendations(league_id: str, week: int, profile: str = Query("balanced", pattern="^(conservative|balanced|upside)$"), db: Session = Depends(get_db)):
    league = db.get(League, league_id)
    roster = db.scalar(select(FantasyRoster).where(FantasyRoster.league_id == league_id, FantasyRoster.is_primary_user.is_(True)))
    if not league or not roster: raise HTTPException(404, "League or primary roster not found")
    rows = list(db.scalars(select(RosterPlayer).where(RosterPlayer.league_id == league_id, RosterPlayer.roster_id == roster.roster_id)))
    try:
        projections, projection_source = _load_or_fetch_projections(league, week, db)
    except Exception:
        projections, projection_source = {}, "unavailable"
    candidates, details = [], {}
    for row in rows:
        player = db.get(NFLPlayer, row.player_id)
        points = fantasy_points(projections.get(row.player_id, {}), league.scoring_settings)
        decision = decision_breakdown(points, player.injury_status, profile, projections.get(row.player_id, {}))
        score = decision["decision_score"]
        candidates.append(Candidate(row.player_id, player.position or "", points, score))
        details[row.player_id] = {"name": player.full_name, "team": player.team, "position": player.position, "injury": player.injury_status, "current": row.is_starter, "decision": decision}
    lineup = optimize(league.roster_positions, candidates)
    recommended = [{"slot": slot, **details[p.player_id], "player_id": p.player_id, "projection": p.projection, "start_score": p.start_score} for slot, p in lineup]
    current_ids = {r.player_id for r in rows if r.is_starter}
    recommended_ids = {p[1].player_id for p in lineup}
    return {"league_id": league_id, "week": week, "profile": profile, "projection_source": projection_source, "current_projected_points": round(sum(c.projection for c in candidates if c.player_id in current_ids), 2), "optimized_projected_points": round(sum(p.projection for _, p in lineup), 2), "recommended_lineup": recommended, "changes": [{"start": details[p]["name"]} for p in recommended_ids-current_ids]}


@router.post("/leagues/{league_id}/projections/{week}/sync")
def sync_weekly_projections(league_id: str, week: int, db: Session = Depends(get_db)):
    league = db.get(League, league_id)
    if not league:
        raise HTTPException(404, "League not found")
    try:
        projections, source = _load_or_fetch_projections(league, week, db, refresh=True)
    except Exception as exc:
        raise HTTPException(502, f"Projection provider failed: {exc}") from exc
    return {"league_id": league_id, "week": week, "source": source, "players": len(projections)}


@router.post("/leagues/{league_id}/projections/{week}/csv")
def import_csv(league_id: str, week: int, rows: list[dict], db: Session = Depends(get_db)):
    db.query(WeeklyPlayerProjection).filter_by(league_id=league_id, week=week).delete()
    for row in rows:
        player_id = str(row.pop("player_id")); db.add(WeeklyPlayerProjection(league_id=league_id, week=week, data={"player_id": player_id, "stats": row}))
    db.commit(); return {"imported": len(rows)}
