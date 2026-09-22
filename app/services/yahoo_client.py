from typing import Any

import httpx

from app.services.platform import FantasyPlatform


class YahooClient(FantasyPlatform):
    BASE_URL = "https://fantasysports.yahooapis.com/fantasy/v2"

    def __init__(self, access_token: str):
        self.client = httpx.AsyncClient(headers={"Authorization": f"Bearer {access_token}"}, timeout=30)

    async def close(self): await self.client.aclose()

    async def _get(self, path: str) -> dict[str, Any]:
        response = await self.client.get(f"{self.BASE_URL}{path}", params={"format": "json"})
        if response.status_code == 401 and "additional_authorization_required" in response.text:
            raise YahooAPIError(
                "Yahoo requires Fantasy Sports read permission for this developer app. "
                "Enable it in Yahoo Developer Network, then reconnect Yahoo."
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise YahooAPIError(f"Yahoo API returned HTTP {response.status_code}") from exc
        return response.json()

    async def leagues(self) -> dict[str, Any]:
        return await self._get("/users;use_login=1/games;game_keys=nfl/leagues")

    async def league(self, league_key: str) -> dict[str, Any]:
        return await self._get(f"/league/{league_key};out=settings,standings/teams")

    async def team_roster(self, team_key: str) -> dict[str, Any]:
        return await self._get(f"/team/{team_key}/roster/players")


class YahooAPIError(RuntimeError):
    pass
