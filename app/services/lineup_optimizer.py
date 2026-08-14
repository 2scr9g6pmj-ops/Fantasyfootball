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
    best: tuple[float, list[tuple[str, Candidate]]] = (-1, [])

    def eligible(slot, position): return position == slot or position in ELIGIBLE.get(slot, set())
    def search(index, remaining, lineup, score):
        nonlocal best
        if index == len(active):
            if score > best[0]: best = score, lineup.copy()
            return
        slot = active[index]
        choices = [p for p in remaining if eligible(slot, p.position)]
        for player in choices:
            search(index + 1, [p for p in remaining if p.player_id != player.player_id], lineup + [(slot, player)], score + player.start_score)
        if not choices: search(index + 1, remaining, lineup, score)
    search(0, players, [], 0)
    return best[1]

