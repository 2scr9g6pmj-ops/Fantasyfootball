from app.services.lineup_optimizer import Candidate, optimize
from app.services.recommendation_engine import start_score
from app.services.scoring_engine import fantasy_points


def test_league_scoring_changes_projection():
    stats = {"pass_yd": 300, "pass_td": 2, "int": 1}
    assert fantasy_points(stats, {"pass_yd": .04, "pass_td": 4, "int": -2}) == 18
    assert fantasy_points(stats, {"pass_yd": .05, "pass_td": 6, "int": -1}) == 26


def test_optimizer_respects_flex_and_unique_players():
    players = [Candidate("rb1", "RB", 10, 60), Candidate("rb2", "RB", 9, 55), Candidate("wr1", "WR", 12, 70)]
    lineup = optimize(["RB", "FLEX", "BN"], players)
    assert {p.player_id for _, p in lineup} == {"rb1", "wr1"}


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
