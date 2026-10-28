// Leagues kept on the server (#96, #179): create and join with a code, pick a squad, read the league and its season,
// and start a watch party that plays the league's gameweek (contract on #177). The routes are
// backend/watchparty/store/api.py; a member's token is good for its own league only, and anything else is a 404.
import type { Seat, Style } from "./api";
import type { PhoneLang } from "./phoneText";

export interface Profile {
  display_name: string;
  style: Style;
  lang: PhoneLang;
}

export interface Joined {
  league_id: string;
  member_id: string;
  member_token: string;
  seat: number;
  join_code?: string; // a create only
}

export interface LeagueSeat {
  seat: number;
  member_id: string | null; // null: an empty seat, played by a bot
  display_name: string;
  is_bot: boolean;
  style: Style;
  lang: string;
  squad: { players: string[]; captain: string } | null; // the viewer's own only
  picked?: boolean; // when the server says, for every seat
}

export interface LeagueData {
  league_id: string;
  name: string;
  join_code: string | null;
  is_demo: boolean;
  you: string | null;
  gameweek: { number: number; status: string };
  seats: LeagueSeat[];
  // The open gameweek's players, the ones the squad check accepts, and its rules (#178). Absent on an older server.
  roster?: { id: string; name: string; position: string; club_id: string; club_name: string }[];
  squad_rules?: { squad_size: number; max_per_club: number };
}

/** The players a squad is picked from and the rules it must keep: the league's own gameweek as the server checks it.
 * Null when the server sends no roster, and the page says it cannot load the players. The one place that reads the
 * route's field names. */
export function gameweekRoster(league: LeagueData): GameRoster | null {
  const { roster, squad_rules: rules } = league;
  if (!roster || roster.length === 0 || !rules) return null;
  const players = Object.fromEntries(roster.map((p) => [p.id, { name: p.name, club: p.club_name, clubId: p.club_id,
                                                                position: p.position }])); // prettier-ignore
  return { players, squadSize: rules.squad_size, maxPerClub: rules.max_per_club };
}

export interface GameRoster {
  players: Record<string, { name: string; club: string; clubId: string; position: string; colour?: string }>;
  squadSize: number;
  maxPerClub: number;
}

export interface SeasonRow {
  rank: number;
  member_id: string;
  seat: number;
  display_name: string;
  is_bot: boolean;
  points: number;
  played: number;
}

export interface Season {
  league_id: string;
  table: SeasonRow[];
  played: { gameweek: number; seed: number }[];
}

/** A refusal from a league route: its status, and the server's reasons as it sent them. */
export class LeagueError extends Error {
  constructor(readonly status: number, readonly problems: string[], readonly detail?: unknown) {
    super(problems.join("; "));
  }
}

/** The reasons in a refusal's `detail`: a sentence, the store's list of problems, or FastAPI's validation list. */
export function problemsOf(detail: unknown, status: number): string[] {
  if (typeof detail === "string") return [detail];
  if (!Array.isArray(detail) || detail.length === 0) return [`The server said ${status}.`];
  return detail.map((d: unknown) => (typeof d === "string" ? d : String((d as { msg?: unknown }).msg ?? JSON.stringify(d))));
}

async function answer<T>(response: Response): Promise<T> {
  if (response.ok) return (await response.json()) as T;
  const detail = await response.json().then((b: { detail?: unknown }) => b.detail, () => undefined);
  throw new LeagueError(response.status, problemsOf(detail, response.status), detail);
}

const post = (path: string, body: unknown, token?: string) =>
  fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(body),
  });

const id = (league: string) => encodeURIComponent(league);

export const createLeague = (name: string, me: Profile) =>
  post("/api/leagues", { name, ...me }).then((r) => answer<Joined>(r));

export const joinLeague = (code: string, me: Profile) =>
  post("/api/leagues/join", { code, ...me }).then((r) => answer<Joined>(r));

const bearer = (token: string | null): Record<string, string> => (token ? { Authorization: `Bearer ${token}` } : {});

/** The league as `token`'s member sees it; with no token, only the demo league answers (store/api.py reader_of). */
export const getLeague = (league: string, token: string | null) =>
  fetch(`/api/leagues/${id(league)}`, { headers: bearer(token) }).then((r) => answer<LeagueData>(r));

export const getSeason = (league: string, token: string | null) =>
  fetch(`/api/leagues/${id(league)}/season`, { headers: bearer(token) }).then((r) => answer<Season>(r));

export const DEMO_LEAGUE = "lg_demo"; // store/service.py DEMO_LEAGUE, seeded on every start

/** Whether this server's league API is mounted and answering: the demo league reads without a token. A 404 for a
 * member's league means "not your token" only when this is true; without DATABASE_URL every league path is a 404,
 * and a kept token must survive that. */
export const leaguesAnswer = () =>
  fetch(`/api/leagues/${DEMO_LEAGUE}`).then((r) => r.ok, () => false);

export const pickSquad = (league: string, gameweek: number, token: string, players: string[], captain: string) =>
  fetch(`/api/leagues/${id(league)}/gameweeks/${gameweek}/squad`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ players, captain }),
  }).then((r) => answer<{ gameweek: number; players: string[]; captain: string }>(r));

/** The watch party for the league's current gameweek, with the caller's seat as the viewer (#177). */
export const startLeagueSession = (league: string, seat: LeagueSeat, token: string) =>
  post("/api/sessions", { league_id: league, manager: `mgr_${seat.seat}`, style: seat.style, lang: seat.lang }, token)
    .then((r) => answer<Seat>(r));

// --- the join code and its link ---------------------------------------------------------------------------------

const CODE = /^[A-Za-z0-9]{6}$/; // JoinLeague.code in store/api.py

export const cleanCode = (code: string) => code.trim().toUpperCase();
export const codeOk = (code: string) => CODE.test(cleanCode(code));
export const shareLink = (origin: string, code: string) => `${origin}/league/join?code=${encodeURIComponent(code)}`;

/** The code a share link carries, or "" when it carries none that could be one. */
export function codeFrom(search: string): string {
  const code = cleanCode(new URLSearchParams(search).get("code") ?? "");
  return CODE.test(code) ? code : "";
}

// --- membership in this browser -----------------------------------------------------------------------------------

export interface Membership {
  member_id: string;
  token: string;
  seat: number;
  lang: PhoneLang;
  name?: string; // the league's, for the list of this browser's leagues
  code?: string; // its join code, so a share link for a league this browser is in finds the seat it has
}

const KEY = "couch-gaffer:league:";
const memory = new Map<string, Membership>(); // this tab's copy of every membership it made
const tabOnly = new Set<string>(); // leagues whose membership the browser would not keep

/** Keeps the membership; false when the browser refused it (private mode, storage full or blocked), in which case
 * it lasts as long as this tab. */
export function remember(league: string, member: Membership): boolean {
  memory.set(league, member);
  try {
    localStorage.setItem(KEY + league, JSON.stringify(member));
    tabOnly.delete(league);
    return true;
  } catch {
    tabOnly.add(league);
    return false;
  }
}

/** Whether this league's membership lasts only as long as this tab. */
export const keptInTabOnly = (league: string) => tabOnly.has(league);

const isMembership = (m: unknown): m is Membership =>
  typeof m === "object" && m !== null && typeof (m as Membership).token === "string"
  && typeof (m as Membership).member_id === "string" && typeof (m as Membership).seat === "number"; // prettier-ignore

/** This browser's membership of the league, or null: none kept, storage unreadable, or a value that is not one. */
export function membership(league: string): Membership | null {
  const here = memory.get(league);
  if (here) return here;
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(KEY + league) ?? "null");
    return isMembership(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

/** Drops a membership the server no longer accepts. */
export function forget(league: string): void {
  memory.delete(league);
  tabOnly.delete(league);
  try {
    localStorage.removeItem(KEY + league);
  } catch {
    // nothing kept, or nothing can be: either way it is gone from this tab
  }
}

/** Every league this browser is a member of, by id. */
export function rememberedLeagues(): { league: string; name: string; code?: string }[] {
  const found = new Map<string, Membership>();
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (!key?.startsWith(KEY)) continue;
      const league = key.slice(KEY.length);
      const m = membership(league);
      if (m) found.set(league, m);
    }
  } catch {
    // unreadable storage: only this tab's
  }
  for (const [league, m] of memory) found.set(league, m);
  return [...found].map(([league, m]) => ({ league, name: m.name ?? league, code: m.code }));
}

/** The league this browser already holds a seat in under this join code, if any. */
export const leagueWithCode = (code: string) =>
  rememberedLeagues().find((m) => m.code !== undefined && m.code === cleanCode(code)) ?? null;

// --- the squad --------------------------------------------------------------------------------------------------

export type SquadIssue = { kind: "count"; n: number; size: number } | { kind: "club"; club: string; cap: number } | { kind: "captain" };

/** Why this squad cannot be saved yet, first reason only; null when it can. The cap is the server's own number for
 * this gameweek; the server checks again and decides. */
export function squadIssue(roster: GameRoster, players: string[], captain: string | null): SquadIssue | null {
  if (players.length !== roster.squadSize) return { kind: "count", n: players.length, size: roster.squadSize };
  const cap = roster.maxPerClub;
  const per = new Map<string, number>();
  for (const p of players) {
    const club = roster.players[p]?.clubId ?? p; // by id, as the server counts: two clubs may share a name
    per.set(club, (per.get(club) ?? 0) + 1);
  }
  const crowded = [...per].find(([, n]) => n > cap);
  if (crowded) {
    const name = Object.values(roster.players).find((p) => p.clubId === crowded[0])?.club ?? crowded[0];
    return { kind: "club", club: name, cap };
  }
  if (!captain || !players.includes(captain)) return { kind: "captain" };
  return null;
}

/** Whether a seat has picked for the current gameweek: the server's flag when it sends one, the viewer's own squad
 * otherwise; null when this page cannot know (another friend's squad is never sent). */
export function hasPicked(seat: LeagueSeat, you: string | null): boolean | null {
  if (typeof seat.picked === "boolean") return seat.picked;
  if (seat.member_id !== null && seat.member_id === you) return seat.squad !== null;
  return null;
}
