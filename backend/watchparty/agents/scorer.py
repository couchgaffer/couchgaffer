"""Ranks the managers' bids for the one group screen, and keeps the airtime ledger (#11).

    stake    = |delta| / largest |delta| among the bids * (0.75 + 0.25 * urgency)
    fairness = 1 + 0.3 * overruled - 0.5 * max(0, airtime_share - 0.2)
    score    = stake * fairness

Points decide; the agent's self-reported urgency only nudges, so a bid cannot talk its way past a bigger
moment. `overruled` counts close calls lost since the manager was last on screen. A clear winner leads on
both points and score by more than the close-call window; anything else is a close call for the host agent,
so a tie on points always reaches it, whatever the agents say about their urgency.

The window is set per pair: the leader on points against each rival. It is 10%, and 25% when the two argue
from different kinds of fact (#88): one from a league-table move, say, and the other from a player nobody
else owns. Points cannot weigh one against the other, so that is the host's call. 25% is the gap between
neighbouring moments: 4 against 3 IP, or a 6 IP goal against a 5 IP goal, goes to the host; 3 against 2, or
8 against 2, stays with the scorer. A pair that argues from the same kind of fact, or where either has none,
keeps 10%, so a third bid's angle never widens the window between the other two.
"""

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

CLOSE_CALL = 0.10
ANGLES_DIFFER_CLOSE_CALL = 0.25
FAIR_SHARE = 0.2  # five managers: a fifth of the screen each


@dataclass
class Ledger:
    """Shared session state: who has had the group screen, and who keeps losing it."""

    airtime: Counter[str] = field(default_factory=Counter)
    overruled: Counter[str] = field(default_factory=Counter)
    decisions: int = 0

    def share(self, manager: str) -> float:
        return round(self.airtime[manager] / self.decisions, 3) if self.decisions else 0.0

    def fairness(self, manager: str) -> float:
        return 1 + 0.3 * self.overruled[manager] - 0.5 * max(0.0, self.share(manager) - FAIR_SHARE)

    def record(self, chosen: str, candidates: list[str], *, close: bool) -> None:
        """One decision. Only a close call's losers were overruled: losing a clear call is not (#27)."""
        self.decisions += 1
        self.airtime[chosen] += 1
        self.overruled[chosen] = 0
        if close:
            self.overruled.update(c for c in candidates if c != chosen)


def angle_kind(bid: dict[str, Any], stake: dict[str, Any]) -> str | None:
    """The kind of the fact a bid argued from, looked up in its own stake: None without an angle."""
    angle = bid.get("angle")
    if not angle:
        return None
    return next((f["kind"] for f in stake.get("facts") or [] if f["id"] == angle), None)


def rank(
    bids: dict[str, dict[str, Any]], stakes: dict[str, dict[str, Any]], ledger: Ledger
) -> tuple[list[dict[str, Any]], bool]:
    """HostDecision.scores, highest first (ties to the lower id), and whether this is a close call."""
    largest = max(abs(stakes[m]["delta_points"]) for m in bids) or 1
    stake = {m: abs(stakes[m]["delta_points"]) / largest * (0.75 + 0.25 * min(1.0, max(0.0, bid["urgency"])))
             for m, bid in bids.items()}  # fmt: skip
    score = {m: round(stake[m] * ledger.fairness(m), 3) for m in bids}
    order = sorted(bids, key=lambda m: (-score[m], m))
    rows = [{"manager": m, "score": score[m], "airtime_share": ledger.share(m), "overruled_count": ledger.overruled[m]}
            | ({"angle": bids[m]["angle"]} if bids[m].get("angle") else {}) for m in order]  # fmt: skip
    if len(bids) == 1:
        return rows, False
    points = {m: abs(stakes[m]["delta_points"]) for m in bids}
    leader = min(bids, key=lambda m: (-points[m], m))
    kind = {m: angle_kind(bids[m], stakes[m]) for m in bids}

    def window(rival: str) -> float:
        """Per pair (review of #113): a third bid's angle must not widen the window between the other two."""
        differ = None not in (kind[leader], kind[rival]) and kind[leader] != kind[rival]
        return ANGLES_DIFFER_CLOSE_CALL if differ else CLOSE_CALL

    clear = all(points[r] < (1 - window(r)) * points[leader] and score[r] < (1 - window(r)) * score[leader]
                for r in bids if r != leader)  # fmt: skip
    return rows, not clear
