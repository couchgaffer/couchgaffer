// One session's streams, merged: the first screen carries the match (meta or gameweek, league, frames, events,
// decisions); every screen carries its own cues. Re-renders on animation frames, not on every message. A gameweek's
// three fixtures are kept apart by fixture_id on one clock (#94); a single match is one fixture, filed under
// DEFAULT_FIXTURE because its messages carry no fixture_id.
import { useEffect, useRef, useState } from "react";

import { PlaybackClock } from "./clock";
import { pruneFrames } from "./frames";
import type { Cue, Gameweek, HostDecision, League, MatchEvent, MatchFrame, MatchMeta, WsMessage } from "./messages";
import { openScreen, type Connection } from "./socket";

/** Where a single match's messages, which carry no fixture_id, are filed. A gameweek names its own fixtures in its
 * gameweek message; the contract does not promise that fx_1 is one of them, or the first. */
export const DEFAULT_FIXTURE = "fx_1";

export interface FixtureStore {
  meta: MatchMeta | null;
  frames: MatchFrame[];
  events: MatchEvent[];
  fullTime: boolean; // this fixture has blown full time
}

export interface MatchStore {
  seen: Set<string>; // cue ids and decided moments: a rejoining screen is sent its history again
  gameweek: Gameweek | null; // null for a single match
  fixtures: Map<string, FixtureStore>; // by fixture_id
  events: MatchEvent[]; // every fixture's events, in arrival order
  league: League | null;
  decisions: HostDecision[];
  cues: Map<string, Cue[]>; // by screen
  clock: PlaybackClock | null;
}

export interface MatchView {
  store: MatchStore;
  t: number; // match time on screen
  now: number; // real time of this render
  connection: Connection;
}

/** True the first time a key is seen in this store. */
export function firstTime(store: { seen: Set<string> }, key: string): boolean {
  if (store.seen.has(key)) return false;
  store.seen.add(key);
  return true;
}

/** The fixtures this session plays: those the gameweek message names, or the single match's one. */
export function fixtureIds(s: MatchStore): string[] {
  return s.gameweek ? s.gameweek.fixtures.flatMap((f) => (f.fixture_id ? [f.fixture_id] : [])) : [DEFAULT_FIXTURE];
}

/** The fixture a message belongs to, made on first sight; null for a fixture this session does not play, which is
 * then neither shown nor counted towards the end of the match. In a gameweek every fixture's message names its
 * fixture, so one that names none belongs to none. */
export function fixtureOf(s: MatchStore, id: string | undefined): FixtureStore | null {
  const key = id ?? (s.gameweek ? "" : DEFAULT_FIXTURE);
  if (!fixtureIds(s).includes(key)) {
    if (import.meta.env.DEV) console.warn("[watchparty] message for a fixture this session does not play", key);
    return null;
  }
  const known = s.fixtures.get(key);
  if (known) return known;
  const made: FixtureStore = { meta: null, frames: [], events: [], fullTime: false };
  s.fixtures.set(key, made);
  return made;
}

/** One message from one screen's stream into the store. A screen that reconnects is sent its history again
 * (#25), so cues, decisions and events are kept once each: a replayed goal must not count twice. */
export function receive(s: MatchStore, screen: string, primary: boolean, m: WsMessage, now: number): void {
  if (m.type === "cue") {
    if (firstTime(s, m.data.cue_id)) s.cues.set(screen, [...(s.cues.get(screen) ?? []), m.data]);
    return;
  }
  if (!primary) return;
  if (m.type === "meta") {
    s.clock ??= new PlaybackClock(m.data.speed, m.data.playback_delay_ms ?? 5000);
    const fixture = fixtureOf(s, m.data.fixture_id);
    if (fixture) fixture.meta = m.data;
  } else if (m.type === "gameweek") {
    s.clock ??= new PlaybackClock(m.data.speed, m.data.playback_delay_ms ?? 5000);
    s.gameweek = m.data;
    for (const meta of m.data.fixtures) {
      const fixture = fixtureOf(s, meta.fixture_id);
      if (fixture) fixture.meta = meta;
    }
  } else if (m.type === "league") s.league = m.data;
  else if (m.type === "decision") {
    if (firstTime(s, `decision:${m.data.moment_id}`)) s.decisions.push(m.data);
  } else if (m.type === "frame") {
    const fixture = fixtureOf(s, m.data.fixture_id);
    if (!fixture) return;
    fixture.frames.push(m.data);
    s.clock?.observe(m.data.t, now);
  } else receiveEvent(s, m.data, now);
}

/** A key event, kept once, into its fixture. */
function receiveEvent(s: MatchStore, e: MatchEvent, now: number): void {
  const fixture = fixtureOf(s, e.fixture_id);
  if (!fixture || !firstTime(s, `event:${e.fixture_id ?? DEFAULT_FIXTURE}:${e.seq}`)) return;
  fixture.events.push(e);
  s.events.push(e);
  s.clock?.observe(e.t, now);
  if (e.type === "full_time") fixture.fullTime = true;
  // A gameweek's fixtures blow within about half a minute of each other: play on until the last one has.
  if (fixtureIds(s).every((id) => s.fixtures.get(id)?.fullTime)) s.clock?.end();
}

/** Keep each fixture's frames still needed at t; one fixture's frames never prune another's. */
export function pruneAll(s: MatchStore, t: number): void {
  for (const fixture of s.fixtures.values()) fixture.frames = pruneFrames(fixture.frames, t);
}

export const empty = (): MatchStore => ({
  seen: new Set(),
  gameweek: null,
  fixtures: new Map(),
  events: [],
  league: null,
  decisions: [],
  cues: new Map(),
  clock: null,
});

const FPS_MS = 1000 / 30;

export function useMatch(session: string, screens: string[], token: string | null): MatchView {
  const store = useRef<MatchStore>(empty());
  const [connection, setConnection] = useState<Connection>("connecting");
  const [, setTick] = useState(0);
  const key = screens.join(",");

  useEffect(() => {
    store.current = empty();
    const s = store.current;
    const states = new Map<string, Connection>();
    const handle = (screen: string, primary: boolean) => (m: WsMessage) =>
      receive(s, screen, primary, m, performance.now());
    const closers = key.split(",").map((screen, i) =>
      openScreen(session, screen, screen === "group" ? null : token, i > 0, handle(screen, i === 0), (c) => {
        states.set(screen, c);
        const all = [...states.values()];
        setConnection(all.includes("refused") ? "refused" : all.every((x) => x === "open") ? "open" : c);
      }),
    );
    let raf = 0;
    let last = 0;
    const loop = (now: number) => {
      raf = requestAnimationFrame(loop);
      if (now - last < FPS_MS) return;
      last = now;
      if (s.clock) pruneAll(s, s.clock.at(now));
      setTick((n) => n + 1);
    };
    raf = requestAnimationFrame(loop);
    return () => {
      cancelAnimationFrame(raf);
      closers.forEach((close) => close());
    };
  }, [session, key, token]);

  const now = performance.now();
  return { store: store.current, t: store.current.clock?.at(now) ?? 0, now, connection };
}
