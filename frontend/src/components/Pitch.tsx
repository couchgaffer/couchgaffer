// The pitch, 105 x 68 m, drawn from the frames: lines, mowing stripes and dots in the invented clubs' colours.
// The players in the latest key moment get a ring; LiveAction names them in an upright chip above.
import { positionsAt } from "../lib/frames";
import { tags } from "../lib/match";
import type { MatchEvent, MatchFrame, MatchMeta } from "../lib/messages";
import { words, type PhoneLang } from "../lib/phoneText";

const W = 105;
const H = 68;
const EDGE = 2.3; // a dot's radius with its ring: every dot stays inside the touchlines (#194)

/** A position on the pitch in metres, kept inside the lines so no dot sits on or past a touchline. */
export function onPitch(pos: { x: number; y: number }): [number, number] {
  const clamp = (v: number, max: number) => Math.min(max - EDGE, Math.max(EDGE, v));
  return [clamp(pos.x * W, W), clamp(pos.y * H, H)];
}

export function Pitch({ meta, frames, events, t, lang = "en" }: {
  meta: MatchMeta | null; frames: MatchFrame[]; events: MatchEvent[]; t: number; lang?: PhoneLang;
}) {
  const w = words(lang);
  const at = positionsAt(frames, t);
  const sides = meta ? [meta.home, meta.away] : [];
  const byId = new Map(sides.flatMap((side) => side.players.map((p) => [p.id, { ...p, side }] as const)));
  const labels = new Map(tags(events, t, lang).map((tag) => [tag.player, tag.label.charAt(0).toUpperCase() + tag.label.slice(1)]));
  const ringed = new Set(labels.keys());
  return (
    <svg
      className="pitch"
      viewBox={`-2 -2 ${W + 4} ${H + 4}`}
      role="img"
      aria-label={meta ? w.pitchOf(meta.home.name, meta.away.name) : w.pitch}
    >
      <rect x={-2} y={-2} width={W + 4} height={H + 4} className="grass" />
      {Array.from({ length: 10 }, (_, i) => (
        <rect key={i} x={(i * W) / 10} y={0} width={W / 20} height={H} className="stripe" />
      ))}
      <g className="lines">
        <rect x={0} y={0} width={W} height={H} />
        <line x1={W / 2} y1={0} x2={W / 2} y2={H} />
        <circle cx={W / 2} cy={H / 2} r={9.15} />
        <rect x={0} y={H / 2 - 20.16} width={16.5} height={40.32} />
        <rect x={W - 16.5} y={H / 2 - 20.16} width={16.5} height={40.32} />
        <rect x={0} y={H / 2 - 9.16} width={5.5} height={18.32} />
        <rect x={W - 5.5} y={H / 2 - 9.16} width={5.5} height={18.32} />
        <rect x={-1.5} y={H / 2 - 3.66} width={1.5} height={7.32} />
        <rect x={W} y={H / 2 - 3.66} width={1.5} height={7.32} />
      </g>
      {at &&
        [...at.players].map(([id, pos]) => {
          const p = byId.get(id);
          if (!p) return null;
          // Home in white, away outlined (#194): no club colours on the pitch
          const side = p.side === meta?.home ? "home" : "away";
          const [x, y] = onPitch(pos);
          return (
            <g key={id} transform={`translate(${x} ${y})`} className={ringed.has(id) ? `${side} ringed` : side}>
              {ringed.has(id) && <circle r={3.4} className="ring" />}
              {/* A black ring keeps two dots apart where they overlap */}
              <circle r={2.3} className="halo" />
              <circle r={2} className="player" />
              <text className="shirt" dy="0.35em">{p.shirt}</text>
            </g>
          );
        })}
      {/* The short labels beside a key moment's players (#194), on a black backing above every dot; LiveAction's
          chips say them aloud */}
      {at &&
        [...at.players].map(([id, pos]) => {
          const p = byId.get(id);
          const label = labels.get(id);
          if (!p || !label) return null;
          const [x, y] = onPitch(pos);
          const text = `${label} · ${p.name.split(" ").at(-1)}`;
          const width = text.length * 0.72 + 1.6; // about half the 1.4-unit type a character, plus the padding
          return (
            <g key={`label-${id}`} className="pl" transform={`translate(${x} ${y - 4.8})`} aria-hidden="true">
              <rect x={-width / 2} y={-1.3} width={width} height={2.6} />
              <text dy="0.35em">{text}</text>
            </g>
          );
        })}
      {at && <circle className="ball" cx={at.ball.x * W} cy={at.ball.y * H} r={0.8} />}
    </svg>
  );
}
