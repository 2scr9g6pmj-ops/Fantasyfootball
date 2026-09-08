from typing import Any

import httpx


class SleeperAPIError(RuntimeError):
    pass


class SleeperClient:
    def __init__(self, base_url: str, timeout: float = 30.0, client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self._owned_client = client is None
        self.client = client or httpx.AsyncClient(timeout=timeout, headers={"User-Agent": "Fantasyfootball/1.0"})

    async def close(self) -> None:
        if self._owned_client:
            await self.client.aclose()

    async def _get(self, path: str) -> Any:
        try:
            response = await self.client.get(f"{self.base_url}{path}")
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SleeperAPIError(f"Sleeper request failed for {path}: {exc}") from exc

    async def get_user(self, username_or_id: str) -> dict[str, Any]:
        data = await self._get(f"/user/{username_or_id}")
        if not data or not data.get("user_id"):
            raise SleeperAPIError(f"Sleeper user {username_or_id!r} was not found")
        return data

    async def get_nfl_state(self) -> dict[str, Any]:
        return await self._get("/state/nfl")

    async def get_user_leagues(self, user_id: str, season: str) -> list[dict[str, Any]]:
        return await self._get(f"/user/{user_id}/leagues/nfl/{season}")

    async def get_league_users(self, league_id: str) -> list[dict[str, Any]]:
        return await self._get(f"/league/{league_id}/users")

    async def get_league_rosters(self, league_id: str) -> list[dict[str, Any]]:
        return await self._get(f"/league/{league_id}/rosters")

    async def get_all_players(self) -> dict[str, dict[str, Any]]:
        return await self._get("/players/nfl")

    async def get_draft(self, draft_id: str) -> dict[str, Any]:
        return await self._get(f"/draft/{draft_id}")

    async def get_draft_picks(self, draft_id: str) -> list[dict[str, Any]]:
        return await self._get(f"/draft/{draft_id}/picks")

