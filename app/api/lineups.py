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


def _normalized_player_name(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


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
) -> tuple[dict[str, dict[str, dict[str, float]]], str]:
    query = select(WeeklyPlayerProjection).where(
        WeeklyPlayerProjection.league_id == league.league_id,
        WeeklyPlayerProjection.week == week,
    )
    rows = list(db.scalars(query))
    if rows and not refresh:
        sources: dict[str, dict[str, dict[str, float]]] = {}
        for row in rows:
            source = row.data.get("source", "manual")
            sources.setdefault(source, {})[row.data.get("player_id")] = row.data.get("stats", {})
        return sources, ", ".join(sorted(sources))

    source_sets: dict[str, dict[str, dict[str, float]]] = {}
    for record in rows:
        source = record.data.get("source", "manual")
        if source != "sleeper":
            source_sets.setdefault(source, {})[record.data.get("player_id")] = record.data.get("stats", {})

    provider = SleeperProjectionProvider(get_settings().sleeper_projection_base_url)
    try:
        projections = provider.projections(week, league.season)
    finally:
        provider.close()
    if refresh:
        for record in rows:
            if record.data.get("source") == "sleeper":
                db.delete(record)
    for player_id, stats in projections.items():
        db.add(WeeklyPlayerProjection(
            league_id=league.league_id,
            week=week,
            data={"player_id": player_id, "stats": stats, "source": "sleeper"},
        ))
    db.commit()
    source_sets["sleeper"] = projections
    return source_sets, ", ".join(sorted(source_sets))


def _scored_projection_consensus(
    player_id: str,
    projection_sets: dict[str, dict[str, dict[str, float]]],
    scoring_settings: dict[str, float],
) -> tuple[float, float | None, dict[str, float]]:
    source_points = {
        source: fantasy_points(players[player_id], scoring_settings)
        for source, players in projection_sets.items()
        if player_id in players
    }
    aggregate = round(sum(source_points.values()) / len(source_points), 2) if source_points else 0.0
    sleeper_points = source_points.get("sleeper")
    return aggregate, sleeper_points, source_points


@router.get("/leagues/{league_id}/recommendations/{week}")
def recommendations(league_id: str, week: int, profile: str = Query("balanced", pattern="^(conservative|balanced|upside)$"), db: Session = Depends(get_db)):
    league = db.get(League, league_id)
    roster = db.scalar(select(FantasyRoster).where(FantasyRoster.league_id == league_id, FantasyRoster.is_primary_user.is_(True)))
    if not league or not roster: raise HTTPException(404, "League or primary roster not found")
    rows = list(db.scalars(select(RosterPlayer).where(RosterPlayer.league_id == league_id, RosterPlayer.roster_id == roster.roster_id)))
    try:
        projection_sets, projection_source = _load_or_fetch_projections(league, week, db)
    except Exception:
        projection_sets, projection_source = {}, "unavailable"
    candidates, details = [], {}
    for row in rows:
        player = db.get(NFLPlayer, row.player_id)
        points, sleeper_points, source_points = _scored_projection_consensus(
            row.player_id, projection_sets, league.scoring_settings
        )
        context = next(
            (players[row.player_id] for players in projection_sets.values() if row.player_id in players),
            {},
        )
        decision = decision_breakdown(points, player.injury_status, profile, context)
        score = decision["decision_score"]
        candidates.append(Candidate(row.player_id, player.position or "", points, score))
        details[row.player_id] = {"name": player.full_name, "team": player.team, "position": player.position, "injury": player.injury_status, "current": row.is_starter, "decision": decision, "sleeper_projection": sleeper_points, "aggregate_projection": points, "projection_sources": source_points}
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
        projection_sets, source = _load_or_fetch_projections(league, week, db, refresh=True)
    except Exception as exc:
        raise HTTPException(502, f"Projection provider failed: {exc}") from exc
    players = {player_id for values in projection_sets.values() for player_id in values}
    return {"league_id": league_id, "week": week, "source": source, "players": len(players)}


@router.post("/leagues/{league_id}/projections/{week}/csv")
def import_csv(
    league_id: str,
    week: int,
    rows: list[dict],
    source: str = Query("manual", pattern="^(sleeper|espn|manual)$"),
    db: Session = Depends(get_db),
):
    if not db.get(League, league_id):
        raise HTTPException(404, "League not found")

    roster_player_ids = set(db.scalars(select(RosterPlayer.player_id).where(
        RosterPlayer.league_id == league_id,
    )))
    players = list(db.scalars(select(NFLPlayer).where(NFLPlayer.player_id.in_(roster_player_ids))))
    by_name: dict[str, list[NFLPlayer]] = {}
    for player in players:
        by_name.setdefault(_normalized_player_name(player.full_name or ""), []).append(player)

    existing = list(db.scalars(select(WeeklyPlayerProjection).where(
        WeeklyPlayerProjection.league_id == league_id,
        WeeklyPlayerProjection.week == week,
    )))
    for record in existing:
        if record.data.get("source", "manual") == source:
            db.delete(record)

    imported = 0
    unmatched: list[str] = []
    for original in rows:
        row = {str(key).strip().casefold(): value for key, value in original.items()}
        player_id = str(row.pop("player_id", "") or "").strip()
        player_name = str(row.pop("player", row.pop("name", "")) or "").strip()
        team = str(row.pop("team", "") or "").strip().upper()
        row.pop("position", None)
        row.pop("week", None)
        if not player_id and player_name:
            matches = by_name.get(_normalized_player_name(player_name), [])
            if team:
                matches = [player for player in matches if (player.team or "").upper() == team]
            if len(matches) == 1:
                player_id = matches[0].player_id
        if not player_id or player_id not in roster_player_ids:
            unmatched.append(player_name or player_id or "Unknown player")
            continue

        projection = row.pop(
            "projection",
            row.pop("projected_points", row.pop("proj", row.pop("fantasy_points", None))),
        )
        try:
            stats = (
                {"fantasy_points": float(projection)}
                if projection not in (None, "")
                else {
                    key: float(value or 0)
                    for key, value in row.items()
                    if value not in (None, "")
                }
            )
        except (TypeError, ValueError):
            unmatched.append(player_name or player_id)
            continue
        db.add(WeeklyPlayerProjection(
            league_id=league_id,
            week=week,
            data={"player_id": player_id, "stats": stats, "source": source},
        ))
        imported += 1
    db.commit()
    return {"imported": imported, "unmatched": unmatched, "source": source}
