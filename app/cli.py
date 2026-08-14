import argparse
import asyncio

from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.services.sleeper_client import SleeperClient
from app.services.sync_service import SyncService


async def run_sync(season: str | None) -> None:
    settings = get_settings()
    Base.metadata.create_all(engine)
    client = SleeperClient(settings.sleeper_base_url)
    try:
        with SessionLocal() as db:
            result = await SyncService(db, client).sync(
                settings.sleeper_username, season or settings.sleeper_season
            )
            print(
                f"Sync complete: season={result.season}, leagues={result.leagues_synced}, "
                f"rosters={result.rosters_synced}, players={result.players_synced}"
            )
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Sleeper leagues and rosters into SQLite")
    parser.add_argument("--season", help="NFL season, such as 2025; defaults to Sleeper's league season")
    args = parser.parse_args()
    asyncio.run(run_sync(args.season))


if __name__ == "__main__":
    main()
