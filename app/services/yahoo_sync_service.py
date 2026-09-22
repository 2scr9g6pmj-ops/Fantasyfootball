from datetime import UTC, datetime
from typing import Any, Iterable

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import FantasyRoster, League, LeagueUser, NFLPlayer, RosterPlayer, SyncHistory, User
from app.services.yahoo_client import YahooClient


def merge_fragments(value: Any) -> dict[str, Any]:
    """Flatten Yahoo's array-of-object resource representation."""
    if isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    if isinstance(value, list):
        for fragment in value:
            if isinstance(fragment, dict):
                result.update(fragment)
    return result


def collection_items(collection: Any, resource_name: str) -> list[Any]:
    if not isinstance(collection, dict):
        return []
    count = int(collection.get("count") or 0)
    items = []
    for index in range(count):
        wrapper = collection.get(str(index), {})
        if isinstance(wrapper, dict) and resource_name in wrapper:
            items.append(wrapper[resource_name])
    return items


def find_collections(value: Any, key: str) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        if key in value and isinstance(value[key], dict):
            yield value[key]
        for child in value.values():
            yield from find_collections(child, key)
    elif isinstance(value, list):
        for child in value:
            yield from find_collections(child, key)


def extract_resources(payload: dict[str, Any], collection: str, resource: str) -> list[dict[str, Any]]:
    for found in find_collections(payload.get("fantasy_content", payload), collection):
        items = [merge_fragments(item) for item in collection_items(found, resource)]
        if items:
            return items
    return []


def _positions(settings: dict[str, Any]) -> list[str]:
    raw = settings.get("roster_positions", {})
    entries = collection_items(raw, "roster_position")
    result: list[str] = []
    aliases = {"W/R/T": "FLEX", "W/R": "FLEX", "Q/W/R/T": "SUPER_FLEX", "BN": "BN", "IR": "IR"}
    for entry in entries:
        item = merge_fragments(entry)
        position = aliases.get(str(item.get("position")), str(item.get("position") or ""))
        result.extend([position] * int(item.get("count") or 1))
    return [position for position in result if position]


class YahooSyncService:
    def __init__(self, db: Session, client: YahooClient):
        self.db = db
        self.client = client

    async def sync(self) -> SyncHistory:
        history = SyncHistory(username="Yahoo", season="current", status="running")
        self.db.add(history)
        self.db.commit()
        try:
            leagues = extract_resources(await self.client.leagues(), "leagues", "league")
            roster_count = player_count = 0
            seasons: set[str] = set()
            for summary in leagues:
                league_key = str(summary["league_key"])
                details_payload = await self.client.league(league_key)
                detailed = extract_resources(details_payload, "leagues", "league")
                league_data = detailed[0] if detailed else summary
                season = str(league_data.get("season") or summary.get("season") or "")
                seasons.add(season)
                league_id = f"yahoo:{league_key}"
                settings = merge_fragments(league_data.get("settings", [{}])[0] if isinstance(league_data.get("settings"), list) else league_data.get("settings", {}))
                positions = _positions(settings)
                teams = extract_resources(details_payload, "teams", "team")
                league = self.db.get(League, league_id) or League(league_id=league_id, name=league_data.get("name") or league_key, season=season)
                league.platform = "yahoo"
                league.name = league_data.get("name") or league.name
                league.season = season
                league.total_rosters = int(league_data.get("num_teams") or len(teams))
                league.status = league_data.get("draft_status")
                league.season_type = "regular"
                league.roster_positions = positions
                league.scoring_settings = settings.get("stat_categories") or {}
                league.settings = settings
                league.metadata_json = {"yahoo_league_key": league_key, "url": league_data.get("url"), "platform": "yahoo"}
                self.db.add(league)
                self.db.flush()
                self.db.execute(delete(RosterPlayer).where(RosterPlayer.league_id == league_id))
                self.db.execute(delete(FantasyRoster).where(FantasyRoster.league_id == league_id))
                self.db.execute(delete(LeagueUser).where(LeagueUser.league_id == league_id))
                for team in teams:
                    added = await self._add_team(league_id, team, positions)
                    roster_count += 1
                    player_count += added

            history.season = ",".join(sorted(seasons)) or "current"
            history.status = "success"
            history.leagues_synced = len(leagues)
            history.rosters_synced = roster_count
            history.players_synced = player_count
            history.completed_at = datetime.now(UTC).replace(tzinfo=None)
            self.db.commit()
            self.db.refresh(history)
            return history
        except Exception as exc:
            self.db.rollback()
            history = self.db.get(SyncHistory, history.id)
            history.status = "failed"
            history.error = str(exc)
            history.completed_at = datetime.now(UTC).replace(tzinfo=None)
            self.db.commit()
            raise

    async def _add_team(self, league_id: str, team: dict[str, Any], positions: list[str]) -> int:
        team_key = str(team["team_key"])
        roster_id = int(team.get("team_id") or team_key.rsplit(".", 1)[-1])
        managers = collection_items(team.get("managers", {}), "manager")
        manager = merge_fragments(managers[0]) if managers else {}
        owner_id = f"yahoo:{manager.get('guid') or manager.get('manager_id') or team_key}"
        user = self.db.get(User, owner_id) or User(user_id=owner_id, username=manager.get("nickname") or owner_id)
        user.display_name = manager.get("nickname")
        user.avatar = manager.get("image_url")
        self.db.add(user)
        self.db.flush()
        self.db.add(LeagueUser(league_id=league_id, user_id=owner_id, display_name=user.display_name, team_name=team.get("name"), metadata_json=manager))
        standings = merge_fragments(team.get("team_standings", [{}])[0] if isinstance(team.get("team_standings"), list) else team.get("team_standings", {}))
        outcome = merge_fragments(standings.get("outcome_totals", {}))
        roster = FantasyRoster(
            league_id=league_id, roster_id=roster_id, owner_id=owner_id,
            is_primary_user=bool(team.get("is_owned_by_current_login")),
            wins=int(outcome.get("wins") or 0), losses=int(outcome.get("losses") or 0), ties=int(outcome.get("ties") or 0),
            points_for=float(standings.get("points_for") or 0), points_against=float(standings.get("points_against") or 0),
            waiver_position=int(team.get("waiver_priority") or 0) or None,
            settings={}, metadata_json={"team_key": team_key, "name": team.get("name"), "platform": "yahoo"},
        )
        roster_payload = await self.client.team_roster(team_key)
        players = extract_resources(roster_payload, "players", "player")
        for player in players:
            player_key = str(player.get("player_key"))
            player_id = f"yahoo:{player_key}"
            name = merge_fragments(player.get("name", {}))
            selected = merge_fragments(player.get("selected_position", [{}])[0] if isinstance(player.get("selected_position"), list) else player.get("selected_position", {}))
            eligible = player.get("eligible_positions", [])
            eligible_positions = [str(item.get("position")) for item in eligible if isinstance(item, dict) and item.get("position")]
            position = str(player.get("display_position") or (eligible_positions[0] if eligible_positions else "")).split(",")[0]
            nfl_player = self.db.get(NFLPlayer, player_id) or NFLPlayer(player_id=player_id)
            nfl_player.full_name = name.get("full") or player.get("name") or player_key
            nfl_player.first_name = name.get("first")
            nfl_player.last_name = name.get("last")
            nfl_player.team = player.get("editorial_team_abbr")
            nfl_player.position = position
            nfl_player.fantasy_positions = eligible_positions
            nfl_player.status = player.get("status_full") or player.get("status")
            nfl_player.injury_status = player.get("status")
            nfl_player.raw_data = player
            self.db.add(nfl_player)
            slot = str(selected.get("position") or "BN")
            roster.players.append(RosterPlayer(
                league_id=league_id, roster_id=roster_id, player_id=player_id,
                is_starter=slot not in {"BN", "IR", "IL", "NA"}, is_reserve=slot in {"IR", "IL"}, is_taxi=False, slot=slot,
            ))
        self.db.add(roster)
        return len(players)
