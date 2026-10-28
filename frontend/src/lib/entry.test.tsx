// #196: the entry pages in the new design. The landing page is one decision; Watch now's three steps send the same
// request as before and say the choices in words; the stepper's foot; the gameweek refused; the league page's squad
// places, table and saved view; the routes.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { App } from "../App";

import { LeaguePage, pickerRows, ROWS_SHOWN, type PageState } from "../pages/League";
import { choicesInWords, DEFAULTS, Landing, Lobby, requestFor, rovingIndex, stepFoot } from "../pages/Lobby";
import { parse, WATCH_NOW } from "../router";
import { gameweekRoster, type LeagueData, type LeagueSeat } from "./league";
import type { League } from "./messages";

const examples = import.meta.glob("../../../contracts/examples/league.json", { eager: true, import: "default" });
const LEAGUE = structuredClone(examples["../../../contracts/examples/league.json"] as League);
const LOBBY = { league: LEAGUE, clubs: {}, players: {} };

const decode = (s: string) =>
  s.replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
const text = (html: string) => decode(html.replace(/<[^>]+>/g, " ")).replace(/\s+/g, " ").trim();
const count = (html: string, re: RegExp) => html.match(re)?.length ?? 0;

describe("the landing page (case 1)", () => {
  const html = renderToStaticMarkup(<Landing />);

  it("is a headline, one line and two ways in, beside the brand, the top bar and one line of small print", () => {
    expect(count(html, /<h1[ >]/g)).toBe(1);
    expect(html).toContain("<h1>Watch the gameweek with your <em>mini-league.</em></h1>");
    expect(count(html, /<p[ >]/g)).toBe(2); // the line and the small print
    const links = [...html.matchAll(/<a href="([^"]+)"/g)].map((m) => m[1]);
    expect(links).toEqual(["/", "/league", "/judges", WATCH_NOW, "/league"]);
    expect(text(html)).toMatch(/Watch now You and four bots\. .* Play with your friends Start or join a league/);
    expect(html).not.toMatch(/<(button|input|form|select)[ >]/);
  });
});

describe("Watch now's request and words (cases 2, 3)", () => {
  it("starts with no changes with today's body: the demo gameweek as Jo, casual, in English", () => {
    expect(requestFor(DEFAULTS)).toEqual({ seed: "demo", manager: "mgr_1", style: "casual", lang: "en", gameweek: true });
  });

  it("follows every choice: another manager, analyst, Español, a single match, a random one", () => {
    const leo = { manager: "mgr_4", style: "analyst" as const, lang: "es", format: "single" as const, seed: "random" as const };
    expect(requestFor(leo, () => 0.1234567)).toEqual({ seed: 123456, manager: "mgr_4", style: "analyst", lang: "es" });
    expect(choicesInWords(leo, "Leo")).toBe("Leo, analyst, Español, a single match, a random match.");
    expect(choicesInWords(DEFAULTS, "Jo")).toBe("Jo, casual, English, a gameweek of three matches, the demo match.");
    const shown = text(renderToStaticMarkup(<Lobby initial={LOBBY} initialChoices={leo} />));
    expect(shown).toContain(choicesInWords(leo, LEAGUE.managers.find((m) => m.id === "mgr_4")!.display_name));
  });

  it("shows the chosen manager's squad and marks the choices pressed on their own step", () => {
    const step2 = renderToStaticMarkup(<Lobby initial={LOBBY} initialStep={2} initialChoices={{ style: "analyst", lang: "es" }} />);
    expect(step2).toMatch(/role="radio" aria-checked="true" class="e-opt" tabindex="0"><b>Analyst<\/b>/);
    expect(step2).toContain('<button type="button" lang="es" aria-pressed="true">Español</button>');
    const step1 = renderToStaticMarkup(<Lobby initial={LOBBY} initialChoices={{ manager: "mgr_2" }} />);
    expect(count(step1, /role="radio" aria-checked="true"/g)).toBe(1);
    expect(count(step1, /class="e-sq"/g)).toBe(1); // the chosen manager's five, on one line under the list
    expect(step1).toMatch(/<p class="e-sq" aria-live="polite"><span class="who">Priya&#x27;s squad<\/span>/);
    for (const row of step1.split('class="e-mgr"').slice(1)) expect(row.split("</button>")[0]).not.toContain("cap"); // no row grows
    expect(step1).toMatch(/aria-checked="true" class="e-mgr" tabindex="0"><span class="nm">Priya/);
  });
});

describe("the stepper (case 4)", () => {
  it("has Back disabled on step 1, Next gone on step 3, and one Start on every step", () => {
    expect(stepFoot(1)).toEqual({ backDisabled: true, next: true });
    expect(stepFoot(2)).toEqual({ backDisabled: false, next: true });
    expect(stepFoot(3)).toEqual({ backDisabled: false, next: false });
    for (const step of [1, 2, 3] as const) {
      const html = renderToStaticMarkup(<Lobby initial={LOBBY} initialStep={step} />);
      expect(count(html, />Start the watch party</g)).toBe(1);
      expect(count(html, />Next</g)).toBe(step === 3 ? 0 : 1);
      expect(html.includes('<button type="button" class="btn line" disabled="">Back</button>')).toBe(step === 1);
      expect(html).toContain(`aria-current="step"><b>${step}</b>`);
    }
  });
});

describe("the gameweek refused (case 5)", () => {
  it("offers the single match beside the reason, and no button when the refusal is not about the gameweek", () => {
    const offer = { text: "This server cannot start a gameweek. It can start a single match.", offerSingle: true };
    const html = renderToStaticMarkup(<Lobby initial={LOBBY} initialError={offer} />);
    expect(html).toMatch(/role="alert" class="notice-line">This server cannot start a gameweek\. It can start a single match\.<button type="button" class="btn line">Start a single match<\/button>/);
    const busy = renderToStaticMarkup(<Lobby initial={LOBBY} initialError={{ text: "The server said 503.", offerSingle: false }} />);
    expect(busy).toContain("The server said 503.");
    expect(busy).not.toContain("Start a single match");
  });
});

// Two invented clubs of eleven, as GET /api/leagues/{id} sends them (#178)
const ROSTER = [["t_hol", "Hollinsford United"], ["t_sal", "Saltmere Rovers"]].flatMap(([club_id, club_name]) =>
  Array.from({ length: 11 }, (_, i) => ({ id: `p_${club_id!.slice(2)}_${i + 1}`, name: `Player ${club_id} ${i + 1}`,
                                          position: i ? "DEF" : "GK", club_id: club_id!, club_name: club_name! }))); // prettier-ignore
const me = (over: Partial<LeagueSeat> = {}): LeagueSeat => ({
  seat: 1, member_id: "mb_me", display_name: "Ana", is_bot: false, style: "casual", lang: "en", squad: null, ...over,
}); // prettier-ignore
const ready = (seat: LeagueSeat): PageState => ({
  kind: "ready", tabOnly: false, member: { member_id: "mb_me", token: "tok", seat: 1, lang: "en" },
  season: { league_id: "lg_0123456789ab", played: [], table: [] },
  league: { league_id: "lg_0123456789ab", name: "Sofa Sundays", join_code: "K7Q2XM", is_demo: false, you: "mb_me",
            gameweek: { number: 2, status: "open" }, seats: [seat], roster: ROSTER,
            squad_rules: { squad_size: 5, max_per_club: 3 } } satisfies LeagueData,
}); // prettier-ignore
const page = (state: PageState) => renderToStaticMarkup(<LeaguePage league="lg_0123456789ab" initial={state} />);

describe("the league page (case 7)", () => {
  it("shows five empty places, a table of every player with Add, and a Start that names the gameweek", () => {
    const html = page(ready(me()));
    expect(count(html, /<span class="e-slot"><span class="num" aria-hidden="true">\d<\/span>Pick a player<\/span>/g)).toBe(5);
    expect(count(html, /class="add" aria-label="Add /g)).toBe(10); // ten rows, then Show all
    expect(html).toContain(">Show all (12 more)</button>");
    expect(text(html)).toContain("Player Position Club");
    expect(text(html)).toContain("Player t_hol 1 Hollinsford Goalkeeper Hollinsford United Add");
    expect(html).toContain(">Start gameweek 2</button>");
    expect(html).toContain(">Copy invite link</button>");
    expect(html).not.toContain("join?code="); // copied, never shown
  });

  it("shows a saved squad as five places with the captain, and Change squad in place of the table", () => {
    const squad = { players: ["p_hol_1", "p_hol_2", "p_sal_3", "p_sal_4", "p_sal_5"], captain: "p_sal_3" };
    const html = page(ready(me({ squad })));
    expect(count(html, /class="e-slot full/g)).toBe(5);
    expect(count(html, /class="e-slot full c captain"/g)).toBe(1);
    expect(text(html)).toContain("Your squad · Squad saved");
    expect(text(html)).toContain("You can change it until kickoff. Change squad");
    expect(html).not.toContain('class="e-pt"');
  });
});

describe("the player table's filter and length (case 7)", () => {
  const roster = gameweekRoster((ready(me()) as Extract<PageState, { kind: "ready" }>).league)!;

  it("shows ten rows with the squad first, filters by position, and Show all reveals the rest", () => {
    const first = pickerRows(roster, ["p_sal_5"], "all", false);
    expect(first.rows).toHaveLength(ROWS_SHOWN);
    expect(first.rows[0]![0]).toBe("p_sal_5");
    expect(first.hidden).toBe(22 - ROWS_SHOWN);
    expect(pickerRows(roster, [], "all", true)).toMatchObject({ hidden: 0 });
    expect(pickerRows(roster, [], "all", true).rows).toHaveLength(22);
    const keepers = pickerRows(roster, [], "GK", false);
    expect(keepers.rows.map(([id]) => id)).toEqual(["p_hol_1", "p_sal_1"]);
    expect(pickerRows(roster, [], "DEF", false)).toMatchObject({ hidden: 10 });
  });

  it("renders the five position tabs and numbers the empty places 1 to 5", () => {
    const html = page(ready(me()));
    expect(text(html)).toContain("All Goalkeepers Defenders Midfielders Forwards");
    expect(html).toContain('<button type="button" aria-pressed="true">All</button>');
    for (const n of [1, 2, 3, 4, 5]) expect(html).toContain(`<span class="num" aria-hidden="true">${n}</span>Pick a player`);
  });
});

describe("the top bar (#196 point 9)", () => {
  it("has the same two links on every entry page, the current one marked", () => {
    const bars = [<Landing />, <Lobby initial={LOBBY} />, page(ready(me()))].map((p) =>
      (typeof p === "string" ? p : renderToStaticMarkup(p)).split("</header>")[0]!);
    for (const bar of bars) expect([...bar.matchAll(/<a href="([^"]+)"/g)].map((m) => m[1])).toEqual(["/", "/league", "/judges"]);
    expect(bars[0]).not.toContain("aria-current");
    expect(bars[2]).toContain('<a href="/league" aria-current="page">Your leagues</a>');
  });
});

describe("keyboard and focus", () => {
  it("moves through a radio group with the arrow keys, wrapping, as native radios do", () => {
    expect(rovingIndex("ArrowDown", 0, 5)).toBe(1);
    expect(rovingIndex("ArrowRight", 4, 5)).toBe(0);
    expect(rovingIndex("ArrowUp", 0, 5)).toBe(4);
    expect(rovingIndex("ArrowLeft", 1, 2)).toBe(0);
    expect(rovingIndex("Home", 3, 5)).toBe(0);
    expect(rovingIndex("End", 0, 5)).toBe(4);
    expect(rovingIndex("Enter", 0, 5)).toBeNull();
  });

  it("makes each radio group one Tab stop, the checked option", () => {
    const step1 = renderToStaticMarkup(<Lobby initial={LOBBY} initialChoices={{ manager: "mgr_3" }} />);
    expect(count(step1, /role="radio" aria-checked="true" class="e-mgr" tabindex="0"/g)).toBe(1);
    expect(count(step1, /role="radio" aria-checked="false" class="e-mgr" tabindex="-1"/g)).toBe(LEAGUE.managers.length - 1);
    const step2 = renderToStaticMarkup(<Lobby initial={LOBBY} initialStep={2} initialChoices={{ style: "analyst" }} />);
    expect(step2).toContain('aria-checked="true" class="e-opt" tabindex="0"><b>Analyst</b>');
    expect(step2).toContain('aria-checked="false" class="e-opt" tabindex="-1"><b>Casual</b>');
  });

  it("gives every step's panel a name and a focus target, so a step change can move focus there", () => {
    for (const step of [1, 2, 3] as const) {
      const html = renderToStaticMarkup(<Lobby initial={LOBBY} initialStep={step} />);
      expect(html).toContain(`<div class="e-pane" tabindex="-1" aria-label="${["Your manager", "Your phone", "The match"][step - 1]}">`);
    }
  });

  it("marks the captain with an abbreviation titled Captain", () => {
    const players = Object.fromEntries(LEAGUE.managers.flatMap((m) => m.squad).map((p) => [p, { name: "A Player", club: "Hollinsford United", colour: "#000", position: "FWD", shirt: 9 }]));
    expect(renderToStaticMarkup(<Lobby initial={{ ...LOBBY, players }} />)).toMatch(/<abbr class="cap" title="Captain">C<\/abbr>/);
  });

  it("sends a missing page back to the landing page by name", () => {
    vi.stubGlobal("location", { pathname: "/nope", search: "" });
    const html = renderToStaticMarkup(<App />);
    vi.unstubAllGlobals();
    expect(html).toContain('<a href="/">Back to Couch Gaffer</a>');
    expect(html).not.toContain("lobby");
  });
});

describe("the routes (case 9)", () => {
  it.each([
    ["/", "", { page: "landing" }],
    ["/", "?watch", { page: "watch-now" }],
    ["/", "?code=ABC234", { page: "landing" }],
    ["/league", "", { page: "league-home" }],
    ["/league/join", "?code=ABC234", { page: "league-home" }],
    ["/league/lg_0123456789ab", "", { page: "league", league: "lg_0123456789ab" }],
    ["/judges", "", { page: "judges" }],
    ["/judges", "?watch", { page: "judges" }],
  ])("%s%s", (path, search, route) => expect(parse(path, search)).toEqual(route));
});
