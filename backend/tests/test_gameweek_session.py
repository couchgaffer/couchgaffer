"""A gameweek session (#151, slice 5 of #94): three fixtures on one clock, one table, one ledger.

Numbered cases are #94's revised test plan; the rest are this ticket's plan (#151).
"""

import asyncio
import json
import logging
import time
from collections import Counter
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import drain
from tests.test_session import Arrivals as SessionArrivals
from tests.test_session import Host, Manager, SlowOn, drained_cues, statuses
from watchparty.agents.manager import ManagerAgent
from watchparty.agents.scorer import Ledger
from watchparty.agents.summary import PHASES
from watchparty.app.main import create_app
from watchparty.app.session import Session, SessionStore
from watchparty.app.workflow import MomentInput, build
from watchparty.matchtools import tools as match_tools
from watchparty.points import PointsEngine, Standings
from watchparty.recap.bundle import check
from watchparty.recap.dispatch import bundle_for, publish_recaps
from watchparty.sim import messages
from watchparty.sim.gameweek import built

pytestmark = pytest.mark.anyio
SEED = 7
SCREENS = ["group"] + [f"phone:mgr_{n}" for n in range(1, 6)]


async def settle(_: float) -> None:
    """Match time passes at once, but every task that can run does so first, as at real speed."""
    for _ in range(50):
        await asyncio.sleep(0)


def gameweek(manager: Manager | None = None, host: Host | None = None, seed: int = SEED, **kw: Any) -> Session:
    return Session(seed=seed, manager="mgr_1", style="casual", lang="en", speed=30, sleep=settle,
                   manager_agent=manager or Manager(), host_agent=host or Host(), gameweek=True, **kw)  # fmt: skip


async def run(s: Session) -> dict[str, list[dict[str, Any]]]:
    queues = {screen: s.subscribe(screen) for screen in SCREENS}
    await s.run()
    return {screen: drain(q) for screen, q in queues.items()}


def of(messages: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [m["data"] for m in messages if m["type"] == kind]


def moment_in(s: Session, fixture: str, n: int = 0) -> str:
    """The id of the fixture's n-th key moment."""
    return [str(mid) for mid, e in s.climaxes.items() if e["fixture_id"] == fixture][n]


def stakes_for(s: Session, deltas: dict[str, float], moment_id: str) -> list[dict[str, Any]]:
    """Real stakes for one gameweek moment, played on engines of their own, with chosen swings."""
    standings = Standings(s.league)
    engines = {f: PointsEngine(meta, s.league, standings=standings, fixture_id=f) for f, meta in s.fixtures.items()}
    real = next(st for e in s.match.events if (st := engines[e["fixture_id"]].feed(e)) and
                st[0]["moment_id"] == moment_id)  # fmt: skip
    out = []
    for stake in real:
        delta = deltas.get(stake["manager"], 0)
        evidence = [e | {"value": delta} if e["id"].endswith("_delta") else e for e in stake["evidence"]]
        out.append(stake | {"delta_points": delta, "above_threshold": stake["manager"] in deltas, "evidence": evidence})
    return out


class Arrivals(SessionArrivals):
    """Gameweek moments reaching the server, each with its real stakes."""

    def arrive(self, mid: str, deltas: dict[str, float]) -> None:
        self.deadlines[mid] = time.monotonic() + self.s.decision_budget_s
        moment = self.s._arrive(stakes_for(self.s, deltas, mid), self.deadlines[mid])
        self.tasks[mid] = asyncio.create_task(self._timed(moment))


# --- the whole gameweek ---------------------------------------------------------------------------


async def test_every_message_of_a_gameweek_is_valid_and_names_its_fixture(
    contract_errors: Callable[[str, Any], list[str]],
) -> None:
    # #94 case 6, at the session
    got = await run(gameweek())
    seqs: list[int] = []
    for screen, msgs in got.items():
        assert [m["type"] for m in msgs[:2]] == ["gameweek", "league"], screen
        assert "meta" not in {m["type"] for m in msgs}
        assert all(f["fixture_id"] for f in msgs[0]["data"]["fixtures"])
        for m in msgs:
            assert contract_errors("ws-message", m) == [], (screen, m)
            if m["type"] in ("event", "frame", "decision"):
                assert m["data"]["fixture_id"] in ("fx_1", "fx_2", "fx_3"), m
            if m["type"] == "cue":  # a moment's cue names its fixture; one about the whole gameweek, none
                assert ("fixture_id" in m["data"]) == ("moment_id" in m["data"]), m
            if m["type"] == "event" and screen == "group":
                seqs.append(m["data"]["seq"])
    assert len(seqs) == len(set(seqs))
    decisions = of(got["group"], "decision")
    assert decisions
    assert len({d["moment_id"] for d in decisions}) == len(decisions)
    assert {d["fixture_id"] for d in decisions} == {"fx_1", "fx_2", "fx_3"}


async def test_stakes_and_bids_name_their_fixture() -> None:
    s = gameweek()
    mid = moment_in(s, "fx_2")
    result = await s.decide(stakes_for(s, {"mgr_1": 6, "mgr_2": 6}, mid))
    assert result is not None
    assert result["fixture_id"] == "fx_2"
    assert {st["fixture_id"] for st in stakes_for(s, {}, mid)} == {"fx_2"}
    bids: list[dict[str, Any]] = []
    real = s._on_bid

    def on_bid(bid: dict[str, Any]) -> None:
        bids.append(bid)
        real(bid)

    s._on_bid = on_bid  # type: ignore[method-assign]
    s.workflow = s._workflow()  # built with the bid callback it is given
    s.idle = [s.workflow]
    await s.decide(stakes_for(s, {"mgr_3": 6}, moment_in(s, "fx_3")))
    assert [(b["manager"], b["fixture_id"]) for b in bids] == [("mgr_3", "fx_3")]


async def test_the_gameweek_blows_half_time_and_full_time_once_at_the_last_fixtures_whistle() -> None:
    # #94 case 4
    s = gameweek()
    got = await run(s)
    events = of(got["group"], "event")
    first = {f: min(e["t"] for e in events if e["fixture_id"] == f) for f in s.fixtures}
    assert set(first.values()) == {0}
    tables = [c for c in of(got["group"], "cue") if c["kind"] == "table" and "moment_id" not in c]
    assert [c["payload"]["headline"] for c in tables] == ["Half time", "Full time"]
    for phase, table in zip(PHASES, tables, strict=True):
        whistles = [e["t"] for e in events if e["type"] == phase]
        assert len(whistles) == 3
        assert max(whistles) - min(whistles) <= 30_000
        assert table["show_at"] == max(whistles) == s.views[phase].t
        assert table["payload"]["table"] == s.views[phase].engine.table()
    summaries = [c for screen in SCREENS[1:] for c in of(got[screen], "cue") if c["kind"] == "summary"]
    assert Counter(c["payload"]["headline"] for c in summaries) == {"Half time": 5, "Full time": 5}


async def test_code_summaries_in_a_gameweek_read_the_whole_gameweek() -> None:
    # With no summary agent; the agent's gameweek summaries are #152's (tests/test_gameweek_summaries.py)
    s = gameweek()
    got = await run(s)
    table = {r["manager"]: r for r in s.views["full_time"].engine.table()}
    mult = {(m["id"], p): 2 if p == m["captain"] else 1 for m in s.league["managers"] for p in m["squad"]}
    for n in range(1, 6):
        [summary] = [c for c in of(got[f"phone:mgr_{n}"], "cue") if c["kind"] == "summary" and
                     c["payload"]["headline"] == "Full time"]  # fmt: skip
        cited = {e["id"]: e["value"] for e in summary["payload"]["evidence"]}
        assert cited[f"ev_table_mgr_{n}_points"] == table[f"mgr_{n}"]["points"]
        # the squad's points from all three fixtures, as each fixture's engine scored them
        mine = s.managers[f"mgr_{n}"]
        gained = sum(sum(e.player_points[p].values()) * mult[(mine["id"], p)]
                     for e in s.engines.values() for p in mine["squad"] if p in e.side_of)  # fmt: skip
        assert len({f for f, e in s.engines.items() for p in mine["squad"] if p in e.side_of}) >= 2
        view = s.views["full_time"].for_caller(mine["id"])
        totals = {e.id: e.value for e in match_tools.squad_points(view, mine["id"]).evidence}
        assert totals[f"ev_squad_mgr_{n}_total"] == pytest.approx(gained)


async def test_the_session_knows_every_club_and_player_of_the_gameweek() -> None:
    s = gameweek()
    players = {p["name"] for meta in s.fixtures.values() for side in ("home", "away") for p in meta[side]["players"]}
    clubs = {meta[side]["name"] for meta in s.fixtures.values() for side in ("home", "away")}
    assert (len(players), len(clubs)) == (66, 6)
    assert set(s.allowed_names) == players | clubs | {m["display_name"] for m in s.managers.values()}
    assert s.league["managers"][1]["squad"][1].startswith("p_")  # the gameweek league, not the single match's
    assert any(p.startswith("p_kes_") or p.startswith("p_rav_") for m in s.league["managers"] for p in m["squad"])


async def test_the_group_stat_card_names_both_clubs() -> None:
    s = gameweek()
    got = await run(s)
    stats = [c for c in of(got["group"], "cue") if c["kind"] == "stat"]
    assert stats
    for card in stats:
        meta = s.fixtures[card["fixture_id"]]
        names = [(meta["home"][k], meta["away"][k]) for k in ("name", "short_name")]
        assert any(card["payload"]["headline"].startswith(f"{h} v {a}: ") for h, a in names), card


# --- moments from several fixtures --------------------------------------------------------------------


async def test_two_moments_from_two_fixtures_a_second_apart_are_both_decided_on_time() -> None:
    # #94 case 12, scaled tenfold
    s = gameweek(manager=Manager(delay=0.38), manager_deadline_s=0.4, decision_budget_s=0.45)
    recorded: list[str] = []
    real = s.ledger.record

    def record(chosen: str, candidates: list[str], *, close: bool) -> None:
        recorded.append(chosen)
        real(chosen, candidates, close=close)

    s.ledger.record = record  # type: ignore[method-assign]
    a, b = moment_in(s, "fx_1"), moment_in(s, "fx_3")
    moments = Arrivals(s)
    moments.arrive(a, {"mgr_1": 6})
    await asyncio.sleep(0.1)
    moments.arrive(b, {"mgr_2": 6})
    got = await moments.results()
    assert {mid: statuses(result) for mid, result in got.items()} == {a: {"mgr_1": "ok"}, b: {"mgr_2": "ok"}}
    assert all(late < 0.03 for late in moments.late.values()), moments.late
    assert recorded == ["mgr_1", "mgr_2"]  # in arrival order
    assert [got[a]["fixture_id"], got[b]["fixture_id"]] == ["fx_1", "fx_3"]  # type: ignore[index]


async def test_a_close_call_in_one_fixture_beside_a_clear_winner_in_another() -> None:
    # #94 case 13
    s = gameweek(manager=Manager(delay=0.05), host=Host())
    queues = {screen: s.subscribe(screen) for screen in SCREENS}
    close, clear = moment_in(s, "fx_2"), moment_in(s, "fx_3")
    moments = Arrivals(s)
    moments.arrive(close, {"mgr_2": 6, "mgr_3": 6})
    moments.arrive(clear, {"mgr_4": 8, "mgr_5": 2})
    got = await moments.results()
    assert (got[close]["source"], got[clear]["source"]) == ("host", "scorer")  # type: ignore[index]
    winner = got[close]["chosen_manager"]  # type: ignore[index]
    loser = next(m for m in ("mgr_2", "mgr_3") if m != winner)
    cues = drained_cues(queues)
    clashes = [c for c in cues["group"] if c["kind"] == "clash"]
    assert [(c["moment_id"], c["fixture_id"]) for c in clashes] == [(close, "fx_2")]
    notes = {screen: [c for c in cs if c["kind"] == "lost_screen"] for screen, cs in cues.items()}
    assert {screen for screen, ns in notes.items() if ns} == {f"phone:{loser}"}
    assert notes[f"phone:{loser}"][0]["fixture_id"] == "fx_2"
    cards = [c for c in cues["group"] if c["kind"] == "explanation"]
    assert [(c["moment_id"], c["fixture_id"], c["payload"]["manager"]) for c in cards] == [
        (close, "fx_2", winner), (clear, "fx_3", "mgr_4")]  # fmt: skip


async def test_a_turn_from_another_fixture_with_too_little_time_goes_to_the_scorer() -> None:
    # #94 case 14
    host = Host(delay=0.24)
    s = gameweek(manager=SlowOn({moment_in(gameweek(), "fx_1"): 0.2}), host=host, manager_deadline_s=0.4,
                 host_deadline_s=0.25, decision_budget_s=0.45)  # fmt: skip
    a, b = moment_in(s, "fx_1"), moment_in(s, "fx_2")
    moments = Arrivals(s)
    moments.arrive(a, {"mgr_1": 6, "mgr_2": 6})
    await asyncio.sleep(0.01)
    moments.arrive(b, {"mgr_3": 6, "mgr_4": 6})
    got = await moments.results()
    assert host.calls == 1
    assert (got[b]["source"], got[b]["chosen_manager"]) == ("fallback", got[b]["scores"][0]["manager"])  # type: ignore[index]
    assert all(late < 0.03 for late in moments.late.values()), moments.late


async def test_a_friend_overruled_in_one_fixture_gets_the_boost_in_another() -> None:
    # #94 case 15: one ledger across the gameweek
    s = gameweek(host=Host())  # overrules: the second-ranked bid wins the close call
    a, b = moment_in(s, "fx_1"), moment_in(s, "fx_2")
    first = await s.decide(stakes_for(s, {"mgr_2": 6, "mgr_3": 6}, a))
    assert first is not None
    assert first["source"] == "host"
    loser = next(m for m in ("mgr_2", "mgr_3") if m != first["chosen_manager"])
    second = await s.decide(stakes_for(s, {loser: 4, "mgr_5": 4}, b))
    assert second is not None
    assert second["fixture_id"] == "fx_2"
    rows = {r["manager"]: r for r in second["scores"]}
    assert rows[loser]["overruled_count"] == 1
    assert rows["mgr_5"]["overruled_count"] == 0
    winner = first["chosen_manager"]
    third = await s.decide(stakes_for(s, {winner: 4, "mgr_5": 4}, moment_in(s, "fx_3")))
    assert third is not None
    shares = {r["manager"]: r["airtime_share"] for r in third["scores"]}
    assert shares[winner] == 0.5  # one of the two decisions so far, from two fixtures


async def test_two_moments_from_two_fixtures_never_see_each_others_bids_or_evidence() -> None:
    # #94 case 16
    host = Host()
    s = gameweek(manager=Manager(delay=0.05), host=host)
    queues = {screen: s.subscribe(screen) for screen in SCREENS}
    a, b = moment_in(s, "fx_1"), moment_in(s, "fx_3")
    moments = Arrivals(s)
    moments.arrive(a, {"mgr_1": 6, "mgr_2": 6})
    moments.arrive(b, {"mgr_3": 4, "mgr_4": 4})
    assert set(s.moments) == {a, b}
    got = await moments.results()
    assert {mid: {r["manager"] for r in d["scores"]} for mid, d in got.items() if d} == {
        a: {"mgr_1", "mgr_2"}, b: {"mgr_3", "mgr_4"}}  # fmt: skip
    assert [{c["manager"] for c in seen} for seen in host.seen] == [{"mgr_1", "mgr_2"}, {"mgr_3", "mgr_4"}]
    phones = drained_cues(queues)
    for screen, mid, fixture in (("phone:mgr_1", a, "fx_1"), ("phone:mgr_3", b, "fx_3")):
        [explanation] = [c for c in phones[screen] if c["kind"] == "explanation"]
        assert (explanation["moment_id"], explanation["fixture_id"]) == (mid, fixture)
        assert all(e["id"].startswith(f"ev_{mid}_") for e in explanation["payload"]["evidence"])
    assert s.moments == {}


async def test_a_simulated_failure_in_a_gameweek_hits_the_next_moment_with_agents_only() -> None:
    # #94 case 17
    s = gameweek()
    arm = s.simulate_failure()
    got = await run(s)
    decisions = of(got["group"], "decision")
    assert decisions[0]["knocked_out"] == {"arm": arm}
    assert set(statuses(decisions[0]).values()) == {"fallback"}
    assert all("knocked_out" not in d for d in decisions[1:])
    assert "ok" in {status for d in decisions[1:] for status in statuses(d).values()}
    assert s.chaos is None


async def test_a_manager_agent_is_told_every_score_and_where_its_players_play() -> None:
    contexts: list[dict[str, Any]] = []
    asked: list[str] = []

    class Seeing(Manager):
        async def bid(self, stake: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
            contexts.append(context)
            asked.append(stake["moment_id"])
            return await super().bid(stake, context)

    s = gameweek(manager=Seeing())
    await run(s)
    assert contexts
    for context in contexts:
        squad = context["manager"]["squad"]
        rows = context["gameweek"]
        assert len(rows) == 3
        assert sum(r["this_moment"] for r in rows) == 1
        clubs = [(meta["home"]["name"], meta["away"]["name"]) for meta in s.fixtures.values()]
        assert all(
            r["match"].startswith(home) and f", {away} " in r["match"]
            for r, (home, away) in zip(rows, clubs, strict=False)
        )
        assert sorted(n for r in rows for n in r["your_players"]) == sorted(s.names[p] for p in squad)
    # each score is the goals that fixture had scored by the moment's climax
    for context, mid in zip(contexts, asked, strict=True):
        at = s.climaxes[mid]["t"]
        for row, (fixture, meta) in zip(context["gameweek"], s.fixtures.items(), strict=True):
            goals = Counter(e["team"] for e in s.match.events if e["fixture_id"] == fixture and e["t"] <= at and
                            e["type"] == "shot" and e["outcome"] == "goal")  # fmt: skip
            want = f"{meta['home']['name']} {goals['home']}, {meta['away']['name']} {goals['away']}"
            assert row["match"] == want
    assert any(ch in "123456789" for c in contexts for row in c["gameweek"] for ch in row["match"])  # a goal, somewhere
    # a line about a moment may name only its own match's clubs and players, and the managers
    managers = {m["display_name"] for m in s.managers.values()}
    for context, mid in zip(contexts, asked, strict=True):
        meta = s.fixtures[s.climaxes[mid]["fixture_id"]]
        own = {meta[side]["name"] for side in ("home", "away")}
        own |= {p["name"] for side in ("home", "away") for p in meta[side]["players"]}
        assert set(context["names"]) == own | managers
        assert len(own) == 24
    request = ManagerAgent(None, "m").request  # type: ignore[arg-type]
    stake = stakes_for(s, {"mgr_1": 6}, moment_in(s, "fx_1"))[0]
    assert "GAMEWEEK" in json.loads(request(stake, contexts[0]).split("\n\n")[0])
    single = {k: v for k, v in contexts[0].items() if k != "gameweek"}
    assert "GAMEWEEK" not in json.loads(request(stake, single).split("\n\n")[0])


# --- one fixture failing ------------------------------------------------------------------------------


def failing(s: Session, fixture: str, after_t: int) -> None:
    """That fixture's points engine raises on its first event past after_t."""
    engine = s.engines[fixture]
    real = engine.feed

    def feed(e: dict[str, Any]) -> list[dict[str, Any]] | None:
        if e["t"] > after_t:
            raise RuntimeError("a fault in one fixture")
        return real(e)

    engine.feed = feed  # type: ignore[method-assign]


@pytest.mark.parametrize("after_minute", [30, 70])
async def test_a_fixture_that_fails_stops_alone_and_the_gameweek_plays_on(
    after_minute: int, caplog: pytest.LogCaptureFixture
) -> None:
    # #94 case 18, before and after half time
    s = gameweek()
    failing(s, "fx_2", after_minute * 60_000)
    got = await run(s)
    events, frames = of(got["group"], "event"), of(got["group"], "frame")
    ends = [e for e in events if e["type"] == "full_time"]
    assert Counter(e["fixture_id"] for e in ends) == {"fx_1": 1, "fx_2": 1, "fx_3": 1}
    whistle = next(e for e in ends if e["fixture_id"] == "fx_2")
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(set(seqs))  # strictly increasing across the gameweek, its whistle included
    assert not [e for e in events[events.index(whistle) + 1 :] if e["fixture_id"] == "fx_2"]
    assert all(f["t"] <= whistle["t"] for f in frames if f["fixture_id"] == "fx_2")
    assert whistle["t"] < 93 * 60_000  # where it stopped, not at full time
    assert s.engines["fx_2"].standings.by_fixture["fx_2"]  # its points so far stay in the table
    tables = [c for c in of(got["group"], "cue") if c["kind"] == "table" and "moment_id" not in c]
    assert [c["payload"]["headline"] for c in tables] == ["Half time", "Full time"]
    summaries = [c for screen in SCREENS[1:] for c in of(got[screen], "cue") if c["kind"] == "summary"]
    assert Counter(c["payload"]["headline"] for c in summaries) == {"Half time": 5, "Full time": 5}
    assert "fixture fx_2 of match" in caplog.text
    assert "ending it on every screen" not in caplog.text
    played = [c["moment_id"] for c in bundle_for(s)["climaxes"]]  # the recap has only the moments played
    assert played == [st[0]["moment_id"] for st in s.stakes_log]
    assert all(s.climaxes[m]["t"] <= whistle["t"] for m in played if s.climaxes[m]["fixture_id"] == "fx_2")
    assert len(played) < len(s.climaxes)


async def test_a_failure_outside_one_fixture_ends_the_gameweek_with_a_whistle_for_each_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = gameweek()
    real = s._on_gameweek

    def on_gameweek(message: dict[str, Any]) -> None:
        if message["data"]["t"] > 50 * 60_000:
            raise RuntimeError("a fault in the play loop itself")
        real(message)

    monkeypatch.setattr(s, "_on_gameweek", on_gameweek)
    got = await run(s)
    events = of(got["group"], "event")
    ends = [e for e in events if e["type"] == "full_time"]
    assert sorted(e["fixture_id"] for e in ends) == ["fx_1", "fx_2", "fx_3"]
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(set(seqs))
    assert all(e["t"] <= 50 * 60_000 for e in ends)  # where it stopped


async def test_a_failure_in_the_gameweeks_half_time_costs_its_table_and_play_goes_on(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    s = gameweek()
    real = s._phase

    def phase(name: str, t: int) -> None:
        if name == "half_time":
            raise RuntimeError("a fault in the gameweek's own half time")
        real(name, t)

    monkeypatch.setattr(s, "_phase", phase)
    got = await run(s)
    ends = [e for e in of(got["group"], "event") if e["type"] == "full_time"]
    assert sorted(e["fixture_id"] for e in ends) == ["fx_1", "fx_2", "fx_3"]
    assert all(e["t"] > 90 * 60_000 for e in ends)  # each fixture blew its own full time
    tables = [c for c in of(got["group"], "cue") if c["kind"] == "table" and "moment_id" not in c]
    assert [c["payload"]["headline"] for c in tables] == ["Full time"]
    assert "the gameweek's half time of match" in caplog.text
    assert "ending it on every screen" not in caplog.text


async def test_with_every_fixture_failed_the_gameweek_still_has_its_table_and_summaries() -> None:
    s = gameweek()
    for fixture in s.fixtures:
        failing(s, str(fixture), 60 * 60_000)
    got = await run(s)
    ends = [e for e in of(got["group"], "event") if e["type"] == "full_time"]
    assert sorted(e["fixture_id"] for e in ends) == ["fx_1", "fx_2", "fx_3"]
    tables = [c for c in of(got["group"], "cue") if c["kind"] == "table" and "moment_id" not in c]
    assert [c["payload"]["headline"] for c in tables] == ["Half time", "Full time"]
    summaries = [c for screen in SCREENS[1:] for c in of(got[screen], "cue") if c["kind"] == "summary"]
    assert Counter(c["payload"]["headline"] for c in summaries) == {"Half time": 5, "Full time": 5}


async def test_a_moment_in_flight_when_its_fixture_fails_reaches_no_screen() -> None:
    s = gameweek(manager=Manager(delay=0.1))
    queues = {screen: s.subscribe(screen) for screen in SCREENS}
    mid = moment_in(s, "fx_2")
    moments = Arrivals(s)
    moments.arrive(mid, {"mgr_1": 6, "mgr_2": 2})
    s._stop("fx_2", {"type": "pass", "t": s.climaxes[mid]["t"]})  # while its bids are out
    got = await moments.results()
    assert got[mid] is None
    seen = [m for q in queues.values() for m in drain(q)]
    assert not [m for m in seen if m["type"] == "decision" or (m["type"] == "cue" and m["data"]["kind"] != "stat")]


async def test_a_moment_whose_id_is_already_in_flight_costs_that_moment_only(caplog: pytest.LogCaptureFixture) -> None:
    s = gameweek(manager=Manager(delay=0.05))
    first = next(iter(s.climaxes))
    s._arrive(stakes_for(s, {"mgr_1": 6}, first), 1e12)  # a moment with that id is in flight
    caplog.set_level(logging.WARNING)
    for message in messages(s.match):
        if message["type"] != "meta":
            s._on_gameweek(message)
        if message["data"].get("moment_id") == first and message["data"].get("climax"):
            break
    assert f"moment {first} of match {s.id} skipped" in caplog.text
    assert s.moment_tasks == []
    assert s.stakes_log == []
    assert set(s.moments) == {first}


# --- screens ------------------------------------------------------------------------------------------


async def test_a_screen_joining_mid_gameweek_gets_the_gameweek_first_and_every_fixture_so_far() -> None:
    # #94 case 19, the server's half; and the rule from #164: `gameweek` first on every socket
    s = gameweek()
    group = s.subscribe("group")
    for message in messages(s.match):
        if message["data"].get("t", 0) > 60 * 60_000:
            break
        if message["type"] != "meta":
            s._on_gameweek(message)
    await asyncio.gather(*s.moment_tasks)
    live = drain(group)
    for screen in ("group", "phone:mgr_2"):
        replay = drain(s.subscribe(screen))
        assert [m["type"] for m in replay[:2]] == ["gameweek", "league"]
        assert all(f["fixture_id"] for f in replay[0]["data"]["fixtures"])
        events, frames = of(replay, "event"), of(replay, "frame")
        assert {e["fixture_id"] for e in events if e["type"] == "half_time"} == {"fx_1", "fx_2", "fx_3"}
        assert [f["fixture_id"] for f in frames] == ["fx_1", "fx_2", "fx_3"]  # the latest of each
        for frame in frames:
            assert frame == [f for f in of(live, "frame") if f["fixture_id"] == frame["fixture_id"]][-1]
        assert of(replay, "decision") == of(live, "decision")
        assert [c for c in of(replay, "cue")] == [c for c in s.log if c.get("screen") == screen]
    cues_only = drain(s.subscribe("phone:mgr_3", cues_only=True))
    assert cues_only[0]["type"] == "gameweek"
    assert {m["type"] for m in cues_only[1:]} <= {"cue"}


async def test_a_single_match_still_starts_with_meta_on_every_socket() -> None:
    s = Session(seed=267, manager="mgr_1", style="casual", lang="en", sleep=settle)
    assert drain(s.subscribe("group"))[0]["type"] == "meta"
    assert drain(s.subscribe("phone:mgr_1", cues_only=True)) == []


# --- the store ------------------------------------------------------------------------------------------


async def test_a_gameweek_is_one_match_against_every_store_limit() -> None:
    store = SessionStore(max_sessions=2, per_client=1, per_client_hour=2, session_kwargs={"sleep": settle})
    s = store.create(SEED, "mgr_1", "casual", "en", "1.1.1.1", gameweek=True)
    assert s is not None
    assert s.gameweek is not None
    assert store.create(SEED, "mgr_1", "casual", "en", "1.1.1.2") is not None  # a single match beside it
    s.subscribe("group")
    store.start(s)
    assert [entry.session_id for entry in store.started] == [s.id]  # one start, not three
    assert store.create(SEED, "mgr_1", "casual", "en", "1.1.1.1", gameweek=True) is None  # per_client: 1
    s.task.cancel()  # type: ignore[union-attr]


def test_a_gameweek_over_http_starts_with_the_gameweek_message() -> None:
    app = create_app(session_kwargs={"sleep": settle})
    with TestClient(app) as client:
        made = client.post("/api/sessions", json={"manager": "mgr_2", "gameweek": True})
        assert made.status_code == 201
        assert client.post("/api/sessions", json={"manager": "mgr_2", "fixtures": 3}).status_code == 422
        with client.websocket_connect(f"/ws/{made.json()['session_id']}/group") as ws:
            assert ws.receive_json()["type"] == "gameweek"
            assert ws.receive_json()["type"] == "league"


# --- model calls (#94 case 25) --------------------------------------------------------------------------


def with_stubs(seed: int, kind: str) -> tuple[Session, Manager, Host]:
    manager, host = Manager(), Host()
    s = Session(seed=seed, manager="mgr_1", style="casual", lang="en", speed=30, sleep=settle,
                manager_agent=manager, host_agent=host, gameweek=kind == "gameweek")  # fmt: skip
    return s, manager, host


async def counted_calls(seed: int, kind: str) -> tuple[int, int]:
    """Manager and host calls for one seed, without playing the session (#185): its events through points engines
    set up as the session sets them up (one Standings shared by a gameweek's fixtures), and each key moment's stakes
    through the moment workflow, built as the session builds it with the same stub agents, so who is asked is
    decided by the session's code. The stubs read neither the moment's summary nor its names, so they are left out."""
    s, manager, host = with_stubs(seed, kind)
    standings = Standings(s.league) if s.gameweek is not None else None
    engines = {f: PointsEngine(meta, s.league, standings=standings, fixture_id=f) for f, meta in s.fixtures.items()}
    moment_workflow = build(s.league["managers"], manager, host, Ledger(), lambda _: None, *s.deadlines)
    for event in s.match.events:
        stakes = engines[event.get("fixture_id")].feed(event)
        if not stakes:
            continue
        moment = MomentInput(stakes[0]["moment_id"], event["t"], "", {st["manager"]: st for st in stakes},
                             time.monotonic() + s.decision_budget_s, False, [], s.names)  # fmt: skip
        await moment_workflow.run(moment)
    return manager.calls, host.calls


async def test_a_gameweek_calls_the_models_at_most_a_third_more_than_a_single_match() -> None:
    # #94 case 25, over 50 seeds with stub agents: bids and host calls, counted from the stakes (#185); the next
    # test holds the count to the real session. Neither side has a summary agent here: summary calls are not counted.
    calls = {"single": [0, 0], "gameweek": [0, 0]}
    for seed in range(50):
        for kind, total in calls.items():
            made = await counted_calls(seed, kind)
            total[0], total[1] = total[0] + made[0], total[1] + made[1]
    assert all(host for _, host in calls.values()), calls  # close calls reach the host in both kinds
    single, week = sum(calls["single"]), sum(calls["gameweek"])
    assert week <= 1.3 * single, calls
    # and the agents are asked at all: a gameweek's squads are spread over three fixtures, so fewer friends have
    # points at stake in each moment than in a single match (0.59 of its calls on 27 Oct)
    assert week >= 0.4 * single, calls
    print(f"model calls over 50 seeds (manager, host): {calls}, ratio {week / single:.2f}")


@pytest.mark.parametrize("seed", range(5))
async def test_a_real_session_makes_exactly_the_counted_model_calls(seed: int) -> None:
    # #185: holds the count above to the session it stands for, on five seeds of each kind
    for kind in ("single", "gameweek"):
        s, manager, host = with_stubs(seed, kind)
        await s.run()
        assert (manager.calls, host.calls) == await counted_calls(seed, kind), (seed, kind)


async def test_a_finished_gameweek_hands_the_recap_job_a_gameweek_bundle() -> None:
    # #154's bundle, from this session at full time: one entry per fixture, every moment filed under its fixture
    submitted: list[dict[str, Any]] = []

    class Store:
        async def submit(self, session_id: str, bundle: dict[str, Any]) -> None:
            submitted.append(bundle)

        async def links(self, session_id: str, managers: list[str]) -> dict[str, str] | None:
            return {m: f"https://recaps.test/{m}" for m in managers}

    s = gameweek(on_full_time=lambda session: publish_recaps(Store(), session, poll_s=0))
    got = await run(s)
    [bundle] = submitted
    check(bundle)
    assert bundle["v"] == 2
    assert [f["fixture_id"] for f in bundle["fixtures"]] == ["fx_1", "fx_2", "fx_3"]
    assert {c["moment_id"] for c in bundle["climaxes"]} == set(s.climaxes)
    assert bundle["table"] == s.engine.table()
    assert s.match.frames == []  # the session lets go of the frames once the job has them
    assert s.gameweek is not None
    assert s.gameweek.frames == []
    assert [m.frames for m in s.gameweek.fixtures] == [[], [], []]
    assert built(SEED, 30).frames  # a copy: the cached gameweek keeps its own for the next session
    links = [c for screen in SCREENS[1:] for c in of(got[screen], "cue") if c["kind"] == "recap_ready"]
    assert len(links) == 5


async def test_a_finished_gameweek_without_a_recap_holds_no_frames() -> None:
    s = gameweek()
    await run(s)
    assert s.gameweek is not None
    assert (s.match.frames, s.gameweek.frames, [m.frames for m in s.gameweek.fixtures]) == ([], [], [[], [], []])
    assert all(m.frames for m in built(SEED, 30).fixtures)
    assert drain(s.subscribe("group"))[0]["type"] == "gameweek"  # a screen joining after full time still works


async def test_built_gameweek_is_shared_and_never_changed_by_a_session() -> None:
    before = json.dumps(built(SEED, 30).events)
    await run(gameweek())
    assert json.dumps(built(SEED, 30).events) == before
