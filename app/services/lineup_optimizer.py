from dataclasses import dataclass
from functools import lru_cache

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
    def eligible(slot, position): return position == slot or position in ELIGIBLE.get(slot, set())
    indexed = list(enumerate(players))
    # Constrained slots first dramatically reduces the exact assignment search.
    active.sort(key=lambda slot: sum(eligible(slot, p.position) for p in players))

    @lru_cache(maxsize=None)
    def search(index: int, used: int):
        if index == len(active): return 0.0, ()
        slot = active[index]
        best_score, best_lineup = search(index + 1, used)
        for player_index, player in indexed:
            bit = 1 << player_index
            if used & bit or not eligible(slot, player.position): continue
            tail_score, tail = search(index + 1, used | bit)
            score = player.start_score + tail_score
            if score > best_score:
                best_score, best_lineup = score, ((slot, player_index),) + tail
        return best_score, best_lineup

    return [(slot, players[index]) for slot, index in search(0, 0)[1]]
