// #194: the match screens, decluttered. The group screen shows the bar, the pitch, the lower third and the rail; the
// phone, the QR code and Try a failure wait in one drawer. No club colours. Every text colour in the shared tokens
// meets 4.5:1. Escape and focus return are checked in the browser run on #194; useMatch is replaced here.
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { FeedCard, GroupStage, LowerThird, pointsOf, repeatsPoints, statLine } from "../components/Cards";
import { onPitch, Pitch } from "../components/Pitch";
import { seconds, Trace } from "../components/Trace";
import { escapeLayer, leagueName, Watch } from "../pages/Watch";
import { ScoreStrip } from "../components/ScoreStrip";
import { groupScreen } from "./cues";
import type { Cue, Gameweek, League, MatchFrame, WsMessage } from "./messages";
import { empty, receive, useMatch } from "./useMatch";

vi.mock("./useMatch", async (original) => ({ ...(await original<typeof import("./useMatch")>()), useMatch: vi.fn() }));

const examples = import.meta.glob("../../../contracts/examples/*.json", { eager: true, import: "default" });
const example = <T,>(name: string): T => structuredClone(examples[`../../../contracts/examples/${name}`] as T);
const GW = example<{ data: Gameweek }>("ws-message.gameweek.json").data;
const LEAGUE = example<League>("league.json");
const CARD = example<Cue>("cue.gameweek.json"); // the KES goal's group card, for Priya
const CLASH = example<Cue>("cue.clash.json"); // Priya against Jo; the host picks Jo
const msg = (type: WsMessage["type"], data: unknown) => ({ v: 1, type, data }) as WsMessage;
const AT = CARD.show_at + 1_000;

function midGameweek(card: Cue = CARD) {
  const s = empty();
  const frame = (fixture: string): MatchFrame => {
    const meta = GW.fixtures.find((f) => f.fixture_id === fixture)!;
    return { t: AT - 1_000, fixture_id: fixture, possession: "home", ball: { x: 0.5, y: 0.5, z_m: 0 },
             players: [{ id: meta.home.players[0].id, x: 0.2, y: 0.5 }, { id: meta.away.players[0].id, x: 0.8, y: 0.5 }] }; // prettier-ignore
  };
  for (const m of [msg("gameweek", GW), msg("league", LEAGUE), ...["fx_1", "fx_2", "fx_3"].map((f) => msg("frame", frame(f))),
                   msg("cue", card)]) receive(s, "group", true, m, 0); // prettier-ignore
  return s;
}

const watch = (tools = false) => {
  vi.mocked(useMatch).mockReturnValue({ store: midGameweek(), t: AT, now: 0, connection: "open" });
  return renderToStaticMarkup(<Watch session="sess_12345678" initialTools={tools} />);
};

beforeEach(() => vi.stubGlobal("location", { hash: "#token=tok&manager=mgr_1", origin: "http://test" }));
afterEach(() => vi.unstubAllGlobals());

describe("the group screen, mid-gameweek (#194 case 5)", () => {
  const html = () => watch();

  it("shows the bar with the three fixtures and the bug, the pitch, the lower third and the rail", () => {
    const h = html();
    expect(h).toContain('<ol class="score-strip"');
    expect(h.match(/<li[^>]*>.*?<\/li>/g)?.filter((li) => li.includes('class="line"'))).toHaveLength(3);
    expect(h).toContain('<header class="match-header broadcast"');
    expect(h).toContain('<svg class="pitch"');
    expect(h).toContain('<section class="lower-third scorer">');
    expect(h).toContain('<h2 id="rail-table" class="eyebrow">Mini-league · all three matches</h2>');
    expect(h).toContain('<h2 id="rail-moments" class="eyebrow">Key moments</h2>');
  });

  it("keeps the phone, the QR code and Try a failure out of the page until the drawer opens", () => {
    const h = html();
    for (const absent of ["phone-app", 'class="qr"', "Try a failure", "Knock out", 'id="tools"']) expect(h).not.toContain(absent);
    expect(h).toMatch(/<button type="button" class="btn line" aria-expanded="false">Phone and tools<\/button>/);
  });

  it("keeps Behind the scenes as a button in the bar (case 9)", () => {
    expect(html()).toMatch(/<button type="button" class="btn line" aria-expanded="false">Behind the scenes<\/button>/);
  });
});

describe("Phone and tools (#194 case 6)", () => {
  it("holds whose phone, the phone, the QR code and Try a failure, named by its button", () => {
    const h = watch(true);
    expect(h).toContain('aria-expanded="true" aria-controls="tools">Phone and tools</button>');
    const drawer = /<aside id="tools" class="drawer" aria-labelledby="tools-title" tabindex="-1">(.*?)<\/aside>$/.exec(
      h.replace(/<\/div>$/, ""),
    )?.[1] ?? ""; // prettier-ignore
    expect(drawer).toContain('<h2 id="tools-title">Phone and tools</h2>');
    expect(drawer).toContain('role="tablist" aria-label="Whose phone"');
    expect(drawer).toContain('<section class="phone-app');
    expect(drawer).toContain('<svg class="qr"');
    expect(drawer).toContain("Try a failure");
    expect(drawer).toContain(">Close</button>");
  });

  it("knocks out the phone shown, the judge's own first (case 7)", () => {
    expect(watch(true)).toMatch(/<button type="button" class="btn line"[^>]*>Knock out your agent<\/button>/);
  });
});

describe("a close call (#194 case 8)", () => {
  const names = new Map([["mgr_1", "Jo"], ["mgr_2", "Priya"]]);
  const stage = (t: number) =>
    renderToStaticMarkup(
      <GroupStage view={groupScreen([CLASH], t, 0, null)} cues={[CLASH]} t={t} names={names} judge={null}
                  pitch={<i>the live pitch</i>} />,
    ); // prettier-ignore

  it("shows the moment above the two pitches, each with who wrote it", () => {
    const clash = structuredClone(CLASH);
    const [a, b] = clash.payload.contenders!;
    clash.payload.contenders = [{ ...a, pitch_source: "manager" }, { ...b, pitch_source: "code" }];
    const html = renderToStaticMarkup(
      <GroupStage view={groupScreen([clash], clash.show_at, 0, null)} cues={[clash]} t={clash.show_at} names={names}
                  judge={null} pitch={<i>the live pitch</i>} />,
    ); // prettier-ignore
    expect(html).toContain('<div class="clash-pitch"><i>the live pitch</i></div>');
    const marks = [...html.matchAll(/<div class="contender">(.*?)<p>/g)].map((m) => /<span class="mark [^"]*"[^>]*>([^<]*)</.exec(m[1] ?? "")?.[1]);
    expect(marks).toEqual(["AI", "Plain stat"]);
  });

  it("says the losing friend's phone gets a note, under the verdict", () => {
    const html = stage(CLASH.payload.verdict_at ?? 0);
    expect(html).toContain('<p class="lost-note">Priya&#x27;s phone gets a note: the moment lost a close call');
  });
});

describe("no club colours on the match screens (#194)", () => {
  it("draws the players by side, home white and away outlined, with no colour from the data", () => {
    const meta = GW.fixtures[0]!;
    const html = renderToStaticMarkup(
      <Pitch meta={meta} frames={[{ t: 0, possession: "home", ball: { x: 0.5, y: 0.5, z_m: 0 },
             players: [{ id: meta.home.players[0].id, x: 0.2, y: 0.5 }, { id: meta.away.players[0].id, x: 0.8, y: 0.5 }] }]}
             events={[]} t={0} />,
    ); // prettier-ignore
    expect(html).toContain('<g transform="translate(21 34)" class="home"><circle r="2.3" class="halo"></circle><circle r="2" class="player">');
    expect(html).toContain('<g transform="translate(84 34)" class="away"><circle r="2.3" class="halo"></circle><circle r="2" class="player">');
    expect(html).not.toContain(meta.home.colour);
    expect(html).not.toContain(meta.away.colour);
  });
});

describe("a league's match (#194)", () => {
  it("names a stored league in the bar, and not the demo leagues", () => {
    expect(leagueName({ league_id: "lg_0123456789ab", name: "Sofa Sundays" })).toBe("Sofa Sundays");
    expect(leagueName({ league_id: "lg_demo", name: "Sunday Sofa League" })).toBeNull();
    expect(leagueName({ league_id: "lg_demo_gw", name: "Sunday Sofa League" })).toBeNull();
    expect(leagueName(null)).toBeNull();
  });
});

// WCAG relative luminance and contrast ratio
const luminance = (hex: string) => {
  const full = hex.length === 4 ? hex.replace(/^#(.)(.)(.)$/, "#$1$1$2$2$3$3") : hex;
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(full.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)); // prettier-ignore
  return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
};
const contrast = (a: string, b: string) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi! + 0.05) / (lo! + 0.05);
};

describe("contrast (#194 case 12)", () => {
  // Read from disk: Vitest serves a stylesheet imported with ?raw as an empty string. The module name is a variable so
  // the type check, which has no Node types here, does not resolve it.
  let tokens = "";
  beforeAll(async () => {
    const fs = (await import(/* @vite-ignore */ "node:fs" as string)) as { readFileSync: (p: URL, e: "utf8") => string };
    tokens = fs.readFileSync(new URL("../tokens.css", import.meta.url), "utf8");
  });
  const token = (name: string) => new RegExp(`--${name}: (#[0-9a-f]{3,6});`).exec(tokens)?.[1] ?? "";

  it("reads every token it checks", () => {
    for (const name of ["bg", "s1", "s2", "text", "muted", "acc", "on-acc"]) expect(token(name)).toMatch(/^#/);
  });

  it("puts text, grey and the accent at 4.5:1 or more on every surface, and black on the accent and the score box", () => {
    for (const fg of ["text", "muted", "acc"]) {
      for (const bg of ["bg", "s1", "s2"]) expect(contrast(token(fg), token(bg)), `${fg} on ${bg}`).toBeGreaterThanOrEqual(4.5);
    }
    expect(contrast(token("on-acc"), token("acc"))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(token("bg"), token("text"))).toBeGreaterThanOrEqual(4.5); // the score box, black on white
  });
});

describe("code's plain stat card in the lower third (#194)", () => {
  const clubs = new Map([["Kestlebury Athletic", "KES"], ["Ravensholme Town", "RAV"]]);

  it("splits the fixture and the numbers off the event and the player", () => {
    expect(statLine("Kestlebury Athletic v Ravensholme Town: Wide: Malik Tennant, xG 0.16", clubs))
      .toEqual({ title: "Wide: Malik Tennant", detail: "KES v RAV · xG 0.16" });
    expect(statLine("KES v RAV: Saved! Tom Ashcombe denies Lasse Vikholm, xG 0.31"))
      .toEqual({ title: "Saved! Tom Ashcombe denies Lasse Vikholm", detail: "KES v RAV · xG 0.31" });
    expect(statLine("Kestlebury Athletic v Ravensholme Town: Yellow card: Matty Brockhurst", clubs))
      .toEqual({ title: "Yellow card: Matty Brockhurst", detail: "KES v RAV" });
    expect(statLine("Off the woodwork: Malik Tennant, xG 0.4")).toEqual({ title: "Off the woodwork: Malik Tennant", detail: "xG 0.4" });
  });

  it("keeps a string that does not fit the pattern whole", () => {
    expect(statLine("GOAL! Malik Tennant")).toEqual({ title: "GOAL! Malik Tennant" });
    expect(statLine("Kick off")).toEqual({ title: "Kick off" });
    expect(statLine("Goal: Reds 1-0")).toEqual({ title: "Goal: Reds 1-0" });
  });

  it("shows the title as the headline and the fixture with the numbers as a grey line, with short club names on the page", () => {
    const stat = { ...CARD, kind: "stat", source: "code",
                   payload: { headline: "Kestlebury Athletic v Ravensholme Town: Wide: Malik Tennant, xG 0.16" } } as Cue; // prettier-ignore
    const lower = renderToStaticMarkup(<LowerThird card={stat} reason={undefined} names={new Map()} judge={null} clubs={clubs} />);
    expect(lower).toContain('<h2>Wide: Malik Tennant</h2><p class="lt-detail">KES v RAV · xG 0.16</p>');
    vi.mocked(useMatch).mockReturnValue({ store: midGameweek(stat), t: AT, now: 0, connection: "open" });
    const page = renderToStaticMarkup(<Watch session="sess_12345678" />);
    expect(page).toContain('<h2>Wide: Malik Tennant</h2><p class="lt-detail">KES v RAV · xG 0.16</p>');
  });
});

const HOST_DECISION = (examples["../../../contracts/examples/host-decision.json"] ?? {}) as Parameters<typeof Trace>[0]["decisions"][number];

describe("the review pass (#194)", () => {
  it("pulls the points out of a line, for the lower third and a close call's claims", () => {
    expect(pointsOf("+2 IP for Arjun from this moment (shot on target).")).toBe("+2 IP");
    expect(pointsOf("Jo's captain saved (-1.5 IP), one off the bonus.")).toBe("-1.5 IP");
    expect(pointsOf("+2,5 IP para ti por esa jugada.")).toBe("+2,5 IP");
    expect(pointsOf("Priya's keeper reaches the Wall tier.")).toBeUndefined();
  });

  it("shows a lower third's points large at the right, and a headline of only points gives way to the line", () => {
    const card = { ...CARD, payload: { headline: "Priya: +6 IP", body: "Her captain scored.", manager: "mgr_2" } } as Cue;
    const html = renderToStaticMarkup(<LowerThird card={card} reason={undefined} names={new Map([["mgr_2", "Priya"]])} judge={null} />);
    expect(html).toContain('<span class="lt-points">+6<small>IP</small></span>');
    expect(html).toContain("<h2>Her captain scored.</h2>");
    expect(html).not.toContain("<p>Her captain scored.</p>");
  });

  it("puts each claim's points beside it on a close call", () => {
    const names = new Map([["mgr_1", "Jo"], ["mgr_2", "Priya"]]);
    const clash = structuredClone(CLASH);
    clash.payload.contenders = [{ manager: "mgr_2", pitch: "+8 IP for Priya from this moment (goal)." },
                                { manager: "mgr_1", pitch: "Jo's keeper saved it." }] as typeof clash.payload.contenders; // prettier-ignore
    const html = renderToStaticMarkup(
      <GroupStage view={groupScreen([clash], clash.show_at, 0, null)} cues={[clash]} t={clash.show_at} names={names} judge={null} pitch={null} />,
    ); // prettier-ignore
    expect(html.match(/<span class="bid">[^<]*<\/span>/g)).toEqual(['<span class="bid">+8 IP</span>']);
  });

  it("sends the ended page's button to the start screen, /?watch, not the two-way choice at / (#196)", () => {
    vi.mocked(useMatch).mockReturnValue({ store: empty(), t: 0, now: 0, connection: "refused" });
    const html = renderToStaticMarkup(<Watch session="sess_12345678" />);
    expect(html).toContain('<a class="btn acc" href="/?watch">Start a new watch party</a>');
  });

  it("shows a big figure only where the number is certain: code's plain line or exactly one signed figure", () => {
    expect(pointsOf("+8 IP for Priya from this moment (goal).")).toBe("+8 IP");
    expect(pointsOf("Leo drops -2 IP, as she gains +6 IP")).toBeUndefined();
    expect(pointsOf("Her keeper's save is worth 6 IP.")).toBeUndefined();
    const card = { ...CARD, payload: { headline: "Priya: Captain scores", body: "Leo drops -2 IP, as she gains +6 IP", manager: "mgr_2" } } as Cue;
    const html = renderToStaticMarkup(<LowerThird card={card} reason={undefined} names={new Map([["mgr_2", "Priya"]])} judge={null} />);
    expect(html).not.toContain("lt-points");
    expect(html).toContain("Leo drops -2 IP, as she gains +6 IP");
  });

  it("keeps HT or FT beside a fixture that is not live, and marks a live minute so the bar can leave it out", () => {
    const fixtures = GW.fixtures.map((meta, i) => ({
      id: meta.fixture_id ?? `fx_${i + 1}`, meta, score: { home: 0, away: 0, minute: 61, xg: { home: 0, away: 0 }, lastGoal: null,
                                          phase: i === 1 ? ("full_time" as const) : ("live" as const) },
    })); // prettier-ignore
    const html = renderToStaticMarkup(<ScoreStrip fixtures={fixtures} focus="fx_1" />);
    expect(html.match(/<span class="when">[^<]*<\/span>/g)).toEqual(['<span class="when">FT</span>']);
    expect(html.match(/<span class="when live">/g)).toHaveLength(2);
  });

  it("draws the fixture strip as plain scores, not tabs, and still names the one on screen (#207)", async () => {
    const fixtures = GW.fixtures.map((meta, i) => ({
      id: meta.fixture_id ?? `fx_${i + 1}`, meta, score: { home: 0, away: 0, minute: 61, xg: { home: 0, away: 0 }, lastGoal: null,
                                          phase: "live" as const },
    })); // prettier-ignore
    const html = renderToStaticMarkup(<ScoreStrip fixtures={fixtures} focus="fx_1" />);
    expect(html).not.toMatch(/<button|<a |role="tab|tabindex|class="[^"]*\btab/);
    expect(html.match(/aria-current="true"/g)).toHaveLength(1);
    expect(html).toContain('<span class="on-screen">On the big screen</span>');
    const fs = (await import(/* @vite-ignore */ "node:fs" as string)) as { readFileSync: (p: URL, e: "utf8") => string };
    const css = fs.readFileSync(new URL("../styles.css", import.meta.url), "utf8");
    // The phone's fixture buttons (#208) are buttons on purpose; every other strip rule draws plain scores
    const rules = (css.match(/[^{}]*\.score-strip[^{}]*\{[^}]*\}/g) ?? []).filter((r) => !r.includes("button.pick"));
    expect(rules.length).toBeGreaterThan(0);
    for (const rule of rules) {
      expect(rule).not.toMatch(/box-shadow|text-decoration|cursor:\s*pointer|:hover|:focus/);
      expect(rule).not.toMatch(/border-bottom[^;]*var\(--acc\)/);
    }
  });

  it("closes the topmost layer on Escape: Behind the scenes before the drawer", () => {
    expect(escapeLayer(true, true)).toBe("trace");
    expect(escapeLayer(false, true)).toBe("tools");
    expect(escapeLayer(true, false)).toBe("trace");
    expect(escapeLayer(false, false)).toBeNull();
  });

  it("gives no decision time for the scorer's clear call", () => {
    const decision = { ...structuredClone(HOST_DECISION), source: "scorer" as const, latency_ms: 40 };
    const html = renderToStaticMarkup(<Trace decisions={[decision]} names={new Map()} t={decision.t} onClose={() => undefined} />);
    expect(html).not.toContain('class="ms"');
  });

  it("shows no placeholder sentence before the first moment", () => {
    const html = renderToStaticMarkup(<LowerThird card={null} reason={undefined} names={new Map()} judge={null} />);
    expect(html).toBe("");
  });

  it("orders the drawer: whose phone, the phone, Try a failure, then the QR code; whose phone as tabs, not a switch", () => {
    const h = watch(true);
    const at = (s: string) => h.indexOf(s);
    expect(at('id="tools-phone"')).toBeLessThan(at('<section class="phone-app'));
    expect(at('<section class="phone-app')).toBeLessThan(at('id="tools-fail"'));
    expect(at('id="tools-fail"')).toBeLessThan(at('id="tools-qr"'));
    expect(h).toContain('<div class="pills switcher" role="tablist" aria-label="Whose phone">');
  });

  it("leaves out code's plain casual line, which only repeats the points the card's head shows", () => {
    expect(repeatsPoints("+8.5 IP for you from that moment.")).toBe(true);
    expect(repeatsPoints("-1 IP for you from that moment.")).toBe(true);
    expect(repeatsPoints("+2,5 IP para ti por esa jugada.")).toBe(true);
    expect(repeatsPoints("+3 IP from this moment (save). Your league total is 215.")).toBe(false);
    const cue = { ...CARD, screen: "phone:mgr_1", source: "code", moment_id: "m_01",
                  payload: { headline: "+8.5 IP", body: "+8.5 IP for you from that moment.", body_source: "code" } } as Cue; // prettier-ignore
    const html = renderToStaticMarkup(<FeedCard cue={cue} style="casual" moment={{ what: "Goal", who: "Benji Cortland" }} />);
    expect(html).toContain('<span class="points-chip up">+8.5 IP</span>');
    expect(html).not.toContain("for you from that moment");
  });

  it("keeps every dot inside the touchlines", () => {
    expect(onPitch({ x: 1.02, y: -0.01 })).toEqual([105 - 2.3, 2.3]);
    expect(onPitch({ x: 0.5, y: 0.5 })).toEqual([52.5, 34]);
  });

  it("says every time on Behind the scenes in seconds, and leaves out a time of zero", () => {
    expect(seconds(1650)).toBe("1.6 s");
    const decision = structuredClone(HOST_DECISION);
    decision.scores = [{ ...decision.scores[0]!, status: "fallback", latency_ms: 0, pitch: "+8 IP for Priya." },
                       { ...decision.scores[1]!, status: "ok", latency_ms: 1200, pitch: "Your captain saved." }] as typeof decision.scores; // prettier-ignore
    const html = renderToStaticMarkup(<Trace decisions={[decision]} names={new Map()} t={decision.t} onClose={() => undefined} />);
    expect(html).toContain('<span class="ms">1.6 s</span>');
    expect(html).not.toContain(" ms<");
    expect(html).toContain('<span class="bid fallback">plain stat</span>');
    expect(html).toContain('<span class="bid ok">ok, 1.2 s</span>');
  });
});
