# Sleeper Fantasy Football Database

Milestone 1 of a multi-league lineup and waiver assistant for Sleeper user **CousinsFF**. This FastAPI service discovers every NFL league for a selected season, identifies the user's roster, maps rostered player IDs, and persists normalized data in SQLite.

The Sleeper integration is strictly read-only. It cannot change lineups, add/drop players, or submit waivers.

## What Milestone 1 includes

- Sleeper username-to-user-ID resolution
- Automatic current league-season discovery, with an explicit season override
- Multi-league discovery and storage of league settings, scoring, roster positions, status, and draft ID
- League-member and all-roster synchronization, including identification of CousinsFF's roster
- Starter, bench, reserve/IR, and taxi classification
- Record, points, waiver priority, and used FAAB persistence
- Rostered NFL player mapping with team, position, injury, and depth-chart fields
- Normalized SQLite schema prepared for later recommendation, transaction, matchup, bracket, and evaluation data
- Transactional sync history and clear upstream error reporting
- FastAPI endpoints and automated tests

## Requirements

- Python 3.11 or newer
- Internet access to `https://api.sleeper.app`

## Local setup (PowerShell)

```powershell
git clone https://github.com/2scr9g6pmj-ops/Fantasyfootball.git
cd Fantasyfootball
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

The defaults already target `CousinsFF`. To select a particular NFL season, set `SLEEPER_SEASON=2025` (or another four-digit year) in `.env`. If it is blank, the app asks Sleeper's documented NFL-state endpoint for the current league season.

Start the application:

```powershell
uvicorn app.main:app --reload
```

For a one-shot or scheduled sync without running the web server:

```powershell
python -m app.cli
# Or: python -m app.cli --season 2025
```

Open `http://127.0.0.1:8000/docs`, then run `POST /api/sync`. You can also sync from PowerShell:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/sync
```

Override the season per request:

```powershell
Invoke-RestMethod -Method Post 'http://127.0.0.1:8000/api/sync?season=2025'
```

The SQLite database is created at `data/fantasyfootball.db`. Database files are intentionally ignored by Git.

## API

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Health check |
| `GET` | `/api/user` | Synced primary user |
| `GET` | `/api/leagues` | All synced leagues; optional `season` query |
| `GET` | `/api/leagues/{league_id}` | League settings and metadata |
| `GET` | `/api/leagues/{league_id}/roster` | CousinsFF roster and player assignments |
| `POST` | `/api/sync` | Discover and sync the selected season |
| `POST` | `/api/leagues/{league_id}/sync` | Resync season data and verify a league |
| `GET` | `/api/sync/history` | Latest sync outcomes |

## Tests

```powershell
python -m pytest -q
```

The suite mocks Sleeper for deterministic client and persistence tests. Milestone validation also includes a live read-only sync against Sleeper for `CousinsFF`.

## Data model

Milestone 1 actively populates `users`, `leagues`, `league_users`, `fantasy_rosters`, `roster_players`, `nfl_players`, and `sync_history`. Tables reserved for the next milestones are also created: weekly matchups and projections, weekly metrics, transactions, playoff brackets, lineup and waiver recommendations, and recommendation results.

JSON columns preserve flexible Sleeper settings without coupling the schema to a single league format. IDs use strings because Sleeper IDs exceed JavaScript-safe integer ranges. The SQLAlchemy models avoid SQLite-specific query behavior, simplifying a later migration to PostgreSQL or Supabase.

## Sleeper API behavior

This project uses only endpoints documented at [docs.sleeper.com](https://docs.sleeper.com/): user lookup, NFL state, user leagues, league users, league rosters, and the NFL player map. Sleeper requires no API token and asks clients to remain below 1,000 calls per minute. The large player map is fetched once per sync and only rostered players are persisted.

## Next milestone

The first Milestone 2 slice is included: a swappable `ProjectionProvider`, league-specific scoring, Conservative/Balanced/Upside profiles, legal lineup optimization, and a responsive dashboard. Import projected stat lines (not generic fantasy points) so each league's scoring is applied independently:

```powershell
$rows = @(
  @{player_id="1234"; pass_yd=285; pass_td=2; int=1; rush_yd=22},
  @{player_id="5678"; rush_yd=74; rush_td=0.5; rec=4; rec_yd=31}
)
Invoke-RestMethod -Method Post -ContentType application/json `
  -Body ($rows | ConvertTo-Json) `
  http://127.0.0.1:8000/api/leagues/LEAGUE_ID/projections/1/csv
```

Then open the dashboard and select the league, week, and risk profile. Advanced workload, matchup, weather, Vegas, waiver, and historical-evaluation inputs remain future milestones.

## Optional Yahoo configuration

Yahoo support imports leagues, teams, standings, roster slots, and players through a read-only Fantasy Sports app at [Yahoo Developer Network](https://developer.yahoo.com/apps/). Set its callback URL to `https://localhost:8000/api/platforms/yahoo/callback`, enable Fantasy Sports read permission, then add its Client ID and Client Secret to `.env`.

Generate the local encryption key:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy the result into `APP_ENCRYPTION_KEY` in `.env`. Never commit `.env`. Restart the HTTPS server, check `/api/platforms/yahoo/status`, and open `/api/platforms/yahoo/connect` to authorize Yahoo. Then use the **Sync Yahoo** button or `POST /api/platforms/yahoo/sync`. OAuth access and refresh tokens are encrypted before SQLite persistence and refreshed automatically; the application never stores a Yahoo password. Yahoo synchronization remains read-only.
