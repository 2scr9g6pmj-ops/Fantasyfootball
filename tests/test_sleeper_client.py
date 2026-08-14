import httpx
import pytest
import respx

from app.services.sleeper_client import SleeperClient, SleeperAPIError


@pytest.mark.asyncio
@respx.mock
async def test_user_and_league_requests():
    respx.get("https://api.test/v1/user/CousinsFF").mock(return_value=httpx.Response(200, json={"user_id": "42"}))
    respx.get("https://api.test/v1/user/42/leagues/nfl/2025").mock(return_value=httpx.Response(200, json=[{"league_id": "7"}]))
    async with httpx.AsyncClient() as http:
        client = SleeperClient("https://api.test/v1", client=http)
        assert (await client.get_user("CousinsFF"))["user_id"] == "42"
        assert (await client.get_user_leagues("42", "2025"))[0]["league_id"] == "7"


@pytest.mark.asyncio
@respx.mock
async def test_missing_user_has_clear_error():
    respx.get("https://api.test/v1/user/nobody").mock(return_value=httpx.Response(200, content=b"null"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(SleeperAPIError, match="not found"):
            await SleeperClient("https://api.test/v1", client=http).get_user("nobody")
