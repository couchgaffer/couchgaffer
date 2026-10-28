"""Play the demo match with the real agents, and print every group-screen decision and every summary.

Shows how many moments reach the host (#11), what it picks against the scorer, its visible reasons, and
how often it beats its deadline; then each manager's half-time and full-time summary (#12), written by the
agent through the MCP match tools or by the code fallback. Laptop latency; the gate is measured in-region.
From backend/:

  FOUNDRY_ENDPOINT=https://<name>.cognitiveservices.azure.com/ uv run python -m tools.eval_session
"""

import asyncio
import time
from collections import Counter
from typing import Any

import httpx

from watchparty.app.main import agents_from_env, create_app


class Timed:
    """Wraps the host agent to record each answer, or its error, and how long it took."""

    def __init__(self, agent: Any) -> None:
        self.agent = agent
        self.calls: list[tuple[str, str, float]] = []

    async def decide(self, moment: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
        start = time.perf_counter()
        try:
            answer: dict[str, Any] = await self.agent.decide(moment, candidates)
            outcome = f"{answer.get('status')} ({answer.get('because')})"
            self.calls.append((moment["moment_id"], outcome, (time.perf_counter() - start) * 1000))
            return answer
        except Exception as exc:
            self.calls.append((moment["moment_id"], type(exc).__name__, (time.perf_counter() - start) * 1000))
            raise


async def no_sleep(_: float) -> None:
    await asyncio.sleep(0)


async def main() -> None:
    agents = agents_from_env()
    host = Timed(agents["host_agent"])
    app = create_app(session_kwargs={**agents, "host_agent": host, "sleep": no_sleep})
    async with app.router.lifespan_context(app):  # the MCP server the summaries call
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://eval") as http:
            made = (await http.post("/api/sessions", json={"seed": "demo", "manager": "mgr_1"})).json()
        session = app.state.store.get(made["session_id"])
        start = time.perf_counter()
        await session.run()
        print(f"match played in {time.perf_counter() - start:.0f} s, summaries included\n")
    decisions = [d for d in session.log if "chosen_manager" in d]
    names = {m["id"]: m["display_name"] for m in session.league["managers"]}
    for d in decisions:
        scores = ", ".join(f"{names[r['manager']]} {r['score']}" for r in d["scores"])
        print(f"{d['moment_id']} {d['source']:8} {names[d['chosen_manager']]:6} [{scores}] {d.get('reason', '')}")
    print(f"\nsources: {dict(Counter(d['source'] for d in decisions))} of {len(decisions)} decisions")
    print(f"airtime: {dict(Counter(names[d['chosen_manager']] for d in decisions))}")
    for moment_id, outcome, ms in host.calls:
        print(f"host {moment_id}: {outcome} in {ms:.0f} ms")
    summaries = [c for c in session.log if c.get("kind") == "summary"]
    print(f"\nsummaries: {dict(Counter(c['source'] for c in summaries))} of {len(summaries)}")
    for c in summaries:
        p = c["payload"]
        print(f"- {p['headline']:9} {names[p['manager']]:6} {p['style']:7} {c['source']:8} {p['body']}")


if __name__ == "__main__":
    asyncio.run(main())
