import json
from typing import Any

import httpx


ESPN_TEAM_IDS = {
    1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL",
    7: "DEN", 8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC",
    13: "LV", 14: "LAR", 15: "MIA", 16: "MIN", 17: "NE", 18: "NO",
    19: "NYG", 20: "NYJ", 21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC",
    25: "SF", 26: "SEA", 27: "TB", 28: "WSH", 29: "CAR", 30: "JAX",
    33: "BAL", 34: "HOU",
}

ESPN_POSITION_IDS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DEF"}

# ESPN stat IDs mapped to the equivalent Sleeper scoring keys. The existing
# scoring engine then applies each league's actual scoring settings.
ESPN_STAT_KEYS = {
    "0": "pass_att", "1": "pass_cmp", "3": "pass_yd", "4": "pass_td",
    "20": "pass_int", "23": "rush_att", "24": "rush_yd", "25": "rush_td",
    "42": "rec_yd", "43": "rec_td", "53": "rec", "73": "fum_lost",
    "74": "fgm_40_49", "75": "fga_40_49", "77": "fgm_50p", "78": "fga_50p",
    "80": "fgm_0_39", "81": "fga_0_39", "86": "xpm", "87": "xpa",
}


class ESPNProjectionProvider:
    """Public ESPN weekly projections adapter.

    ESPN's web page is backed by this JSON feed. We retain only active fantasy
    positions with a projection for the requested week.
    """

    def __init__(self, base_url: str, client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=60)
        self._owns_client = client is None

    def projections(self, week: int, season: str) -> list[dict[str, Any]]:
        player_filter = {"players": {"limit": 2000}}
        response = self.client.get(
            f"{self.base_url}/seasons/{season}/players",
            params={"scoringPeriodId": week, "view": "kona_player_info"},
            headers={"X-Fantasy-Filter": json.dumps(player_filter)},
        )
        response.raise_for_status()
        result = []
        for player in response.json():
            position = ESPN_POSITION_IDS.get(player.get("defaultPositionId"))
            team = ESPN_TEAM_IDS.get(player.get("proTeamId"))
            if not player.get("active") or not position or not team:
                continue
            projection = next((
                stat for stat in player.get("stats", [])
                if stat.get("seasonId") == int(season)
                and stat.get("scoringPeriodId") == week
                and stat.get("statSourceId") == 1
                and stat.get("statSplitTypeId") == 1
                and stat.get("stats")
            ), None)
            if not projection:
                continue
            stats = {
                ESPN_STAT_KEYS[key]: float(value)
                for key, value in projection["stats"].items()
                if key in ESPN_STAT_KEYS
            }
            outlook = (
                player.get("outlooks", {}).get("outlooksByWeek", {}).get(str(week))
            )
            result.append({
                "espn_id": str(player["id"]),
                "name": player.get("fullName") or "",
                "team": team,
                "position": position,
                "stats": stats,
                "outlook": outlook,
            })
        return result

    def close(self) -> None:
        if self._owns_client:
            self.client.close()
