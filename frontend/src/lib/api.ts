// The app server's HTTP API (backend/watchparty/app/main.py).
import type { HostDecision, League } from "./messages";

export type Style = "analyst" | "casual";
export type Manager = League["managers"][number];

export interface Club {
  name: string;
  short_name: string;
  colour: string;
}

export interface Roster {
  league: League;
  players: Record<string, { name: string; club: string; colour: string; position: string; shirt: number }>;
  clubs: Record<string, Club>;
}

/** The single match's roster at the top level, as before; a gameweek's league and six clubs under `gameweek` (#155),
 * absent on a server without one. */
export interface Lobby extends Roster {
  gameweek?: Roster;
}

export interface Seat {
  session_id: string;
  phone_token: string;
  manager: string;
}

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const detail = await response.json().then((b: { detail?: unknown }) => b.detail, () => undefined);
    throw new Error(typeof detail === "string" ? detail : `The server said ${response.status}. Please try again.`);
  }
  return (await response.json()) as T;
}

export const getLobby = () => fetch("/api/lobby").then((r) => json<Lobby>(r));

export type Format = "gameweek" | "single";

export interface SessionRequest {
  seed: "demo" | number;
  manager: string;
  style: Style;
  lang: string;
  gameweek?: true;
}

/** The lobby's choices as the session request. A single match sends the body it always has: the backend refuses
 * a field it does not know (SessionRequest, extra="forbid"), so `gameweek` goes only with a gameweek (#94). */
export function sessionRequest(choice: Omit<SessionRequest, "gameweek"> & { format: Format }): SessionRequest {
  const { format, ...body } = choice;
  return format === "gameweek" ? { ...body, gameweek: true } : body;
}

/** The server refused a request it does not accept (422, for example a gameweek on a server without one): sending it
 * again fails the same way. */
export class Refused extends Error {}

export function createSession(body: SessionRequest) {
  return fetch("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then((r) => (r.status === 422 ? Promise.reject(new Refused("The server refused this request.")) : json<Seat>(r)));
}

/** A knockout the server accepted: whose agent, and the arm id it returned for this press. */
export interface Knockout {
  manager: string;
  arm: number;
}

/** Knock out one manager's agent: the next time it bids, that phone gets the code fallback (#90). */
export const knockOut = (session: string, token: string, manager: string): Promise<Knockout> =>
  fetch(`/api/sessions/${encodeURIComponent(session)}/chaos`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ manager }),
  })
    .then((r) => json<{ arm: number }>(r))
    .then(({ arm }) => ({ manager, arm }));

/** Armed until the screens reach the decision the server marked as spending this press (its arm id). The server
 * holds one press at a time, so a newer press spent (from another tab, or an every-agent press) means ours was
 * replaced: that clears it too. A decision still ahead of the playback clock `t` does not count: the phone has
 * not shown the plain stat yet. */
export const stillArmed = (k: Knockout | null, decisions: HostDecision[], t: number): k is Knockout =>
  k !== null && !decisions.some((d) => d.knocked_out && d.knocked_out.arm >= k.arm && d.t <= t);

export async function setStyle(session: string, token: string, style: Style): Promise<void> {
  const r = await fetch(`/api/sessions/${encodeURIComponent(session)}/style`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ style }),
  });
  if (!r.ok) throw new Error("The style could not be changed.");
}

/** Session secrets travel in the URL fragment, which browsers never send to a server. */
export function readFragment(hash: string): { token: string | null; manager: string | null } {
  const params = new URLSearchParams(hash.replace(/^#/, ""));
  return { token: params.get("token"), manager: params.get("manager") };
}

export function fragment(token: string, manager: string): string {
  return `#${new URLSearchParams({ token, manager }).toString()}`;
}
