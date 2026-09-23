from dataclasses import dataclass

ELIGIBLE = {
    "FLEX": {"RB", "WR", "TE"}, "WRT": {"RB", "WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"}, "SUPERFLEX": {"QB", "RB", "WR", "TE"},
    "REC_FLEX": {"WR", "TE"}, "IDP_FLEX": {"DL", "LB", "DB"},
}


@dataclass
class Candidate:
    player_id: str
    position: str
    projection: float
    start_score: float


def optimize(slots: list[str], players: list[Candidate]) -> list[tuple[str, Candidate]]:
    active = [s for s in slots if s not in {"BN", "IR", "TAXI"}]
    slot_types = list(dict.fromkeys(active))
    capacities = tuple(active.count(slot) for slot in slot_types)

    def eligible(slot: str, position: str) -> bool:
        return position == slot or position in ELIGIBLE.get(slot, set())

    # A roster may have 20+ players. Trying every permutation grows factorially
    # and can exhaust a web request. This DP keeps only the best assignment for
    # each set of filled slot counts, so its state space stays very small.
    unique_players = list({player.player_id: player for player in players}.values())
    empty = tuple(0 for _ in slot_types)
    states: dict[tuple[int, ...], tuple[float, list[tuple[str, Candidate]]]] = {
        empty: (0.0, [])
    }
    for player in unique_players:
        next_states = dict(states)
        for counts, (score, assignments) in states.items():
            for index, slot in enumerate(slot_types):
                if counts[index] >= capacities[index] or not eligible(slot, player.position):
                    continue
                updated = list(counts)
                updated[index] += 1
                key = tuple(updated)
                candidate = (score + player.start_score, assignments + [(slot, player)])
                if key not in next_states or candidate[0] > next_states[key][0]:
                    next_states[key] = candidate
        states = next_states

    _, assignments = max(
        states.items(), key=lambda item: (sum(item[0]), item[1][0])
    )[1]
    by_slot = {slot: [] for slot in slot_types}
    for slot, player in assignments:
        by_slot[slot].append(player)
    return [(slot, by_slot[slot].pop()) for slot in active if by_slot[slot]]

