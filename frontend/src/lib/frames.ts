// Player and ball positions at any match time, interpolated between the 3-second frames.
import type { MatchFrame } from "./messages";

export interface Positions {
  ball: { x: number; y: number };
  players: Map<string, { x: number; y: number }>;
}

const lerp = (a: number, b: number, f: number) => a + (b - a) * f;

/** Frames must be in time order. Before the first or after the last, the nearest frame is held. */
export function positionsAt(frames: readonly MatchFrame[], t: number): Positions | null {
  if (frames.length === 0) return null;
  let i = frames.findIndex((f) => f.t > t);
  if (i === -1) i = frames.length;
  const a = frames[Math.max(i - 1, 0)]!;
  const b = frames[Math.min(i, frames.length - 1)]!;
  const f = b.t > a.t ? Math.min(Math.max((t - a.t) / (b.t - a.t), 0), 1) : 0;
  const next = new Map(b.players.map((p) => [p.id, p]));
  const players = new Map<string, { x: number; y: number }>();
  for (const p of a.players) {
    const q = next.get(p.id) ?? p; // a player sent off is not in later frames
    players.set(p.id, { x: lerp(p.x, q.x, f), y: lerp(p.y, q.y, f) });
  }
  return { ball: { x: lerp(a.ball.x, b.ball.x, f), y: lerp(a.ball.y, b.ball.y, f) }, players };
}

/** Keep the frames still needed: everything from just before `t` onwards. */
export function pruneFrames(frames: MatchFrame[], t: number): MatchFrame[] {
  const i = frames.findIndex((f) => f.t > t);
  return i > 1 ? frames.slice(i - 2) : frames;
}
