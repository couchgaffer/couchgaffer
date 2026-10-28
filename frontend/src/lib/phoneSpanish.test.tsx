// #127: after #119 a Spanish friend's phone still wrote its own words in English: the tabs, the style hint, the
// empty feed, the Live tab (ticker, pitch, scoreboard) and the table. Each tab is rendered here on a Spanish phone
// and on an English one, which must read exactly as before. Built on contracts/examples/match-meta.json.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { PhoneApp, type MatchState } from "../components/PhoneApp";
import { Ticker } from "../components/Ticker";
import type { Manager } from "./api";
import { scoreboard } from "./match";
import type { Cue, MatchEvent, MatchMeta } from "./messages";
import type { PhoneTab } from "./phoneText";

const META = Object.values(
  import.meta.glob("../../../contracts/examples/match-meta.json", { eager: true, import: "default" }),
)[0] as MatchMeta;
const players = new Map([META.home, META.away].flatMap((side) => side.players.map((p) => [p.id, p.name])));
const MIN = 60_000;

const ev = (seq: number, t: number, type: MatchEvent["type"], over: Partial<MatchEvent> = {}): MatchEvent =>
  ({ seq, t, type, team: "home", x: 0.5, y: 0.5, ...over }) as MatchEvent; // prettier-ignore

// A save at 42', with the keeper p_away_1 (Rhys Mallory) stopping p_home_2 (Reece Holloway), then the whistles.
const SAVE_T = 42 * MIN;
const EVENTS: MatchEvent[] = [
  ev(1, 0, "kickoff"),
  ev(2, SAVE_T, "shot", { player: "p_home_2", opponent: "p_away_1", outcome: "saved", climax: true, moment_id: "m_1",
                         shot: { xg: 0.31, speed_km_h: 104, body_part: "right_foot" } }), // prettier-ignore
  ev(3, 45 * MIN, "half_time"),
  ev(4, 90 * MIN, "full_time"),
];

const manager = (lang: string): Manager =>
  ({ id: "mgr_4", display_name: "Lucía", is_bot: false, style: "casual", lang, squad: [], captain: "p_home_2",
     points: 0 }) as unknown as Manager; // prettier-ignore

const matchAt = (t: number): MatchState => ({
  meta: META, events: EVENTS, frames: [], t, score: scoreboard(EVENTS, t), players,
  managers: new Map([["mgr_4", "Lucía"], ["mgr_1", "Jo"]]),
  rows: [{ manager: "mgr_4", points: 12.5, rank: 1, delta: 2.5 }, { manager: "mgr_1", points: 10, rank: 2 }],
  moves: new Map([["mgr_4", 1], ["mgr_1", -1]]),
}) as MatchState; // prettier-ignore

const phone = (lang: string, tab: PhoneTab, t: number, cues: Cue[] = [], judge: string | null = null) =>
  renderToStaticMarkup(
    <PhoneApp manager={manager(lang)} cues={cues} style="casual" onStyle={() => {}} match={matchAt(t)} judge={judge}
              initialTab={tab} />,
  ); // prettier-ignore

const AT_SAVE = SAVE_T + 1_000;
const HALF = 45 * MIN + 1_000;
const FULL = 90 * MIN + 1_000;

/** An analyst card from the agent, in Spanish, with its English translation, its numbers and a rulebook entry. */
const analyst: Cue = {
  cue_id: "c_9", seq: 9, screen: "phone:mgr_4", show_at: 1_000, min_dwell_ms: 4000, expires_at: 9_000_000,
  kind: "explanation", source: "manager", moment_id: "m_1",
  payload: {
    headline: "+2,5 IP", body: "Tu portero salvó el gol.", body_en: "Your keeper saved the goal.", lang: "es",
    manager: "mgr_4", evidence: [{ id: "e_1", label: "xG, Hollinsford United", value: 0.31, source: { kind: "computed" } }],
    rulebook: [{ id: "r_1", title: "Saves by a goalkeeper" }],
  },
} as Cue; // prettier-ignore
const fallback: Cue = { ...analyst, cue_id: "c_10", seq: 10, source: "fallback",
                        payload: { headline: "+2,5 IP", manager: "mgr_4" } }; // prettier-ignore

const analystPhone = (lang: string) =>
  renderToStaticMarkup(
    <PhoneApp manager={manager(lang)} cues={[analyst, fallback]} style="analyst" match={matchAt(AT_SAVE)} judge={null} />,
  ); // prettier-ignore

describe("a Spanish friend's phone (#127)", () => {
  it("says lang=es at its root, and names its tabs, its style choice and its hint in Spanish", () => {
    const html = phone("es", "you", 0);
    expect(html).toContain('<section class="phone-app casual" aria-label="Teléfono de Lucía" lang="es">');
    expect(html).toContain('role="tablist" aria-label="Secciones"');
    expect(html.match(/role="tab"[^>]*>([^<]*)</g)?.map((m) => m.split(">").at(-1)?.replace("<", ""))).toEqual([
      "Para ti", "En directo", "Clasificación",
    ]); // prettier-ignore
    expect(html).toContain('aria-label="Cómo lo prefieres"');
    expect(html).toContain(">Sencillo</button>");
    expect(html).toContain(">Analista</button>");
    expect(html).toContain('<span class="hint">Una frase sencilla por jugada</span>');
    expect(html).not.toMatch(/For you|>Live<|>Table<|One plain line|Casual|Analyst|Sections|How you like it/);
  });

  it("writes the empty feed in Spanish, for the viewer's own squad and for a friend's", () => {
    const own = phone("es", "you", 0, [], "mgr_4");
    expect(own).toContain("<strong>Tus jugadas aparecen aquí.</strong> Cuando pase algo en tu equipo, este teléfono");
    const friend = phone("es", "you", 0);
    expect(friend).toContain("Cuando pase algo en el equipo de Lucía, este teléfono te explica qué significa");
    expect(friend).not.toMatch(/Your moments land here|When something happens/);
  });

  it("writes its place in the league as Spanish does, with a decimal comma", () => {
    expect(phone("es", "you", AT_SAVE)).toContain('<span class="standing">1.º · 12,5 IP</span>');
  });

  it("shows the Live tab's save in Spanish: ticker, pitch chips, scoreboard and the pitch's name", () => {
    const html = phone("es", "live", AT_SAVE);
    expect(html).toContain("<strong>Parada</strong><small>Rhys Mallory para el disparo de Reece Holloway · xG 0,31</small>");
    expect(html).toContain("<strong>Disparo, 104 km/h</strong><small>Reece Holloway</small>");
    expect(html).toContain("<strong>Parada</strong><small>Rhys Mallory</small>");
    expect(html).toContain("<strong>Saque inicial</strong>");
    expect(html).toContain('<h3 class="section-title">Jugadas clave</h3>');
    expect(html).toContain('aria-label="Campo: Hollinsford United contra Saltmere Rovers"');
    expect(html).toContain('aria-label="Marcador"');
    expect(html).toContain('<span class="xg">0,31 xG</span>');
    expect(html).not.toMatch(/Save|Kick-off|denies|Key moments|Pitch|Score"|0\.31/);
  });

  it("names a sprint in the moment, and a card, in Spanish on the pitch", () => {
    const card = ev(5, SAVE_T, "card", { player: "p_home_3", outcome: "yellow", climax: true, moment_id: "m_2" });
    const sprint = ev(6, SAVE_T - 1_000, "carry", { player: "p_home_3", moment_id: "m_2", player_speed_km_h: 31 });
    const html = renderToStaticMarkup(
      <PhoneApp manager={manager("es")} cues={[]} style="casual" judge={null} initialTab="live"
                match={{ ...matchAt(AT_SAVE), events: [sprint, card] }} />,
    ); // prettier-ignore
    expect(html).toContain("<strong>Tarjeta amarilla; sprint, 31 km/h</strong>");
    expect(html).toContain("<strong>Tarjeta amarilla</strong><small>Joel Okonta</small>");
  });

  it("calls half time Descanso, and full time Fin del partido in the ticker and Final on the pitch and scoreboard", () => {
    const half = phone("es", "live", HALF);
    expect(half).toContain("<strong>Descanso</strong>");
    expect(half).toContain('<span class="pitch-banner">Descanso</span>');
    expect(half).toMatch(/<span class="clock ">Descanso<\/span>/);
    const full = phone("es", "live", FULL);
    expect(full).toContain("<strong>Fin del partido</strong>");
    expect(full).toContain('<span class="pitch-banner">Final</span>');
    expect(full).toMatch(/<span class="clock ">Final<\/span>/);
    expect(half + full).not.toMatch(/Half time|Full time|>HT<|>FT</);
  });

  it("waits for kick-off in Spanish", () => {
    const html = phone("es", "live", -1);
    expect(html).toContain('<p class="empty">Las jugadas clave aparecerán aquí durante el partido.</p>');
    expect(html).toMatch(/<span class="clock ">Empieza pronto<\/span>/);
  });

  it("labels the table and its moves in Spanish", () => {
    const html = phone("es", "table", AT_SAVE);
    expect(html).toContain('aria-label="Clasificación de la miniliga"');
    expect(html).toContain('aria-label="sube"');
    expect(html).toContain('<span class="delta up">+2,5</span><span class="points">12,5<small> IP</small></span>');
    expect(html).toContain('aria-label="baja"');
    expect(html).not.toMatch(/Mini-league table|moved up|moved down/);
  });

  it("writes an analyst card's marks in Spanish, and marks the English it shows as written as English", () => {
    const html = analystPhone("es");
    expect(html).toContain("<summary>Los números que hay detrás (1)</summary>");
    expect(html).toContain('<span lang="en">xG, Hollinsford United</span> <b>0,31</b>');
    expect(html).toContain('<span class="mark rule">Reglamento</span> <span lang="en">Saves by a goalkeeper</span>');
    expect(html).toContain('<p class="translation" lang="en">Your keeper saved the goal.</p>');
    expect(html).toContain('<span class="mark ai" title="Lo escribió un agente de IA. El código comprobó cada número.">IA</span>');
    expect(html).toMatch(/<span class="mark fallback" title="El agente no llegó a tiempo[^"]*">Dato simple<\/span>/);
    expect(html).not.toMatch(/The numbers behind it|>Rulebook<|>AI<|Plain stat/);
  });

  it("calls the viewer Tú, where the backend names the judge's manager You", () => {
    const you = { ...manager("es"), display_name: "You" };
    const match = { ...matchAt(AT_SAVE), managers: new Map([["mgr_4", "You"], ["mgr_1", "Jo"]]) };
    const render = (tab: PhoneTab) =>
      renderToStaticMarkup(<PhoneApp manager={you} cues={[]} style="casual" match={match} judge="mgr_4" initialTab={tab} />);
    expect(render("you")).toContain('aria-label="Tu teléfono"');
    expect(render("you")).toContain('<span class="avatar you" aria-hidden="true">T</span>Tú<span class="standing">');
    expect(render("table")).toContain('<span class="name">Tú</span>');
    expect(render("you") + render("table")).not.toMatch(/>You<|de Tú/);
    const english = renderToStaticMarkup(
      <PhoneApp manager={{ ...you, lang: "en" }} cues={[]} style="casual" match={match} judge="mgr_4" initialTab="table" />,
    ); // prettier-ignore
    expect(english).toContain('<span class="name">You</span>');
  });

  it("tells a friend's Spanish phone the screen went to ti, not to Tú", () => {
    const jo = { ...manager("es"), id: "mgr_1", display_name: "Jo" };
    const match = { ...matchAt(AT_SAVE), managers: new Map([["mgr_4", "You"], ["mgr_1", "Jo"]]) };
    const lost: Cue = { ...fallback, cue_id: "c_11", seq: 11, screen: "phone:mgr_1", kind: "lost_screen", source: "host",
                        payload: { headline: "Shown on your phone", manager: "mgr_1", winner: "mgr_4" } }; // prettier-ignore
    const html = renderToStaticMarkup(<PhoneApp manager={jo} cues={[lost]} style="casual" match={match} judge="mgr_4" />);
    expect(html).toContain("La pantalla fue para ti.");
    expect(html).not.toContain("para Tú");
  });

  it("treats a regional tag the same way", () => {
    expect(phone("es-ES", "live", HALF)).toContain('<span class="pitch-banner">Descanso</span>');
  });

});

describe("an English friend's phone and the group screen (#127)", () => {
  it("read exactly as before, with no lang attribute anywhere", () => {
    const you = phone("en", "you", 0);
    expect(you).toContain('<section class="phone-app casual" aria-label="Lucía&#x27;s phone">');
    expect(you).toContain('role="tablist" aria-label="Sections"');
    expect(you).toContain(">For you</button>");
    expect(you).toContain(">Live</button>");
    expect(you).toContain(">Table</button>");
    expect(you).toContain('aria-label="How you like it"');
    expect(you).toContain('<span class="hint">One plain line a moment</span>');
    expect(you).toContain(
      "<strong>Your moments land here.</strong> When something happens to Lucía&#x27;s squad, this phone explains what it means, a few seconds before the big screen shows it.",
    ); // prettier-ignore
    expect(phone("en", "you", 0, [], "mgr_4")).toContain("When something happens to your squad,");
    expect(phone("en", "you", AT_SAVE)).toContain('<span class="standing">1st · 12.5 IP</span>');
    const live = phone("en", "live", AT_SAVE);
    expect(live).toContain("<strong>Save</strong><small>Rhys Mallory denies Reece Holloway · xG 0.31</small>");
    expect(live).toContain("<strong>Shot, 104 km/h</strong>");
    expect(live).toContain('aria-label="Pitch: Hollinsford United against Saltmere Rovers"');
    expect(live).toContain('<h3 class="section-title">Key moments</h3>');
    expect(live).toContain('<span class="xg">0.31 xG</span>');
    expect(phone("en", "live", HALF)).toContain('<span class="pitch-banner">Half time</span>');
    expect(phone("en", "live", HALF)).toMatch(/<span class="clock ">HT<\/span>/);
    expect(phone("en", "live", FULL)).toContain('<span class="pitch-banner">Full time</span>');
    expect(phone("en", "live", FULL)).toMatch(/<span class="clock ">FT<\/span>/);
    expect(phone("en", "live", -1)).toContain("Key moments will appear here as the match plays.");
    const table = phone("en", "table", AT_SAVE);
    expect(table).toContain('aria-label="Mini-league table"');
    expect(table).toContain('aria-label="moved up"');
    expect(table).toContain('<span class="delta up">+2.5</span><span class="points">12.5<small> IP</small></span>');
    const cards = analystPhone("en");
    expect(cards).toContain("<summary>The numbers behind it (1)</summary>");
    expect(cards).toContain("<span>xG, Hollinsford United</span> <b>0.31</b>");
    expect(cards).toContain('<span class="mark rule">Rulebook</span> <span>Saves by a goalkeeper</span>');
    expect(cards).toContain(">AI</span>");
    expect(cards).toContain(">Plain stat</span>");
    expect(cards).toContain('<span class="points-chip up">+2,5 IP</span>');
    expect(cards).not.toMatch(/ lang="en"/); // its body is Spanish in this fixture, so lang="es" there is right
    for (const html of [you, live, table]) expect(html).not.toMatch(/ lang="/);
    const judge = renderToStaticMarkup(
      <PhoneApp manager={{ ...manager("en"), display_name: "You" }} cues={[]} style="casual" match={matchAt(0)} judge="mgr_4" />,
    ); // prettier-ignore
    expect(judge).toContain('aria-label="Your phone"');
  });

  it("keeps the group screen's ticker English when no language is passed", () => {
    const html = renderToStaticMarkup(<Ticker meta={META} events={EVENTS} t={FULL} names={players} />);
    expect(html).toContain("<strong>Full time</strong>");
    expect(html).toContain("<strong>Half time</strong>");
    expect(html).not.toMatch(/Final|Descanso/);
  });
});
