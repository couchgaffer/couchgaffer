// #179: the league screens. The requests each screen sends (shapes from backend/watchparty/store/api.py and the
// contract on #177), what each screen shows in each state (not a member, not picked, an invalid squad, picked), the
// member's token kept per league and a browser that will not keep it, Spanish for every label, and the lobby's
// third choice and its opening line.
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LeagueHome, LeaguePage, loadLeague, refusal, SquadPicker, type PageState } from "../pages/League";
import { Landing, Lobby } from "../pages/Lobby";
import { parse } from "../router";
import {
  codeFrom, codeOk, createLeague, forget, gameweekRoster, hasPicked, joinLeague, keptInTabOnly, LeagueError, membership,
  pickSquad, remember, rememberedLeagues, shareLink, squadIssue, startLeagueSession, type GameRoster, type LeagueData,
  type LeagueSeat, type Season,
} from "./league";
import { LEAGUE_WORDS } from "./leagueText";

const decode = (s: string) =>
  s.replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
const text = (html: string) => decode(html.replace(/<[^>]+>/g, " ")).replace(/\s+/g, " ");

// Two invented clubs of eleven, five a squad and three from one club, as GET /api/leagues/{id} sends them (#178).
const CLUBS = [["t_hol", "Hollinsford"], ["t_sal", "Saltmere"]] as const;
const ROUTE_ROSTER = CLUBS.flatMap(([club_id, club_name]) => Array.from({ length: 11 }, (_, i) => ({
  id: `p_${club_id.slice(2)}_${i + 1}`, name: `${club_name} Player ${i + 1}`, position: i ? "MID" : "GK", club_id, club_name,
}))); // prettier-ignore
const RULES = { squad_size: 5, max_per_club: 3 };
const ROSTER: GameRoster = {
  players: Object.fromEntries(ROUTE_ROSTER.map((p) => [p.id, { name: p.name, club: p.club_name, clubId: p.club_id,
                                                              position: p.position }])),
  squadSize: 5, maxPerClub: 3,
}; // prettier-ignore
const FIVE = ["p_hol_9", "p_hol_10", "p_sal_9", "p_sal_10", "p_sal_11"];

const seat = (n: number, over: Partial<LeagueSeat> = {}): LeagueSeat => ({
  seat: n, member_id: null, display_name: `Bot ${n}`, is_bot: true, style: "casual", lang: "en", squad: null, ...over,
}); // prettier-ignore
const league = (seats: LeagueSeat[], you: string | null = "mb_me", roster = true): LeagueData => ({
  league_id: "lg_0123456789ab", name: "Sunday Five", join_code: "ABC234", is_demo: false, you, seats,
  gameweek: { number: 1, status: "open" }, ...(roster ? { roster: ROUTE_ROSTER, squad_rules: RULES } : {}),
}); // prettier-ignore
const SEASON_EMPTY: Season = {
  league_id: "lg_0123456789ab", played: [],
  table: [{ rank: 1, member_id: "mb_me", seat: 1, display_name: "Ana", is_bot: false, points: 0, played: 0 }],
}; // prettier-ignore
const ME = (over: Partial<LeagueSeat> = {}) =>
  seat(1, { member_id: "mb_me", display_name: "Ana", is_bot: false, ...over });
const FRIEND = (over: Partial<LeagueSeat> = {}) =>
  seat(2, { member_id: "mb_friend", display_name: "Bea", is_bot: false, ...over });
const ready = (seats: LeagueSeat[], over: Partial<Extract<PageState, { kind: "ready" }>> = {}): PageState => ({
  kind: "ready", league: league(seats), season: SEASON_EMPTY,
  member: { member_id: "mb_me", token: "tok", seat: 1, lang: "en" }, tabOnly: false, ...over,
}); // prettier-ignore
const page = (state: PageState) => text(renderToStaticMarkup(<LeaguePage league="lg_0123456789ab" initial={state} />));

/** A Storage that keeps values in a Map, or throws on every call when `broken`. */
function fakeStorage(broken: { read?: boolean; write?: boolean } = {}) {
  const data = new Map<string, string>();
  const fail = () => { throw new DOMException("blocked", "SecurityError"); }; // prettier-ignore
  return {
    data,
    getItem: (k: string) => (broken.read ? fail() : (data.get(k) ?? null)),
    setItem: (k: string, v: string) => (broken.write ? fail() : void data.set(k, v)),
    removeItem: (k: string) => void data.delete(k),
    key: (i: number) => (broken.read ? fail() : ([...data.keys()][i] ?? null)),
    get length() { return broken.read ? fail() : data.size; }, // prettier-ignore
  };
}

/** Records every request and answers each with `reply`. */
function fakeFetch(reply: (url: string) => { status: number; body: unknown }) {
  const calls: { url: string; method: string; headers: Record<string, string>; body: unknown }[] = [];
  const fetch = vi.fn(async (url: string, init: RequestInit = {}) => {
    calls.push({ url, method: init.method ?? "GET", headers: (init.headers ?? {}) as Record<string, string>,
                 body: init.body ? JSON.parse(init.body as string) : undefined }); // prettier-ignore
    const { status, body } = reply(url);
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetch);
  return calls;
}

beforeEach(() => vi.stubGlobal("localStorage", fakeStorage()));
afterEach(() => {
  forget("lg_0123456789ab");
  forget("lg_aaaaaaaaaaaa");
  vi.unstubAllGlobals();
});

describe("the way in (#179 cases 1, 2, 24; the landing page since #196)", () => {
  const landing = text(renderToStaticMarkup(<Landing />));

  // #196: the league is one of the landing page's two choices, not a link beside Watch now's match choice
  it("offers your league as one of the landing page's two ways in, and Watch now's match group holds only matches", () => {
    const html = renderToStaticMarkup(<Landing />);
    expect(html).toMatch(/<a href="\/league"><span><b>Play with your friends<\/b>/);
    const group = /<span class="seg" role="group" aria-labelledby="seg-match">(.*?)<\/span>/.exec(renderToStaticMarkup(<Lobby initialStep={3} />))?.[1] ?? "";
    expect(text(group).trim()).toBe("The demo match A random match");
  });

  // #196: the line moved from the lobby to the landing page
  it("says code picks clear winners and the host settles close calls", () => {
    expect(landing).toContain("code picks a clear winner for the big screen, and an AI host settles close calls.");
    expect(landing).not.toContain("decides whose moment");
  });
});

describe("the routes (case 3)", () => {
  it.each([
    ["/league", { page: "league-home" }],
    ["/league/join", { page: "league-home" }],
    ["/league/lg_0123456789ab", { page: "league", league: "lg_0123456789ab" }],
    ["/league/lg_demo", { page: "league", league: "lg_demo" }],
    ["/league/", { page: "missing" }],
    ["/league/x", { page: "missing" }],
    ["/league/lg_x", { page: "missing" }],
    ["/league/lg_0123456789abc", { page: "missing" }],
    ["/league/lg_1/x", { page: "missing" }],
    ["/league/join/x", { page: "missing" }],
  ])("%s", (path, route) => expect(parse(path)).toEqual(route));
});

describe("the requests (cases 4, 7, 19, 26)", () => {
  const me = { display_name: "Ana", style: "analyst" as const, lang: "es" as const };

  it("creates with the league name and the member's profile, nothing else", async () => {
    const calls = fakeFetch(() => ({ status: 201, body: { league_id: "lg_1", join_code: "ABC234", member_id: "m",
                                                          member_token: "t", seat: 1 } })); // prettier-ignore
    await createLeague("Sunday Five", me);
    expect(calls).toEqual([{ url: "/api/leagues", method: "POST", headers: { "Content-Type": "application/json" },
                             body: { name: "Sunday Five", display_name: "Ana", style: "analyst", lang: "es" } }]); // prettier-ignore
  });

  it("joins with the code and the profile", async () => {
    const calls = fakeFetch(() => ({ status: 201, body: { league_id: "lg_1", member_id: "m", member_token: "t", seat: 2 } }));
    await joinLeague("ABC234", me);
    expect(calls[0]).toMatchObject({ url: "/api/leagues/join", method: "POST",
                                     body: { code: "ABC234", display_name: "Ana", style: "analyst", lang: "es" } }); // prettier-ignore
  });

  it("saves a squad with the member's token, to the gameweek's route", async () => {
    const calls = fakeFetch(() => ({ status: 200, body: { gameweek: 1, players: FIVE, captain: FIVE[0] } }));
    const saved = await pickSquad("lg_0123456789ab", 1, "tok", FIVE, FIVE[0]!);
    expect(saved.captain).toBe(FIVE[0]);
    expect(calls[0]).toMatchObject({ url: "/api/leagues/lg_0123456789ab/gameweeks/1/squad", method: "PUT",
                                     headers: { Authorization: "Bearer tok" }, body: { players: FIVE, captain: FIVE[0] } }); // prettier-ignore
  });

  it("starts the watch party with league_id, the caller's seat and its style and language, and the token", async () => {
    const calls = fakeFetch(() => ({ status: 201, body: { session_id: "s", phone_token: "p", manager: "mgr_3" } }));
    await startLeagueSession("lg_0123456789ab", seat(3, { member_id: "mb_me", style: "analyst", lang: "es" }), "tok");
    expect(calls).toEqual([{ url: "/api/sessions", method: "POST",
                             headers: { "Content-Type": "application/json", Authorization: "Bearer tok" },
                             body: { league_id: "lg_0123456789ab", manager: "mgr_3", style: "analyst", lang: "es" } }]); // prettier-ignore
  });

  it("carries the server's reasons on a refusal", async () => {
    fakeFetch(() => ({ status: 422, body: { detail: ["a squad has exactly 5 players", "the captain must be one of the squad"] } }));
    const e = await pickSquad("lg_0123456789ab", 1, "tok", [], "x").catch((x: unknown) => x);
    expect(e).toBeInstanceOf(LeagueError);
    expect((e as LeagueError).problems).toEqual(["a squad has exactly 5 players", "the captain must be one of the squad"]);
  });
});

describe("what a refusal says (cases 5, 8, 15, 20, 21)", () => {
  const en = LEAGUE_WORDS.en;
  const es = LEAGUE_WORDS.es;
  const unknownField = [{ type: "extra_forbidden", loc: ["body", "league_id"], msg: "Extra inputs are not permitted" }];

  it.each([
    ["join", new LeagueError(404, ["No league has that code."]), en.refused.noCode],
    ["join", new LeagueError(409, ["Every seat in this league is taken."]), en.refused.full],
    ["create", new LeagueError(429, ["Too many tries"]), en.refused.slowDown],
    ["start", new LeagueError(422, ["Extra inputs are not permitted"], unknownField), en.refused.cannotStart],
    ["start", new LeagueError(503, ["busy"]), en.refused.busy],
    ["start", new LeagueError(404, ["Not Found"]), en.refused.notYours],
    ["start", new LeagueError(400, ["Leagues are not available on this server."]), en.refused.noLeagues],
    ["create", new LeagueError(500, ["boom"]), en.refused.other(500)],
  ] as const)("%s %#", (action, error, said) => {
    expect(refusal(error, action, en)).toEqual({ text: said, problems: [] });
  });

  it("lists a 422's reasons as sent, under a heading in the member's language", () => {
    const e = new LeagueError(422, ["at most 3 players from one club: t_hol"]);
    expect(refusal(e, "pick", es)).toEqual({ text: es.refused.problems, problems: ["at most 3 players from one club: t_hol"] });
  });

  it("does not call another 422 from start a missing league feature: the server's reason shows", () => {
    const detail = "manager must be your own seat in this league: mgr_2";
    expect(refusal(new LeagueError(422, [detail], detail), "start", en)).toEqual({ text: en.refused.problems, problems: [detail] });
  });

  it("shows a 409 from a pick or a start as the server worded it, under a lead line in the member's language", () => {
    const kicked = "This gameweek has kicked off. Squads are fixed until it is recorded.";
    const playing = "A friend is playing this gameweek right now. Try again once it is over.";
    expect(refusal(new LeagueError(409, [kicked]), "pick", es)).toEqual({ text: es.refused.problems, problems: [kicked] });
    expect(refusal(new LeagueError(409, [playing]), "start", en)).toEqual({ text: en.refused.problems, problems: [playing] });
  });
});

describe("the member's token in this browser (cases 11, 12)", () => {
  const mine = { member_id: "mb_me", token: "tok", seat: 1, lang: "en" as const, name: "Sunday Five" };

  it("is kept per league and read back", () => {
    expect(remember("lg_0123456789ab", mine)).toBe(true);
    expect(keptInTabOnly("lg_0123456789ab")).toBe(false);
    const kept = (localStorage as unknown as { data: Map<string, string> }).data;
    expect(JSON.parse(kept.get("couch-gaffer:league:lg_0123456789ab")!)).toEqual(mine);
    expect(membership("lg_aaaaaaaaaaaa")).toBeNull();
    forget("lg_0123456789ab"); // a fresh tab: only what the browser kept
    const storage = fakeStorage();
    storage.data.set("couch-gaffer:league:lg_0123456789ab", JSON.stringify(mine));
    vi.stubGlobal("localStorage", storage);
    expect(membership("lg_0123456789ab")).toEqual(mine);
    expect(rememberedLeagues()).toEqual([{ league: "lg_0123456789ab", name: "Sunday Five" }]);
  });

  it("lasts for this tab when the browser will not keep it, and says so", () => {
    vi.stubGlobal("localStorage", fakeStorage({ write: true }));
    expect(remember("lg_0123456789ab", mine)).toBe(false);
    expect(keptInTabOnly("lg_0123456789ab")).toBe(true);
    expect(membership("lg_0123456789ab")).toEqual(mine);
    expect(page(ready([ME()], { tabOnly: true }))).toContain(LEAGUE_WORDS.en.storageNote);
    expect(page(ready([ME()]))).not.toContain(LEAGUE_WORDS.en.storageNote);
  });

  it("reads as no membership when storage cannot be read or holds something else", () => {
    vi.stubGlobal("localStorage", fakeStorage({ read: true }));
    expect(membership("lg_0123456789ab")).toBeNull();
    expect(rememberedLeagues()).toEqual([]);
    const storage = fakeStorage();
    storage.data.set("couch-gaffer:league:lg_0123456789ab", "{not json");
    storage.data.set("couch-gaffer:league:lg_aaaaaaaaaaaa", JSON.stringify({ token: 5 }));
    vi.stubGlobal("localStorage", storage);
    expect(membership("lg_0123456789ab")).toBeNull();
    expect(membership("lg_aaaaaaaaaaaa")).toBeNull();
  });

  it("renders the forms when storage throws on every call", () => {
    vi.stubGlobal("localStorage", fakeStorage({ read: true, write: true }));
    expect(text(renderToStaticMarkup(<LeagueHome />))).toContain(LEAGUE_WORDS.en.create.heading);
  });
});

describe("loading the league page (cases 9, 10)", () => {
  const NOT_FOUND = { status: 404, body: { detail: "Not Found" } };

  it("is not a member with no token kept when the server will not show the league, and sends no token", async () => {
    const calls = fakeFetch(() => NOT_FOUND);
    expect(await loadLeague("lg_0123456789ab", "es")).toEqual({ kind: "not-member", lang: "es" });
    expect(calls.map((c) => [c.url, c.headers.Authorization])).toEqual([
      ["/api/leagues/lg_0123456789ab", undefined], ["/api/leagues/lg_0123456789ab/season", undefined],
    ]); // prettier-ignore
  });

  it("shows a league anyone may read (the demo) read-only with no token: members and season, no picker, no Start", async () => {
    fakeFetch((url) => ({ status: 200, body: url.endsWith("/season") ? SEASON_EMPTY : league([ME(), FRIEND()], null) }));
    const state = await loadLeague("lg_demo", "en");
    expect(state).toMatchObject({ kind: "ready", member: null });
    const shown = page(state);
    expect(shown).toContain(LEAGUE_WORDS.en.readOnly);
    expect(shown).toMatch(/Members Ana .*Bea/);
    expect(shown).toContain(LEAGUE_WORDS.en.season.heading);
    expect(shown).not.toContain(LEAGUE_WORDS.en.squad.heading);
    expect(shown).not.toContain(LEAGUE_WORDS.en.start.button(1));
  });

  it("drops the kept token when the league API answers and still refuses it for this league", async () => {
    remember("lg_0123456789ab", { member_id: "mb_me", token: "other-leagues", seat: 1, lang: "es" });
    const calls = fakeFetch((url) => (url === "/api/leagues/lg_demo" ? { status: 200, body: league([], null) } : NOT_FOUND));
    expect(await loadLeague("lg_0123456789ab", "en")).toEqual({ kind: "not-member", lang: "es" });
    expect(membership("lg_0123456789ab")).toBeNull();
    expect(calls.map((c) => c.url)).toContain("/api/leagues/lg_demo");
  });

  it.each([
    ["a 500", { status: 500, body: { detail: "Internal Server Error" } }],
    ["a 503", { status: 503, body: { detail: "busy" } }],
    ["a network failure", null],
  ] as const)("keeps the token when the league read fails with %s, even while the league API answers", async (_, failure) => {
    const mine = { member_id: "mb_me", token: "tok", seat: 1, lang: "en" as const };
    remember("lg_0123456789ab", mine);
    const demo = () => new Response(JSON.stringify(league([], null)), { status: 200, headers: { "Content-Type": "application/json" } });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url === "/api/leagues/lg_demo") return demo(); // the probe would say the API answers
      if (failure === null) throw new TypeError("Failed to fetch");
      return new Response(JSON.stringify(failure.body), { status: failure.status, headers: { "Content-Type": "application/json" } });
    }));
    expect(await loadLeague("lg_0123456789ab", "en")).toMatchObject({ kind: "failed" });
    expect(membership("lg_0123456789ab")).toEqual(mine);
  });

  it("keeps the token when every league path is a 404 (no league API on this server), and says leagues are down", async () => {
    const mine = { member_id: "mb_me", token: "tok", seat: 1, lang: "es" as const };
    remember("lg_0123456789ab", mine);
    fakeFetch(() => NOT_FOUND);
    expect(await loadLeague("lg_0123456789ab", "en")).toEqual({ kind: "failed", lang: "es", text: LEAGUE_WORDS.es.leaguesDown });
    expect(membership("lg_0123456789ab")).toEqual(mine);
  });

  it("reads the league and season with the token, and a joiner's membership learns the league's name and code", async () => {
    remember("lg_0123456789ab", { member_id: "mb_me", token: "tok", seat: 1, lang: "en" });
    const calls = fakeFetch((url) => ({ status: 200, body: url.endsWith("/season") ? SEASON_EMPTY : league([ME()]) }));
    const state = await loadLeague("lg_0123456789ab", "en");
    expect(state).toMatchObject({ kind: "ready", league: { name: "Sunday Five" }, season: SEASON_EMPTY });
    expect(calls.map((c) => [c.url, c.headers.Authorization])).toEqual([
      ["/api/leagues/lg_0123456789ab", "Bearer tok"], ["/api/leagues/lg_0123456789ab/season", "Bearer tok"],
    ]); // prettier-ignore
    const kept = (localStorage as unknown as { data: Map<string, string> }).data.get("couch-gaffer:league:lg_0123456789ab");
    expect(JSON.parse(kept!)).toMatchObject({ name: "Sunday Five", code: "ABC234" });
    expect(rememberedLeagues()).toEqual([{ league: "lg_0123456789ab", name: "Sunday Five", code: "ABC234" }]);
  });
});

describe("the gameweek's players come from the league route (#178)", () => {
  it("takes the squad size and the cap from the route's squad_rules, and counts clubs by id", () => {
    const rules = { ...league([ME()]), squad_rules: { squad_size: 6, max_per_club: 4 } };
    const roster = gameweekRoster(rules)!;
    expect([roster.squadSize, roster.maxPerClub]).toEqual([6, 4]);
    expect(squadIssue(roster, FIVE, FIVE[0]!)).toEqual({ kind: "count", n: 5, size: 6 });
    const hol = ["p_hol_1", "p_hol_2", "p_hol_3", "p_hol_4", "p_hol_5", "p_sal_1"];
    expect(squadIssue(roster, hol, "p_hol_1")).toEqual({ kind: "club", club: "Hollinsford", cap: 4 });
    // two clubs with one name are two clubs, as the server counts them
    const twins = { ...league([ME()]), roster: league([ME()]).roster!.map((p) => ({ ...p, club_name: "United" })) };
    expect(squadIssue(gameweekRoster(twins)!, ["p_hol_1", "p_hol_2", "p_hol_3", "p_sal_1", "p_sal_2"], "p_hol_1")).toBeNull();
  });

  it("reads the roster and the cap from the league's gameweek", () => {
    expect(gameweekRoster(league([ME()]))).toEqual(ROSTER);
    expect(gameweekRoster(league([ME()], "mb_me", false))).toBeNull();
  });

  it("says the players could not be loaded when the league sends none, with no picker", () => {
    const shown = page(ready([ME()], { league: league([ME()], "mb_me", false) }));
    expect(shown).toContain(LEAGUE_WORDS.en.noPlayers);
    expect(shown).not.toContain(LEAGUE_WORDS.en.squad.save);
  });
});

describe("the league page (cases 6, 9, 13, 16, 17, 18)", () => {
  it("tells a browser that is not a member so, with a way to join, and no picker or start", () => {
    const shown = page({ kind: "not-member", lang: "en" });
    expect(shown).toContain("This browser is not a member of this league");
    expect(shown).toContain("Join with a code");
    expect(shown).not.toContain(LEAGUE_WORDS.en.squad.heading);
    expect(shown).not.toContain(LEAGUE_WORDS.en.start.button(1));
  });

  it("shows the join code, the share link, every seat and the gameweek", () => {
    const shown = page(ready([ME(), FRIEND(), seat(3), seat(4), seat(5)]));
    expect(shown).toContain("Sunday Five");
    expect(shown).toContain("Gameweek 1");
    expect(shown).toContain("ABC234");
    expect(renderToStaticMarkup(<LeaguePage league="x" initial={ready([ME()])} />)).toContain('>Copy invite link</button>'); // #196: the link is copied, not shown
    expect(renderToStaticMarkup(<LeaguePage league="x" initial={ready([ME()])} />)).not.toContain("join?code=");
    expect(shown).toMatch(/Ana You Not picked yet/);
    expect(shown).toMatch(/Bot 5 Bot/);
  });

  it("offers a member who has not picked the gameweek's players by club, and says a squad is picked for them", () => {
    const shown = page(ready([ME()]));
    expect(shown).toContain(LEAGUE_WORDS.en.squad.heading);
    expect(shown).toContain("Hollinsford");
    expect(shown).toContain("Show all (12 more)"); // #196: ten rows, then Show all
    expect(shown).toContain(LEAGUE_WORDS.en.start.unpicked);
    expect(shown).toContain(LEAGUE_WORDS.en.start.button(1));
  });

  it("shows a member who has picked their five with clubs and captain, and their seat as picked", () => {
    const shown = page(ready([ME({ squad: { players: FIVE, captain: "p_sal_9" } })]));
    expect(shown).toContain(LEAGUE_WORDS.en.squad.yours);
    // #196: a squad is five places, each with the player, position and club
    expect(shown).toContain("Hollinsford Player 9 Midfielder · Hollinsford");
    expect(renderToStaticMarkup(<LeaguePage league="x" initial={ready([ME({ squad: { players: FIVE, captain: "p_sal_9" } })])} />))
      .toMatch(/class="e-slot full c captain"><b><span class="nmx" title="Saltmere Player 9">Saltmere Player 9<\/span><abbr class="cap" title="Captain">C<\/abbr><\/b>/);
    expect(shown).toMatch(/Ana You Picked/);
    expect(shown).not.toContain(LEAGUE_WORDS.en.start.unpicked);
    expect(shown).not.toContain(LEAGUE_WORDS.en.squad.save);
  });

  it("shows whether other friends have picked when the server says, and not shown otherwise", () => {
    expect(page(ready([ME(), FRIEND({ picked: true }), seat(3, { member_id: "mb_c", is_bot: false, display_name: "Cy", picked: false })])))
      .toMatch(/Bea Picked.*Cy Not picked yet/);
    expect(page(ready([ME(), FRIEND()]))).toMatch(/Bea Not shown/);
    expect(hasPicked(FRIEND(), "mb_me")).toBeNull();
    expect(hasPicked(ME({ squad: { players: FIVE, captain: FIVE[0]! } }), "mb_me")).toBe(true);
  });

  it("does not wear the group screen's league-table class, whose rules stack every list item at the top", () => {
    const classes = (html: string) => [...html.matchAll(/class="([^"]*)"/g)].flatMap((m) => m[1]!.split(" "));
    expect(classes(renderToStaticMarkup(<LeaguePage league="x" initial={ready([ME(), FRIEND()])} />))).not.toContain("league");
    expect(classes(renderToStaticMarkup(<LeagueHome />))).not.toContain("league");
  });

  it("says no gameweek is played yet until one is, then shows the table", () => {
    expect(page(ready([ME()]))).toContain("No gameweek played yet.");
    const season: Season = { ...SEASON_EMPTY, played: [{ gameweek: 1, seed: 5 }],
                             table: [{ ...SEASON_EMPTY.table[0]!, points: 21.5, played: 1 }] }; // prettier-ignore
    const shown = page(ready([ME()], { season }));
    expect(shown).not.toContain("No gameweek played yet.");
    expect(shown).toMatch(/Season table # Name Points Played 1 Ana 21\.5 1/);
  });
});

describe("the squad picker (cases 14, 15)", () => {
  it("holds a squad to the server's cap from one club, three for this gameweek", () => {
    expect(squadIssue(ROSTER, FIVE, FIVE[0]!)).toBeNull();
    expect(squadIssue(ROSTER, ["p_hol_1", "p_hol_2", "p_hol_3", "p_hol_4", "p_sal_1"], "p_hol_1"))
      .toEqual({ kind: "club", club: "Hollinsford", cap: 3 });
    expect(squadIssue(ROSTER, FIVE.slice(0, 4), FIVE[0]!)).toEqual({ kind: "count", n: 4, size: 5 });
    expect(squadIssue(ROSTER, FIVE, null)).toEqual({ kind: "captain" });
    expect(squadIssue(ROSTER, FIVE, "p_hol_1")).toEqual({ kind: "captain" });
  });

  const picker = (initial: { players: string[]; captain: string } | null) =>
    renderToStaticMarkup(<SquadPicker roster={ROSTER} w={LEAGUE_WORDS.en} initial={initial} save={async () => {}} initialAll />);

  // #196: the button's class is the new design's
  it("says what is missing and will not save an invalid squad", () => {
    const html = picker({ players: FIVE.slice(0, 4), captain: "x" });
    expect(text(html)).toContain("Pick 1 more: a squad has 5 players.");
    expect(html).toMatch(/<button type="submit" class="btn acc" disabled="">Save my squad/);
  });

  // #196: Add buttons in a table and captain buttons in the five places, in place of checkboxes and radios
  it("stops a sixth pick once five are chosen, and offers the captain among the five only", () => {
    const html = picker({ players: FIVE, captain: "p_hol_9" });
    // FIVE holds three from Saltmere: its other eight say the club is full; Hollinsford's nine are Add, disabled
    expect(html.match(/<button type="button" class="add" aria-label="Add [^"]*" disabled="">/g)).toHaveLength(11 - 2);
    expect(html.match(/<span class="full-club">Saltmere is full<\/span>/g)).toHaveLength(11 - 3);
    expect(html.match(/class="add out" aria-label="Remove /g)).toHaveLength(5);
    expect(html.match(/aria-label="Make [^"]* captain"/g)).toHaveLength(5);
    expect(html).toMatch(/<button type="submit" class="btn acc">Save my squad/);
  });
});

describe("the join link and the forms (cases 7, 8)", () => {
  it("builds the share link and reads its code", () => {
    expect(shareLink("https://x.test", "ABC234")).toBe("https://x.test/league/join?code=ABC234");
    expect(codeFrom("?code=abc234")).toBe("ABC234");
    expect(codeFrom("?code=ABC")).toBe("");
    expect(codeOk(" abc234 ")).toBe(true);
    expect(codeOk("ABC-23")).toBe(false);
  });

  it("warns when the share link is for a league this browser already has a seat in, and offers that seat", () => {
    remember("lg_0123456789ab", { member_id: "mb_me", token: "tok", seat: 1, lang: "en", name: "Sunday Five", code: "ABC234" });
    const html = renderToStaticMarkup(<LeagueHome search="?code=abc234" />);
    expect(text(html)).toContain(LEAGUE_WORDS.en.alreadyIn.text("Sunday Five"));
    expect(html).toContain('<a class="link" href="/league/lg_0123456789ab">Open my seat</a>'); // #196: styled as a link
    expect(text(renderToStaticMarkup(<LeagueHome search="?code=XYZ789" />))).not.toContain("already has a seat");
  });

  // #196: one form at a time under "Start a league" and "I have a code"; a share link opens the code form
  it("opens the join form with the code filled in, from the share link", () => {
    const html = renderToStaticMarkup(<LeagueHome search="?code=abc234" />);
    expect(html).toContain('value="ABC234"');
    expect(html).toContain('<form class="e-form" aria-label="Join a league">');
    expect(html).not.toContain('aria-label="Start a league"');
    expect(html).toContain('<button type="button" aria-pressed="true">I have a code</button>');
    const plain = renderToStaticMarkup(<LeagueHome />);
    expect(plain).toContain('<form class="e-form" aria-label="Start a league">');
    expect(plain).not.toContain('aria-label="Join a league"');
  });

  // #202 case 1: the style and language choices show open on /league, and Change reads as an open toggle
  it("shows the style and language groups open by default, with Change as an expanded toggle", () => {
    for (const html of [renderToStaticMarkup(<LeagueHome />), renderToStaticMarkup(<LeagueHome search="?code=abc234" />)]) {
      expect(html).toContain('<div class="e-prefs">');
      expect(html).toContain('role="group" aria-labelledby="pref-style"');
      expect(html).toContain('role="group" aria-labelledby="pref-lang"');
      expect(html).toContain(`aria-expanded="true">${LEAGUE_WORDS.en.change}</button>`);
    }
  });

  // #202 case 2, folded state: the same link folds them away (the press itself is checked in a browser)
  it("hides the groups and marks Change collapsed when folded", () => {
    const html = renderToStaticMarkup(<LeagueHome initialPrefsOpen={false} />);
    expect(html).not.toContain("e-prefs");
    expect(html).not.toContain("pref-style");
    expect(html).toContain(`aria-expanded="false">${LEAGUE_WORDS.en.change}</button>`);
  });
});

describe("Spanish (case 22)", () => {
  /** Every string a dictionary holds, functions called with a sample argument, by path. */
  const leaves = (o: unknown, path = ""): [string, string][] =>
    typeof o === "string" ? [[path, o]]
    : typeof o === "function" ? [[path, String((o as (...a: unknown[]) => unknown)(3, 2))]]
    : Object.entries(o as object).flatMap(([k, v]) => leaves(v, `${path}.${k}`)); // prettier-ignore

  it("has every label in both languages, and the Spanish ones are Spanish", () => {
    const en = new Map(leaves(LEAGUE_WORDS.en));
    const es = new Map(leaves(LEAGUE_WORDS.es));
    expect([...es.keys()]).toEqual([...en.keys()]);
    const same = [...en].filter(([k, v]) => es.get(k) === v).map(([k]) => k);
    // "#", "Bot" and "Club" read the same in both (#196 adds the player table's "Club")
    expect(same).toEqual([".bot", ".squad.clubHeading", ".season.rank"]);
  });

  // #196: the page is "Play with your friends"; one form shows at a time, so each form is rendered
  it("renders the forms in Spanish when Spanish is chosen", () => {
    const create = text(renderToStaticMarkup(<LeagueHome initialLang="es" initialPrefsOpen />));
    const join = text(renderToStaticMarkup(<LeagueHome initialLang="es" search="?code=ABC234" />));
    for (const label of ["Juega con tus amigos", "Crear una liga", "Tengo un código", "Tu nombre", "Nombre de la liga",
      "Cómo funciona una liga", "Tu móvil: sencillo, en Español.", "Cambiar", "Cómo lo prefieres", "Idioma"]) {
      expect(create).toContain(label);
    }
    for (const label of ["Código", "Unirme", "Seis letras o números"]) expect(join).toContain(label);
    expect(`${create} ${join}`).not.toMatch(/Start a league|I have a code|Your name|How a league works/);
  });

  // #196: the start button names the gameweek, and the picker's table is in Spanish too
  it("renders the league page in the member's own language", () => {
    const shown = page(ready([ME({ lang: "es" })]));
    for (const label of ["Jornada 1", "Invita a tus amigos", "Miembros", "Elige tu equipo", "Clasificación", "Empezar la jornada 1",
      "Jugador", "Posición", "Centrocampista", "Añadir", "Elige un jugador", "Tus ligas"]) {
      expect(shown).toContain(label);
    }
    expect(shown).not.toContain("Pick your squad");
    expect(page({ kind: "not-member", lang: "es" })).toContain("Este navegador no es miembro de esta liga");
  });
});
