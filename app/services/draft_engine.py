from __future__ import annotations

import math
from collections import Counter
from copy import deepcopy
from typing import Any


def initial_state(data: dict[str, Any]) -> dict[str, Any]:
    state = deepcopy(data)
    state.setdefault("players", [])
    state.setdefault("teams", {})
    state.setdefault("settings", {"teams": 12, "roster_positions": ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX"], "scoring": {}})
    state.setdefault("current_pick", 1)
    state.setdefault("current_round", 1)
    state.setdefault("overrides", {"players": {}, "settings": {}})
    rostered = {str(player_id) for player_ids in state["teams"].values() for player_id in player_ids}
    state["removed_player_ids"] = sorted(set(state.get("removed_player_ids", [])) | rostered)
    return state


def apply_event(state: dict[str, Any], event_type: str, p: dict[str, Any]) -> dict[str, Any]:
    state = deepcopy(state)
    if event_type == "pick":
        pid, team = str(p["player_id"]), str(p["team_id"])
        state["teams"].setdefault(team, [])
        if pid not in state["teams"][team]: state["teams"][team].append(pid)
        state["removed_player_ids"] = list(set(state["removed_player_ids"]) | {pid})
        state["current_pick"] = int(p.get("pick", state["current_pick"])) + 1
        teams = max(1, int(state["settings"].get("teams", 12)))
        state["current_round"] = math.ceil(state["current_pick"] / teams)
    elif event_type == "set_clock":
        state["current_pick"] = int(p.get("pick", state["current_pick"]))
        state["current_round"] = int(p.get("round", state["current_round"]))
    elif event_type == "set_roster":
        state["teams"][str(p["team_id"])] = [str(x) for x in p.get("player_ids", [])]
        state["removed_player_ids"] = sorted({x for ids in state["teams"].values() for x in ids})
    elif event_type == "keeper":
        q = dict(p); q.setdefault("pick", state["current_pick"]); state = apply_event(state, "pick", q)
        state.setdefault("keepers", {})[str(p["player_id"])] = {"team_id": str(p["team_id"]), "round": p.get("round")}
    elif event_type == "pool":
        remove = {str(x) for x in p.get("remove", [])}; add = {str(x) for x in p.get("add", [])}
        state["removed_player_ids"] = sorted((set(state["removed_player_ids"]) | remove) - add)
    elif event_type == "player_override":
        pid = str(p["player_id"]); values = {k: v for k, v in p.items() if k != "player_id"}
        state["overrides"]["players"].setdefault(pid, {}).update(values)
    elif event_type == "settings_override":
        state["settings"].update(p); state["overrides"]["settings"].update(p)
    return state


def recommendations(state: dict[str, Any], user_team_id: str) -> list[dict[str, Any]]:
    settings, overrides = state["settings"], state["overrides"]["players"]
    roster = state["teams"].get(str(user_team_id), [])
    by_id = {str(p["player_id"]): {**p, **overrides.get(str(p["player_id"]), {})} for p in state["players"]}
    counts = Counter(by_id[x].get("position") for x in roster if x in by_id)
    slots = Counter(settings.get("roster_positions", []))
    available = [p for pid, p in by_id.items() if pid not in set(state["removed_player_ids"])]
    position_values: dict[str, list[float]] = {}
    for p in available: position_values.setdefault(p.get("position", ""), []).append(float(p.get("projection", 0)))
    for vals in position_values.values(): vals.sort(reverse=True)
    pick = int(state["current_pick"]); teams = max(1, int(settings.get("teams", 12)))
    until_next = max(1, (teams * 2 - ((pick - 1) % (teams * 2))) if ((pick - 1) // teams) % 2 == 0 else ((pick - 1) % teams) * 2 + 1)
    out = []
    max_projection = max((float(p.get("projection", 0)) for p in available), default=1.0) or 1.0
    for p in available:
        pos = p.get("position", ""); projection = float(p.get("projection", 0)); adp = float(p.get("adp", 999))
        tier = int(p.get("tier", 9)); upside = float(p.get("upside", projection))
        vals = position_values.get(pos, [projection]); replacement = vals[min(len(vals)-1, max(1, int(settings.get("teams", 12))))]
        vor = max(0.0, projection - replacement)
        need = max(0, slots.get(pos, 0) - counts.get(pos, 0)) * 3.0
        scarcity = max(0.0, projection - (vals[min(len(vals)-1, 5)] if vals else 0))
        injury = str(p.get("injury", "")).lower(); risk = 12 if injury in {"out", "ir"} else 5 if injury in {"doubtful", "questionable"} else 0
        reach = max(0.0, adp - pick - until_next) * .18
        tier_bonus = max(0, 10 - tier) * 2
        data_grade = round(max(0, min(100, projection / max_projection * 100)), 1)
        eye_supplied = "eye_test_grade" in p or bool(p.get("eye_test_notes"))
        eye_grade = round(max(0, min(100, float(p.get("eye_test_grade", data_grade)))), 1)
        floor = float(p.get("floor", projection - max(0, upside-projection) * .45))
        ceiling = float(p.get("ceiling", upside))
        boom = round(max(0, min(100, float(p.get("boom_score", 50 + max(0, ceiling-projection) / max(1, projection) * 200)))), 1)
        bust = round(max(0, min(100, float(p.get("bust_risk", risk * 6 + max(0, projection-floor) / max(1, projection) * 100)))), 1)
        lens_gap = eye_grade - data_grade
        lens = "Consensus pick" if abs(lens_gap) < 5 else "Eye-test pick" if lens_gap > 0 else "Data pick"
        if boom >= 80 and bust >= 40: lens = "Boom pick"
        score = 50 + .9*vor + .3*max(0, upside-projection) + need + .4*scarcity + tier_bonus - risk - reach + .12*(eye_grade-data_grade)
        survive = max(2, min(98, round(100 / (1 + math.exp(-(adp-pick-until_next/2)/max(2, until_next/4))))))
        guidance = "Draft" if survive < 35 or adp <= pick + 2 else "Wait" if survive < 70 else "Target Later"
        reason = f"{pos} tier {tier}; {projection:.1f} projected, {vor:.1f} above replacement"
        if need: reason += "; fills a roster need"
        if risk: reason += "; injury/news risk applied"
        data_case = p.get("data_case") or f"{projection:.1f} projected points and {vor:.1f} above replacement."
        eye_case = p.get("eye_test_notes") or "Independent eye-test input pending; scouting grade defaults to the data baseline."
        sleeper = adp >= pick + max(6, until_next/2) and (ceiling >= projection * 1.08 or eye_grade >= data_grade + 8)
        out.append({**p, "score": round(score, 1), "guidance": guidance, "survive_next_pick_pct": survive, "tier_scarcity": round(scarcity, 1), "explanation": reason,
                    "recommendation_lens": lens, "data_grade": data_grade, "eye_test_grade": eye_grade, "eye_test_supplied": eye_supplied,
                    "floor": round(floor, 1), "ceiling": round(ceiling, 1), "boom_score": boom, "bust_risk": bust,
                    "data_case": data_case, "eye_test_case": eye_case, "steves_sleeper": sleeper,
                    "special_callout": "Steve's Sleeper" if sleeper else None,
                    "justification": f"Data: {data_case} Eye test: {eye_case}"})
    return sorted(out, key=lambda x: x["score"], reverse=True)


def roster_summary(state: dict[str, Any], team_id: str) -> dict[str, Any]:
    players = {str(p["player_id"]): p for p in state["players"]}
    ids = state["teams"].get(str(team_id), [])
    return {"player_ids": ids, "positions": dict(Counter(players[x].get("position", "Unknown") for x in ids if x in players))}
