STAT_ALIASES = {"pass_yds": "pass_yd", "rush_yds": "rush_yd", "rec_yds": "rec_yd"}


def fantasy_points(stats: dict[str, float], scoring: dict[str, float]) -> float:
    # Some providers publish an already-scored weekly projection instead of a
    # stat line. Keep that value intact; stat-line providers continue through
    # the league-specific calculation below.
    if "fantasy_points" in stats:
        return round(float(stats["fantasy_points"] or 0), 2)
    total = 0.0
    for stat, value in stats.items():
        key = STAT_ALIASES.get(stat, stat)
        total += value * float(scoring.get(key, scoring.get(stat, 0)) or 0)
    return round(total, 2)
