from difflib import SequenceMatcher
import unicodedata

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import get_settings
from app.models import FantasyRoster, League, NFLPlayer, RosterPlayer, WeeklyPlayerProjection
from app.services.lineup_optimizer import Candidate, eligible_for_slot, optimize
from app.services.projection_provider import SleeperProjectionProvider
from app.services.espn_projection_provider import ESPNProjectionProvider
from app.services.recommendation_engine import decision_breakdown
from app.services.scoring_engine import fantasy_points

router = APIRouter(prefix="/api")


def _normalized_player_name(value: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    tokens = [
        token for token in "".join(
            character if character.isalnum() else " " for character in ascii_name.casefold()
        ).split()
        if token not in {"jr", "sr", "ii", "iii", "iv", "v"}
    ]
    return "".join(tokens)


def _normalized_team(value: str | None) -> str:
    aliases = {"JAC": "JAX", "LA": "LAR", "STL": "LAR", "OAK": "LV", "WAS": "WSH"}
    team = (value or "").strip().upper()
    return aliases.get(team, team)


def _match_espn_player(record: dict, players: list[NFLPlayer]) -> NFLPlayer | None:
    """Match ESPN to Sleeper using team plus exact/safely fuzzy normalized name."""
    team = _normalized_team(record.get("team"))
    position = record.get("position")
    candidates = [
        player for player in players
        if _normalized_team(player.team) == team
        and (not position or position in (player.fantasy_positions or []) or player.position == position)
    ]
    target = _normalized_player_name(record.get("name") or "")
    exact = [player for player in candidates if _normalized_player_name(player.full_name or "") == target]
    if len(exact) == 1:
        return exact[0]
    scored = sorted((
        (
            SequenceMatcher(None, target, _normalized_player_name(player.full_name or "")).ratio(),
            player,
        )
        for player in candidates
    ), key=lambda item: item[0])
    if not scored or scored[-1][0] < 0.92:
        return None
    if len(scored) > 1 and scored[-1][0] - scored[-2][0] < 0.05:
        return None
    return scored[-1][1]


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


def _roster_comment(
    candidate: Candidate,
    recommended_ids: set[str],
    current_ids: set[str],
    details: dict,
    lineup: list[tuple[str, Candidate]],
) -> str:
    player = details[candidate.player_id]
    health = player["injury"] or "healthy"
    if candidate.player_id in recommended_ids:
        slot = next(slot for slot, selected in lineup if selected.player_id == candidate.player_id)
        status = "Keep in your current lineup" if candidate.player_id in current_ids else "Move into your lineup"
        return (
            f"{status} at {slot}. The model gives {player['name']} a "
            f"{candidate.projection:.2f}-point aggregate projection and a "
            f"{candidate.start_score:.1f} decision score; current availability is {health}."
        )

    eligible_starters = [
        (slot, selected)
        for slot, selected in lineup
        if eligible_for_slot(slot, candidate.position)
    ]
    if not eligible_starters:
        return (
            f"Bench for now. {player['name']} has a {candidate.projection:.2f}-point "
            f"aggregate projection and no compatible starting slot in this lineup; "
            f"current availability is {health}."
        )
    slot, starter = min(
        eligible_starters,
        key=lambda item: (item[1].start_score, item[1].projection),
    )
    starter_details = details[starter.player_id]
    projection_gap = round(starter.projection - candidate.projection, 2)
    score_gap = round(starter.start_score - candidate.start_score, 1)
    close = abs(projection_gap) <= 2 or abs(score_gap) <= 8
    return (
        f"{'Close call, but bench' if close else 'Bench'} {player['name']} for now. "
        f"{starter_details['name']} holds the {slot} edge by {projection_gap:+.2f} projected "
        f"points and {score_gap:+.1f} decision-score points; {player['name']} is {health}."
    )


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


def _fetch_espn_projections(league: League, week: int, db: Session) -> dict:
    provider = ESPNProjectionProvider(get_settings().espn_projection_base_url)
    try:
        records = provider.projections(week, league.season)
    finally:
        provider.close()
    players_by_id = {
        player.player_id: player for player in db.scalars(select(NFLPlayer))
    }
    sleeper_response = httpx.get("https://api.sleeper.app/v1/players/nfl", timeout=60)
    sleeper_response.raise_for_status()
    for player_id, data in sleeper_response.json().items():
        if player_id in players_by_id:
            continue
        position = data.get("position")
        fantasy_positions = data.get("fantasy_positions") or ([position] if position else [])
        players_by_id[player_id] = NFLPlayer(
            player_id=player_id,
            full_name=data.get("full_name") or " ".join(filter(None, [data.get("first_name"), data.get("last_name")])),
            team=data.get("team"),
            position=position,
            fantasy_positions=fantasy_positions,
        )
    players = list(players_by_id.values())
    existing = list(db.scalars(select(WeeklyPlayerProjection).where(
        WeeklyPlayerProjection.league_id == league.league_id,
        WeeklyPlayerProjection.week == week,
    )))
    for record in existing:
        if record.data.get("source") == "espn":
            db.delete(record)

    imported = 0
    unmatched = []
    for record in records:
        player = _match_espn_player(record, players)
        if not player:
            unmatched.append({
                "name": record["name"], "team": record["team"],
                "position": record["position"],
            })
            continue
        db.add(WeeklyPlayerProjection(
            league_id=league.league_id,
            week=week,
            data={
                "player_id": player.player_id,
                "stats": record["stats"],
                "source": "espn",
                "provider_player_id": record["espn_id"],
                "provider_name": record["name"],
                "provider_team": record["team"],
                "outlook": record.get("outlook"),
            },
        ))
        imported += 1
    db.commit()
    return {
        "source_players": len(records),
        "imported": imported,
        "unmatched_count": len(unmatched),
        "unmatched": unmatched[:50],
    }


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
    roster_players = []
    for candidate in sorted(
        candidates,
        key=lambda item: (item.player_id not in recommended_ids, -item.start_score, -item.projection),
    ):
        player = details[candidate.player_id]
        roster_players.append({
            "player_id": candidate.player_id,
            "name": player["name"],
            "team": player["team"],
            "position": player["position"],
            "injury": player["injury"],
            "currently_started": candidate.player_id in current_ids,
            "recommended_start": candidate.player_id in recommended_ids,
            "sleeper_projection": player["sleeper_projection"],
            "aggregate_projection": player["aggregate_projection"],
            "projection_sources": player["projection_sources"],
            "start_score": candidate.start_score,
            "comment": _roster_comment(candidate, recommended_ids, current_ids, details, lineup),
        })
    return {"league_id": league_id, "week": week, "profile": profile, "projection_source": projection_source, "current_projected_points": round(sum(c.projection for c in candidates if c.player_id in current_ids), 2), "optimized_projected_points": round(sum(p.projection for _, p in lineup), 2), "recommended_lineup": recommended, "roster_players": roster_players, "changes": _lineup_changes(lineup, current_ids, details)}


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


@router.post("/leagues/{league_id}/projections/{week}/espn/sync")
def sync_espn_projections(
    league_id: str,
    week: int,
    db: Session = Depends(get_db),
):
    league = db.get(League, league_id)
    if not league:
        raise HTTPException(404, "League not found")
    try:
        result = _fetch_espn_projections(league, week, db)
    except Exception as exc:
        raise HTTPException(502, f"ESPN projection provider failed: {exc}") from exc
    return {"league_id": league_id, "week": week, **result}
