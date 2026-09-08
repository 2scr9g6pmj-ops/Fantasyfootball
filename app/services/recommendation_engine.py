PROFILES = {
    "conservative": {"projection": .75, "health": .25},
    "balanced": {"projection": .85, "health": .15},
    "upside": {"projection": .95, "health": .05},
}

INJURY_BUST_RISK = {None: 10, "": 10, "Questionable": 35, "Doubtful": 65, "Out": 100, "IR": 100}


def start_score(projection: float, injury: str | None, profile: str = "balanced") -> float:
    weights = PROFILES.get(profile, PROFILES["balanced"])
    health = {None: 100, "": 100, "Questionable": 65, "Doubtful": 25, "Out": 0, "IR": 0}.get(injury, 75)
    normalized_projection = min(100, projection * 5)
    return round(normalized_projection * weights["projection"] + health * weights["health"], 1)


def decision_breakdown(
    projection: float,
    injury: str | None,
    profile: str = "balanced",
    context: dict | None = None,
) -> dict:
    """Combine quantitative and explicitly supplied scouting signals.

    Eye-test values are never inferred from statistics. When a projection feed does
    not include ``eye_test_grade`` or ``eye_test_notes``, the quantitative grade is
    used as a neutral baseline and the response records that scouting is pending.
    """
    context = context or {}
    data_grade = start_score(projection, injury, profile)
    has_eye_test = "eye_test_grade" in context or bool(context.get("eye_test_notes"))
    eye_test_grade = float(context.get("eye_test_grade", data_grade))
    eye_test_grade = round(max(0, min(100, eye_test_grade)), 1)
    combined_score = round(data_grade * .65 + eye_test_grade * .35, 1)
    floor = round(float(context.get("floor", projection * .75)), 2)
    ceiling = round(float(context.get("ceiling", projection * 1.25)), 2)
    boom_score = round(max(0, min(100, float(context.get("boom_score", 50 + (ceiling - projection) * 4)))), 1)
    default_bust = INJURY_BUST_RISK.get(injury, 25) + max(0, (projection - floor) * 2)
    bust_risk = round(max(0, min(100, float(context.get("bust_risk", default_bust)))), 1)
    gap = eye_test_grade - data_grade
    lens = "Consensus pick" if abs(gap) < 5 else "Eye-test pick" if gap > 0 else "Data pick"
    if boom_score >= 80 and bust_risk >= 40: lens = "Boom pick"
    data_case = context.get("data_case") or f"{projection:.1f} league-adjusted projected points; quantitative grade {data_grade:.1f}."
    eye_case = context.get("eye_test_notes") or "Independent eye-test input pending; scouting grade defaults to the data baseline."
    matchup = {
        key: context[key]
        for key in ("opponent", "matchup", "game_script", "weather", "scheme", "role_notes")
        if context.get(key) not in (None, "")
    }
    return {
        "decision_score": combined_score,
        "recommendation_lens": lens,
        "data_grade": data_grade,
        "eye_test_grade": eye_test_grade,
        "eye_test_supplied": has_eye_test,
        "floor": floor,
        "ceiling": ceiling,
        "boom_score": boom_score,
        "bust_risk": bust_risk,
        "data_case": data_case,
        "eye_test_case": eye_case,
        "matchup_context": matchup,
        "justification": f"Data: {data_case} Eye test: {eye_case}",
    }

