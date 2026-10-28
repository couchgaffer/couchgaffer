// #119: a Spanish friend's phone writes its own titles in Spanish: the moment cards, the half-time and full-time
// summaries and the recap card. The backend keeps writing them in English, because recap/plan.py finds the
// full-time summary by its English headline; an English phone shows exactly what it showed before.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { FeedCard } from "../components/Cards";
import { moments, PhoneApp, type MatchState } from "../components/PhoneApp";
import type { Manager } from "./api";
import type { Cue, MatchEvent } from "./messages";
import { cardTitle, phoneLang } from "./phoneText";

const players = new Map([["p_home_9", "Dylan Crossfield"], ["p_away_1", "Tom Ashcombe"], ["p_home_7", "Lasse Vikholm"]]);
let seq = 1;
const shot = (moment: string, outcome: string, over: Partial<MatchEvent> = {}): MatchEvent =>
  ({ seq: seq++, t: seq * 60_000, type: "shot", team: "home", x: 0.9, y: 0.5, player: "p_home_9", outcome,
     opponent: "p_away_1", moment_id: moment, climax: true, ...over }) as MatchEvent; // prettier-ignore

const EVENTS: MatchEvent[] = [
  shot("m_goal", "goal"),
  shot("m_save", "saved", { player: "p_home_7" }),
  shot("m_chance", "off_target"),
  shot("m_yellow", "yellow", { type: "card" }),
  shot("m_red", "red", { type: "card" }),
  shot("m_other", "won", { type: "foul" }),
];

const card = (kind: Cue["kind"], headline: string, over: Partial<Cue> = {}): Cue => ({
  cue_id: `c_${seq}`, seq: seq++, screen: "phone:mgr_4", show_at: 1_000, min_dwell_ms: 4000, expires_at: 9_000_000,
  kind, source: "code", payload: { headline, manager: "mgr_4" }, ...over,
}); // prettier-ignore

const CUES: Cue[] = [
  card("stat", "+3 IP", { moment_id: "m_goal" }),
  card("stat", "+1 IP", { moment_id: "m_save" }),
  card("summary", "Half time", { show_at: 2_000 }),
  card("summary", "Full time", { show_at: 3_000 }),
  { ...card("recap_ready", "Your recap is ready", { show_at: 4_000 }),
    payload: { headline: "Your recap is ready", recap_url: "https://example.test/r.mp4", manager: "mgr_4" } },
]; // prettier-ignore

const manager = (lang: string): Manager =>
  ({ id: "mgr_4", display_name: "Lucía", is_bot: false, style: "casual", lang, squad: [], captain: "p_home_9",
     points: 0 }) as unknown as Manager; // prettier-ignore

const match: MatchState = {
  meta: null, events: EVENTS, frames: [], t: 5_000, players, managers: new Map([["mgr_4", "Lucía"]]), rows: [],
  moves: new Map(), score: { home: 0, away: 0, minute: 90, phase: "full_time", xg: { home: 0, away: 0 }, lastGoal: null },
} as unknown as MatchState; // prettier-ignore

const phone = (lang: string) =>
  renderToStaticMarkup(<PhoneApp manager={manager(lang)} cues={CUES} style="casual" match={match} judge={null} />);

const whats = (lang: string) => [...moments(EVENTS, players, phoneLang(lang)).values()].map((m) => m.what);

describe("a Spanish friend's phone (#119)", () => {
  it("names each key moment in Spanish", () => {
    expect(whats("es")).toEqual(["Gol", "Parada", "Ocasión", "Tarjeta amarilla", "Tarjeta roja", "Jugada clave"]);
    const html = phone("es");
    expect(html).toContain('<span class="fc-title" lang="es">Gol<small>Dylan Crossfield</small></span>');
  });

  it("says who stopped the shot in Spanish, and who the keeper is when the name is missing", () => {
    const save = moments(EVENTS, players, "es").get("m_save");
    expect(save).toEqual({ what: "Parada", who: "Tom Ashcombe para el disparo de Lasse Vikholm" });
    const unnamed = moments([shot("m_x", "saved", { opponent: undefined })], players, "es").get("m_x");
    expect(unnamed?.who).toBe("El portero para el disparo de Dylan Crossfield");
    expect(phone("es")).toContain("<small>Tom Ashcombe para el disparo de Lasse Vikholm</small>");
  });

  it("calls a moment with no known title a Jugada clave, not a Key moment", () => {
    const html = renderToStaticMarkup(<FeedCard cue={card("stat", "+1 IP")} style="casual" ownerLang="es" />);
    expect(html).toContain(">Jugada clave</span>");
    expect(html).not.toContain("Key moment");
  });

  it("heads the half-time and full-time summaries Descanso and Final del partido, as the backend's Spanish says it", () => {
    const html = phone("es");
    expect(html).toContain('<span class="fc-title" lang="es">Descanso</span>');
    expect(html).toContain('<span class="fc-title" lang="es">Final del partido</span>');
    expect(html).not.toMatch(/Half time|Full time/);
  });

  it("says the recap is ready, and offers to play it, in Spanish", () => {
    const html = phone("es");
    expect(html).toContain('<span class="fc-title" lang="es">Tu resumen está listo</span>');
    expect(html).toMatch(/<a class="recap-button"[^>]*lang="es"><span aria-hidden="true">▶<\/span> Ver tu resumen<\/a>/);
    expect(html).not.toMatch(/Your recap is ready|Watch your recap/);
  });

  it("shows a summary title it has no Spanish for as the backend wrote it, marked as English", () => {
    const extra = card("summary", "Extra time");
    expect(cardTitle(extra, "es")).toEqual({ text: "Extra time", lang: "en" });
    expect(cardTitle(card("summary", "Full time"), "es")).toEqual({ text: "Final del partido", lang: "es" });
    const html = renderToStaticMarkup(<FeedCard cue={extra} style="casual" ownerLang="es" />);
    expect(html).toContain('<span class="fc-title" lang="en">Extra time</span>'); // the phone's root says es (#127)
  });

  it("treats a regional Spanish tag as Spanish", () => {
    expect(phoneLang("es-ES")).toBe("es");
    expect(whats("es-ES")[0]).toBe("Gol");
  });

  it("keeps the points chip in the friend's language (#108), as a plain es even from a regional tag", () => {
    expect(phone("es")).toContain('<span class="points-chip up" lang="es">+3 IP</span>');
    expect(phone("es-ES")).toContain('<span class="points-chip up" lang="es">+3 IP</span>');
  });
});

describe("an English friend's phone (#119)", () => {
  it("shows the same cards exactly as before", () => {
    expect(whats("en")).toEqual(["Goal", "Save", "Chance", "Yellow card", "Red card", "Key moment"]);
    const html = phone("en");
    expect(html).toContain('<span class="fc-title">Goal<small>Dylan Crossfield</small></span>');
    expect(html).toContain("<small>Tom Ashcombe denies Lasse Vikholm</small>");
    expect(html).toContain('<span class="fc-title">Half time</span>');
    expect(html).toContain('<span class="fc-title">Full time</span>');
    expect(html).toContain('<span class="fc-title">Your recap is ready</span>');
    expect(html).toMatch(/<a class="recap-button"[^>]*><span aria-hidden="true">▶<\/span> Watch your recap<\/a>/);
    expect(html).not.toMatch(/lang="es"/);
  });
});

describe("phoneLang", () => {
  it("reads es and its regional tags as Spanish, and nothing else", () => {
    expect(["es", "es-ES", "ES-mx"].map(phoneLang)).toEqual(["es", "es", "es"]);
    expect(["en", "est", "esoteric", undefined].map(phoneLang)).toEqual(["en", "en", "en", "en"]);
  });
});
