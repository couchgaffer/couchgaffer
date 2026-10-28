// The WebSocket stream as a discriminated union of the generated contract types.
import type { Cue } from "../types/cue.schema";
import type { Gameweek } from "../types/gameweek.schema";
import type { HostDecision } from "../types/host-decision.schema";
import type { League } from "../types/league.schema";
import type { MatchEvent } from "../types/match-event.schema";
import type { MatchFrame } from "../types/match-frame.schema";
import type { MatchMeta } from "../types/match-meta.schema";

export type { Cue, Gameweek, HostDecision, League, MatchEvent, MatchFrame, MatchMeta };

export type WsMessage =
  | { v: 1; type: "meta"; data: MatchMeta }
  | { v: 1; type: "gameweek"; data: Gameweek }
  | { v: 1; type: "league"; data: League }
  | { v: 1; type: "frame"; data: MatchFrame }
  | { v: 1; type: "event"; data: MatchEvent }
  | { v: 1; type: "decision"; data: HostDecision }
  | { v: 1; type: "cue"; data: Cue };

export type Screen = "group" | `phone:${string}`;

const TYPES = new Set(["meta", "gameweek", "league", "frame", "event", "decision", "cue"]);

/** A cheap shape check on what arrives; the backend tests validate every message against contracts/. */
export function parseMessage(raw: string): WsMessage | null {
  try {
    const m: unknown = JSON.parse(raw);
    if (typeof m !== "object" || m === null) return null;
    const { v, type, data } = m as { v?: unknown; type?: unknown; data?: unknown };
    if (v !== 1 || typeof type !== "string" || !TYPES.has(type) || typeof data !== "object" || data === null) {
      return null;
    }
    return m as WsMessage;
  } catch {
    return null;
  }
}
