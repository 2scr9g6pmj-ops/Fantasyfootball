from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import FantasyRoster, League, LeagueUser, NFLPlayer, RosterPlayer, SyncHistory, User
from app.services.sleeper_client import SleeperClient


def _points(settings: dict[str, Any], prefix: str) -> float:
    return float(settings.get(prefix, 0) or 0) + float(settings.get(f"{prefix}_decimal", 0) or 0) / 100


class SyncService:
    def __init__(self, db: Session, client: SleeperClient):
        self.db = db
        self.client = client

    async def sync(self, username: str, season: str | None = None) -> SyncHistory:
        if not season:
            state = await self.client.get_nfl_state()
            season = str(state.get("league_season") or state["season"])
        history = SyncHistory(username=username, season=season, status="running")
        self.db.add(history)
        self.db.commit()
        try:
            user_data = await self.client.get_user(username)
            user_id = str(user_data["user_id"])
            self._upsert_user(user_data)
            leagues = await self.client.get_user_leagues(user_id, season)

            league_payloads: list[tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]] = []
            rostered_ids: set[str] = set()
            for league_data in leagues:
                league_id = str(league_data["league_id"])
                league_users = await self.client.get_league_users(league_id)
                rosters = await self.client.get_league_rosters(league_id)
                league_payloads.append((league_data, league_users, rosters))
                for roster in rosters:
                    rostered_ids.update(str(p) for p in (roster.get("players") or []))

            all_players = await self.client.get_all_players() if rostered_ids else {}
            for player_id in rostered_ids:
                self._upsert_player(player_id, all_players.get(player_id, {}))

            roster_count = 0
            for league_data, league_users, rosters in league_payloads:
                league_id = str(league_data["league_id"])
                self._upsert_league(league_data)
                self.db.execute(delete(LeagueUser).where(LeagueUser.league_id == league_id))
                for item in league_users:
                    if item.get("user_id"):
                        self._upsert_user(item)
                        metadata = item.get("metadata") or {}
                        self.db.add(LeagueUser(
                            league_id=league_id, user_id=str(item["user_id"]),
                            display_name=item.get("display_name"), team_name=metadata.get("team_name"),
                            is_commissioner=bool(item.get("is_owner")), metadata_json=metadata,
                        ))
                self.db.execute(delete(RosterPlayer).where(RosterPlayer.league_id == league_id))
                self.db.execute(delete(FantasyRoster).where(FantasyRoster.league_id == league_id))
                for roster in rosters:
                    self._add_roster(league_id, roster, user_id, league_data.get("roster_positions") or [])
                    roster_count += 1

            history.status = "success"
            history.leagues_synced = len(leagues)
            history.rosters_synced = roster_count
            history.players_synced = len(rostered_ids)
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

    def _upsert_user(self, data: dict[str, Any]) -> None:
        user_id = str(data["user_id"])
        row = self.db.get(User, user_id) or User(user_id=user_id, username=data.get("username") or user_id)
        row.username = data.get("username") or row.username
        row.display_name = data.get("display_name")
        row.avatar = data.get("avatar")
        self.db.add(row)

    def _upsert_league(self, data: dict[str, Any]) -> None:
        league_id = str(data["league_id"])
        row = self.db.get(League, league_id) or League(league_id=league_id, name=data.get("name") or league_id, season=str(data.get("season") or ""))
        for key in ("name", "status", "season_type", "draft_id", "previous_league_id"):
            setattr(row, key, data.get(key))
        row.season = str(data.get("season") or "")
        row.total_rosters = int(data.get("total_rosters") or 0)
        row.roster_positions = data.get("roster_positions") or []
        row.scoring_settings = data.get("scoring_settings") or {}
        row.settings = data.get("settings") or {}
        row.metadata_json = data.get("metadata") or {}
        self.db.add(row)

    def _upsert_player(self, player_id: str, data: dict[str, Any]) -> None:
        row = self.db.get(NFLPlayer, player_id) or NFLPlayer(player_id=player_id)
        row.full_name = data.get("full_name") or " ".join(filter(None, [data.get("first_name"), data.get("last_name")])) or player_id
        for key in ("first_name", "last_name", "team", "position", "status", "injury_status", "depth_chart_position", "depth_chart_order"):
            setattr(row, key, data.get(key))
        row.fantasy_positions = data.get("fantasy_positions") or []
        row.raw_data = data
        self.db.add(row)

    def _add_roster(self, league_id: str, data: dict[str, Any], primary_user_id: str, positions: list[str]) -> None:
        settings = data.get("settings") or {}
        roster_id = int(data["roster_id"])
        co_owner_ids = [str(value) for value in (data.get("co_owners") or [])]
        owner_id = str(data["owner_id"]) if data.get("owner_id") else None
        roster = FantasyRoster(
            league_id=league_id, roster_id=roster_id, owner_id=owner_id, co_owner_ids=co_owner_ids,
            is_primary_user=owner_id == primary_user_id or primary_user_id in co_owner_ids,
            wins=int(settings.get("wins") or 0), losses=int(settings.get("losses") or 0), ties=int(settings.get("ties") or 0),
            points_for=_points(settings, "fpts"), points_against=_points(settings, "fpts_against"),
            waiver_position=settings.get("waiver_position"), waiver_budget_used=settings.get("waiver_budget_used"),
            settings=settings, metadata_json=data.get("metadata") or {},
        )
        starters, reserve, taxi = data.get("starters") or [], set(data.get("reserve") or []), set(data.get("taxi") or [])
        starter_slots = [p for p in positions if p not in {"BN", "IR", "TAXI"}]
        for player_id in data.get("players") or []:
            player_id = str(player_id)
            starter_index = starters.index(player_id) if player_id in starters else None
            roster.players.append(RosterPlayer(
                league_id=league_id, roster_id=roster_id, player_id=player_id,
                is_starter=starter_index is not None, is_reserve=player_id in reserve, is_taxi=player_id in taxi,
                slot=starter_slots[starter_index] if starter_index is not None and starter_index < len(starter_slots) else "BN",
            ))
        self.db.add(roster)
