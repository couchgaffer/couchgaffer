"""The league rulebook for Foundry IQ (#18): one document per rule, per squad and per glossary term.

Rule numbers come from rules_v1, so the knowledge base cannot drift from the points engine. Each rule says
why it exists, which is what the agents retrieve it for; the numbers they quote still come from the match
tools' evidence and are checked by code. Only our own invented rules and synthetic data (rules 4.6, 4.7).
"""

from typing import Any

from watchparty.points import RULES, load_league
from watchparty.sim import DEMO_SEED, simulate

POSITIONS = {"GK": "goalkeeper", "DEF": "defender", "MID": "midfielder", "FWD": "forward"}


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _rules() -> list[dict[str, str]]:
    r, cap = RULES["rules"], RULES["captain_multiplier"]
    tiers = r["save_tier"]["tiers"]
    why = {
        "goal": f"A goal is worth {r['goal']['points']} IP to the scorer. Why: it is the moment the match turns on.",
        "long_odds_goal": (
            f"A goal from a shot under {r['long_odds_goal']['max_xg']} xG earns "
            f"{r['long_odds_goal']['points']} IP more. Why: the finish that should not have gone in "
            "deserves more than the tap-in."
        ),
        "shot_on_target": (
            f"A shot that is saved or scored earns {r['shot_on_target']['points']} IP. Why: forcing "
            "the keeper is a real contribution even without a goal."
        ),
        "chance_created": (
            f"The pass before a shot of {r['chance_created']['min_xg']} xG or more earns "
            f"{r['chance_created']['per_xg']} IP per 1 xG of that shot, rounded. Why: creators shape "
            "chances as much as finishers do, and a better chance is worth more."
        ),
        "progressive_pass": (
            f"A completed progressive pass earns {r['progressive_pass']['points']} IP, at most "
            f"{r['progressive_pass']['max_per_half']} a half. Why: moving the ball forward matters, "
            "and the cap stops a player farming points with safe passes."
        ),
        "ball_win": (
            f"A tackle or interception won earns {r['ball_win']['points']} IP. Why: winning the ball back counts."
        ),
        "press_win": (
            f"Pressure that wins the ball back within {r['press_win']['window_ms'] // 1000} seconds earns "
            f"{r['press_win']['points']} IP. Why: a successful press is a team action the stats sheet "
            "usually misses."
        ),
        "save_points": (
            f"A save earns {r['save_points']['points']} IP plus {r['save_points']['per_xg']} IP per 1 xG "
            "of the shot, rounded. Why: stopping a big chance matters more than catching a speculative "
            "shot."
        ),
        "save_tier": (
            f"The Wall tier: a keeper's {_ordinal(tiers[0][0])} save of the match earns {tiers[0][1]} IP "
            f"more, and the {_ordinal(tiers[1][0])} save {tiers[1][1]} IP more. Why: a run of saves is a "
            "story, and the tier makes the next save a moment to watch."
        ),
        "shutout_stretch": (
            f"A keeper or defender earns {r['shutout_stretch']['points']} IP for each "
            f"{r['shutout_stretch']['every_ms'] // 60_000} minutes without conceding. Why: keeping "
            "the other side out is earned over time, not in one moment."
        ),
        "conceded": (
            f"A keeper or defender on the pitch when a goal is conceded loses "
            f"{abs(r['conceded']['points'])} IP. Why: defending is shared, and so is the cost."
        ),
        "yellow_card": f"A yellow card costs {abs(r['yellow_card']['points'])} IP. Why: discipline matters.",
        "red_card": (
            f"A red card costs {abs(r['red_card']['points'])} IP. Why: it leaves the side a player short "
            "for the rest of the match."
        ),
    }
    docs = [{"id": "rule-" + key.replace("_", "-"), "kind": "rule", "title": spec["label"], "content": why[key]}
            for key, spec in r.items()]  # fmt: skip
    docs.append({"id": "rule-captain", "kind": "rule", "title": "Captain",
                 "content": f"Each manager names a captain, whose points count {cap} times. Why: the captain is the "
                            "manager's own big call, so it should swing the table."})  # fmt: skip
    return docs


def _squads() -> list[dict[str, str]]:
    # Every single match has the same two clubs and players, and league_v1 picks from them; a gameweek injects
    # other clubs (sim/gameweek.py) and its league, league_gw_v1, names players these docs do not cover yet.
    meta = simulate(DEMO_SEED).meta
    players = {p["id"]: p | {"club": meta[side]["name"]} for side in ("home", "away") for p in meta[side]["players"]}
    docs = []
    for m in load_league()["managers"]:
        names = []
        for pid in m["squad"]:
            p = players[pid]
            captain = ", captain: points count double" if pid == m["captain"] else ""
            names.append(f"{p['name']} ({POSITIONS[p['position']]}, {p['club']}{captain})")
        docs.append({"id": "squad-" + m["id"].replace("_", "-"), "kind": "squad",
                     "title": f"{m['display_name']}'s squad",
                     "content": f"{m['display_name']} picked {', '.join(names)}."})  # fmt: skip
    return docs


GLOSSARY = {
    "ip": (
        "Impact points (IP)",
        "The league's own points. Players earn them for what they do in the match, and a "
        "manager's total is the sum over their squad, with the captain doubled.",
    ),
    "wall-tier": (
        "Wall tier",
        "The save milestones in the save tier rule. When a keeper is one save away, the next "
        "save is a moment to watch.",
    ),
    "captain": ("Captain", "The one player in a squad whose points count double."),
    "key-moment": (
        "Key moment",
        "A passage of play that changes someone's points or rank. It matters to a manager "
        f"when it swings their total by {RULES['threshold_points']} IP or more, moves them in the table, "
        "or puts their keeper one save from the Wall tier.",
    ),
    "xg": ("Expected goals (xG)", "How likely a shot was to be scored, from 0 to 1, given where and how it was taken."),
    "control-index": (
        "Control index",
        "How much of the last five minutes a side spent in the final third with the ball.",
    ),
    "group-screen": (
        "The group screen",
        "The one shared screen. Each manager's agent bids for it; code scores the "
        "bids with a fairness ledger, and the host agent settles only the close calls.",
    ),
    "plain-stat": (
        "Plain stat",
        "When an agent misses its deadline or fails its checks, code shows the plain number "
        "instead, so the match never waits.",
    ),
}


def documents() -> list[dict[str, Any]]:
    glossary = [{"id": f"glossary-{key}", "kind": "glossary", "title": title, "content": text}
                for key, (title, text) in GLOSSARY.items()]  # fmt: skip
    return _rules() + _squads() + glossary
