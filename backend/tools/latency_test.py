"""Latency gate for #6: do manager explanations come back within the broadcast delay?

For each candidate deployment and each of the 30 scripted moments it measures, with keyless Entra auth:
  manager: one call per manager at stake, run concurrently (the planned design)
  host:    one call choosing the group screen from the managers' pitches
  batch:   one call returning every at-stake manager's bid (the fallback design)
Quality is measured too, so a fast model that breaks the evidence rule does not win.

Usage (from backend/):
  FOUNDRY_ENDPOINT=https://<name>.cognitiveservices.azure.com/ \\
    uv run python -m tools.latency_test gpt-4.1-mini gpt-5-mini:minimal --out results.json
"""

import argparse
import asyncio
import json
import math
import os
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any

from openai import AsyncOpenAI

from tools.latency_fixtures import MOMENTS, Moment, league, meta
from watchparty.agents.checks import number_problems
from watchparty.agents.foundry import foundry_client

TIMEOUT_S = 30.0
MAX_OUTPUT_TOKENS = 1500


def _strict(name: str, properties: dict[str, Any]) -> dict[str, Any]:
    schema = {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}
    return {"type": "json_schema", "name": name, "strict": True, "schema": schema}


_BID_FIELDS: dict[str, Any] = {
    "urgency": {"type": "number"},
    "explanation": {"type": "string"},
    "explanation_en": {"type": "string"},
    "pitch_to_host": {"type": "string"},
    "evidence_ids": {"type": "array", "items": {"type": "string"}},
}
MANAGER_SCHEMA = _strict("manager_bid", _BID_FIELDS)
HOST_SCHEMA = _strict("host_decision", {"chosen_manager": {"type": "string"}, "reason": {"type": "string"}})
BATCH_SCHEMA = _strict(
    "bids",
    {
        "bids": {
            "type": "array",
            "items": _strict("bid", {"manager_id": {"type": "string"}, **_BID_FIELDS})["schema"],
        }
    },
)

RULES = """Rules:
- Use only numbers that appear in EVIDENCE, written exactly as given.
- Put the id of every evidence item you use in evidence_ids.
- Name only the players and clubs in NAMES. Never mention real people, clubs or competitions.
- analyst style: up to two sentences with the key numbers. casual style: one short, warm sentence, at most one number.
- Write explanation in the manager's language. explanation_en is the English version, or "" when the language is en.
- pitch_to_host: one line, at most 140 characters: why the shared screen should show this for this manager. The
  whole room reads it on the shared screen: about the manager by name, in the third person, a statement, one fact.
- urgency: 0 to 1, how much this moment matters to this manager.
- Friendly mini-league banter only. This league is free to play: never mention betting, money or prizes."""


@dataclass(frozen=True)
class Candidate:
    deployment: str
    reasoning: str | None  # reasoning effort for reasoning models; None for models without it

    @property
    def label(self) -> str:
        return f"{self.deployment}:{self.reasoning}" if self.reasoning else self.deployment


@dataclass(frozen=True)
class Record:
    model: str
    kind: str  # manager | host | batch
    latency_ms: float
    valid: bool
    numbers_ok: bool
    tokens_in: int
    tokens_out: int
    moment_id: str = ""


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile."""
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    return ordered[max(1, math.ceil(p / 100 * len(ordered))) - 1]


def _names() -> list[str]:
    return [p["name"] for side in ("home", "away") for p in meta()[side]["players"]] + [
        meta()[side]["name"] for side in ("home", "away")
    ]


def _manager(manager_id: str) -> dict[str, Any]:
    return next(m for m in league()["managers"] if m["id"] == manager_id)


def _player_names(ids: list[str]) -> list[str]:
    by_id = {p["id"]: p["name"] for side in ("home", "away") for p in meta()[side]["players"]}
    return [by_id[i] for i in ids]


def _context(moment: Moment, stake: dict[str, Any]) -> dict[str, Any]:
    m = _manager(stake["manager"])
    return {
        "manager": m["display_name"],
        "style": m["style"],
        "language": m["lang"],
        "squad": _player_names(m["squad"]),
        "captain": _player_names([m["captain"]])[0],
        "moment": moment.summary,
        "rule_fired": stake["reason_code"],
        "EVIDENCE": [{k: e[k] for k in ("id", "label", "value")} for e in stake["evidence"]],
    }


def manager_request(moment: Moment, stake: dict[str, Any]) -> tuple[str, str]:
    m = _manager(stake["manager"])
    instructions = (
        f"You are {m['display_name']}'s personal companion in a free-to-play fantasy-style mini-league, "
        f"explaining what this moment means for their squad. Style: {m['style']}. Language: {m['lang']}.\n{RULES}"
    )
    return instructions, json.dumps({"NAMES": _names(), **_context(moment, stake)}, ensure_ascii=False)


def batch_request(moment: Moment) -> tuple[str, str]:
    instructions = (
        "You are the personal companion for each manager below in a free-to-play fantasy-style mini-league. "
        f"Return one bid per manager, in that manager's style and language.\n{RULES}"
    )
    managers = [{"manager_id": s["manager"], **_context(moment, s)} for s in moment.stakes]
    return instructions, json.dumps({"NAMES": _names(), "managers": managers}, ensure_ascii=False)


def host_request(moment: Moment, bids: dict[str, dict[str, Any]], airtime: dict[str, float]) -> tuple[str, str]:
    instructions = (
        "You host the one shared screen of a friends' mini-league watch party. Choose which manager's moment "
        "the group sees. Weigh how much it matters against fairness: managers with less airtime deserve a turn. "
        "chosen_manager must be one of the candidate ids. reason: at most 140 characters, shown to everyone, "
        "no numbers, names only from the candidates."
    )
    candidates = [
        {"id": mid, "name": _manager(mid)["display_name"], "pitch": bid["pitch_to_host"], "urgency": bid["urgency"]}
        for mid, bid in bids.items()
    ]
    return instructions, json.dumps(
        {"moment": moment.summary, "candidates": candidates, "airtime_share": airtime}, ensure_ascii=False
    )


async def _call(
    client: AsyncOpenAI, model: Candidate, fmt: dict[str, Any], instructions: str, payload: str
) -> tuple[float, dict[str, Any] | None, int, int]:
    params: dict[str, Any] = {
        "model": model.deployment,
        "instructions": instructions,
        "input": payload,
        "text": {"format": fmt},
        "store": False,  # synthetic data, but nothing needs keeping on the service
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "timeout": TIMEOUT_S,
    }
    if model.reasoning:
        params["reasoning"] = {"effort": model.reasoning}
    start = time.perf_counter()
    try:
        response = await client.responses.create(**params)
    except Exception as exc:  # timeouts and service errors count as failed calls, with their elapsed time
        print(f"{model.deployment}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return (time.perf_counter() - start) * 1000, None, 0, 0
    elapsed = (time.perf_counter() - start) * 1000
    usage = response.usage
    tokens = (usage.input_tokens, usage.output_tokens) if usage else (0, 0)
    try:
        parsed = json.loads(response.output_text)
    except ValueError:
        parsed = None
    return elapsed, parsed, *tokens


def _bid_ok(bid: Any, stake: dict[str, Any]) -> bool:
    ids = {e["id"] for e in stake["evidence"]}
    return (
        isinstance(bid, dict)
        and set(_BID_FIELDS) <= bid.keys()
        and 0 <= bid["urgency"] <= 1
        and 0 < len(bid["explanation"]) <= 280
        and 0 < len(bid["pitch_to_host"]) <= 140
        and len(bid["evidence_ids"]) > 0  # production requires a citation (#28)
        and set(bid["evidence_ids"]) <= ids
    )


def _bid_numbers_ok(bid: dict[str, Any], stake: dict[str, Any]) -> bool:
    """The production number check: only the cited evidence, with its sign and unit (#23, #28)."""
    lang = _manager(stake["manager"])["lang"]
    texts = [(bid["explanation"], lang), (bid["explanation_en"], "en"), (bid["pitch_to_host"], "en")]
    cited = bid["evidence_ids"]
    return not any(number_problems(text, stake["evidence"], cited, text_lang) for text, text_lang in texts)


def _host_ok(decision: Any, bids: dict[str, dict[str, Any]]) -> bool:
    """A usable host answer: one of the bidders, and a reason of 1 to 140 characters (#58)."""
    if not isinstance(decision, dict):
        return False
    chosen, reason = decision.get("chosen_manager"), decision.get("reason")
    return isinstance(chosen, str) and chosen in bids and isinstance(reason, str) and 0 < len(reason.strip()) <= 140


def _batch_bids(parsed: Any, stakes: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]] | None:
    """Each manager at stake's bid, exactly one each and no others; None if the batch is anything else (#58)."""
    batch = parsed.get("bids") if isinstance(parsed, dict) else None
    if not isinstance(batch, list) or not all(isinstance(b, dict) and isinstance(b.get("manager_id"), str)
                                              for b in batch):  # fmt: skip
        return None
    by_manager = {b["manager_id"]: b for b in batch}
    return by_manager if len(batch) == len(stakes) and by_manager.keys() == stakes.keys() else None


async def run_model(client: AsyncOpenAI, model: Candidate, moments: list[Moment]) -> list[Record]:
    records: list[Record] = []
    airtime: dict[str, float] = {m["id"]: 0.0 for m in league()["managers"]}

    def record(kind: str, moment: Moment, result: tuple[float, Any, int, int], valid: bool, nums: bool) -> None:
        latency, _, t_in, t_out = result
        records.append(Record(model.label, kind, latency, valid, nums, t_in, t_out, moment.moment_id))

    for n, moment in enumerate(moments, start=1):
        results = await asyncio.gather(
            *(_call(client, model, MANAGER_SCHEMA, *manager_request(moment, s)) for s in moment.stakes)
        )
        bids: dict[str, dict[str, Any]] = {}
        for stake, result in zip(moment.stakes, results, strict=True):
            bid = result[1]
            valid = bid is not None and _bid_ok(bid, stake)
            record("manager", moment, result, valid, valid and _bid_numbers_ok(bid, stake))  # type: ignore[arg-type]
            if valid:
                bids[stake["manager"]] = bid  # type: ignore[assignment]

        if bids:
            share = {k: v / n for k, v in airtime.items()}
            result = await _call(client, model, HOST_SCHEMA, *host_request(moment, bids, share))
            decision = result[1]
            valid = _host_ok(decision, bids)
            no_numbers = valid and not number_problems(decision["reason"], [], [])  # type: ignore[index]
            record("host", moment, result, valid, no_numbers)
            if valid:
                airtime[decision["chosen_manager"]] += 1  # type: ignore[index]

        result = await _call(client, model, BATCH_SCHEMA, *batch_request(moment))
        stakes = {s["manager"]: s for s in moment.stakes}
        by_manager = _batch_bids(result[1], stakes)
        valid = by_manager is not None and all(_bid_ok(b, stakes[m]) for m, b in by_manager.items())
        nums = valid and all(_bid_numbers_ok(b, stakes[m]) for m, b in (by_manager or {}).items())
        record("batch", moment, result, valid, nums)
    return records


def summarise(records: list[Record]) -> dict[str, dict[str, dict[str, float]]]:
    out: dict[str, dict[str, dict[str, float]]] = {}
    groups: dict[tuple[str, str], list[Record]] = {}
    for r in records:
        groups.setdefault((r.model, r.kind), []).append(r)
    # The moment's explanation is ready when its slowest concurrent manager call returns.
    slowest: dict[tuple[str, str], Record] = {}
    for r in records:
        if r.kind == "manager" and r.moment_id:
            key = (r.model, r.moment_id)
            if key not in slowest or r.latency_ms > slowest[key].latency_ms:
                slowest[key] = r
    for (model, _), r in slowest.items():
        groups.setdefault((model, "slowest_manager"), []).append(r)
    for (model, kind), rs in groups.items():
        latencies = [r.latency_ms for r in rs]
        out.setdefault(model, {})[kind] = {
            "n": len(rs),
            "p50_ms": round(percentile(latencies, 50)),
            "p95_ms": round(percentile(latencies, 95)),
            "valid": round(sum(r.valid for r in rs) / len(rs), 3),
            "numbers_ok": round(sum(r.numbers_ok for r in rs) / len(rs), 3),
            "tokens_in_avg": round(sum(r.tokens_in for r in rs) / len(rs)),
            "tokens_out_avg": round(sum(r.tokens_out for r in rs) / len(rs)),
        }
    return out


def _table(summary: dict[str, dict[str, dict[str, float]]]) -> str:
    rows = ["| Model | Measure | n | p50 ms | p95 ms | Valid | Numbers OK | Tokens in | Tokens out |", "|" + "---|" * 9]
    for model, kinds in summary.items():
        for kind in ("manager", "slowest_manager", "host", "batch"):
            if s := kinds.get(kind):
                rows.append(
                    f"| {model} | {kind} | {s['n']} | {s['p50_ms']} | {s['p95_ms']} | {s['valid']:.0%} "
                    f"| {s['numbers_ok']:.0%} | {s['tokens_in_avg']} | {s['tokens_out_avg']} |"
                )
    return "\n".join(rows)


async def _main(candidates: list[Candidate], out: str | None) -> None:
    client = foundry_client(os.environ["FOUNDRY_ENDPOINT"])
    records: list[Record] = []
    for model in candidates:
        await _call(client, model, MANAGER_SCHEMA, *manager_request(MOMENTS[0], MOMENTS[0].stakes[0]))  # warm-up
        records += await run_model(client, model, MOMENTS)
    summary = summarise(records)
    print(_table(summary))
    if out:
        with open(out, "w") as f:
            json.dump({"summary": summary, "records": [asdict(r) for r in records]}, f, indent=1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("candidates", nargs="+", help="deployment[:reasoning_effort]")
    parser.add_argument("--out", help="write the summary and every call to this JSON file")
    args = parser.parse_args()
    candidates = [Candidate(*c.split(":", 1)) if ":" in c else Candidate(c, None) for c in args.candidates]
    asyncio.run(_main(candidates, args.out))


if __name__ == "__main__":
    main()
