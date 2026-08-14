import pytest
from sqlalchemy import select

from app.models import FantasyRoster, League, NFLPlayer, RosterPlayer, User
from app.services.sync_service import SyncService


class FakeSleeper:
    async def get_nfl_state(self): return {"league_season": "2025"}
    async def get_user(self, username): return {"user_id": "u1", "username": username, "display_name": "Cousins"}
    async def get_user_leagues(self, user_id, season):
        return [{"league_id": "l1", "name": "Test League", "season": season, "total_rosters": 2,
                 "status": "in_season", "roster_positions": ["QB", "RB", "BN"],
                 "scoring_settings": {"rec": 1}, "settings": {"playoff_teams": 4}}]
    async def get_league_users(self, league_id):
        return [{"user_id": "u1", "username": "CousinsFF", "display_name": "Cousins", "metadata": {"team_name": "Team"}}]
    async def get_league_rosters(self, league_id):
        return [{"roster_id": 1, "owner_id": "captain", "co_owners": ["u1"], "players": ["p1", "p2"], "starters": ["p1"], "reserve": ["p2"],
                 "settings": {"wins": 3, "losses": 1, "fpts": 400, "fpts_decimal": 25, "fpts_against": 350}}]
    async def get_all_players(self):
        return {"p1": {"full_name": "Starter One", "position": "QB", "team": "MIN"},
                "p2": {"full_name": "Bench Two", "position": "RB", "injury_status": "Questionable"}}


@pytest.mark.asyncio
async def test_sync_persists_league_primary_roster_and_players(db):
    result = await SyncService(db, FakeSleeper()).sync("CousinsFF")
    assert result.status == "success" and result.leagues_synced == 1
    assert db.get(User, "u1").username == "CousinsFF"
    assert db.get(League, "l1").scoring_settings == {"rec": 1}
    roster = db.get(FantasyRoster, ("l1", 1))
    assert roster.is_primary_user and roster.points_for == 400.25
    assert db.get(NFLPlayer, "p2").injury_status == "Questionable"
    slots = {p.player_id: p.slot for p in db.scalars(select(RosterPlayer))}
    assert slots == {"p1": "QB", "p2": "BN"}
