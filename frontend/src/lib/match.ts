// What the scoreboard and the pitch tags show at a match time, derived from the events.
import type { MatchEvent } from "./messages";
import { eventWord, words, type PhoneLang } from "./phoneText";

type Side = "home" | "away";

export interface Scoreboard {
  home: number;
  away: number;
  minute: number;
  phase: "pre" | "live" | "half_time" | "full_time";
  xg: Record<Side, number>; // each side's expected goals so far
  lastGoal: { player: string; minute: number; side: Side } | null;
}

/** A match minute as football writes it: the first minute is 1'. */
export function minuteLabel(t: number): string {
  return `${Math.floor(t / 60_000) + 1}'`;
}

export function scoreboard(events: readonly MatchEvent[], t: number): Scoreboard {
  const past = events.filter((e) => e.t <= t);
  const goals = past.filter((e) => e.type === "shot" && e.outcome === "goal");
  const xg = (side: Side) =>
    Math.round(past.reduce((sum, e) => sum + (e.type === "shot" && e.team === side ? (e.shot?.xg ?? 0) : 0), 0) * 100) /
    100;
  const last = goals.at(-1);
  const whistle = [...past].reverse().find((e) => e.type === "kickoff" || e.type === "half_time" || e.type === "full_time");
  return {
    home: goals.filter((e) => e.team === "home").length,
    away: goals.filter((e) => e.team === "away").length,
    minute: Math.floor(t / 60_000) + 1,
    phase: past.length === 0 ? "pre" : whistle?.type === "full_time" ? "full_time" : whistle?.type === "half_time" ? "half_time" : "live",
    xg: { home: xg("home"), away: xg("away") },
    lastGoal: last?.player ? { player: last.player, minute: Math.floor(last.t / 60_000) + 1, side: last.team } : null,
  };
}

export interface TickerItem {
  seq: number;
  t: number;
  minute: number;
  kind: "goal" | "save" | "yellow_card" | "red_card" | "kickoff" | "half_time" | "full_time";
  side: Side;
  player?: string; // the scorer, the keeper who saved, the booked player
  against?: string; // for a save: whose shot it was
  xg?: number;
  fixture?: string; // in a gameweek, the fixture it happened in
}

/** The match's key moments up to t, newest first, for the ticker: goals, the key moments' saves, cards and
 * the whistles. */
export function ticker(events: readonly MatchEvent[], t: number): TickerItem[] {
  const items: TickerItem[] = [];
  for (const e of events) {
    if (e.t > t) continue;
    const base = { seq: e.seq, t: e.t, minute: Math.floor(e.t / 60_000) + 1, side: e.team };
    if (e.type === "shot" && e.outcome === "goal") items.push({ ...base, kind: "goal", player: e.player, xg: e.shot?.xg });
    else if (e.type === "shot" && e.outcome === "saved" && e.climax) {
      const keeper = e.team === "home" ? "away" : "home"; // a save is the keeper's side's moment
      items.push({ ...base, side: keeper, kind: "save", player: e.opponent, against: e.player, xg: e.shot?.xg });
    }
    else if (e.type === "card") items.push({ ...base, kind: e.outcome === "red" ? "red_card" : "yellow_card", player: e.player });
    else if (e.type === "kickoff" || e.type === "half_time" || e.type === "full_time") {
      if (!(e.type === "kickoff" && items.some((i) => i.kind === "kickoff"))) items.push({ ...base, kind: e.type });
    }
  }
  return items.sort((a, b) => b.t - a.t || b.seq - a.seq); // newest first, whatever order they arrived in
}

/** A gameweek's key moments up to t from every fixture, newest first, each tagged with its fixture (#94). Whistles
 * too: each fixture blows its own, so "Full time" in one is not read as the end of the others. */
export function gameweekTicker(fixtures: readonly { id: string; events: readonly MatchEvent[] }[], t: number): TickerItem[] {
  const all = fixtures.flatMap((f) => ticker(f.events, t).map((item) => ({ ...item, fixture: f.id })));
  return all.sort((a, b) => b.t - a.t || b.seq - a.seq);
}

export interface Tag {
  player: string;
  label: string; // a fact about the moment, for example "shot, 104 km/h"
}

const TAG_MS = 120_000; // match time a tag stays up: about 4 real seconds at 30x

/** Name tags for the players in the latest key moment's climax (rule 4.1 ii, player identification), in the phone's
 * language (#127). */
export function tags(events: readonly MatchEvent[], t: number, lang: PhoneLang = "en"): Tag[] {
  const w = words(lang).tag;
  const climax = [...events].reverse().find((e) => e.climax && e.t <= t && t - e.t < TAG_MS);
  if (!climax?.player) return [];
  const speed = climax.shot ? `${climax.shot.speed_km_h.toFixed(0)} km/h` : undefined;
  const what =
    climax.type === "shot" ? (climax.outcome === "goal" ? w.goal : w.shot)
    : climax.type === "card" ? w.card(climax.outcome) : eventWord(climax.type, lang); // prettier-ignore
  const out: Tag[] = [{ player: climax.player, label: speed ? `${what}, ${speed}` : what }];
  if (climax.type === "shot" && climax.outcome === "saved" && climax.opponent) {
    out.push({ player: climax.opponent, label: w.save });
  }
  const sprint = events.find((e) => e.moment_id === climax.moment_id && e.t <= climax.t && e.player_speed_km_h);
  if (sprint?.player && sprint.player_speed_km_h) {
    const label = `${w.sprint}, ${sprint.player_speed_km_h.toFixed(0)} km/h`;
    const same = out.find((tag) => tag.player === sprint.player);
    if (same) same.label += `; ${label}`;
    else out.push({ player: sprint.player, label });
  }
  return out;
}
