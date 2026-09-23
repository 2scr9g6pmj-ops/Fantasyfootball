# Sleeper Fantasy Football Database

Milestone 1 of a multi-league lineup and waiver assistant for Sleeper user **CousinsFF**. This FastAPI service discovers every NFL league for a selected season, identifies the user's roster, maps rostered player IDs, and persists normalized data in SQLite.

The Sleeper integration is strictly read-only. It cannot change lineups, add/drop players, or submit waivers.

## Live draft optimizer

Open `/draft` for the real-time Draft Room. A draft session combines automatic pick imports with manual corrections and overrides. Yahoo and ESPN are the primary platform targets and are represented in the platform model without requiring login; their draft adapters remain to be implemented. Sleeper currently provides the reference automatic pick-import adapter.

Create a session with `POST /api/drafts`. Its body accepts `platform` (`manual`, `yahoo`, `espn`, or `sleeper`), optional `platform_draft_id`, `user_team_id`, a player pool, team rosters, current pick/round, and league settings. Player records may include `player_id`, `name`, `position`, `projection`, `upside`, `adp`, `rank`, `tier`, `injury`, and news metadata.

Every live edit is an event sent to `POST /api/drafts/{id}/events`:

| Event | Payload purpose |
| --- | --- |
| `pick` | `player_id`, drafting `team_id`, and optional `pick` |
| `keeper` | Player/team plus optional keeper round |
| `set_clock` | Correct current `pick` and `round` |
| `set_roster` | Replace a team's `player_ids` |
| `pool` | `remove` or `add` player IDs |
| `player_override` | Override projection, upside, ADP, rank, tier, injury, or news fields |
| `settings_override` | Override teams, scoring, or `roster_positions` |

Each response contains the rebuilt state, roster construction, top recommendations, Draft/Wait/Target Later guidance, positional scarcity, estimated next-pick survival, and an explanation. `POST /undo` and `POST /redo` move the event cursor; applying a new event after undo creates a clean history branch. `POST /sync` imports new picks for a configured Sleeper draft and deduplicates already imported picks.

The recommendation score combines projection, value above replacement, upside, roster need, tier scarcity, injury/news risk, and an ADP-based reach penalty. Survival is an estimate—not a guarantee—and becomes more useful when current ADP and complete player-pool data are supplied.

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

Account, league, roster, and player synchronization uses endpoints documented at [docs.sleeper.com](https://docs.sleeper.com/). Sleeper requires no API token and asks clients to remain below 1,000 calls per minute. The large player map is fetched once per sync and only rostered players are persisted. Weekly projections use a separate, configurable provider adapter and are cached in the database; CSV import remains available if that feed changes or is unavailable.

## Next milestone

The first Milestone 2 slice is included: a swappable `ProjectionProvider`, league-specific scoring, Conservative/Balanced/Upside profiles, legal lineup optimization, and a responsive dashboard. The dashboard fetches available weekly Sleeper projected stat lines through a configurable provider and stores a league/week snapshot. Because that projection feed is separate from Sleeper's documented v1 API, manual CSV import remains the supported fallback. Import projected stat lines (not generic fantasy points) so each league's scoring is applied independently:

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
