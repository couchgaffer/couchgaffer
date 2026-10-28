"""Recaps from a gameweek (#94 case 23, #154): bundle version 2, each clip drawn from its own fixture.

The gameweek session is its own slice (#151), so these tests score a built gameweek the way it will: three points
engines on one Standings, the session's stat cues per climax, a decision per moment and a full-time summary per
manager. The bundle is made by gameweek_bundle, the function that session will call.
"""

import asyncio
import copy
import gzip
import hashlib
import itertools
import json
import math
import shutil
import subprocess
import types
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from PIL import ImageDraw

from tests.test_recap import ffmpeg
from watchparty.app.cues import Cues, points_headline, stat_headline
from watchparty.app.session import Session
from watchparty.points.engine import HERE, PointsEngine
from watchparty.points.standings import Standings
from watchparty.recap import render, worker
from watchparty.recap.bundle import check, gameweek_bundle, make_bundle, trimmed
from watchparty.recap.plan import AFTER_MS, BEFORE_MS, MAX_CLIPS, plan
from watchparty.sim import DEMO_SEED
from watchparty.sim.clubs import CLUBS
from watchparty.sim.gameweek import Gameweek, build_gameweek

MANAGERS = [f"mgr_{n}" for n in range(1, 6)]


def scored(seed: int = DEMO_SEED) -> tuple[Gameweek, dict[str, Any]]:
    """A built gameweek, scored as the gameweek session scores it, and its version 2 bundle."""
    gw = build_gameweek(seed)
    league = json.loads((HERE / "league_gw_v1.json").read_text())
    standings = Standings(league)
    engines = {m.meta["fixture_id"]: PointsEngine(m.meta, league, standings=standings,
                                                  fixture_id=m.meta["fixture_id"]) for m in gw.fixtures}  # fmt: skip
    names = {p["id"]: p["name"] for m in gw.fixtures for s in ("home", "away") for p in m.meta[s]["players"]}
    langs = {m["id"]: m.get("lang", "en") for m in league["managers"]}
    cues, climaxes, stakes_log, log = Cues(), [], [], []
    for e in gw.events:
        stakes = engines[e["fixture_id"]].feed(e)
        if not stakes:
            continue
        mid, t = e["moment_id"], e["t"]
        climaxes.append(e)
        stakes_log.append(stakes)
        log.append(cues.make("group", "stat", "code", t, {"headline": stat_headline(e, names)}, mid))
        for s in (s for s in stakes if s["delta_points"]):
            payload = {"headline": points_headline(s["delta_points"], langs[s["manager"]]), "manager": s["manager"]}
            log.append(cues.make(f"phone:{s['manager']}", "stat", "code", t, payload, mid))
        top = max(stakes, key=lambda s: (abs(s["delta_points"]), s["manager"]))
        log.append({"moment_id": mid, "chosen_manager": top["manager"], "t": t})  # the host's pick, as logged
    full = max(e["t"] for e in gw.events)
    for m in MANAGERS:
        body = f"{m} closes the gameweek on {standings.points[m]:g} IP."
        log.append(cues.make(f"phone:{m}", "summary", "code", full, {"headline": "Full time", "body": body}))
    bundle = gameweek_bundle(session_id="gw_session_0000", gameweek=gw, league=league, climaxes=climaxes,
                             stakes=stakes_log, log=log, table=standings.table())  # fmt: skip
    return gw, bundle


@cache
def demo() -> tuple[Gameweek, dict[str, Any]]:
    return scored()


@pytest.fixture
def gw() -> Gameweek:
    return demo()[0]


@pytest.fixture
def bundle() -> dict[str, Any]:
    return copy.deepcopy(demo()[1])


def full_frames(gw: Gameweek, fixture_id: str) -> list[dict[str, Any]]:
    return next(m.frames for m in gw.fixtures if m.meta["fixture_id"] == fixture_id)


def test_a_gameweek_bundle_is_plain_json_with_one_entry_per_fixture(bundle: dict[str, Any]) -> None:
    assert json.loads(gzip.decompress(gzip.compress(json.dumps(bundle).encode()))) == bundle
    assert bundle["v"] == 2
    assert "meta" not in bundle
    assert "frames" not in bundle
    assert [f["fixture_id"] for f in bundle["fixtures"]] == ["fx_1", "fx_2", "fx_3"]
    for f in bundle["fixtures"]:
        assert f["meta"]["fixture_id"] == f["fixture_id"]
        assert f["frames"]
        assert {fr["fixture_id"] for fr in f["frames"]} == {f["fixture_id"]}
    assert bundle["gameweek"]["gameweek_id"] == f"gw_{DEMO_SEED}"
    assert "fixtures" not in bundle["gameweek"]  # each fixture's meta is written once, in its entry


def test_each_fixtures_frames_are_trimmed_to_its_own_clip_windows(bundle: dict[str, Any], gw: Gameweek) -> None:
    for f in bundle["fixtures"]:
        own = [e["t"] for e in bundle["climaxes"] if e["fixture_id"] == f["fixture_id"]]
        assert own
        gap = 3000  # one frame either side of a window: the neighbours a clip interpolates from
        for frame in f["frames"]:
            assert any(t - BEFORE_MS - gap <= frame["t"] <= t + AFTER_MS + gap for t in own), frame["t"]
        assert len(f["frames"]) * 10 < len(full_frames(gw, f["fixture_id"]))


def test_trimming_loses_nothing_a_clip_draws(bundle: dict[str, Any], gw: Gameweek) -> None:
    for f in bundle["fixtures"]:
        kept, whole = render.Positions(f["frames"]), render.Positions(full_frames(gw, f["fixture_id"]))
        for e in (e for e in bundle["climaxes"] if e["fixture_id"] == f["fixture_id"]):
            start, end = max(0, e["t"] - BEFORE_MS), e["t"] + AFTER_MS
            for i in range(200):
                t = start + i * (end - start) / 200
                assert kept.at(t) == whole.at(t), (f["fixture_id"], e["moment_id"], t)


def test_trimming_keeps_the_neighbours_at_kickoff_and_at_the_end() -> None:
    frames = [{"t": t} for t in range(0, 30_000, 3000)]
    assert [f["t"] for f in trimmed(frames, [2000])] == [0, 3000, 6000]  # window 0 to 5000: the first after is 6000
    assert [f["t"] for f in trimmed(frames, [26_000])] == [15_000, 18_000, 21_000, 24_000, 27_000]  # last frame 27000
    assert trimmed(frames, []) == []
    assert trimmed([], [12_000]) == []


def test_climaxes_stakes_and_cues_carry_their_fixture(bundle: dict[str, Any], gw: Gameweek) -> None:
    fixture_of = {e["moment_id"]: e["fixture_id"] for e in gw.events if e.get("climax")}
    assert {e["moment_id"] for e in bundle["climaxes"]} == set(fixture_of)
    assert all(e["fixture_id"] == fixture_of[e["moment_id"]] for e in bundle["climaxes"])
    assert all(s["fixture_id"] == fixture_of[s["moment_id"]] for moment in bundle["stakes"] for s in moment)
    about_a_moment = [entry for entry in bundle["log"] if "moment_id" in entry]
    assert about_a_moment
    assert all(entry["fixture_id"] == fixture_of[entry["moment_id"]] for entry in about_a_moment)


def test_a_cue_without_a_fixture_gets_its_climaxs_and_the_sessions_log_is_left_alone() -> None:
    gw, _ = demo()
    climax = next(e for e in gw.events if e.get("climax"))
    cue = {"cue_id": "c_1", "moment_id": climax["moment_id"], "screen": "group"}
    other = {"cue_id": "c_2", "moment_id": climax["moment_id"], "fixture_id": "fx_9"}  # the session's own, kept
    summary = {"cue_id": "c_3", "kind": "summary"}  # about no moment: unchanged
    log = [cue, other, summary]
    made = gameweek_bundle(session_id="s" * 16, gameweek=gw, league={}, climaxes=[climax], stakes=[], log=log,
                           table=[])  # fmt: skip
    assert made["log"] == [{**cue, "fixture_id": climax["fixture_id"]}, other, summary]
    assert "fixture_id" not in cue  # a copy: the live session's log is not changed


def test_the_plan_still_picks_up_to_five_per_manager_and_a_recap_can_mix_fixtures(bundle: dict[str, Any]) -> None:
    mixed = 0
    for manager in MANAGERS:
        recap = plan(bundle, manager)
        stakes = [s for m in bundle["stakes"] for s in m if s["manager"] == manager and s["above_threshold"]]
        biggest = sorted(stakes, key=lambda s: -abs(s["delta_points"]))[:MAX_CLIPS]
        assert 1 <= len(recap.clips) <= MAX_CLIPS
        assert {s["moment_id"] for s in biggest} <= {c.moment_id for c in recap.clips}
        assert [c.climax_t for c in recap.clips] == sorted(c.climax_t for c in recap.clips)
        climax = {e["moment_id"]: e for e in bundle["climaxes"]}
        assert all(c.fixture_id == climax[c.moment_id]["fixture_id"] for c in recap.clips)
        mixed += len({c.fixture_id for c in recap.clips}) > 1
    assert mixed >= 1


class Spy(render.Positions):
    """Records which fixture's positions each drawn video frame read."""

    def __init__(self, fixture_id: str | None, frames: list[dict[str, Any]], calls: list[str | None]) -> None:
        super().__init__(frames)
        self.fixture_id, self.calls = fixture_id, calls

    def at(self, t: float) -> tuple[tuple[float, float], dict[str, tuple[float, float]]]:
        self.calls.append(self.fixture_id)
        return super().at(t)


def mixed_recap(bundle: dict[str, Any]) -> Any:
    return next(r for r in (plan(bundle, m) for m in MANAGERS) if len({c.fixture_id for c in r.clips}) > 1)


def spy_positions(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    """Which fixture's positions each drawn clip frame reads, in order."""
    calls: list[str | None] = []
    real = render.fixtures

    def spied(b: dict[str, Any]) -> dict[str | None, render.Fixture]:
        by_id = real(b)
        for fid, fixture in by_id.items():
            fixture.positions = Spy(fid, fixture.positions.frames, calls)
        return by_id

    monkeypatch.setattr(render, "fixtures", spied)
    return calls


def test_each_clip_is_drawn_from_its_own_fixture(bundle: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(render, "FPS", 2)  # a few frames per clip: which fixture they read is the point
    calls = spy_positions(monkeypatch)
    recap = mixed_recap(bundle)
    list(render.frames(bundle, recap))
    steps = len(calls) // len(recap.clips)
    assert steps > 0
    assert calls == [c.fixture_id for c in recap.clips for _ in range(steps)]


def test_a_clips_dots_and_name_tags_are_its_own_fixtures_players(
    bundle: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the frames actually draw: every dot in a clip is one of its two clubs' colours (or the white ball),
    both clubs appear, and each name tag is one of that clip's own players."""
    monkeypatch.setattr(render, "FPS", 2)
    fills: list[Any] = []
    texts: list[str] = []
    real_ellipse, real_text = ImageDraw.ImageDraw.ellipse, ImageDraw.ImageDraw.text

    def ellipse(self: ImageDraw.ImageDraw, xy: Any, *args: Any, **kwargs: Any) -> None:
        if kwargs.get("fill") is not None:  # the pitch's centre circle is an outline, drawn once
            fills.append(kwargs["fill"])
        real_ellipse(self, xy, *args, **kwargs)

    def text(self: ImageDraw.ImageDraw, xy: Any, s: str, *args: Any, **kwargs: Any) -> None:
        texts.append(s)
        real_text(self, xy, s, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "ellipse", ellipse)
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", text)
    by_id = render.fixtures(bundle)
    everyone = {name for f in by_id.values() for name in f.names.values()}  # every player of the gameweek
    recaps = [plan(bundle, m) for m in MANAGERS]
    tagged = 0
    for recap in recaps:
        drawn: list[tuple[list[Any], list[str]]] = []
        for _ in render.frames(bundle, recap):  # what was drawn since the last frame belongs to this one
            if fills:
                drawn.append((list(fills), list(texts)))
            fills.clear()
            texts.clear()
        steps = len(drawn) // len(recap.clips)
        assert steps > 0
        assert steps * len(recap.clips) == len(drawn)
        for k, clip in enumerate(recap.clips):
            fixture = by_id[clip.fixture_id]
            clubs = {render._rgb(fixture.meta[s]["colour"]) for s in ("home", "away")}
            for frame_fills, frame_texts in drawn[k * steps : (k + 1) * steps]:
                assert set(frame_fills) == clubs | {render.TEXT}, (clip.moment_id, clip.fixture_id)
                assert frame_fills.count(render.TEXT) == 1  # the ball; no dot falls back to white
                # A tag is drawn for each of the clip's players on the pitch in that frame (a sent-off one is not)
                tags = {t for t in frame_texts if t in everyone}
                assert tags <= {fixture.names[p] for p in clip.players}
                tagged += len(tags)
    assert tagged  # at least one name tag was checked


def drawn_texts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    texts: list[str] = []
    real = ImageDraw.ImageDraw.text

    def text(self: ImageDraw.ImageDraw, xy: Any, s: str, *args: Any, **kwargs: Any) -> None:
        texts.append(s)
        real(self, xy, s, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", text)
    return texts


def spy_scorelines(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, float, list[str]]]:
    """Every score line drawn: its fixture, the frame's match time and the text it drew."""
    texts = drawn_texts(monkeypatch)
    seen: list[tuple[str, float, list[str]]] = []
    real = render._scoreline

    def spied(d: ImageDraw.ImageDraw, fixture: render.Fixture, t: float) -> None:
        texts.clear()
        real(d, fixture, t)
        seen.append((fixture.meta["fixture_id"], t, list(texts)))

    monkeypatch.setattr(render, "_scoreline", spied)
    return seen


def test_each_clip_frame_names_its_fixtures_clubs_and_the_score_at_that_frame(
    bundle: dict[str, Any], gw: Gameweek, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(render, "FPS", 2)
    seen = spy_scorelines(monkeypatch)
    scored_any = False
    for recap in (plan(bundle, m) for m in MANAGERS):
        seen.clear()
        list(render.frames(bundle, recap))
        steps = len(seen) // len(recap.clips)
        assert steps > 0
        assert steps * len(recap.clips) == len(seen)
        for k, clip in enumerate(recap.clips):
            match = next(m for m in gw.fixtures if m.meta["fixture_id"] == clip.fixture_id)
            home, away = match.meta["home"], match.meta["away"]
            for fixture_id, t, drawn in seen[k * steps : (k + 1) * steps]:
                assert fixture_id == clip.fixture_id
                goals = [e["team"] for e in match.events if e["type"] == "shot" and e["outcome"] == "goal"
                         and e["t"] <= t]  # fmt: skip
                h, a = goals.count("home"), goals.count("away")
                scored_any |= h + a > 0
                assert drawn in ([f"{home[n]}  {h} - {a}  {away[n]}"] for n in ("name", "short_name")), drawn
    assert scored_any  # a score other than 0 - 0 was checked


def test_the_score_line_counts_a_goal_from_its_own_frame_on_never_in_the_build_up(
    bundle: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    recap = mixed_recap(bundle)
    clip = recap.clips[0]
    c = clip.climax_t
    for f in bundle["fixtures"]:
        f["goals"] = []
    own = next(f for f in bundle["fixtures"] if f["fixture_id"] == clip.fixture_id)
    other = next(f for f in bundle["fixtures"] if f["fixture_id"] != clip.fixture_id)
    own["goals"] = [{"t": c, "team": "home"}, {"t": c + 1, "team": "away"}]  # at the climax, and 1 ms after
    other["goals"] = [{"t": c, "team": "away"}]  # another fixture's goal at the same time is not this one's
    fixture = render.fixtures(bundle)[clip.fixture_id]
    assert [fixture.score(t) for t in (c - 1, c, c + 1)] == [(0, 0), (1, 0), (1, 1)]
    seen = spy_scorelines(monkeypatch)
    list(render.frames(bundle, recap))  # at 25 frames a second, as the job draws them
    first = [(t, drawn) for _, t, drawn in seen[: len(seen) // len(recap.clips)]]  # the first clip's frames
    name = "name" if own["meta"]["home"]["name"] in first[0][1][0] else "short_name"
    line = f"{own['meta']['home'][name]}  {{}} - {{}}  {own['meta']['away'][name]}"
    assert any(t < c for t, _ in first)
    assert any(t > c + 1 for t, _ in first)
    for t, drawn in first:
        assert drawn == [line.format(int(t >= c), int(t >= c + 1))], t


@pytest.mark.parametrize("pair", list(itertools.permutations(range(len(CLUBS)), 2)))
def test_every_pairing_of_the_six_clubs_fits_the_score_line(
    pair: tuple[int, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    from PIL import Image

    home, away = ({**CLUBS[i], "players": []} for i in pair)  # the line reads names, short names and colours
    boxes: list[Any] = []
    real = ImageDraw.ImageDraw.rounded_rectangle

    def rounded_rectangle(self: ImageDraw.ImageDraw, box: Any, *args: Any, **kwargs: Any) -> None:
        boxes.append(box)
        real(self, box, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "rounded_rectangle", rounded_rectangle)
    d = ImageDraw.Draw(Image.new("RGB", (render.W, render.H)))
    goals = [{"t": 1, "team": "home"}] * 10 + [{"t": 1, "team": "away"}] * 10  # two-digit scores, the widest line
    render._scoreline(d, render.Fixture({"home": home, "away": away}, [], goals), 1)
    assert len(boxes) == 2
    assert boxes[1][2] <= render.W - 60  # the away chip ends inside the right margin
    texts = drawn_texts(monkeypatch)
    render._scoreline(d, render.Fixture({"home": home, "away": away}, [], []), 1)
    full = f"{home['name']}  0 - 0  {away['name']}"
    fits = d.textlength(full, font=render.font(30, True)) + 2 * (30 + 16) <= render.W - 120  # chips and gaps
    assert fits  # in Arial and in DejaVu, the job's font, every pairing's full names fit at 0 - 0
    assert texts == [full]


def test_a_single_match_clip_draws_no_score_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(render, "FPS", 2)
    seen = spy_scorelines(monkeypatch)
    recap = plan(v1_bundle(), "mgr_1")
    assert recap.clips
    list(render.frames(v1_bundle(), recap))
    assert seen == []


def test_a_clip_whose_fixture_is_missing_raises_rather_than_drawing_another(bundle: dict[str, Any]) -> None:
    recap = mixed_recap(bundle)
    gone = recap.clips[-1].fixture_id
    bundle["fixtures"] = [f for f in bundle["fixtures"] if f["fixture_id"] != gone]
    with pytest.raises(ValueError, match=f"moment {recap.clips[-1].moment_id}: its fixture '{gone}' is not in"):
        list(render.frames(bundle, recap))
    with pytest.raises(ValueError, match=f"names fixture '{gone}', which the bundle does not hold"):
        check(bundle)


def test_a_clip_whose_frames_stop_short_raises_rather_than_freezing_the_dots(bundle: dict[str, Any]) -> None:
    recap = mixed_recap(bundle)
    clip = recap.clips[0]
    own = next(f for f in bundle["fixtures"] if f["fixture_id"] == clip.fixture_id)
    own["frames"] = [f for f in own["frames"] if f["t"] < clip.end_t - 3000]  # the outcome's frames are gone
    with pytest.raises(ValueError, match=f"moment {clip.moment_id}: fixture {clip.fixture_id} has no frames from"):
        list(render.frames(bundle, recap))
    with pytest.raises(ValueError, match=f"fixture {clip.fixture_id} has no frames around moment {clip.moment_id}"):
        check(bundle)


def test_a_bundle_missing_one_middle_clip_window_is_refused(bundle: dict[str, Any]) -> None:
    # A window between two whole ones: its fixture's first and last frames are still there (#155)
    own = next(f for f in bundle["fixtures"] if len([e for e in bundle["climaxes"]
                                                      if e["fixture_id"] == f["fixture_id"]]) >= 3)  # fmt: skip
    middle = [e for e in bundle["climaxes"] if e["fixture_id"] == own["fixture_id"]][1]
    start, end = middle["t"] - BEFORE_MS, middle["t"] + AFTER_MS
    own["frames"] = [f for f in own["frames"] if not start - 3000 < f["t"] < end + 3000]
    assert own["frames"][0]["t"] < start
    assert own["frames"][-1]["t"] > end
    with pytest.raises(ValueError, match=f"fixture {own['fixture_id']} has no frames around moment "
                                         f"{middle['moment_id']}"):  # fmt: skip
        check(bundle)


def _drop(key: str) -> Any:
    return lambda b: b.pop(key)


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        (lambda b: b["climaxes"][0].pop("fixture_id"), "names fixture None, which the bundle does not hold"),
        (lambda b: b["fixtures"][1].pop("goals"), r"fixture entries \['fx_2'\] need exactly"),
        (lambda b: b["fixtures"].append(copy.deepcopy(b["fixtures"][0])), "a fixture_id appears twice"),
        (lambda b: b.update(meta={}), r"unexpected \['meta'\]"),
        (_drop("fixtures"), r"missing \['fixtures'\]"),
        (lambda b: b["fixtures"][0].update(frames=[]), "fixture fx_1 has no frames around moment"),
    ],
)
def test_a_damaged_gameweek_bundle_is_refused_saying_what_is_wrong(
    bundle: dict[str, Any], damage: Any, message: str, tmp_path: Path
) -> None:
    check(bundle)  # whole, it passes
    damage(bundle)
    with pytest.raises(ValueError, match=message):
        worker.render_all(bundle, tmp_path)  # before any video is started


def test_a_single_match_bundle_laid_out_as_a_gameweek_is_refused() -> None:
    check(v1_bundle())
    with pytest.raises(ValueError, match=r"unexpected \['fixtures'\]"):
        check({**v1_bundle(), "fixtures": []})
    with pytest.raises(ValueError, match=r"missing \['frames'\]"):
        check({k: v for k, v in v1_bundle().items() if k != "frames"})


def test_a_climax_without_a_fixture_cannot_make_a_gameweek_bundle(gw: Gameweek) -> None:
    climax = {k: v for k, v in next(e for e in gw.events if e.get("climax")).items() if k != "fixture_id"}
    with pytest.raises(ValueError, match=f"climaxes without a fixture_id: {climax['moment_id']}"):
        gameweek_bundle(session_id="s" * 16, gameweek=gw, league={}, climaxes=[climax], stakes=[], log=[], table=[])


def test_a_fixture_that_stopped_before_any_climax_keeps_no_frames_and_the_rest_still_render(
    gw: Gameweek, monkeypatch: pytest.MonkeyPatch
) -> None:
    full = demo()[1]
    keep = {e["moment_id"] for e in full["climaxes"] if e["fixture_id"] != "fx_2"}
    stopped = gameweek_bundle(
        session_id="s" * 16, gameweek=gw, league=full["league"], table=full["table"],
        climaxes=[e for e in full["climaxes"] if e["moment_id"] in keep],
        stakes=[m for m in full["stakes"] if m[0]["moment_id"] in keep],
        log=[c for c in full["log"] if c.get("moment_id", "") in keep or "moment_id" not in c],
    )  # fmt: skip
    assert next(f for f in stopped["fixtures"] if f["fixture_id"] == "fx_2")["frames"] == []
    monkeypatch.setattr(render, "FPS", 1)
    calls = spy_positions(monkeypatch)
    clips: list[str | None] = []
    for manager in MANAGERS:
        recap = plan(stopped, manager)
        assert "fx_2" not in {c.fixture_id for c in recap.clips}
        list(render.frames(stopped, recap))
        clips += [c.fixture_id for c in recap.clips]
    assert set(clips) == {"fx_1", "fx_3"}  # the other two still make clips
    assert set(calls) == {"fx_1", "fx_3"}  # and their clips were drawn


@pytest.mark.parametrize("v", [3, None, "2"])
def test_the_job_refuses_a_bundle_version_it_does_not_know(bundle: dict[str, Any], v: Any, tmp_path: Path) -> None:
    bundle["v"] = v
    with pytest.raises(ValueError, match="unknown bundle version"):
        worker.render_all(bundle, tmp_path)


@cache
def v1_bundle() -> dict[str, Any]:
    made: dict[str, Any] = {}

    async def no_sleep(_: float) -> None:
        await asyncio.sleep(0)

    async def at_full_time(session: Session) -> None:
        made["bundle"] = make_bundle(session)

    s = Session(seed=DEMO_SEED, manager="mgr_3", style="casual", lang="en", speed=30, sleep=no_sleep,
                on_full_time=at_full_time)  # fmt: skip
    asyncio.run(s.run())
    bundle: dict[str, Any] = made["bundle"]
    return bundle


def test_a_gameweek_bundle_is_smaller_than_one_single_match_bundle(capsys: pytest.CaptureFixture[str]) -> None:
    v1 = len(gzip.compress(json.dumps(v1_bundle()).encode()))
    v2 = len(gzip.compress(json.dumps(demo()[1]).encode()))
    with capsys.disabled():
        print(f"\nrecap bundle, gzipped: v1 single match {v1} bytes, v2 gameweek {v2} bytes")
    assert v2 < v1


@ffmpeg
def test_the_job_renders_all_five_from_a_gameweek_bundle(tmp_path: Path) -> None:
    recorded = tmp_path / "input.json.gz"
    recorded.write_bytes(gzip.compress(json.dumps(demo()[1]).encode()))
    assert worker.main(["--local", str(recorded), str(tmp_path / "out")]) == 0
    videos = sorted((tmp_path / "out").glob("*.mp4"))
    assert [p.name for p in videos] == [f"{m}.mp4" for m in MANAGERS]
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    for video in videos:
        probe = json.loads(subprocess.run(  # noqa: S603
            [ffprobe, "-v", "error", "-show_entries", "stream=codec_name,width,height:format=duration", "-of",
             "json", str(video)], check=True, capture_output=True, text=True).stdout)  # fmt: skip
        stream = probe["streams"][0]
        assert (stream["codec_name"], stream["width"], stream["height"]) == ("h264", 1080, 1920)
        # Three clips of 4.5 s are 112 whole frames each, not 112.5, so the shortest recap is 19.92 s, single
        # match or gameweek alike (render.frames).
        assert 19.9 <= float(probe["format"]["duration"]) <= 30, video.name


def test_a_moment_worth_0_ip_is_labelled_by_the_league_move_never_plus_0() -> None:
    # The demo match on develop already picked these: mgr_4 fell from 3rd to 4th at m_03 without scoring.
    single, gameweek = v1_bundle(), demo()[1]
    clips = [c for b in (single, gameweek) for m in MANAGERS for c in plan(b, m).clips]
    assert not [c for c in clips if c.points.startswith(("+0 ", "0 ", "-0 "))]
    m_03 = next(c for c in plan(single, "mgr_4").clips if c.moment_id == "m_03")
    assert m_03.points == "Puesto en la liga 3 → 4"  # mgr_4 is Leo, in Spanish
    assert m_03.caption == ""  # no phone text of his own: the card does not repeat the line
    jo = next(c for c in plan(gameweek, "mgr_1").clips if c.moment_id == "m_01")
    assert jo.points == "League position 2 → 3"


def test_every_club_shows_against_the_pitch_and_kestlebury_against_every_other_club() -> None:
    colours = {c["short_name"]: render._rgb(c["colour"]) for c in CLUBS}
    assert all(math.dist(rgb, render.GRASS) >= 90 for rgb in colours.values()), colours
    assert all(math.dist(colours["KES"], rgb) >= 110 for club, rgb in colours.items() if club != "KES")


def draw_calls(render_module: Any, bundle: dict[str, Any], recap: Any) -> str:
    """A hash of every drawing call a recap makes, in order, with the frame count. Text is measured by a stand-in
    font, so the hash is the same on any machine whatever fonts it has."""
    calls: list[str] = []

    def record(name: str) -> Any:
        return lambda self, *args, **kwargs: calls.append(f"{name}{args}{sorted(kwargs.items())}")

    def textlength(self: Any, text: str, font: Any = None, *args: Any, **kwargs: Any) -> float:
        return float(len(text) * font.size * 0.55)

    with pytest.MonkeyPatch.context() as mp:
        for name in ("text", "ellipse", "rounded_rectangle", "rectangle", "line"):
            mp.setattr(ImageDraw.ImageDraw, name, record(name))
        mp.setattr(ImageDraw.ImageDraw, "textlength", textlength)
        mp.setattr(render_module, "font", lambda size, bold=False: types.SimpleNamespace(size=size, bold=bold))
        count = sum(1 for _ in render_module.frames(bundle, recap))
    return hashlib.sha256(f"{count}{calls}".encode()).hexdigest()


# Recorded from develop's render and plan (05a5fff) on the demo match's v1 bundle, before gameweek recaps. Jo's
# recap has no moment worth 0 IP, so the league-move label (#154) does not touch it.
V1_MGR_1 = "c8c60bfa2b83db9672f8f94d1784d03502111092ead7e3dd0041d689a74a98f5"


def test_a_single_match_recap_draws_exactly_what_it_drew_before_gameweeks() -> None:
    bundle = v1_bundle()
    assert draw_calls(render, bundle, plan(bundle, "mgr_1")) == V1_MGR_1
