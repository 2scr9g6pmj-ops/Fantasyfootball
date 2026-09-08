from app.services.draft_engine import apply_event, initial_state, recommendations


def sample_state():
    return initial_state({
        "players": [
            {"player_id": "a", "name": "Alpha RB", "position": "RB", "projection": 220, "upside": 250, "adp": 2, "tier": 1},
            {"player_id": "b", "name": "Beta WR", "position": "WR", "projection": 210, "upside": 235, "adp": 20, "tier": 2},
            {"player_id": "c", "name": "Gamma RB", "position": "RB", "projection": 150, "adp": 40, "tier": 4},
        ],
        "teams": {"me": []}, "settings": {"teams": 2, "roster_positions": ["RB", "WR"], "scoring": {}},
    })


def test_pick_removes_player_and_override_recalculates():
    state = apply_event(sample_state(), "pick", {"player_id": "a", "team_id": "other", "pick": 1})
    assert "a" not in [p["player_id"] for p in recommendations(state, "me")]
    before = next(p["score"] for p in recommendations(state, "me") if p["player_id"] == "b")
    state = apply_event(state, "player_override", {"player_id": "b", "projection": 100, "upside": 110, "injury": "Out"})
    after = next(p["score"] for p in recommendations(state, "me") if p["player_id"] == "b")
    assert after < before


def test_recommendations_include_guidance_scarcity_and_explanation():
    result = recommendations(sample_state(), "me")[0]
    assert result["guidance"] in {"Draft", "Wait", "Target Later"}
    assert 0 <= result["survive_next_pick_pct"] <= 100
    assert "above replacement" in result["explanation"]
    assert result["recommendation_lens"] == "Consensus pick"
    assert "data_case" in result and "eye_test_case" in result
    assert 0 <= result["boom_score"] <= 100
    assert 0 <= result["bust_risk"] <= 100


def test_eye_test_grade_changes_score_and_is_identified():
    state = sample_state()
    baseline = next(p for p in recommendations(state, "me") if p["player_id"] == "b")
    state = apply_event(state, "player_override", {"player_id": "b", "eye_test_grade": 98, "eye_test_notes": "Explosive separator with an expanding role."})
    updated = next(p for p in recommendations(state, "me") if p["player_id"] == "b")
    assert updated["score"] > baseline["score"]
    assert updated["recommendation_lens"] == "Eye-test pick"
    assert updated["eye_test_supplied"] is True


def test_preloaded_roster_is_removed_from_available_pool():
    state = sample_state()
    state["teams"]["me"] = ["a"]
    state = initial_state(state)
    assert "a" not in [p["player_id"] for p in recommendations(state, "me")]
