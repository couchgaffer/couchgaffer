import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from watchparty.matchtools import tools
from watchparty.matchtools.view import LOOKUPS_PER_SUMMARY, MAX_SERVED, RULEBOOK_LOOKUPS, MatchView
from watchparty.points import PointsEngine, load_league
from watchparty.points.engine import RULES
from watchparty.sim import DEMO_SEED, simulate

pytestmark = pytest.mark.anyio

MATCH = simulate(DEMO_SEED)
HALF_TIME = next(e["t"] for e in MATCH.events if e["type"] == "half_time")
PLAYERS = [p["id"] for side in ("home", "away") for p in MATCH.meta[side]["players"]]
MANAGERS = [f"mgr_{n}" for n in range(1, 6)]
EVIDENCE = "common.schema.json#/$defs/evidence"


def view_at(t: int, served: bool = False) -> MatchView:
    """The match as it stood at t: the engine fed every event up to t, and the stakes so far."""
    league = load_league()
    engine = PointsEngine(MATCH.meta, league)
    stakes = [s for e in MATCH.events if e["t"] <= t and (s := engine.feed(e))]
    return MatchView(MATCH.meta, league, engine, MATCH.events, stakes, t, recording=served)


FULL, HALF = view_at(MATCH.events[-1]["t"]), view_at(HALF_TIME)


def values(out: Any) -> dict[str, float]:
    return {e.id: e.value for e in out.evidence}


def test_standings_give_every_managers_points_and_rank() -> None:
    got = values(tools.standings(FULL))
    for row in FULL.engine.table():
        assert got[f"ev_table_{row['manager']}_points"] == row["points"]
        assert got[f"ev_table_{row['manager']}_rank"] == row["rank"]
    assert [m.name for m in tools.standings(FULL).managers] == [m["display_name"] for m in load_league()["managers"]]


def test_squad_points_add_up_to_what_the_manager_gained() -> None:
    for manager in load_league()["managers"]:
        out = tools.squad_points(FULL, manager["id"])
        got = values(out)
        gained = FULL.engine.manager_points[manager["id"]] - manager["points"]
        assert got[f"ev_squad_{manager['id']}_total"] == pytest.approx(gained)
        per_rule = [v for k, v in got.items() if not k.endswith("_total")]
        assert sum(per_rule) == pytest.approx(gained)
        assert out.captain.id == manager["captain"]


def test_whole_match_stats_match_the_points_engine() -> None:
    got = values(tools.match_stats(FULL))
    for e in FULL.engine.match_stats():
        assert got[e["id"].replace("ev_stats_", "ev_stats_0_94_")] == e["value"], e["id"]


def test_the_halves_add_up_and_a_window_never_sees_the_future() -> None:
    def shots(view: MatchView, start: int, end: int) -> dict[str, float]:
        out = tools.match_stats(view, start, end)
        return {side: values(out)[f"ev_stats_{start}_{out.to_minute}_{side}_shots"] for side in ("home", "away")}

    first, second, whole = shots(FULL, 0, 45), shots(FULL, 45, 130), shots(FULL, 0, 130)
    assert all(first[side] + second[side] == whole[side] for side in whole)
    assert tools.match_stats(HALF, 0, 130).to_minute == 46  # clamped to the view's own minute
    assert shots(HALF, 0, 130)["home"] < whole["home"]
    with pytest.raises(ValueError, match="from_minute"):
        tools.match_stats(FULL, 50, 40)


def test_key_moments_are_the_ones_that_mattered_to_that_manager_so_far() -> None:
    for manager in MANAGERS:
        out = tools.key_moments(HALF, manager)
        mattered = [s for moment in HALF.stakes for s in moment if s["manager"] == manager and s["above_threshold"]]
        assert [m.moment_id for m in out.moments] == [s["moment_id"] for s in mattered]
        assert {e.id for e in out.evidence} == {e["id"] for s in mattered for e in s["evidence"]}
        assert all(s["t"] <= HALF_TIME for s in mattered)


def test_player_summary_counts_their_events_and_points() -> None:
    keeper = values(tools.player_summary(FULL, "p_home_1"))
    saves = sum(e["type"] == "shot" and e["outcome"] == "saved" and e.get("opponent") == "p_home_1"
                for e in MATCH.events)  # fmt: skip
    assert keeper["ev_player_p_home_1_saves"] == saves
    assert keeper["ev_player_p_home_1_total"] == pytest.approx(sum(FULL.engine.player_points["p_home_1"].values()))
    scorer = next(e["player"] for e in MATCH.events if e["type"] == "shot" and e["outcome"] == "goal"
                  and e["t"] > HALF_TIME)  # fmt: skip
    goals = sum(e["type"] == "shot" and e["outcome"] == "goal" and e["player"] == scorer and e["t"] <= HALF_TIME
                for e in MATCH.events)  # fmt: skip
    assert values(tools.player_summary(HALF, scorer))[f"ev_player_{scorer}_goals"] == goals  # nothing from later


def test_scoring_rules_are_rules_v1() -> None:
    out = tools.scoring_rules(FULL)
    got = values(out)
    assert out.version == "v1"
    assert got["ev_rule_goal_points"] == RULES["rules"]["goal"]["points"]
    assert got["ev_rule_captain_multiplier"] == RULES["captain_multiplier"]
    assert got["ev_rule_save_tier_1_saves"] == RULES["rules"]["save_tier"]["tiers"][0][0]
    assert {r.id for r in out.rules} == set(RULES["rules"])


@pytest.mark.parametrize(
    ("call", "problem"),
    [
        (lambda v: tools.squad_points(v, "mgr_9"), "manager"),
        (lambda v: tools.key_moments(v, "mgr_0"), "manager"),
        (lambda v: tools.player_summary(v, "p_home_99"), "player"),
    ],
)
def test_unknown_ids_are_refused(call: Callable[[MatchView], Any], problem: str) -> None:
    with pytest.raises(ValueError, match=problem):
        call(FULL)


def every_output(view: MatchView) -> list[Any]:
    return [
        tools.standings(view), tools.scoring_rules(view), tools.match_stats(view), tools.match_stats(view, 0, 45),
        *(tools.squad_points(view, m) for m in MANAGERS), *(tools.key_moments(view, m) for m in MANAGERS),
        *(tools.player_summary(view, p) for p in PLAYERS),
    ]  # fmt: skip


@pytest.mark.parametrize("view", [HALF, FULL], ids=["half_time", "full_time"])
def test_every_evidence_item_meets_the_contract_and_ids_are_unique(
    view: MatchView, contract_errors: Callable[[str, Any], list[str]]
) -> None:
    for out in every_output(view):
        ids = [e.id for e in out.evidence]
        assert len(ids) == len(set(ids)), type(out).__name__
        for e in out.evidence:
            assert contract_errors(EVIDENCE, e.model_dump(exclude_none=True)) == [], e


def test_a_snapshot_records_what_it_served_and_a_live_view_does_not() -> None:
    snapshot = view_at(HALF_TIME, served=True)
    out = tools.standings(snapshot)
    served = {k: v["value"] for k, v in snapshot.served_to("").items()}
    assert {e.id: e.value for e in out.evidence}.items() <= served.items()
    tools.standings(FULL)
    assert FULL.served == {}


def test_each_agent_may_cite_only_what_it_was_served_itself() -> None:
    snapshot = view_at(HALF_TIME, served=True)
    mgr_1, mgr_2 = snapshot.for_caller("mgr_1"), snapshot.for_caller("mgr_2")
    theirs = values(tools.squad_points(mgr_2, "mgr_2"))  # mgr_2's agent fetches first (#54)
    assert theirs.keys() <= snapshot.served_to("mgr_2").keys()
    assert not theirs.keys() & snapshot.served_to("mgr_1").keys()
    table = values(tools.standings(mgr_1))  # every manager's points and rank: open to every agent
    assert "ev_table_mgr_2_points" in table
    assert table.keys() <= snapshot.served_to("mgr_1").keys()


def test_another_managers_squad_and_moments_are_shown_but_never_citable() -> None:
    snapshot = view_at(HALF_TIME, served=True)
    mgr_1 = snapshot.for_caller("mgr_1")
    seen = values(tools.squad_points(mgr_1, "mgr_2")) | values(tools.key_moments(mgr_1, "mgr_2"))
    assert seen
    assert not seen.keys() & snapshot.served_to("mgr_1").keys()
    mine = values(tools.squad_points(mgr_1, "mgr_1")) | values(tools.key_moments(mgr_1, "mgr_1"))
    assert mine.keys() <= snapshot.served_to("mgr_1").keys()


def test_what_a_snapshot_records_is_capped() -> None:
    snapshot = view_at(HALF_TIME, served=True)
    for start in range(0, 40):
        for end in range(start + 1, 46):
            tools.match_stats(snapshot, start, end)
    assert len(snapshot.served_to("")) == MAX_SERVED


class FakeRulebook:
    """Stands in for the Foundry IQ knowledge base."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def explain(self, question: str) -> list[dict[str, str]]:
        self.asked.append(question)
        content = "A save earns 1 IP plus 4 IP per 1 xG. Why: stopping a big chance matters more."
        return [{"id": "rule-save-points", "title": "Save", "content": content}]


async def test_rule_explanations_come_from_the_rulebook_and_are_recorded_per_manager() -> None:
    rulebook, snapshot = FakeRulebook(), view_at(HALF_TIME, served=True)
    out = await tools.rule_explanation(snapshot, rulebook, "Why was that save worth 8 IP?", caller="mgr_2")
    assert rulebook.asked == ["Why was that save worth 8 IP?"]
    assert [e.id for e in out.entries] == ["rule-save-points"]
    assert out.entries[0].reason == "Stopping a big chance matters more."  # the why, without numbers to quote
    given = {"rule-save-points": {"title": "Save", "reason": "Stopping a big chance matters more."}}
    assert snapshot.rulebook == {"mgr_2": given}


async def test_the_same_question_is_asked_once_per_match() -> None:
    rulebook, snapshot = FakeRulebook(), view_at(HALF_TIME, served=True)
    await tools.rule_explanation(snapshot, rulebook, "Why do saves count?", caller="mgr_1")
    await tools.rule_explanation(snapshot, rulebook, "why do  saves COUNT?", caller="mgr_2")
    assert len(rulebook.asked) == 1
    assert set(snapshot.rulebook) == {"mgr_1", "mgr_2"}  # each manager's agent was given the entries


async def test_each_summary_gets_its_own_lookups_so_the_last_ones_are_not_starved() -> None:
    rulebook, half = FakeRulebook(), view_at(HALF_TIME, served=True)
    for n in range(LOOKUPS_PER_SUMMARY):
        await tools.rule_explanation(half, rulebook, f"Question {n}?", caller="mgr_1")
    with pytest.raises(ValueError, match="leave rulebook_ids"):  # and it says what to do instead
        await tools.rule_explanation(half, rulebook, "One more?", caller="mgr_1")
    await tools.rule_explanation(half, rulebook, "Question 0?", caller="mgr_1")  # an answer it has is free
    await tools.rule_explanation(half, rulebook, "Another?", caller="mgr_5")  # the last summary still asks
    full = half.frozen(half.t)  # the full-time summary is a new summary, with its own
    await tools.rule_explanation(full, rulebook, "Full time?", caller="mgr_1")
    assert len(rulebook.asked) == LOOKUPS_PER_SUMMARY + 2


async def test_a_match_has_a_budget_of_rulebook_lookups() -> None:
    rulebook, snapshot = FakeRulebook(), view_at(HALF_TIME, served=True)
    assert RULEBOOK_LOOKUPS == 10 * LOOKUPS_PER_SUMMARY  # ten summaries a match
    for n in range(RULEBOOK_LOOKUPS):
        await tools.rule_explanation(snapshot, rulebook, f"Question {n}?", caller=f"c{n // LOOKUPS_PER_SUMMARY}")
    with pytest.raises(ValueError, match="used up"):
        await tools.rule_explanation(snapshot, rulebook, "One more?", caller="someone_else")
    assert len(rulebook.asked) == RULEBOOK_LOOKUPS  # no Foundry IQ request past the budget


def passages(*items: tuple[int, str]) -> dict[str, Any]:
    text = json.dumps(
        [{"ref_id": ref, "title": title, "content": f"{title}. Why: it matters."} for ref, title in items]
    )
    return {"response": [{"content": [{"type": "text", "text": text}]}],
            "references": [{"id": str(ref), "docKey": f"rule-{ref % 2}"} for ref, _ in items]}  # fmt: skip


async def foundry_iq(handler: Any, timeout_s: float = 8) -> list[dict[str, str]]:
    from watchparty.matchtools.rulebook import FoundryIQ

    async def token() -> str:
        return "t"

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        return await FoundryIQ("https://search.test", token, timeout_s=timeout_s, http=http).explain("Why?")


@pytest.mark.parametrize(
    ("response", "entries"),
    [
        (httpx.Response(429, json={"error": "busy"}), []),  # throttled: no entries, no exception
        (httpx.Response(200, json={"response": [{"content": [{"type": "text", "text": "not json"}]}]}), []),
        (httpx.Response(200, json={"unexpected": True}), []),
        (httpx.Response(200, json=passages((0, "Save"), (1, "Goal"), (2, "Save again"))), ["rule-0", "rule-1"]),
    ],
)
async def test_a_failed_or_odd_lookup_gives_no_entries_and_duplicates_collapse(
    response: httpx.Response, entries: list[str]
) -> None:
    assert [e["id"] for e in await foundry_iq(lambda _: response)] == entries


async def test_a_slow_lookup_gives_up_on_time() -> None:
    async def slow(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return httpx.Response(200, json={})

    start = time.perf_counter()
    assert await foundry_iq(slow, timeout_s=0.1) == []
    assert time.perf_counter() - start < 1


async def test_two_summaries_asking_the_same_question_at_once_spend_one_lookup() -> None:
    class Slow(FakeRulebook):
        async def explain(self, question: str) -> list[dict[str, str]]:
            await asyncio.sleep(0.05)  # both askers are waiting before the answer comes back (#42)
            return await super().explain(question)

    rulebook, snapshot = Slow(), view_at(HALF_TIME, served=True)
    first, second = await asyncio.gather(
        tools.rule_explanation(snapshot, rulebook, "Why do saves count?", caller="mgr_1"),
        tools.rule_explanation(snapshot, rulebook, "why do saves  count?", caller="mgr_2"),
    )
    assert (len(rulebook.asked), snapshot.lookups.used) == (1, 1)
    assert first.entries == second.entries
    assert set(snapshot.rulebook) == {"mgr_1", "mgr_2"}


async def test_a_lookup_that_waits_for_a_slot_still_gives_up_on_time() -> None:
    from watchparty.matchtools.rulebook import FoundryIQ

    async def slow(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return httpx.Response(200, json={})

    async def token() -> str:
        return "t"

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as http:
        client = FoundryIQ("https://search.test", token, timeout_s=0.2, http=http)
        start = time.perf_counter()
        # Two slots: the third lookup waits for one. Its 0.2 s counts from when it was asked, not from its slot.
        results = await asyncio.gather(*(client.explain(f"Why {n}?") for n in range(3)))
    assert results == [[], [], []]
    assert time.perf_counter() - start < 0.35  # not 0.4: the third did not wait 0.2 s and then spend 0.2 s more


def test_the_loader_fails_unless_the_knowledge_base_answers() -> None:
    from tools.foundry_iq import verify

    verify([{"id": "rule-save-points", "title": "Save", "content": "..."}])
    with pytest.raises(SystemExit, match="rule-save-points"):
        verify([])  # a 401, 403, 404 or timeout: explain() gives no entries (#42)


@pytest.mark.parametrize("error", ["CredentialUnavailableError", "ClientAuthenticationError"])
async def test_a_sign_in_failure_gives_no_entries_like_any_other_failure(
    error: str, caplog: pytest.LogCaptureFixture
) -> None:
    import azure.core.exceptions
    import azure.identity

    from watchparty.matchtools.rulebook import FoundryIQ

    failure = getattr(azure.identity, error, None) or getattr(azure.core.exceptions, error)

    async def token() -> str:  # the managed identity cannot get a Search token (#46)
        raise failure("no token")

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={}))) as http:
        with caplog.at_level("WARNING", logger="watchparty.matchtools.rulebook"):
            assert await FoundryIQ("https://search.test", token, http=http).explain("Why?") == []
    assert any(error in r.getMessage() for r in caplog.records)
    assert not any("no token" in r.getMessage() for r in caplog.records)  # the type only, no credential detail


def test_the_foundry_iq_response_is_read_as_rulebook_entries() -> None:
    from watchparty.matchtools.rulebook import parse

    # The shape the knowledge base returned on 24 Oct: passages by ref_id, and references mapping each
    # ref_id to our document key.
    passages = [{"ref_id": 0, "title": "Save, worth more against a better chance", "content": "A save earns..."},
                {"ref_id": 1, "title": "Impact points (IP)", "content": "The league's own points."},
                {"ref_id": 2, "title": "Unreferenced", "content": "No document key for this one."}]  # fmt: skip
    result = {
        "response": [{"content": [{"type": "text", "text": json.dumps(passages)}]}],
        "references": [{"type": "searchIndex", "id": "0", "docKey": "rule-save-points"},
                       {"type": "searchIndex", "id": "1", "docKey": "glossary-ip"}],
    }  # fmt: skip
    assert [(e["id"], e["title"]) for e in parse(result)] == [
        ("rule-save-points", "Save, worth more against a better chance"),
        ("glossary-ip", "Impact points (IP)"),
    ]
    assert parse({"response": []}) == []


async def test_a_lookup_that_gave_nothing_asks_for_no_citation() -> None:
    from watchparty.agents.summary import summary_problems

    class Empty(FakeRulebook):
        async def explain(self, question: str) -> list[dict[str, str]]:
            return []  # what a sign-in failure, a 429 or a timeout gives (#46)

    snapshot = view_at(HALF_TIME, served=True)
    out = await tools.rule_explanation(snapshot, Empty(), "Why?", caller="mgr_2")
    assert out.entries == []
    mine = snapshot.rulebook.get("mgr_2", {})
    points = next(e for e in tools.standings(snapshot).evidence if e.id == "ev_table_mgr_2_points")
    manager = next(m for m in snapshot.league["managers"] if m["id"] == "mgr_2") | {"style": "analyst"}
    answer = {"text": f"You are on {points.value:g} IP.", "text_en": "", "evidence_ids": [points.id],
              "rulebook_ids": []}  # fmt: skip
    names = [m["display_name"] for m in snapshot.league["managers"]]
    served = snapshot.served_to("")
    assert summary_problems(answer, manager, served, names, mine) == []  # it completes from the match tools
