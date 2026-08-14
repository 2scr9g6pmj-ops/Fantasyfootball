PROFILES = {
    "conservative": {"projection": .75, "health": .25},
    "balanced": {"projection": .85, "health": .15},
    "upside": {"projection": .95, "health": .05},
}


def start_score(projection: float, injury: str | None, profile: str = "balanced") -> float:
    weights = PROFILES.get(profile, PROFILES["balanced"])
    health = {None: 100, "": 100, "Questionable": 65, "Doubtful": 25, "Out": 0, "IR": 0}.get(injury, 75)
    normalized_projection = min(100, projection * 5)
    return round(normalized_projection * weights["projection"] + health * weights["health"], 1)

