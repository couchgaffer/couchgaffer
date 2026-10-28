import json
from collections import Counter
from collections.abc import Callable
from functools import cache
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from watchparty.sim import DEMO_SEED, Match, messages, play, simulate
from watchparty.sim.__main__ import main as cli
from watchparty.sim.engine import MOMENT_SPACING, _schedule

SEEDS = range(1, 51)
FULL_TIME = 90 * 60_000
MIN_GAP = 5 * 60_000  # key moments at least 10 s apart in real time at 30x


@cache
def match(seed: int) -> Match:
    return simulate(seed)


def stats(m: Match) -> dict[str, float]:
    shots = [e for e in m.events if e["type"] == "shot"]
    passes = [e for e in m.events if e["type"] == "pass"]
    home_ms = sum(b["t"] - a["t"] for a, b in pairwise(m.frames) if a["possession"] == "home")
    return {
        "shots": len(shots),
        "xg": sum(e["shot"]["xg"] for e in shots),
        "goals": sum(e["outcome"] == "goal" for e in shots),
        "pass_completion": sum(e["outcome"] == "success" for e in passes) / len(passes),
        "home_possession": home_ms / (m.frames[-1]["t"] - m.frames[0]["t"]),
    }


def moments(m: Match) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for e in m.events:
        if "moment_id" in e:
            out.setdefault(e["moment_id"], []).append(e)
    return out


def test_same_seed_gives_identical_output_and_seeds_differ() -> None:
    def dump(m: Match) -> str:
        return json.dumps([m.meta, m.events, m.frames])

    assert dump(simulate(7)) == dump(simulate(7))
    assert dump(simulate(7)) != dump(simulate(8))


@pytest.mark.parametrize("seed", [1, DEMO_SEED])
def test_every_message_matches_the_contracts(seed: int, contract_errors: Callable[[str, Any], list[str]]) -> None:
    m = match(seed)
    assert contract_errors("match-meta", m.meta) == []
    for event in m.events:
        assert contract_errors("match-event", event) == [], event
    for frame in m.frames:
        assert contract_errors("match-frame", frame) == [], frame
    for message in messages(m):
        assert contract_errors("ws-message", message) == []


@pytest.mark.parametrize("seed", SEEDS)
def test_sequence_and_clock(seed: int) -> None:
    m = match(seed)
    assert [e["seq"] for e in m.events] == list(range(len(m.events)))
    assert all(a["t"] <= b["t"] for a, b in pairwise(m.events))
    assert [f["t"] for f in m.frames] == list(range(0, m.frames[-1]["t"] + 1, 3000))
    kinds = [e["type"] for e in m.events]
    assert kinds[0] == "kickoff"
    assert kinds[-1] == "full_time"
    assert kinds.count("half_time") == 1
    # The referee blows at the first break in play after 45:00.
    assert FULL_TIME // 2 <= next(e["t"] for e in m.events if e["type"] == "half_time") <= FULL_TIME // 2 + 60_000
    assert FULL_TIME <= m.events[-1]["t"] <= FULL_TIME + 6 * 60_000


def test_counterattacks_start_in_the_attackers_own_half() -> None:
    for seed in SEEDS:
        for events in moments(match(seed)).values():
            if events[0]["scenario"] == "counterattack":
                win, shot = events[0], events[-1]
                own_half = win["x"] < 0.5 if win["team"] == "home" else win["x"] > 0.5
                assert win["type"] == "interception"
                assert own_half, win
                assert shot["type"] == "shot"
                assert shot["team"] == win["team"]


@pytest.mark.parametrize("seed", SEEDS)
def test_key_moments_are_well_formed_and_spaced(seed: int) -> None:
    by_moment = moments(match(seed))
    assert 12 <= len(by_moment) <= 15
    assert list(by_moment) == [f"m_{n:02d}" for n in range(1, len(by_moment) + 1)]
    climaxes = []
    for events in by_moment.values():
        assert [e.get("climax", False) for e in events] == [False] * (len(events) - 1) + [True]
        assert len({e["scenario"] for e in events}) == 1
        climaxes.append(events[-1]["t"])
    assert all(b - a >= MIN_GAP for a, b in pairwise(climaxes))


@pytest.mark.parametrize("stoppage", [2, 3, 4, 5])
def test_every_schedule_fits_its_moments_6_minutes_apart(stoppage: int) -> None:
    import random

    full_time = FULL_TIME + stoppage * 60_000
    for seed in range(2_000):  # #31: with 2 minutes of stoppage, 15 moments did not fit
        starts = _schedule(random.Random(seed), full_time)  # noqa: S311  reproducible simulation, not security
        assert 12 <= len(starts) <= 15
        assert all(b - a >= MOMENT_SPACING for a, b in pairwise(starts)), (seed, stoppage)
        # Clear of kickoff, half time and full time, as _schedule promises (#31)
        assert starts[0] >= 3 * 60_000, (seed, stoppage)
        assert starts[-1] <= full_time - 2 * 60_000, (seed, stoppage)
        assert not any(43 * 60_000 <= t < 45 * 60_000 + 90_000 for t in starts), (seed, stoppage)


def test_averages_over_50_seeds_match_real_top_flight_football() -> None:
    """Real top-flight averages: about 25 shots, 2.5 to 3 xG and 2.7 goals per match, about 82% passes completed."""
    rows = [stats(match(seed)) for seed in SEEDS]

    def mean(key: str) -> float:
        return sum(r[key] for r in rows) / len(rows)

    assert 22 <= mean("shots") <= 28
    assert 2.2 <= mean("xg") <= 3.2
    assert 2.0 <= mean("goals") <= 3.2
    assert 0.78 <= mean("pass_completion") <= 0.86
    assert 0.45 <= mean("home_possession") <= 0.55


@pytest.mark.parametrize("seed", SEEDS)
def test_no_seed_is_absurd(seed: int) -> None:
    """Single matches vary a lot in real football; this only rules out the impossible-looking."""
    s = stats(match(seed))
    assert 12 <= s["shots"] <= 40, s
    assert 0.8 <= s["xg"] <= 5.0, s
    assert 0 <= s["goals"] <= 7, s
    assert 0.72 <= s["pass_completion"] <= 0.9, s
    assert 0.35 <= s["home_possession"] <= 0.65, s


def test_every_scenario_appears_across_seeds() -> None:
    seen = Counter(events[0]["scenario"] for seed in SEEDS for events in moments(match(seed)).values())
    expected = {"build_up", "counterattack", "high_press", "set_piece", "low_block", "keeper_save_run"}
    assert expected <= set(seen)
    assert seen["late_equaliser"] > 0


def test_events_carry_the_metrics_the_brief_suggests() -> None:
    events = match(1).events
    passes = [e for e in events if e["type"] == "pass"]
    assert all({"distance_m", "difficulty", "progressive", "ball_speed_km_h"} <= e["pass"].keys() for e in passes)
    assert any(e["pass"]["progressive"] for e in passes)
    assert all(e["shot"]["speed_km_h"] > 0 for e in events if e["type"] == "shot")
    assert any("player_speed_km_h" in e for e in events)


def test_a_sent_off_player_disappears() -> None:
    for seed in SEEDS:
        m = match(seed)
        red = next((e for e in m.events if e["type"] == "card" and e["outcome"] == "red"), None)
        if red:
            later = [e for e in m.events if e["t"] > red["t"]]
            assert red["player"] not in {e.get("player") for e in later} | {e.get("receiver") for e in later}
            assert all(p["id"] != red["player"] for f in m.frames if f["t"] > red["t"] for p in f["players"])
            return
    pytest.fail("no red card in 50 seeds")


def test_a_sent_off_striker_does_not_take_the_second_half_kickoff() -> None:
    m = match(214)  # p_away_9, the usual taker, is sent off in the first half
    red = next(e for e in m.events if e["type"] == "card" and e["outcome"] == "red")
    assert red["player"] == "p_away_9"
    assert red["t"] < next(e["t"] for e in m.events if e["type"] == "half_time")
    later = [e for e in m.events if e["t"] > red["t"]]
    assert "p_away_9" not in {e.get(k) for e in later for k in ("player", "receiver", "opponent")}
    kickoff = next(e for e in later if e["type"] == "kickoff" and e["team"] == "away")
    frame = next(f for f in m.frames if f["t"] >= kickoff["t"])
    assert kickoff["player"] in {p["id"] for p in frame["players"]}


def test_frames_put_the_ball_carrier_on_the_ball() -> None:
    m = match(1)
    for f in m.frames[::50]:
        nearest = min(f["players"], key=lambda p: (p["x"] - f["ball"]["x"]) ** 2 + (p["y"] - f["ball"]["y"]) ** 2)
        assert abs(nearest["x"] - f["ball"]["x"]) < 0.1


def test_demo_seed_has_the_story_for_the_video() -> None:
    m = match(DEMO_SEED)
    scenarios = [events[0]["scenario"] for events in moments(m).values()]
    assert scenarios.count("keeper_save_run") >= 2  # the keeper reaches the Wall tier
    assert "late_equaliser" in scenarios
    assert any(e["type"] == "card" and e["outcome"] == "red" for e in m.events)
    assert 2 <= stats(m)["goals"] <= 5


def test_messages_are_time_ordered_and_start_with_meta() -> None:
    msgs = list(messages(match(1)))
    assert msgs[0] == {"v": 1, "type": "meta", "data": match(1).meta}
    times = [msg["data"]["t"] for msg in msgs[1:]]
    assert times == sorted(times)


@pytest.mark.anyio
async def test_play_paces_by_match_speed() -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    m = match(1)
    out = [msg async for msg in play(m, speed=30, sleep=fake_sleep)]
    assert out == list(messages(m))
    assert sum(slept) == pytest.approx(m.events[-1]["t"] / 1000 / 30, rel=1e-6)
    assert min(slept) >= 0


def test_cli_exports_a_dataset(tmp_path: Path, contract_errors: Callable[[str, Any], list[str]]) -> None:
    out = tmp_path / "match.jsonl"
    cli(["--seed", "3", "--out", str(out)])
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert lines == list(messages(match(3)))
    assert contract_errors("ws-message", lines[0]) == []
