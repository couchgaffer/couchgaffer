import { describe, expect, it } from "vitest";

import { stillArmed } from "./api";
import { PlaybackClock } from "./clock";
import { current, groupCard, hostReason, latestTable, phoneFeed, rankMoves, writtenBy, writtenByAI } from "./cues";
import { positionsAt, pruneFrames } from "./frames";
import { minuteLabel, scoreboard, tags, ticker } from "./match";
import { parseMessage, type Cue, type HostDecision, type MatchEvent, type MatchFrame, type WsMessage } from "./messages";
import { backoffMs, gaveUp, screenUrl } from "./socket";
import { empty, receive } from "./useMatch";
import { bidLabel } from "../components/Trace";

const SPEED = 30;
const DELAY = 5000; // real ms, so 150,000 ms of match time

describe("PlaybackClock", () => {
  it("runs playback_delay_ms behind the stream, at match speed", () => {
    const clock = new PlaybackClock(SPEED, DELAY);
    clock.observe(300_000, 10_000);
    expect(clock.at(10_000)).toBe(150_000);
    clock.observe(303_000, 10_100);
    expect(clock.at(10_150)).toBe(303_000 + 50 * SPEED - 150_000);
  });

  it("waits at kick-off until the delay has passed, and never runs backwards", () => {
    const clock = new PlaybackClock(SPEED, DELAY);
    expect(clock.at(0)).toBe(0);
    clock.observe(0, 1000);
    expect(clock.at(1000)).toBe(0);
    clock.observe(200_000, 2000);
    const shown = clock.at(9000);
    clock.observe(150_000, 9100); // a late, older message
    expect(clock.at(9100)).toBeGreaterThanOrEqual(shown);
  });

  it("stops extrapolating when the stream stalls, and plays out the end once it is over", () => {
    const clock = new PlaybackClock(SPEED, DELAY);
    clock.observe(600_000, 0);
    expect(clock.at(60_000)).toBe(600_000 + 1000 * SPEED - 150_000);
    clock.end();
    expect(clock.at(60_000)).toBe(600_000);
  });
});

const frame = (t: number, x: number): MatchFrame => ({
  t,
  possession: "home",
  ball: { x, y: 0.5 },
  players: [{ id: "p_home_9", x, y: 0.5 }],
});

describe("positionsAt", () => {
  const frames = [frame(0, 0), frame(3000, 0.3), frame(6000, 0.6)];

  it("interpolates between frames and holds the ends", () => {
    expect(positionsAt(frames, 1500)?.ball.x).toBeCloseTo(0.15);
    expect(positionsAt(frames, 4500)?.players.get("p_home_9")?.x).toBeCloseTo(0.45);
    expect(positionsAt(frames, -100)?.ball.x).toBe(0);
    expect(positionsAt(frames, 99_000)?.ball.x).toBeCloseTo(0.6);
    expect(positionsAt([], 0)).toBeNull();
  });

  it("keeps the frames still needed", () => {
    expect(pruneFrames(frames, 5000).map((f) => f.t)).toEqual([0, 3000, 6000]);
    expect(pruneFrames([...frames, frame(9000, 0.9)], 7000).map((f) => f.t)).toEqual([3000, 6000, 9000]);
  });
});

let seq = 0;
const cue = (over: Partial<Cue> & Pick<Cue, "kind">): Cue => ({
  cue_id: `c_${seq}`,
  seq: seq++,
  screen: "group",
  show_at: 100_000,
  min_dwell_ms: 6000,
  expires_at: 340_000,
  source: "code",
  payload: { headline: "Saved!" },
  ...over,
});

describe("groupCard", () => {
  it("shows a card at show_at and drops it at expires_at", () => {
    const stat = cue({ kind: "stat" });
    expect(groupCard([stat], 99_999, 0, null).card).toBeNull();
    const up = groupCard([stat], 100_000, 0, null);
    expect(up.card?.cue_id).toBe(stat.cue_id);
    expect(groupCard([stat], 340_000, 99_000, up.slot).card).toBeNull();
  });

  it("upgrades a stat card to its explanation in place, without restarting the dwell", () => {
    const stat = cue({ kind: "stat" });
    const first = groupCard([stat], 100_000, 1000, null);
    const explanation = cue({ kind: "explanation", replaces: stat.cue_id, source: "manager" });
    const next = groupCard([stat, explanation], 101_000, 2000, first.slot);
    expect(next.card?.cue_id).toBe(explanation.cue_id);
    expect(next.slot?.since).toBe(1000);
  });

  it("keeps the card up for its minimum dwell before the next moment takes over", () => {
    const a = cue({ kind: "stat" });
    const shown = groupCard([a], 100_000, 0, null);
    const b = cue({ kind: "stat", show_at: 200_000 });
    expect(groupCard([a, b], 200_000, 3000, shown.slot).card?.cue_id).toBe(a.cue_id);
    expect(groupCard([a, b], 200_000, 6000, shown.slot).card?.cue_id).toBe(b.cue_id);
  });

  it("never puts a table or a host reason in the card slot", () => {
    const table = cue({ kind: "table", payload: { headline: "Table", table: [] } });
    expect(groupCard([table], 100_000, 0, null).card).toBeNull();
  });
});

describe("other screens' cues", () => {
  it("finds the host's reason for the moment on screen", () => {
    const reason = cue({ kind: "host_reason", source: "host", moment_id: "m_03" });
    expect(hostReason([reason], "m_03", 100_000)?.cue_id).toBe(reason.cue_id);
    expect(hostReason([reason], "m_04", 100_000)).toBeUndefined();
    expect(hostReason([reason], "m_03", 99_000)).toBeUndefined();
  });

  it("keeps the newest due table", () => {
    const row = (manager: string, rank: number) => ({ manager, points: 200, rank });
    const early = cue({ kind: "table", payload: { headline: "T", table: [row("mgr_1", 1)] } });
    const later = cue({ kind: "table", show_at: 200_000, payload: { headline: "T", table: [row("mgr_1", 2)] } });
    expect(latestTable([early, later], 150_000)?.[0]?.rank).toBe(1);
    expect(latestTable([early, later], 200_000)?.[0]?.rank).toBe(2);
    expect(latestTable([], 0)).toBeNull();
  });

  it("keeps each rank change until the next table, not for one frame (#49)", () => {
    const row = (manager: string, rank: number) => ({ manager, points: 200, rank });
    const before = cue({ kind: "table", payload: { headline: "T", table: [row("mgr_1", 2), row("mgr_2", 1)] } });
    const after = cue({ kind: "table", show_at: 200_000,
                        payload: { headline: "T", table: [row("mgr_1", 1), row("mgr_2", 2)] } }); // prettier-ignore
    expect(rankMoves([before, after], 150_000).get("mgr_1") ?? 0).toBe(0); // one table: nothing to compare
    for (const t of [200_000, 900_000]) {
      // a render later, and much later: still shown until the next table replaces it
      const moves = rankMoves([before, after], t);
      expect([moves.get("mgr_1"), moves.get("mgr_2")]).toEqual([1, -1]);
    }
  });

  it("feeds a phone newest first, with upgrades in place", () => {
    const stat = cue({ kind: "stat", screen: "phone:mgr_1" });
    const explanation = cue({ kind: "explanation", replaces: stat.cue_id, screen: "phone:mgr_1", source: "manager" });
    const summary = cue({ kind: "summary", screen: "phone:mgr_1", show_at: 2_700_000, source: "manager" });
    expect(phoneFeed([stat, explanation, summary], 3_000_000).map((c) => c.kind)).toEqual(["summary", "explanation"]);
    expect(phoneFeed([stat, explanation, summary], 100_000).map((c) => c.kind)).toEqual(["explanation"]);
    expect(current([stat, explanation])).toEqual([explanation]);
  });

  it("marks agent-written text as AI, and code's cards and fallbacks as not", () => {
    expect(writtenByAI(cue({ kind: "explanation", source: "manager" }))).toBe(true);
    expect(writtenByAI(cue({ kind: "host_reason", source: "host" }))).toBe(true);
    expect(writtenByAI(cue({ kind: "explanation", source: "fallback" }))).toBe(false);
    expect(writtenByAI(cue({ kind: "stat", source: "code" }))).toBe(false);
  });

  it("labels a group card by who wrote its line, not by who decided the screen (#138)", () => {
    const line = (source: Cue["source"], body_source: "manager" | "code") =>
      cue({ kind: "explanation", source, payload: { headline: "Priya: Saved!", body: "x", body_source } });
    expect(writtenBy(line("fallback", "manager"))).toBe("ai"); // the host fell back; the agent wrote the pitch
    expect(writtenBy(line("scorer", "code"))).toBe("code"); // a clear call on code's plain line
    expect(writtenBy(line("host", "code"))).toBe("code");
    expect(writtenBy(cue({ kind: "explanation", source: "fallback" }))).toBe("code"); // no body_source: as before
    expect(writtenBy(cue({ kind: "explanation", source: "scorer" }))).toBe(null);
  });

  it("says in Behind the scenes when code's line stood in for an agent's pitch (#138)", () => {
    expect(bidLabel({ status: "retried", pitch_source: "code" })).toBe("retried, plain pitch");
    expect(bidLabel({ status: "ok", pitch_source: "manager" })).toBe("ok");
    expect(bidLabel({ status: "fallback", pitch_source: "code" })).toBe("plain stat");
  });
});

const event = (over: Partial<MatchEvent> & Pick<MatchEvent, "t" | "type">): MatchEvent => ({
  seq: 0,
  team: "home",
  x: 0.5,
  y: 0.5,
  ...over,
});

describe("scoreboard and tags", () => {
  const events = [
    event({ t: 0, type: "kickoff" }),
    event({ t: 60_000, type: "carry", player: "p_home_8", player_speed_km_h: 33.6, moment_id: "m_01" }),
    event({ t: 61_000, type: "shot", player: "p_home_9", outcome: "goal", climax: true, moment_id: "m_01",
            shot: { xg: 0.4, speed_km_h: 104, body_part: "right_foot" } }),
    event({ t: 200_000, type: "shot", team: "away", player: "p_away_9", opponent: "p_home_1", outcome: "saved",
            climax: true, moment_id: "m_02", shot: { xg: 0.2, speed_km_h: 90, body_part: "head" } }),
    event({ t: 5_400_000, type: "full_time" }),
  ];

  it("counts goals and knows the phase", () => {
    expect(scoreboard(events, -1).phase).toBe("pre");
    expect(scoreboard(events, 61_000)).toMatchObject({ home: 1, away: 0, minute: 2, phase: "live" });
    expect(scoreboard(events, 5_400_000).phase).toBe("full_time");
  });

  it("adds up each side's xG and remembers the last goal, as a broadcast does", () => {
    const at = scoreboard(events, 300_000);
    expect(at.xg).toEqual({ home: 0.4, away: 0.2 });
    expect(at.lastGoal).toEqual({ player: "p_home_9", minute: 2, side: "home" });
    expect(scoreboard(events, 30_000).lastGoal).toBeNull();
  });

  it("knows half time between the whistle and the second half's kick-off", () => {
    const halves = [event({ t: 0, type: "kickoff" }), event({ t: 2_760_000, type: "half_time" }),
                    event({ t: 2_850_000, type: "kickoff" })]; // prettier-ignore
    expect(scoreboard(halves, 2_800_000).phase).toBe("half_time");
    expect(scoreboard(halves, 2_900_000).phase).toBe("live");
  });

  it("lists the key moments newest first: goals, saves, cards and whistles", () => {
    const items = ticker([...events, event({ t: 400_000, type: "card", team: "away", player: "p_away_4", outcome: "yellow" })],
                         5_400_000); // prettier-ignore
    expect(items.map((i) => [i.kind, i.minute, i.player])).toEqual([
      ["full_time", 91, undefined],
      ["yellow_card", 7, "p_away_4"],
      ["save", 4, "p_home_1"],
      ["goal", 2, "p_home_9"],
      ["kickoff", 1, undefined],
    ]);
    expect(items.find((i) => i.kind === "save")).toMatchObject({ against: "p_away_9", side: "home" }); // the keeper's
    expect(ticker(events, 100_000).map((i) => i.kind)).toEqual(["goal", "kickoff"]); // nothing from the future
  });

  it("names a card by its colour", () => {
    const booked = [event({ t: 600_000, type: "card", player: "p_away_5", outcome: "red", climax: true, moment_id: "m_04" })];
    expect(tags(booked, 610_000)).toEqual([{ player: "p_away_5", label: "red card" }]);
  });

  it("writes a match minute the way football does", () => {
    expect([minuteLabel(0), minuteLabel(61_000), minuteLabel(5_400_000)]).toEqual(["1'", "2'", "91'"]);
  });

  it("tags the key moment's players, with the shot and sprint speeds", () => {
    expect(tags(events, 62_000)).toEqual([
      { player: "p_home_9", label: "goal, 104 km/h" },
      { player: "p_home_8", label: "sprint, 34 km/h" },
    ]);
    expect(tags(events, 200_000).map((t) => t.label)).toEqual(["shot, 90 km/h", "save"]);
    expect(tags(events, 400_000)).toEqual([]);
  });
});

describe("parseMessage", () => {
  it("accepts the envelope and refuses anything else", () => {
    expect(parseMessage('{"v":1,"type":"frame","data":{}}')?.type).toBe("frame");
    for (const bad of ["nope", "null", '{"v":2,"type":"frame","data":{}}', '{"v":1,"type":"x","data":{}}']) {
      expect(parseMessage(bad)).toBeNull();
    }
  });
});

describe("reconnecting", () => {
  it("backs off, and gives up on a session the server no longer has", () => {
    expect([0, 1, 2, 3, 4].map(backoffMs)).toEqual([500, 1000, 2000, 4000, 5000]);
    expect(gaveUp(1008, 0)).toBe(true); // the server refused this screen
    expect(gaveUp(1006, 2)).toBe(false); // a network blip: keep trying
    expect(gaveUp(1006, 6)).toBe(true); // a rejected handshake arrives as 1006, again and again
    expect(gaveUp(1000, 0)).toBe(true); // the server closed it: the session is over and gone
    expect(gaveUp(1012, 0)).toBe(false); // another server stopped serving it mid-match (#95): reconnect
  });
});

describe("a screen's history after it joins or reconnects", () => {
  it("keeps each cue, decision and event once, so a replayed goal does not count twice", () => {
    const store = empty();
    const goal = event({ t: 600_000, type: "shot", outcome: "goal", team: "home", seq: 40 });
    const decision = { moment_id: "m_02", t: 600_000, chosen_manager: "mgr_1", source: "scorer", close_call: false,
                       scores: [{ manager: "mgr_1", score: 1, airtime_share: 0, overruled_count: 0 }] }; // prettier-ignore
    const history: WsMessage[] = [
      { v: 1, type: "event", data: goal },
      { v: 1, type: "cue", data: cue({ kind: "stat" }) },
      { v: 1, type: "decision", data: decision as HostDecision },
    ];
    for (const join of [1, 2]) history.forEach((m) => receive(store, "group", true, m, join)); // join, then reconnect
    expect([store.events.length, store.decisions.length, store.cues.get("group")?.length]).toEqual([1, 1, 1]);
    expect(scoreboard(store.events, 700_000)).toMatchObject({ home: 1, away: 0 });
  });
});

describe("a phone that scans the QR code after full time (#25)", () => {
  it("is sent the state on joining and shows its recap link within the playback delay", () => {
    const clock = new PlaybackClock(SPEED, DELAY);
    const fullTime = 5_582_928;
    clock.observe(fullTime, 1000); // the replayed full-time whistle and the latest frame
    clock.end();
    const recap = cue({ kind: "recap_ready", screen: "phone:mgr_1", show_at: fullTime,
                        payload: { headline: "Your recap is ready", recap_url: "https://example.test/r.mp4" } }); // prettier-ignore
    expect(phoneFeed([recap], clock.at(1000))).toEqual([]);
    expect(phoneFeed([recap], clock.at(1000 + DELAY))).toEqual([recap]);
  });
});

describe("a screen's address", () => {
  it("asks for cues only when the page has the match from another screen (#49)", () => {
    const loc = { protocol: "https:", host: "example.test" };
    expect(screenUrl(loc, "s1", "group", null, false)).toBe("wss://example.test/ws/s1/group");
    expect(screenUrl(loc, "s1", "phone:mgr_2", "t k", true)).toBe("wss://example.test/ws/s1/phone:mgr_2?token=t%20k&cues=only");
  });
});

describe("a knocked-out agent (#90)", () => {
  const decided = (t: number, managers: string[], arm?: number) =>
    ({ t, scores: managers.map((manager) => ({ manager })), ...(arm ? { knocked_out: { arm, manager: "mgr_2" } } : {}) }) as unknown as HostDecision;

  it("stays armed when a moment already in flight finishes with that manager in it", () => {
    const k = { manager: "mgr_2", arm: 2 };
    const inFlight = decided(600_000, ["mgr_2", "mgr_3"]); // started before the press: the server kept it
    const earlierPress = decided(700_000, ["mgr_2"], 1); // spent a previous press, not this one
    expect(stillArmed(k, [inFlight, earlierPress], 900_000)).toBe(true);
  });

  it("clears only when the screens reach the decision carrying its arm id", () => {
    const k = { manager: "mgr_2", arm: 2 };
    const spent = decided(800_000, ["mgr_2", "mgr_4"], 2);
    expect(stillArmed(k, [spent], 799_999)).toBe(true); // decided, but the phone has not shown it yet
    expect(stillArmed(k, [spent], 800_000)).toBe(false);
    expect(stillArmed(null, [], 0)).toBe(false);
  });

  it("clears when a newer press replaced it and that one was spent", () => {
    const k = { manager: "mgr_2", arm: 2 };
    const newer = decided(800_000, ["mgr_1", "mgr_3"], 3); // another tab, or an every-agent press, took the slot
    expect(stillArmed(k, [newer], 799_999)).toBe(true);
    expect(stillArmed(k, [newer], 800_000)).toBe(false);
  });
});
