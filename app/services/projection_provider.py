import csv
from abc import ABC, abstractmethod
from pathlib import Path


class ProjectionProvider(ABC):
    @abstractmethod
    def projections(self, week: int) -> dict[str, dict[str, float]]: ...


class CSVProjectionProvider(ProjectionProvider):
    """CSV columns: player_id,week plus projected stats (pass_yd, rec, etc.)."""
    def __init__(self, path: str | Path): self.path = Path(path)

    def projections(self, week: int) -> dict[str, dict[str, float]]:
        if not self.path.exists(): return {}
        result = {}
        with self.path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if int(row.get("week") or 0) != week: continue
                pid = row.pop("player_id"); row.pop("week", None)
                result[pid] = {key: float(value or 0) for key, value in row.items()}
        return result

