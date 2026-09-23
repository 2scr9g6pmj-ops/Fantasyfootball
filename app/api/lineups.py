from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import get_settings
from app.models import FantasyRoster, League, NFLPlayer, RosterPlayer, WeeklyPlayerProjection
from app.services.lineup_optimizer import Candidate, eligible_for_slot, optimize
from app.services.projection_provider import SleeperProjectionProvider
from app.services.recommendation_engine import decision_breakdown
from app.services.scoring_engine import fantasy_points

router = APIRouter(prefix="/api")


def _explain_selection(
    slot: str,
    selected: Candidate,
    candidates: list[Candidate],
    recommended_ids: set[str],
    details: dict,
    profile: str,
) -> tuple[str, dict | None]:
    player = details[selected.player_id]
    reason = (
        f"{player['name']} fits the optimal {slot} lineup with a "
        f"{selected.projection:.2f}-point league-adjusted projection and a "
        f"{selected.start_score:.1f} {profile} decision score."
    )
    alternatives = sorted(
        (
            candidate for candidate in candidates
            if candidate.player_id not in recommended_ids
            and eligible_for_slot(slot, candidate.position)
        ),
        key=lambda candidate: (candidate.start_score, candidate.projection),
        reverse=True,
    )
    if not alternatives:
        return reason + " There was no eligible bench alternative for this slot.", None

    alternative = alternatives[0]
    alternative_details = details[alternative.player_id]
    score_gap = round(selected.start_score - alternative.start_score, 1)
    projection_gap = round(selected.projection - alternative.projection, 2)
    close = abs(score_gap) <= 8 or abs(projection_gap) <= 2
    health_note = ""
    if player["injury"] != alternative_details["injury"]:
        selected_health = player["injury"] or "healthy"
        alternative_health = alternative_details["injury"] or "healthy"
        health_note = (
            f" Availability check: {player['name']} is {selected_health}; "
            f"{alternative_details['name']} is {alternative_health}."
        )
    comparison = (
        f"{'Close call: ' if close else ''}{player['name']} starts over "
        f"{alternative_details['name']} by {projection_gap:+.2f} projected points "
        f"and {score_gap:+.1f} decision-score points.{health_note}"
    )
    return f"{reason} {comparison}", {
        "is_close": close,
        "alternative_player_id": alternative.player_id,
        "alternative": alternative_details["name"],
        "projection_gap": projection_gap,
        "decision_score_gap": score_gap,
        "explanation": comparison,
    }


def _lineup_changes(
    lineup: list[tuple[str, Candidate]], current_ids: set[str], details: dict
) -> list[dict]:
    recommended_ids = {candidate.player_id for _, candidate in lineup}
    starts = [candidate for _, candidate in lineup if candidate.player_id not in current_ids]
    sits = [player_id for player_id in current_ids if player_id not in recommended_ids]
    changes = []
    for start in starts:
        compatible_sits = [
            player_id for player_id in sits
            if details[player_id]["position"] == start.position
        ]
        sit_id = compatible_sits[0] if compatible_sits else (sits[0] if sits else None)
        if sit_id:
            sits.remove(sit_id)
        changes.append({
            "start": details[start.player_id]["name"],
            "sit": details[sit_id]["name"] if sit_id else None,
            "reason": (
                f"Start {details[start.player_id]['name']}"
                + (f" over {details[sit_id]['name']}" if sit_id else "")
                + f" because the optimized {start.position} option has a "
                f"{start.projection:.2f}-point projection and {start.start_score:.1f} decision score."
            ),
        })
    return changes


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
    current_ids = {r.player_id for r in rows if r.is_starter}
    recommended_ids = {p[1].player_id for p in lineup}
    recommended = []
    for slot, player in lineup:
        explanation, close_call = _explain_selection(
            slot, player, candidates, recommended_ids, details, profile
        )
        recommended.append({
            "slot": slot, **details[player.player_id], "player_id": player.player_id,
            "projection": player.projection, "start_score": player.start_score,
            "explanation": explanation, "close_call": close_call,
        })
    return {"league_id": league_id, "week": week, "profile": profile, "projection_source": projection_source, "current_projected_points": round(sum(c.projection for c in candidates if c.player_id in current_ids), 2), "optimized_projected_points": round(sum(p.projection for _, p in lineup), 2), "recommended_lineup": recommended, "changes": _lineup_changes(lineup, current_ids, details)}


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
