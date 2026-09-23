import csv
from abc import ABC, abstractmethod
from pathlib import Path

import httpx


class ProjectionProvider(ABC):
    @abstractmethod
    def projections(self, week: int, season: str | None = None) -> dict[str, dict[str, float]]: ...


class CSVProjectionProvider(ProjectionProvider):
    """CSV columns: player_id,week plus projected stats (pass_yd, rec, etc.)."""
    def __init__(self, path: str | Path): self.path = Path(path)

    def projections(self, week: int, season: str | None = None) -> dict[str, dict[str, float]]:
        if not self.path.exists(): return {}
        result = {}
        with self.path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if int(row.get("week") or 0) != week: continue
                pid = row.pop("player_id"); row.pop("week", None)
                result[pid] = {key: float(value or 0) for key, value in row.items()}
        return result


class SleeperProjectionProvider(ProjectionProvider):
    """Adapter for Sleeper's separate projections feed.

    The URL is configurable because this feed is not part of Sleeper's documented
    v1 API. Callers can replace this provider without changing lineup scoring.
    """

    def __init__(
        self,
        base_url: str,
        client: httpx.Client | None = None,
        timeout: float = 20.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(
            timeout=timeout, headers={"User-Agent": "Fantasyfootball/1.0"}
        )
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def projections(self, week: int, season: str | None = None) -> dict[str, dict[str, float]]:
        if not season:
            raise ValueError("season is required for Sleeper projections")
        response = self.client.get(
            f"{self.base_url}/{season}/{week}", params={"season_type": "regular"}
        )
        response.raise_for_status()
        result: dict[str, dict[str, float]] = {}
        for row in response.json():
            player_id = row.get("player_id")
            stats = row.get("stats") or {}
            if not player_id:
                continue
            result[str(player_id)] = {
                key: float(value)
                for key, value in stats.items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            }
        return result
