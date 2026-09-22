from abc import ABC, abstractmethod
from typing import Any


class FantasyPlatform(ABC):
    @abstractmethod
    async def leagues(self) -> dict[str, Any]: ...

    @abstractmethod
    async def team_roster(self, team_key: str) -> dict[str, Any]: ...
