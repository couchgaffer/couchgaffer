// #153, slice 7 of #94: the screens in a gameweek. The store keeps three fixtures apart on one clock; the group
// screen follows the fixture of the card on screen, derived from the store, so a reload lands on the same fixture
// (#94 cases 19 and 20); a strip of the three scores; ticker items and phone cards say which fixture they are from;
// a single match reads as before. The message log is synthetic, built on contracts/examples/*gameweek*.json.
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fixtureTag, PhoneApp } from "../components/PhoneApp";
import { Lobby, rosterFor, startError } from "../pages/Lobby";
import { Watch } from "../pages/Watch";
import { createSession, Refused, sessionRequest } from "./api";
import { groupScreen } from "./cues";
import { gameweekTicker } from "./match";
import type { Cue, Gameweek, HostDecision, League, MatchEvent, MatchFrame, MatchMeta, WsMessage } from "./messages";
import type { PhoneTab } from "./phoneText";
import { focusedFixture, matchState } from "./state";
import { empty, fixtureIds, pruneAll, receive, useMatch, type MatchStore } from "./useMatch";

vi.mock("./useMatch", async (original) => ({ ...(await original<typeof import("./useMatch")>()), useMatch: vi.fn() }));

const examples = import.meta.glob("../../../contracts/examples/*.json", { eager: true, import: "default" });
const example = <T,>(name: string): T => structuredClone(examples[`../../../contracts/examples/${name}`] as T);

const GW = example<{ data: Gameweek }>("ws-message.gameweek.json").data; // fx_1 HOL-SAL, fx_2 KES-RAV, fx_3 PEL-GRI
const LEAGUE = example<League>("league.json");
const GOAL_FX2 = example<MatchEvent>("match-event.gameweek.json"); // 43:14, Benji Cortland for KES, xG 0.31
const CARD_FX2 = example<Cue>("cue.gameweek.json"); // group card for that goal, 2534000 to 2720000
const DECISION_FX2 = example<HostDecision>("host-decision.gameweek.json");
const MIN = 60_000;

const msg = (type: WsMessage["type"], data: unknown) => ({ v: 1, type, data }) as WsMessage;
const ev = (seq: number, fixture: string, t: number, type: MatchEvent["type"], over: Partial<MatchEvent> = {}) =>
  ({ seq, fixture_id: fixture, t, type, team: "home", x: 0.5, y: 0.5, ...over }) as MatchEvent; // prettier-ignore

/** One frame per fixture, the ball where only that fixture puts it: x 0.1 in fx_1, 0.5 in fx_2, 0.9 in fx_3. */
const BALL_X: Record<string, number> = { fx_1: 0.1, fx_2: 0.5, fx_3: 0.9 };
const frame = (fixture: string, t: number): MatchFrame => {
  const meta = GW.fixtures.find((f) => f.fixture_id === fixture)!;
  return {
    t, fixture_id: fixture, possession: "home", ball: { x: BALL_X[fixture]!, y: 0.5, z_m: 0 },
    players: [{ id: meta.home.players[0].id, x: 0.2, y: 0.5 }, { id: meta.away.players[0].id, x: 0.8, y: 0.5 }],
  }; // prettier-ignore
};

// The key events: whistles in each fixture a few seconds apart; a goal in fx_1 at 10', the example's KES goal in
// fx_2 at 43', a RAV equaliser at 60'; a saved PEL shot in fx_3 at 50'.
const EVENTS: MatchEvent[] = [
  ev(1, "fx_1", 0, "kickoff"), ev(2, "fx_2", 0, "kickoff"), ev(3, "fx_3", 0, "kickoff"),
  ev(10, "fx_1", 10 * MIN, "shot", { player: "p_hol_9", outcome: "goal", climax: true, moment_id: "m_01",
                                      shot: { xg: 0.44, speed_km_h: 88, body_part: "left_foot" } }),
  GOAL_FX2,
  ev(1300, "fx_1", 45 * MIN, "half_time"), ev(1301, "fx_2", 45 * MIN + 5_000, "half_time"),
  ev(1302, "fx_3", 45 * MIN + 9_000, "half_time"),
  ev(1303, "fx_1", 46 * MIN, "kickoff"), ev(1304, "fx_2", 46 * MIN, "kickoff"), ev(1305, "fx_3", 46 * MIN, "kickoff"),
  ev(1500, "fx_3", 50 * MIN, "shot", { player: "p_pel_9", opponent: "p_gri_1", outcome: "saved", climax: true,
                                       moment_id: "m_11", shot: { xg: 0.12, speed_km_h: 95, body_part: "head" } }),
  ev(1700, "fx_2", 60 * MIN, "shot", { team: "away", player: "p_rav_9", outcome: "goal", climax: true,
                                       moment_id: "m_12", shot: { xg: 0.2, speed_km_h: 80, body_part: "right_foot" } }),
]; // prettier-ignore

const card = (id: number, fixture: string | undefined, showAt: number, manager = "mgr_3"): Cue => ({
  cue_id: `c_${id}`, seq: id, screen: "group", moment_id: `m_${id}`, ...(fixture ? { fixture_id: fixture } : {}),
  show_at: showAt, min_dwell_ms: 6000, expires_at: showAt + 200_000, kind: "explanation", source: "scorer",
  payload: { headline: `Card ${id}`, body: "A moment.", manager },
}); // prettier-ignore

const CARD_FX3 = card(70, "fx_3", 50 * MIN); // the PEL save, up from 50' to about 53'
const AT_FX2 = CARD_FX2.show_at + 1_000; // the KES goal's card on screen
const AT_FX3 = CARD_FX3.show_at; // the moment the fx_3 card comes due
const LATER = 62 * MIN; // after every frame below, and the RAV equaliser; no card due

const FRAMES = [0, 30, 50, 61].flatMap((m) => ["fx_1", "fx_2", "fx_3"].map((fx) => frame(fx, m * MIN)));

/** The message log in stream order: the gameweek, the league, then frames and events by time, the cues and the
 * decision as the backend sends them, ahead of the delayed clock. */
function streamLog(): WsMessage[] {
  const timed = [...FRAMES.map((f) => ({ t: f.t, m: msg("frame", f) })), ...EVENTS.map((e) => ({ t: e.t, m: msg("event", e) }))];
  timed.sort((a, b) => a.t - b.t);
  return [
    msg("gameweek", GW), msg("league", LEAGUE),
    ...timed.map((x) => x.m),
    msg("cue", CARD_FX2), msg("cue", CARD_FX3), msg("decision", DECISION_FX2),
  ];
}

function storeOf(log: WsMessage[]): MatchStore {
  const s = empty();
  log.forEach((m) => receive(s, "group", true, m, 0));
  return s;
}

const watchAt = (store: MatchStore, t: number) => {
  vi.mocked(useMatch).mockReturnValue({ store, t, now: 0, connection: "open" });
  return renderToStaticMarkup(<Watch session="sess_12345678" />);
};
const header = (html: string) => /<header class="match-header broadcast".*?<\/header>/.exec(html)?.[0] ?? "";
const focusedStrip = (html: string) => [...html.matchAll(/<li class="focused" aria-current="true">(.*?)<\/li>/g)].map((m) => m[1]);
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

beforeEach(() => vi.stubGlobal("location", { hash: "", origin: "http://test" }));
afterEach(() => vi.unstubAllGlobals());

describe("the store in a gameweek", () => {
  it("files the gameweek's three fixtures in its order, on one clock, with every fixture's names", () => {
    const s = storeOf(streamLog());
    expect([...s.fixtures.keys()]).toEqual(["fx_1", "fx_2", "fx_3"]);
    expect(s.fixtures.get("fx_2")?.meta?.home.name).toBe("Kestlebury Athletic");
    expect(s.fixtures.get("fx_3")?.events.map((e) => e.seq)).toEqual([3, 1302, 1305, 1500]);
    expect(s.fixtures.get("fx_1")?.frames.map((f) => [f.fixture_id, f.t])).toEqual(
      [0, 30, 50, 61].map((m) => ["fx_1", m * MIN]),
    );
    const names = matchState(s, LATER).players;
    expect([names.get("p_hol_9"), names.get("p_kes_9"), names.get("p_gri_1")]).toEqual(["Jonah Pryce", "Benji Cortland", "Joe Ashbrigg"]);
  });

  it("keeps each event once when a reconnect sends the history again", () => {
    const s = storeOf([...streamLog(), ...streamLog()]);
    expect(s.events).toHaveLength(EVENTS.length);
    expect(s.fixtures.get("fx_2")?.events).toHaveLength(5);
    expect(s.decisions).toHaveLength(1);
  });

  it("ends the clock only when the last fixture blows full time", () => {
    const s = storeOf([msg("gameweek", GW)]);
    const quiet = (store: MatchStore) => store.clock?.at(10_000) ?? -1; // 10 s after the last message, at 30x
    receive(s, "group", true, msg("event", ev(1, "fx_1", 90 * MIN, "full_time")), 0);
    receive(s, "group", true, msg("event", ev(2, "fx_2", 90 * MIN + 10_000, "full_time")), 0);
    expect(quiet(s)).toBeLessThan(90 * MIN); // still live: holds back by the playback delay
    receive(s, "group", true, msg("event", ev(3, "fx_3", 90 * MIN + 20_000, "full_time")), 0);
    expect(quiet(s)).toBe(90 * MIN + 20_000); // over: plays out to the last whistle
  });

  it("files nothing for a fixture the gameweek does not name, and its full time ends nothing", () => {
    const s = storeOf([msg("gameweek", GW)]);
    const quiet = (store: MatchStore) => store.clock?.at(10_000) ?? -1;
    receive(s, "group", true, msg("event", ev(1, "fx_1", 90 * MIN, "full_time")), 0);
    receive(s, "group", true, msg("event", ev(2, "fx_2", 90 * MIN + 10_000, "full_time")), 0);
    receive(s, "group", true, msg("event", ev(3, "fx_9", 90 * MIN + 20_000, "full_time")), 0);
    receive(s, "group", true, msg("frame", { ...frame("fx_1", 0), fixture_id: "fx_9" }), 0);
    expect([...s.fixtures.keys()]).toEqual(["fx_1", "fx_2", "fx_3"]);
    expect(s.events.map((e) => e.seq)).toEqual([1, 2]);
    expect(quiet(s)).toBeLessThan(90 * MIN); // fx_3 still plays
    expect(fixtureIds(storeOf([]))).toEqual(["fx_1"]); // a single match's one fixture
    const { fixture_id: _, ...bare } = ev(4, "fx_1", 91 * MIN, "shot");
    receive(s, "group", true, msg("event", bare), 0);
    expect(s.events.map((e) => e.seq)).toEqual([1, 2]); // no fixture_id in a gameweek: not a fixture's event
  });

  it("prunes each fixture's frames by the clock, never by another fixture's", () => {
    const s = storeOf([msg("gameweek", GW), ...FRAMES.map((f) => msg("frame", f))]);
    s.fixtures.get("fx_3")!.frames = [frame("fx_3", 40 * MIN), frame("fx_3", 45 * MIN)]; // fx_3 behind the others
    pruneAll(s, 55 * MIN);
    const times = (fx: string) => s.fixtures.get(fx)?.frames.map((f) => f.t / MIN);
    expect([times("fx_1"), times("fx_2"), times("fx_3")]).toEqual([[30, 50, 61], [30, 50, 61], [40, 45]]);
  });
});

describe("the group screen follows the card on screen (#94 case 20)", () => {
  it("shows fx_1 before any card", () => {
    const html = watchAt(storeOf(streamLog()), 20 * MIN);
    expect(header(html)).toContain("Hollinsford United");
    expect(header(html)).toContain('1<span class="dash">-</span>0');
    expect(focusedStrip(html)[0]).toContain("HOL 1-0 SAL");
  });

  it("switches pitch, header, score and strip together in the render the fx_3 card comes due", () => {
    const s = storeOf(streamLog());
    const before = watchAt(s, AT_FX3 - 1); // the fx_2 card has gone; it is still the latest due card
    expect(header(before)).toContain("Kestlebury Athletic");
    expect(before).toContain('aria-label="Pitch: Kestlebury Athletic against Ravensholme Town"');
    const html = watchAt(s, AT_FX3);
    expect(header(html)).toContain("Pellbrook City");
    expect(header(html)).toContain("Grindlestone Wanderers");
    expect(header(html)).toContain('0<span class="dash">-</span>0');
    expect(header(html)).toContain("0.12 xG"); // fx_3's own shot, not fx_2's 0.31
    expect(html).toContain('aria-label="Pitch: Pellbrook City against Grindlestone Wanderers"');
    expect(html).toContain(`class="ball" cx="${0.9 * 105}"`); // fx_3's ball, from fx_3's frame
    expect(html.match(/class="ball"/g)).toHaveLength(1);
    expect(focusedStrip(html)).toHaveLength(1);
    expect(focusedStrip(html)[0]).toContain("PEL 0-0 GRI");
    expect(text(html)).toContain("Card 70");
  });

  it("stays on the latest card's fixture after the card has gone", () => {
    const s = storeOf(streamLog());
    expect(focusedFixture(s, CARD_FX3.expires_at + 1)).toBe("fx_3");
  });

  it("keeps the fixture of a card still in its dwell, even when a newer card from another fixture is due", () => {
    const s = storeOf(streamLog());
    const held = groupScreen(s.cues.get("group") ?? [], AT_FX2, 0, null); // the fx_2 card goes up
    const newer = card(80, "fx_3", AT_FX2 + 30_000);
    receive(s, "group", true, msg("cue", newer), 0);
    const t = newer.show_at; // one real second later, inside the fx_2 card's 6 s dwell
    const view = groupScreen(s.cues.get("group") ?? [], t, 1_000, held.slot);
    expect(view.card?.cue_id).toBe(CARD_FX2.cue_id);
    expect(matchState(s, t, view.card).focus).toBe("fx_2");
    expect(focusedFixture(s, t)).toBe("fx_3"); // with nothing on screen, the newest due card
  });

  it("follows a close call's fixture while it takes the screen", () => {
    const s = storeOf(streamLog());
    const clash = { ...example<Cue>("cue.clash.json"), fixture_id: "fx_3", show_at: 70 * MIN, expires_at: 73 * MIN,
                    payload: { ...example<Cue>("cue.clash.json").payload, verdict_at: 70 * MIN + 30_000 } }; // prettier-ignore
    receive(s, "group", true, msg("cue", clash), 0);
    const view = groupScreen(s.cues.get("group") ?? [], 70 * MIN, 0, null);
    expect(view.clash?.phase).toBe("pitches");
    expect(matchState(s, 70 * MIN, view.card ?? view.clash?.cue).focus).toBe("fx_3");
  });

  it("passes over a card naming a fixture it does not have, and in a gameweek a card naming none", () => {
    const s = storeOf(streamLog());
    receive(s, "group", true, msg("cue", card(90, "fx_9", 55 * MIN)), 0);
    expect(focusedFixture(s, 55 * MIN)).toBe("fx_3");
    expect(watchAt(s, 55 * MIN)).toContain("Pellbrook City");
    const gameweekWide = card(91, undefined, 56 * MIN);
    receive(s, "group", true, msg("cue", gameweekWide), 0);
    expect(focusedFixture(s, 56 * MIN)).toBe("fx_3"); // about the whole gameweek: no fixture to switch to
    expect(focusedFixture(s, 56 * MIN, gameweekWide)).toBe("fx_3");
    expect(fixtureTag({ ...gameweekWide, screen: "phone:mgr_2" }, matchState(s, 56 * MIN))).toBeUndefined();
  });
});

describe("a reload mid-gameweek (#94 case 19)", () => {
  /** What a fresh page is sent: the gameweek, the league, the cues and decisions, every key event, and only the
   * latest frame of each fixture. */
  const replay = (): WsMessage[] => {
    const latest = ["fx_1", "fx_2", "fx_3"].map((fx) => FRAMES.filter((f) => f.fixture_id === fx).at(-1)!);
    return [
      msg("gameweek", GW), msg("league", LEAGUE), msg("cue", CARD_FX2), msg("cue", CARD_FX3), msg("decision", DECISION_FX2),
      ...EVENTS.map((e) => msg("event", e)), ...latest.map((f) => msg("frame", f)),
    ]; // prettier-ignore
  };

  it("restores the focused fixture's header, frame, score, strip and ticker in its first render", () => {
    const live = storeOf(streamLog());
    const fresh = storeOf(replay());
    for (const t of [AT_FX2, LATER]) expect(watchAt(fresh, t)).toBe(watchAt(live, t));
    const html = watchAt(fresh, LATER);
    expect(header(html)).toContain("Pellbrook City");
    expect(html).toContain(`class="ball" cx="${0.9 * 105}"`);
    expect(focusedStrip(html)[0]).toContain("PEL 0-0 GRI");
  });

  it("lands on the card a fresh page shows when it reloads during another card's dwell", () => {
    const s = storeOf(replay());
    const newer = card(80, "fx_3", AT_FX2 + 30_000);
    receive(s, "group", true, msg("cue", newer), 0);
    const html = watchAt(s, newer.show_at); // a fresh page has no dwell to keep: it shows the newest due card
    expect(text(html)).toContain("Card 80");
    expect(header(html)).toContain("Pellbrook City");
  });

  it("works out each fixture's xG from its own shots", () => {
    const xg = matchState(storeOf(replay()), LATER).fixtures?.map((f) => [f.id, f.score.xg.home, f.score.xg.away]);
    expect(xg).toEqual([["fx_1", 0.44, 0], ["fx_2", 0.31, 0.2], ["fx_3", 0.12, 0]]);
  });
});

describe("the ticker in a gameweek", () => {
  it("lists every fixture's moments and whistles, each labelled with its clubs", () => {
    const s = storeOf(streamLog());
    const items = gameweekTicker(matchState(s, LATER).fixtures ?? [], LATER);
    expect(items.map((i) => [i.kind, i.fixture])).toEqual([
      ["goal", "fx_2"], ["save", "fx_3"],
      ["half_time", "fx_3"], ["half_time", "fx_2"], ["half_time", "fx_1"],
      ["goal", "fx_2"], ["goal", "fx_1"],
      ["kickoff", "fx_3"], ["kickoff", "fx_2"], ["kickoff", "fx_1"],
    ]); // prettier-ignore
    const html = watchAt(s, LATER);
    const ticker = /<ol class="ticker">.*?<\/ol>/.exec(html)?.[0] ?? "";
    expect(ticker).toContain('<span class="fixture-label">KES v RAV</span>');
    expect(ticker).toContain('<span class="fixture-label">PEL v GRI</span>');
    expect(ticker).toContain("Kwesi Mardle");
  });

  it("says Full time for the fixture that blew it, while the others play on", () => {
    const s = storeOf([...streamLog(), msg("event", ev(1900, "fx_1", 61 * MIN, "full_time"))]);
    const html = watchAt(s, LATER);
    const ticker = /<ol class="ticker">.*?<\/ol>/.exec(html)?.[0] ?? "";
    expect(ticker).toMatch(/<strong>Full time<\/strong><span class="fixture-label">HOL v SAL<\/span>/);
    expect(ticker.match(/Full time/g)).toHaveLength(1);
  });
});

describe("a phone in a gameweek", () => {
  const manager = (lang: string) => ({ ...LEAGUE.managers[1]!, lang });
  const phoneCard: Cue = {
    ...CARD_FX2, cue_id: "c_59", seq: 59, screen: "phone:mgr_2", kind: "explanation", source: "manager",
    payload: { headline: "+6 IP", body: "Your captain scored.", manager: "mgr_2" },
  }; // prettier-ignore
  const summary: Cue = {
    cue_id: "c_99", seq: 99, screen: "phone:mgr_2", show_at: 45 * MIN + 20_000, min_dwell_ms: 0, expires_at: 99 * MIN,
    kind: "summary", source: "manager", payload: { headline: "Half time", body: "All three matches so far." },
  }; // prettier-ignore
  const phone = (lang: string, tab: PhoneTab = "you", cues: Cue[] = [phoneCard, summary]) =>
    renderToStaticMarkup(
      <PhoneApp manager={manager(lang)} cues={cues} style="casual" match={matchState(storeOf(streamLog()), LATER)}
                judge="mgr_2" initialTab={tab} />,
    ); // prettier-ignore

  it("tags a card with its fixture and the score at that minute, not the score now", () => {
    const html = phone("en");
    expect(html).toContain('<span class="fixture-tag" title="The score in that match at the time">KES 1-0 RAV</span>');
    expect(html.match(/fixture-tag/g)).toHaveLength(1); // the gameweek summary has no fixture
    expect(html).toContain('<span class="line">KES 1-1 RAV</span>'); // the strip has the score now
    expect(focusedStrip(html)[0]).toContain("PEL 0-0 GRI"); // the latest group card is fx_3's
    expect(html).toContain('aria-label="Gameweek scores"');
    expect(html).toContain("On the big screen");
  });

  it("writes every new label in Spanish on a Spanish phone", () => {
    const html = phone("es");
    expect(html).toContain('aria-label="Resultados de la jornada"');
    expect(html).toContain("En la pantalla grande");
    expect(html).toContain('title="El marcador de ese partido en ese momento">KES 1-0 RAV</span>');
    expect(html).not.toContain("On the big screen");
    expect(phone("es", "live")).toContain('<span class="fixture-label">PEL contra GRI</span>');
    expect(phone("en", "live")).toContain('<span class="fixture-label">PEL v GRI</span>');
  });
});

describe("a single match, as before", () => {
  const META = example<MatchMeta>("match-meta.json");
  const single = (): MatchStore =>
    storeOf([
      msg("meta", META), msg("league", LEAGUE),
      msg("event", { seq: 1, t: 0, type: "kickoff", team: "home", x: 0.5, y: 0.5 }),
      msg("event", { seq: 2, t: 10 * MIN, type: "shot", team: "home", x: 0.9, y: 0.5, outcome: "goal", player: "p_home_9" }),
      msg("frame", { t: 9 * MIN, possession: "home", ball: { x: 0.3, y: 0.5, z_m: 0 }, players: [] }),
      msg("cue", card(5, undefined, 10 * MIN)),
    ]); // prettier-ignore

  it("is one fixture under fx_1, with no strip, no ticker labels and no card tags", () => {
    const s = single();
    expect([...s.fixtures.keys()]).toEqual(["fx_1"]);
    const m = matchState(s, 11 * MIN);
    expect([m.gameweek, m.focus, m.meta?.match_id, m.events.length, m.frames.length]).toEqual([false, "fx_1", META.match_id, 2, 1]);
    const html = watchAt(s, 11 * MIN);
    expect(header(html)).toContain(META.home.name);
    expect(html).not.toContain("score-strip");
    expect(html).not.toContain("fixture-label");
    // #194: the rail's heading says which matches the table counts; a single match names no others
    expect(html).toContain('<h2 id="rail-table" class="eyebrow">Mini-league</h2>');
    const phone = renderToStaticMarkup(
      <PhoneApp manager={LEAGUE.managers[0]!} cues={[{ ...card(6, undefined, 10 * MIN), screen: "phone:mgr_1" }]}
                style="casual" match={m} judge="mgr_1" />,
    ); // prettier-ignore
    expect(phone).not.toContain("fixture-tag");
    expect(phone).not.toContain("score-strip");
  });
});

describe("the lobby (#94, the owner's decision of 27 Oct)", () => {
  // #196: the format choice is on Watch now's third step
  it("offers the gameweek by default, with the single match one choice away", () => {
    const html = renderToStaticMarkup(<Lobby initialStep={3} />);
    expect(html).toContain('<button type="button" aria-pressed="true">A gameweek: three matches at once</button>');
    expect(html).toContain('<button type="button" aria-pressed="false">A single match</button>');
  });

  // #196: the lede that changed with the format is gone; step 3's line and the words beside Start say it instead
  it("says three matches for a gameweek, and the single match's words when it is chosen (#155)", () => {
    const html = renderToStaticMarkup(<Lobby initialStep={3} />);
    expect(html).toContain("Three matches on one clock and one table.");
    expect(html).toContain("a gameweek of three matches");
    expect(html).not.toContain("a single match.");
    const single = renderToStaticMarkup(<Lobby initialStep={3} initialFormat="single" />);
    expect(single).toContain("One match on its own.");
    expect(single).toContain("a single match, the demo match.");
    expect(single).not.toContain("Three matches on one clock");
    expect(single).not.toContain("a gameweek of three matches");
  });

  it("renders the squads of the format chosen (#155)", () => {
    const player = (name: string, club: string) => ({ name, club, colour: "#000", position: "FWD", shirt: 9 });
    const gwLeague = structuredClone(LEAGUE);
    for (const m of gwLeague.managers) m.squad = ["p_kes_9", "p_kes_9", "p_kes_9", "p_kes_9"];
    const lobby = {
      league: LEAGUE, clubs: {},
      players: Object.fromEntries(LEAGUE.managers.flatMap((m) => m.squad).map((p) => [p, player("Single Onlyman", "HOL")])),
      gameweek: { league: gwLeague, clubs: {}, players: { p_kes_9: player("Gameweek Onlyman", "KES") } },
    };
    const gameweek = renderToStaticMarkup(<Lobby initial={lobby} />);
    expect(gameweek).toContain("Onlyman, KES");
    expect(gameweek).not.toContain("Onlyman, HOL");
    const single = renderToStaticMarkup(<Lobby initial={lobby} initialFormat="single" />);
    expect(single).toContain("Onlyman, HOL");
    expect(single).not.toContain("Onlyman, KES");
  });

  it("shows the squads of the format chosen: the gameweek's own, or the single match's as before (#155)", () => {
    const player = (club: string) => ({ name: "A Player", club, colour: "#000", position: "FWD", shirt: 9 });
    const single = { league: LEAGUE, players: { p_home_9: player("Hollinsford United") }, clubs: {} };
    const gwLeague = structuredClone(LEAGUE);
    for (const m of gwLeague.managers) m.squad = ["p_kes_9", "p_kes_9", "p_kes_9", "p_kes_9"];
    const gameweek = { league: gwLeague, players: { p_kes_9: player("Kestlebury Athletic") }, clubs: {} };
    const lobby = { ...single, gameweek };
    expect(rosterFor(lobby, "gameweek")).toBe(gameweek);
    expect(rosterFor(lobby, "single")).toBe(lobby); // the top level, exactly what the single match showed before
    expect(rosterFor(lobby, "single")?.players).toBe(single.players);
    expect(rosterFor(single, "gameweek")).toBe(single); // a server with no gameweek roster
    expect(rosterFor(null, "gameweek")).toBeNull();
  });

  it("asks for a gameweek only when one is chosen, so a single match sends the body it always has", () => {
    const choice = { seed: "demo" as const, manager: "mgr_1", style: "casual" as const, lang: "en" };
    expect(sessionRequest({ ...choice, format: "gameweek" })).toEqual({ ...choice, gameweek: true });
    expect(sessionRequest({ ...choice, format: "single" })).toEqual(choice);
    expect("gameweek" in sessionRequest({ ...choice, format: "single" })).toBe(false);
  });

  it("says plainly that a server refusing the gameweek cannot start one, and offers the single match", async () => {
    // What FastAPI answers a field it does not know: 422 with a list as the detail
    const refusal = { detail: [{ type: "extra_forbidden", loc: ["body", "gameweek"], msg: "Extra inputs are not permitted" }] };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(refusal), { status: 422 })));
    const choice = { seed: "demo" as const, manager: "mgr_1", style: "casual" as const, lang: "en" };
    const failed = await createSession(sessionRequest({ ...choice, format: "gameweek" })).catch((e: unknown) => e);
    expect(failed).toBeInstanceOf(Refused);
    const shown = startError(failed, "gameweek");
    expect(shown).toEqual({ text: "This server cannot start a gameweek. It can start a single match.", offerSingle: true });
    expect(startError(failed, "single")).toEqual({ text: "This server cannot start that kind of match.", offerSingle: false });
    expect(`${shown.text} ${startError(failed, "single").text}`).not.toMatch(/try again|422/i);
    expect(startError(new Error("The server said 503. Please try again."), "gameweek").offerSingle).toBe(false);
  });
});
