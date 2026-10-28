"""Evaluate the real manager agent (#10) on the 30 latency fixtures, in both styles.

Reports how often answers pass the checks first time, after the one retry, or fall back, with latency,
and prints the same moments in analyst and casual style side by side. From backend/:

  FOUNDRY_ENDPOINT=https://<name>.cognitiveservices.azure.com/ uv run python -m tools.eval_manager
  ... -m tools.eval_manager --lang es   # every manager in Spanish (#16), samples with their English copies
"""

import asyncio
import os
import sys
import time
from collections import Counter
from typing import Any

from tools.latency_fixtures import MOMENTS, league, meta
from tools.latency_test import percentile
from watchparty.agents.foundry import foundry_client
from watchparty.agents.manager import ManagerAgent, foundry_runner


def contexts() -> tuple[list[str], dict[str, str]]:
    players = {p["id"]: p["name"] for side in ("home", "away") for p in meta()[side]["players"]}
    names = [*players.values(), *(meta()[s]["name"] for s in ("home", "away"))]
    names += [m["display_name"] for m in league()["managers"]]
    return names, players


async def run(
    agent: ManagerAgent, style: str, lang: str | None = None
) -> tuple[Counter[str], list[float], list[tuple[str, str]]]:
    names, players = contexts()
    outcomes: Counter[str] = Counter()
    latencies, samples = [], []
    for moment in MOMENTS:
        for stake in moment.stakes:
            manager = next(m for m in league()["managers"] if m["id"] == stake["manager"]) | {"style": style}
            manager |= {"lang": lang} if lang else {}
            squad = [players[p] for p in manager["squad"]]
            context = {"moment": moment.summary, "manager": manager, "names": names, "squad": squad,
                       "captain": players[manager["captain"]]}  # fmt: skip
            start = time.perf_counter()
            try:
                bid = await asyncio.wait_for(agent.bid(stake, context), 10)
                outcomes[bid["status"]] += 1
                english = f"\n      en: {bid['explanation_en']}" if bid["explanation_en"] else ""
                samples.append((moment.moment_id + " " + manager["display_name"], bid["explanation"] + english))
            except Exception as exc:  # failed twice or timed out: the workflow would fall back
                outcomes[f"fallback ({type(exc).__name__})"] += 1
            latencies.append((time.perf_counter() - start) * 1000)
    return outcomes, latencies, samples


async def main() -> None:
    model = os.environ.get("MANAGER_MODEL", "gpt-5.4-mini")
    agent = ManagerAgent(foundry_runner(foundry_client(os.environ["FOUNDRY_ENDPOINT"]), model), model)
    results: dict[str, Any] = {}
    lang = sys.argv[sys.argv.index("--lang") + 1] if "--lang" in sys.argv else None
    for style in ("analyst", "casual"):
        results[style] = await run(agent, style, lang)
        outcomes, latencies, _ = results[style]
        total = sum(outcomes.values())
        print(f"{style}: {dict(outcomes)} of {total} | p50 {percentile(latencies, 50):.0f} ms, "
              f"p95 {percentile(latencies, 95):.0f} ms (laptop; includes any retry)")  # fmt: skip
    print("\nSame moments, both styles:")
    casual = dict(results["casual"][2])
    pairs = [(who, text, casual[who]) for who, text in results["analyst"][2] if who in casual]
    for who, analyst, plain in pairs[:6]:
        print(f"- {who}\n    analyst: {analyst}\n    casual:  {plain}")


if __name__ == "__main__":
    asyncio.run(main())
