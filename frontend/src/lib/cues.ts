// Which cues a screen shows: at show_at on the delayed clock, for at least min_dwell_ms, gone at expires_at,
// and a cue that `replaces` another (a stat card upgraded to an explanation) takes its place.
import type { Cue } from "./messages";

export type Evidence = NonNullable<Cue["payload"]["evidence"]>[number];
export type TableRow = NonNullable<Cue["payload"]["table"]>[number];

/** The cues still standing: each one replaced by a later cue is dropped, and the replacement keeps its slot. */
export function current(cues: readonly Cue[]): Cue[] {
  const replaced = new Set(cues.flatMap((c) => (c.replaces ? [c.replaces] : [])));
  return cues.filter((c) => !replaced.has(c.cue_id));
}

/** The cue_id a cue ultimately stands in for, so an upgrade does not restart the dwell. */
function root(cue: Cue, byId: Map<string, Cue>): string {
  let id = cue.cue_id;
  for (let c: Cue | undefined = cue; c?.replaces; c = byId.get(c.replaces)) id = c.replaces;
  return id;
}

export interface Slot {
  root: string; // the first cue of the chain on screen
  since: number; // real ms it went up
}

const CARD_KINDS = new Set<Cue["kind"]>(["stat", "explanation"]);

/**
 * The group screen's one graphic: the newest due card, unless the one on screen has not yet had its
 * minimum dwell. Returns the card (null for none) and the slot to keep for the next tick.
 */
export function groupCard(
  cues: readonly Cue[],
  t: number,
  now: number,
  slot: Slot | null,
): { card: Cue | null; slot: Slot | null } {
  const byId = new Map(cues.map((c) => [c.cue_id, c]));
  const standing = current(cues).filter((c) => CARD_KINDS.has(c.kind));
  const due = standing.filter((c) => c.show_at <= t && t < c.expires_at);
  const newest = due.reduce<Cue | null>(
    (best, c) => (!best || c.show_at > best.show_at || (c.show_at === best.show_at && c.seq > best.seq) ? c : best),
    null,
  );
  const onScreen = slot ? standing.find((c) => root(c, byId) === slot.root) : undefined;
  const dwelling = onScreen && slot && t < onScreen.expires_at && now - slot.since < onScreen.min_dwell_ms;
  if (onScreen && dwelling) return { card: onScreen, slot };
  if (!newest) return { card: null, slot: null };
  const id = root(newest, byId);
  return { card: newest, slot: slot?.root === id ? slot : { root: id, since: now } };
}

/** The host's reason for a moment, once it is due. */
export function hostReason(cues: readonly Cue[], momentId: string | undefined, t: number): Cue | undefined {
  return cues.find((c) => c.kind === "host_reason" && c.moment_id === momentId && c.show_at <= t);
}

/** The latest league table that is due, if any. */
export function latestTable(cues: readonly Cue[], t: number): TableRow[] | null {
  const tables = cues.filter((c) => c.kind === "table" && c.show_at <= t && c.payload.table);
  const last = tables.reduce<Cue | null>((best, c) => (!best || c.seq > best.seq ? c : best), null);
  return last ? [...(last.payload.table ?? [])] : null;
}

/** How far each manager moved between the two newest due tables, up positive: shown until the next table
 * (#49). Code, not a render effect, so a re-render every frame cannot clear it. */
export function rankMoves(cues: readonly Cue[], t: number): Map<string, number> {
  const tables = cues
    .filter((c) => c.kind === "table" && c.show_at <= t && c.payload.table)
    .sort((a, b) => a.seq - b.seq);
  const [before, after] = tables.slice(-2).map((c) => c.payload.table ?? []);
  if (!after) return new Map();
  const was = new Map(before?.map((r) => [r.manager, r.rank]));
  return new Map(after.map((r) => [r.manager, (was.get(r.manager) ?? r.rank) - r.rank]));
}

/** A lost-screen note belongs on a phone only when someone else took the screen (#89). */
function lostToSomeoneElse(cue: Cue, me: string | undefined): boolean {
  return cue.kind !== "lost_screen" || (cue.payload.winner !== undefined && cue.payload.winner !== me);
}

/** A phone's feed: every due cue, upgrades in place, newest first. `me` is the phone's own manager. */
export function phoneFeed(cues: readonly Cue[], t: number, me?: string): Cue[] {
  return current(cues)
    .filter((c) => c.show_at <= t && c.kind !== "tag" && lostToSomeoneElse(c, me))
    .sort((a, b) => b.show_at - a.show_at || b.seq - a.seq);
}

export type Contender = NonNullable<Cue["payload"]["contenders"]>[number];

/**
 * Where a close call stands on the group screen at match time t (#89):
 * - pitches: show_at to verdict_at, the two pitches take over the group screen;
 * - verdict: verdict_at for twice as long again, the verdict takes over the group screen;
 * - chosen: from then to expires_at, the normal screen with the chosen card and a verdict line.
 */
export type ClashState =
  | { phase: "pitches"; cue: Cue; contenders: Contender[] }
  | { phase: "verdict" | "chosen"; cue: Cue; winner: string; loser: string };

/** A clash the screen can show: two different contenders, the winner one of them, show_at < verdict_at < expires_at. */
function wellFormed(cue: Cue): boolean {
  const { contenders, verdict_at: verdictAt, winner } = cue.payload;
  if (cue.kind !== "clash" || !contenders || verdictAt === undefined || !winner) return false;
  const [a, b] = contenders;
  const distinct = a !== undefined && b !== undefined && a.manager !== b.manager;
  const ordered = cue.show_at < verdictAt && verdictAt < cue.expires_at;
  return distinct && ordered && contenders.some((c) => c.manager === winner);
}

/** When a clash's verdict hands the group screen back: it holds twice as long as the pitches did, in match time. */
export function handBackAt(cue: Cue): number {
  const verdictAt = cue.payload.verdict_at ?? cue.show_at;
  return Math.min(cue.expires_at, verdictAt + 2 * (verdictAt - cue.show_at));
}

/**
 * The close call on the group screen at match time t, nothing from expires_at. Derived from match time alone,
 * never a component timer, so a reload lands in the phase the match is at: after the hand-back, straight to the
 * normal view with the chosen card, never the pitches again.
 */
export function clashAt(cues: readonly Cue[], t: number): ClashState | null {
  const due = cues.filter((c) => wellFormed(c) && c.show_at <= t && t < c.expires_at);
  const cue = due.reduce<Cue | null>((best, c) => (!best || c.seq > best.seq ? c : best), null);
  if (!cue) return null;
  const contenders = cue.payload.contenders ?? [];
  const winner = cue.payload.winner ?? "";
  if (t < (cue.payload.verdict_at ?? 0)) return { phase: "pitches", cue, contenders };
  const loser = contenders.find((c) => c.manager !== winner)?.manager ?? "";
  return { phase: t < handBackAt(cue) ? "verdict" : "chosen", cue, winner, loser };
}

export interface GroupView {
  clash: ClashState | null; // the close call on screen, if any
  card: Cue | null; // the card in the lower third: after the verdict, the chosen one
  slot: Slot | null;
}

/** True when a card is about the clash's own moment. */
function sameMoment(card: Cue, clash: Cue): boolean {
  return card.moment_id !== undefined && card.moment_id === clash.moment_id;
}

/**
 * The group screen at match time t. While a clash takes the screen over (pitches, then verdict) nothing takes the
 * card slot, so the chosen card starts its dwell when the screen is handed back. After that the clash stays with
 * the card of its own moment; a card from an earlier moment gives way to it, and a later moment's card ends it.
 */
export function groupScreen(cues: readonly Cue[], t: number, now: number, slot: Slot | null): GroupView {
  const clash = clashAt(cues, t);
  if (clash && clash.phase !== "chosen") return { clash, card: null, slot: null };
  const shown = groupCard(cues, t, now, slot);
  if (!clash || !shown.card || sameMoment(shown.card, clash.cue)) return { clash, ...shown };
  if (shown.card.show_at > clash.cue.show_at) return { clash: null, ...shown };
  return { clash, card: null, slot: null };
}

/** The judge's manager is named "You" for the session (backend session.py), so a sentence about them says "you". */
export const YOU = "You";

/** "Priya's moment, over Leo's"; "Your moment, over Leo's"; "Priya's moment, over yours". */
export function verdictText(winner: string, loser: string): string {
  const theirs = loser === YOU ? "yours" : `${loser}'s`;
  return `${winner === YOU ? "Your" : `${winner}'s`} moment, over ${theirs}`;
}

type NoteLang = "en" | "es";
const LOST_SCREEN: Record<NoteLang, { headline: string; body: (name: string) => string }> = {
  en: { headline: "Shown on your phone", body: (name) => `The screen went to ${name === YOU ? "you" : name}.` },
  es: { headline: "Solo en tu teléfono", body: (name) => `La pantalla fue para ${name === YOU ? "ti" : name}.` },
};

export interface Note {
  lang: string;
  headline: string;
  body: string;
  translation?: string; // English detail under a note in another language
}

/**
 * What a lost-screen note says on a phone (#89). Text the backend wrote in a language (payload.lang) is shown as
 * written. Without one the backend wrote English: an English phone shows it, and any other phone gets the note in
 * its owner's language with the backend's English kept underneath as the detail.
 */
export function lostScreenNote(cue: Cue, ownerLang: string, names: Map<string, string>): Note {
  const { lang, headline, body, body_en: bodyEn, winner } = cue.payload;
  const name = names.get(winner ?? "") ?? winner ?? "";
  const english = lang?.startsWith("en") ? undefined : bodyEn;
  if (lang) return { lang, headline, body: body ?? LOST_SCREEN[lang.startsWith("es") ? "es" : "en"].body(name), translation: english };
  if (!ownerLang.startsWith("es")) return { lang: "en", headline, body: body ?? LOST_SCREEN.en.body(name) };
  return { lang: "es", headline: LOST_SCREEN.es.headline, body: LOST_SCREEN.es.body(name), translation: body };
}

export type Author = "ai" | "code" | null;

/** Who wrote a card's text: an agent (AI), code (a plain stat), or nobody to mark. A group explanation card says
 * who wrote its line in body_source; its source is who decided the screen, not who wrote the line (#138). */
export function writtenBy(cue: Cue): Author {
  const body = cue.payload.body_source;
  if (body) return body === "manager" ? "ai" : "code";
  if (cue.source === "manager" || cue.source === "host") return "ai";
  return cue.source === "fallback" ? "code" : null;
}

/** Text written by an agent is marked AI; code's plain cards are not. */
export function writtenByAI(cue: Cue): boolean {
  return writtenBy(cue) === "ai";
}

/**
 * Who decided what the group screen shows, apart from who wrote the line (#184):
 * - a plain stat card (source "code"): code, which puts up every key moment as it happens, before the scorer or
 *   host has picked;
 * - after a close call: the clash's own source, the host or its fallback;
 * - a host's reason for the card's moment: the host, since the backend sends one only on a host decision. The card
 *   itself says "fallback" whenever the chosen friend's agent was knocked out, even when the host chose it;
 * - otherwise the card's source. A knocked-out friend's card on a clear call also says "fallback", and no group cue
 *   tells that apart from a close call the fallback settled without a clash.
 */
export function decidedBy(card: Cue, reason: Cue | undefined, clash: Cue | undefined): Cue["source"] {
  if (clash) return clash.source;
  if (reason?.kind === "host_reason" && reason.moment_id === card.moment_id) return "host";
  return card.source;
}
