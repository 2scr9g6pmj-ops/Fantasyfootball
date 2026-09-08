from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from xml.etree import ElementTree as ET

import httpx
from cryptography.fernet import Fernet, InvalidToken


class YahooAPIError(RuntimeError):
    pass


def require_yahoo_settings(settings) -> None:
    missing = [name for name in ("yahoo_client_id", "yahoo_client_secret", "yahoo_token_encryption_key") if not getattr(settings, name)]
    if missing:
        raise YahooAPIError(f"Yahoo is not configured. Missing: {', '.join(x.upper() for x in missing)}")


def make_state(connection_id: str, secret: str, now: int | None = None) -> str:
    payload = f"{connection_id}.{now or int(time.time())}"
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}.{signature}".encode()).decode()


def verify_state(value: str, secret: str, max_age: int = 600) -> str:
    try:
        decoded = base64.urlsafe_b64decode(value.encode()).decode()
        connection_id, timestamp, signature = decoded.split(".", 2)
        payload = f"{connection_id}.{timestamp}"
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected) or abs(int(time.time()) - int(timestamp)) > max_age:
            raise ValueError
        return connection_id
    except (ValueError, TypeError, base64.binascii.Error) as exc:
        raise YahooAPIError("Invalid or expired Yahoo OAuth state") from exc


def encrypt_tokens(tokens: dict[str, Any], key: str) -> str:
    return Fernet(key.encode()).encrypt(json.dumps(tokens).encode()).decode()


def decrypt_tokens(value: str, key: str) -> dict[str, Any]:
    try: return json.loads(Fernet(key.encode()).decrypt(value.encode()))
    except (InvalidToken, ValueError, json.JSONDecodeError) as exc: raise YahooAPIError("Stored Yahoo credentials could not be decrypted") from exc


def _text(node: ET.Element | None, name: str) -> str | None:
    if node is None: return None
    child = node.find(f".//{{*}}{name}")
    return child.text if child is not None else None


def parse_leagues(xml: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml)
    results = []
    for league in root.findall(".//{*}league"):
        key = _text(league, "league_key")
        if key and not any(x["league_key"] == key for x in results):
            results.append({"league_key": key, "league_id": _text(league, "league_id"), "name": _text(league, "name"), "season": _text(league, "season")})
    return results


def parse_league_bundle(xml: str) -> dict[str, Any]:
    root = ET.fromstring(xml); league = root.find(".//{*}league")
    if league is None: raise YahooAPIError("Yahoo league response did not contain a league")
    roster_positions = []
    for slot in league.findall(".//{*}roster_position"):
        position, count = _text(slot, "position"), int(_text(slot, "count") or 0)
        roster_positions.extend([position] * count)
    teams = []
    for team in league.findall(".//{*}team"):
        key = _text(team, "team_key")
        if key and not any(t["team_key"] == key for t in teams): teams.append({"team_key": key, "team_id": _text(team, "team_id"), "name": _text(team, "name")})
    picks = []
    for result in league.findall(".//{*}draft_result"):
        picks.append({"pick": int(_text(result, "pick") or 0), "round": int(_text(result, "round") or 0),
                      "team_key": _text(result, "team_key"), "player_key": _text(result, "player_key")})
    scoring = {}
    for stat in league.findall(".//{*}stat"):
        stat_id, value = _text(stat, "stat_id"), _text(stat, "value")
        if stat_id and value is not None: scoring[stat_id] = value
    return {"league_key": _text(league, "league_key"), "league_id": _text(league, "league_id"), "name": _text(league, "name"),
            "season": _text(league, "season"), "num_teams": int(_text(league, "num_teams") or len(teams)),
            "roster_positions": roster_positions, "scoring": scoring, "teams": teams, "picks": picks}


def parse_players(xml: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml); players = []
    for player in root.findall(".//{*}player"):
        key = _text(player, "player_key")
        if not key or any(x["player_id"] == key for x in players): continue
        average_pick = _text(player, "average_pick") or _text(player, "pre_season_average_pick")
        adp = float(average_pick) if average_pick else 999.0
        players.append({"player_id": key, "yahoo_player_id": _text(player, "player_id"), "name": _text(player, "full") or _text(player, "name"),
                        "position": _text(player, "display_position"), "team": _text(player, "editorial_team_abbr"),
                        "injury": _text(player, "status"), "adp": adp, "rank": round(adp), "tier": max(1, min(9, int((adp - 1) // 24 + 1))),
                        "projection": 0.0, "upside": 0.0})
    return players


class YahooClient:
    def __init__(self, settings, client: httpx.AsyncClient | None = None):
        self.settings = settings; self._owned = client is None; self.client = client or httpx.AsyncClient(timeout=30)

    async def close(self):
        if self._owned: await self.client.aclose()

    def authorization_url(self, connection_id: str) -> str:
        require_yahoo_settings(self.settings)
        return f"{self.settings.yahoo_auth_url}?{urlencode({'client_id': self.settings.yahoo_client_id, 'redirect_uri': self.settings.yahoo_redirect_uri, 'response_type': 'code', 'state': make_state(connection_id, self.settings.yahoo_client_secret), 'language': 'en-us'})}"

    async def exchange_code(self, code: str) -> dict[str, Any]:
        response = await self.client.post(self.settings.yahoo_token_url, auth=(self.settings.yahoo_client_id, self.settings.yahoo_client_secret),
                                          data={"grant_type": "authorization_code", "redirect_uri": self.settings.yahoo_redirect_uri, "code": code})
        if response.is_error: raise YahooAPIError(f"Yahoo token exchange failed ({response.status_code})")
        return response.json()

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        response = await self.client.post(self.settings.yahoo_token_url, auth=(self.settings.yahoo_client_id, self.settings.yahoo_client_secret),
                                          data={"grant_type": "refresh_token", "redirect_uri": self.settings.yahoo_redirect_uri, "refresh_token": refresh_token})
        if response.is_error: raise YahooAPIError(f"Yahoo token refresh failed ({response.status_code})")
        return response.json()

    async def get(self, path: str, token: str) -> str:
        response = await self.client.get(f"{self.settings.yahoo_fantasy_base_url}{path}", headers={"Authorization": f"Bearer {token}", "Accept": "application/xml"})
        if response.is_error: raise YahooAPIError(f"Yahoo Fantasy request failed ({response.status_code})")
        return response.text

    async def leagues(self, token: str) -> list[dict[str, Any]]:
        return parse_leagues(await self.get("/users;use_login=1/games;game_codes=nfl/leagues", token))

    async def league_bundle(self, league_key: str, token: str) -> dict[str, Any]:
        return parse_league_bundle(await self.get(f"/league/{league_key};out=settings,teams,draftresults", token))

    async def available_players(self, league_key: str, token: str, max_players: int = 350) -> list[dict[str, Any]]:
        players = []
        for start in range(0, max_players, 25):
            page = parse_players(await self.get(f"/league/{league_key}/players;status=A;start={start};count=25;out=draft_analysis", token))
            players.extend(page)
            if len(page) < 25: break
        return players


def token_expiry(tokens: dict[str, Any]) -> datetime:
    return datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=max(0, int(tokens.get("expires_in", 3600)) - 60))
