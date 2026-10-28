"""Validate every example against its schema, and check that known-bad messages fail.

Run from the repo root:  uv run --with jsonschema python contracts/validate.py
"""

import copy
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).parent
SCHEMAS = ROOT / "schemas"
EXAMPLES = ROOT / "examples"


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def registry() -> Registry:
    resources = []
    for path in SCHEMAS.glob("*.schema.json"):
        schema = load(path)
        resources.append((schema["$id"], Resource.from_contents(schema)))
    return Registry().with_resources(resources)


def validator(name: str, reg: Registry) -> Draft202012Validator:
    schema = load(SCHEMAS / f"{name}.schema.json")
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, registry=reg)


def errors(v: Draft202012Validator, instance: dict) -> list[str]:
    return [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}" for e in v.iter_errors(instance)]


def main() -> int:
    reg = registry()
    failed = 0

    # Every schema is itself valid, and every example matches the schema its name starts with.
    for path in sorted(SCHEMAS.glob("*.schema.json")):
        validator(path.name.removesuffix(".schema.json"), reg)
    for path in sorted(EXAMPLES.glob("*.json")):
        name = path.name.split(".")[0]
        problems = errors(validator(name, reg), load(path))
        print(f"{'ok  ' if not problems else 'FAIL'} {path.name}")
        for p in problems:
            print(f"     {p}")
        failed += bool(problems)

    # Known-bad messages must be rejected, or the conditional rules are not working.
    def broken(example: str, change) -> tuple[str, dict]:
        instance = copy.deepcopy(load(EXAMPLES / example))
        change(instance)
        return example.split(".")[0], instance

    bad = {
        "shot without shot details": broken("match-event.shot.json", lambda d: d.pop("shot")),
        "climax without moment_id": broken("match-event.shot.json", lambda d: d.pop("moment_id")),
        "host decision without reason": broken("host-decision.json", lambda d: d.pop("reason")),
        "bid without evidence": broken("bid.analyst.json", lambda d: d.update(evidence_ids=[])),
        "cue for a sixth manager": broken("cue.phone.json", lambda d: d.update(screen="phone:mgr_6")),
        "table cue without table": broken("cue.group.json", lambda d: d.update(kind="table")),
        "league of four": broken("league.json", lambda d: d["managers"].pop()),
        "ws cue with bad payload": broken("ws-message.cue.json", lambda d: d["data"].pop("payload")),
        "fact of an unknown kind": broken("stake.json", lambda d: d["facts"][0].update(kind="vibes")),
        "differential with two owners": broken("stake.json", lambda d: d["facts"][0].update(owners=2)),
        "fact without evidence": broken("stake.json", lambda d: d["facts"][1].update(evidence_ids=[])),
        "declined bid with an explanation": broken("bid.declined.json", lambda d: d.update(explanation="x")),
        "declined bid with an angle": broken("bid.declined.json", lambda d: d.update(angle="f_x")),
        "answered bid without an explanation": broken("bid.analyst.json", lambda d: d.pop("explanation")),
        "host decision for an unverified reason": broken("host-decision.clash.json", lambda d: d.update(because="vibes")),
        "clash with one contender": broken("cue.clash.json", lambda d: d["payload"]["contenders"].pop()),
        "clash without a verdict time": broken("cue.clash.json", lambda d: d["payload"].pop("verdict_at")),
        "clash on a phone": broken("cue.clash.json", lambda d: d.update(screen="phone:mgr_1")),
        "lost screen without a winner": broken("cue.lost-screen.json", lambda d: d["payload"].pop("winner")),
        "lost screen on the group screen": broken("cue.lost-screen.json", lambda d: d.update(screen="group")),
        "declined bid with a pitch": broken("bid.declined.json", lambda d: d.update(pitch_to_host="x")),
        "declined bid with evidence": broken("bid.declined.json", lambda d: d.update(evidence_ids=["ev_x"])),
        "declined bid with urgency": broken("bid.declined.json", lambda d: d.update(urgency=0.5)),
        "answered bid without urgency": broken("bid.analyst.json", lambda d: d.pop("urgency")),
        "answered bid without a pitch": broken("bid.analyst.json", lambda d: d.pop("pitch_to_host")),
        "answered bid without evidence ids": broken("bid.analyst.json", lambda d: d.pop("evidence_ids")),
        "clash without a winner": broken("cue.clash.json", lambda d: d["payload"].pop("winner")),
        "host decision for lead_change": broken("host-decision.clash.json", lambda d: d.update(because="lead_change")),
        "host close call without contenders": broken("host-decision.clash.json", lambda d: d.pop("contenders")),
        "host close call without because": broken("host-decision.clash.json", lambda d: d.pop("because")),
        "host close call without either": broken("host-decision.json", lambda d: [d.pop("because"), d.pop("contenders")]),
        # Gameweek (#94): fixture ids, club-keyed player ids, the gameweek message and the elsewhere fact.
        "fixture fx_0": broken("match-event.gameweek.json", lambda d: d.update(fixture_id="fx_0")),
        "fixture fx_10": broken("match-frame.gameweek.json", lambda d: d.update(fixture_id="fx_10")),
        "fixture fx_a": broken("cue.gameweek.json", lambda d: d.update(fixture_id="fx_a")),
        "fixture fixture_1": broken("stake.gameweek.json", lambda d: d.update(fixture_id="fixture_1")),
        "bid with a bad fixture": broken("bid.gameweek.json", lambda d: d.update(fixture_id="fx_")),
        "decision with a bad fixture": broken("host-decision.gameweek.json", lambda d: d.update(fixture_id="FX_1")),
        "player of a two-letter club": broken("match-event.gameweek.json", lambda d: d.update(player="p_co_9")),
        "player of a four-letter club": broken("match-event.gameweek.json", lambda d: d.update(player="p_cors_9")),
        "player of an upper-case club": broken("match-event.gameweek.json", lambda d: d.update(player="p_COR_9")),
        "player number 100": broken("match-event.gameweek.json", lambda d: d.update(player="p_cor_100")),
        "player without a number": broken("match-frame.gameweek.json", lambda d: d["players"][0].update(id="p_cor_")),
        "elsewhere without evidence": broken("stake.gameweek.json", lambda d: d["facts"][-1].update(evidence_ids=[])),
        "elsewhere without points": broken("stake.gameweek.json", lambda d: d["facts"][-1].pop("points")),
        "elsewhere with points as text": broken("stake.gameweek.json", lambda d: d["facts"][-1].update(points="9")),
        "elsewhere naming fixtures": broken("stake.gameweek.json", lambda d: d["facts"][-1].update(fixtures=["fx_1"])),
        "elsewhere with a player": broken("stake.gameweek.json", lambda d: d["facts"][-1].update(player="p_kes_9")),
        "gameweek fixture without fixture_id": broken("gameweek.json", lambda d: d["fixtures"][1].pop("fixture_id")),
        "gameweek of two fixtures": broken("gameweek.json", lambda d: d["fixtures"].pop()),
        "gameweek of four fixtures": broken("gameweek.json", lambda d: d["fixtures"].append({**d["fixtures"][0], "fixture_id": "fx_4"})),
        "gameweek with a match id": broken("gameweek.json", lambda d: d.update(gameweek_id="match_demo_01")),
        "gameweek with a stray field": broken("gameweek.json", lambda d: d.update(home=d["fixtures"][0]["home"])),
        "ws gameweek carrying a meta": broken("ws-message.gameweek.json", lambda d: d.update(data=load(EXAMPLES / "match-meta.json"))),
        "ws meta carrying a gameweek": broken("ws-message.gameweek.json", lambda d: d.update(type="meta")),
    }
    for label, (name, instance) in bad.items():
        rejected = bool(errors(validator(name, reg), instance))
        print(f"{'ok  ' if rejected else 'FAIL'} rejects {label}")
        failed += not rejected

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
