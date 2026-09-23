from app.services.lineup_optimizer import Candidate, eligible_for_slot, optimize
from app.services.recommendation_engine import start_score
from app.services.scoring_engine import fantasy_points
from app.services.projection_provider import SleeperProjectionProvider
from app.api.lineups import _match_espn_player, _roster_comment, _scored_projection_consensus
from app.models import NFLPlayer
from app.services.espn_projection_provider import ESPNProjectionProvider
import httpx


def test_league_scoring_changes_projection():
    stats = {"pass_yd": 300, "pass_td": 2, "int": 1}
    assert fantasy_points(stats, {"pass_yd": .04, "pass_td": 4, "int": -2}) == 18
    assert fantasy_points(stats, {"pass_yd": .05, "pass_td": 6, "int": -1}) == 26


def test_provider_scored_projection_is_preserved():
    assert fantasy_points({"fantasy_points": 17.35}, {"pass_td": 6}) == 17.35


def test_optimizer_respects_flex_and_unique_players():
    players = [Candidate("rb1", "RB", 10, 60), Candidate("rb2", "RB", 9, 55), Candidate("wr1", "WR", 12, 70)]
    lineup = optimize(["RB", "FLEX", "BN"], players)
    assert {p.player_id for _, p in lineup} == {"rb1", "wr1"}


def test_slot_eligibility_supports_flex_and_superflex():
    assert eligible_for_slot("FLEX", "WR")
    assert eligible_for_slot("SUPER_FLEX", "QB")
    assert not eligible_for_slot("RB", "WR")


def test_optimizer_handles_realistic_roster_and_duplicate_slots():
    positions = ["QB"] * 3 + ["RB"] * 7 + ["WR"] * 7 + ["TE"] * 3
    players = [
        Candidate(f"p{index}", position, float(index), float(index))
        for index, position in enumerate(positions, start=1)
    ]
    lineup = optimize(
        ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPER_FLEX", "BN"],
        players,
    )
    assert len(lineup) == 8
    assert len({player.player_id for _, player in lineup}) == 8
    assert [slot for slot, _ in lineup] == [
        "QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPER_FLEX"
    ]


def test_injury_reduces_start_score():
    assert start_score(15, None) > start_score(15, "Out")


def test_sleeper_projection_provider_returns_projected_stats():
    def handler(request):
        assert request.url.path == "/projections/nfl/2026/3"
        assert request.url.params["season_type"] == "regular"
        return httpx.Response(200, json=[
            {"player_id": "p1", "stats": {"pass_yd": 275, "pass_td": 2, "opponent": "NYJ"}}
        ])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = SleeperProjectionProvider("https://example.test/projections/nfl", client=client)
    assert provider.projections(3, "2026") == {
        "p1": {"pass_yd": 275.0, "pass_td": 2.0}
    }


def test_projection_consensus_keeps_sleeper_and_averages_sources():
    aggregate, sleeper, sources = _scored_projection_consensus(
        "p1",
        {
            "sleeper": {"p1": {"rush_yd": 100}},
            "manual": {"p1": {"rush_yd": 80}},
        },
        {"rush_yd": 0.1},
    )
    assert aggregate == 9
    assert sleeper == 10
    assert sources == {"sleeper": 10, "manual": 8}


def test_projection_consensus_combines_sleeper_stats_and_espn_points():
    aggregate, sleeper, sources = _scored_projection_consensus(
        "p1",
        {
            "sleeper": {"p1": {"rush_yd": 100}},
            "espn": {"p1": {"fantasy_points": 14}},
        },
        {"rush_yd": 0.1},
    )
    assert aggregate == 12
    assert sleeper == 10
    assert sources == {"sleeper": 10, "espn": 14}


def test_roster_comment_covers_starters_and_bench_close_calls():
    starter = Candidate("start", "QB", 16.0, 80.0)
    bench = Candidate("bench", "QB", 15.0, 75.0)
    details = {
        "start": {"name": "Starter", "injury": None},
        "bench": {"name": "Bench", "injury": "Questionable"},
    }
    lineup = [("QB", starter)]

    assert "Keep in your current lineup" in _roster_comment(
        starter, {"start"}, {"start"}, details, lineup
    )
    bench_comment = _roster_comment(bench, {"start"}, {"start"}, details, lineup)
    assert "Close call, but bench" in bench_comment
    assert "Starter holds the QB edge" in bench_comment


def test_espn_provider_returns_all_active_weekly_players():
    def handler(request):
        assert request.url.params["scoringPeriodId"] == "3"
        return httpx.Response(200, json=[
            {
                "active": True, "defaultPositionId": 2, "fullName": "Travis Etienne Jr.",
                "id": 1, "proTeamId": 18,
                "stats": [{"seasonId": 2026, "scoringPeriodId": 3, "statSourceId": 1,
                           "statSplitTypeId": 1, "stats": {"24": 50, "25": .5, "53": 3}}],
                "outlooks": {"outlooksByWeek": {"3": "Weekly outlook"}},
            },
            {"active": False, "defaultPositionId": 2, "fullName": "Retired Player",
             "id": 2, "proTeamId": 18, "stats": []},
        ])

    provider = ESPNProjectionProvider("https://example.test/ffl", httpx.Client(transport=httpx.MockTransport(handler)))
    assert provider.projections(3, "2026") == [{
        "espn_id": "1", "name": "Travis Etienne Jr.", "team": "NO", "position": "RB",
        "stats": {"rush_yd": 50.0, "rush_td": .5, "rec": 3.0}, "outlook": "Weekly outlook",
    }]


def test_espn_match_requires_team_and_handles_suffixes():
    saints = NFLPlayer(player_id="s1", full_name="Travis Etienne", team="NO", position="RB", fantasy_positions=["RB"])
    jaguars = NFLPlayer(player_id="s2", full_name="Travis Etienne", team="JAX", position="RB", fantasy_positions=["RB"])
    assert _match_espn_player(
        {"name": "Travis Etienne Jr.", "team": "NO", "position": "RB"},
        [saints, jaguars],
    ) is saints
    assert _match_espn_player(
        {"name": "Travis Etienne Jr.", "team": "MIA", "position": "RB"},
        [saints, jaguars],
    ) is None
