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
        response.raise_for_status()
        return response.json()

    async def leagues(self) -> list[dict[str, Any]]:
        data = await self._get("/users;use_login=1/games;game_keys=nfl/leagues")
        return data.get("fantasy_content", {})

    async def team_roster(self, team_key: str) -> dict[str, Any]:
        return await self._get(f"/team/{team_key}/roster")

