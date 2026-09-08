from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import FantasyRoster, League, NFLPlayer, RosterPlayer, WeeklyPlayerProjection
from app.services.lineup_optimizer import Candidate, optimize
from app.services.recommendation_engine import decision_breakdown
from app.services.scoring_engine import fantasy_points

router = APIRouter(prefix="/api")


@router.get("/leagues/{league_id}/recommendations/{week}")
def recommendations(league_id: str, week: int, profile: str = Query("balanced", pattern="^(conservative|balanced|upside)$"), db: Session = Depends(get_db)):
    league = db.get(League, league_id)
    roster = db.scalar(select(FantasyRoster).where(FantasyRoster.league_id == league_id, FantasyRoster.is_primary_user.is_(True)))
    if not league or not roster: raise HTTPException(404, "League or primary roster not found")
    rows = list(db.scalars(select(RosterPlayer).where(RosterPlayer.league_id == league_id, RosterPlayer.roster_id == roster.roster_id)))
    projections = {r.data.get("player_id"): r.data.get("stats", {}) for r in db.scalars(select(WeeklyPlayerProjection).where(WeeklyPlayerProjection.league_id == league_id, WeeklyPlayerProjection.week == week))}
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
    return {"league_id": league_id, "week": week, "profile": profile, "current_projected_points": round(sum(c.projection for c in candidates if c.player_id in current_ids), 2), "optimized_projected_points": round(sum(p.projection for _, p in lineup), 2), "recommended_lineup": recommended, "changes": [{"start": details[p]["name"]} for p in recommended_ids-current_ids]}


@router.post("/leagues/{league_id}/projections/{week}/csv")
def import_csv(league_id: str, week: int, rows: list[dict], db: Session = Depends(get_db)):
    db.query(WeeklyPlayerProjection).filter_by(league_id=league_id, week=week).delete()
    for row in rows:
        player_id = str(row.pop("player_id")); db.add(WeeklyPlayerProjection(league_id=league_id, week=week, data={"player_id": player_id, "stats": row}))
    db.commit(); return {"imported": len(rows)}
