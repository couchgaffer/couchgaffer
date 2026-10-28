// The group screen's lower third and close calls, a phone's feed cards and the league table.
import type { ReactNode } from "react";

import type { Manager, Style } from "../lib/api";
import {
  decidedBy, hostReason, lostScreenNote, verdictText, writtenBy,
  type Author, type ClashState, type Contender, type GroupView, type TableRow,
} from "../lib/cues"; // prettier-ignore
import { minuteLabel } from "../lib/match";
import type { Cue } from "../lib/messages";
import { cardTitle, decimal, langAttr, momentWhat, phoneLang, watchRecap, words, type PhoneLang } from "../lib/phoneText";

/** Who wrote the text shown: `by` when the caller knows (a close call's winning pitch), else the cue's own. */
export function SourceMark({ cue, by, lang = "en" }: { cue: Cue; by?: Author; lang?: PhoneLang }) {
  const marks = words(lang).marks;
  const author = by ?? writtenBy(cue);
  if (author === "ai") {
    return (
      <span className="mark ai" title={marks.aiTitle}>
        {marks.ai}
      </span>
    );
  }
  if (author === "code") {
    return (
      <span className="mark fallback" title={marks.fallbackTitle}>
        {marks.fallback}
      </span>
    );
  }
  return null;
}

export function Avatar({ name, you }: { name: string; you?: boolean }) {
  return <span className={`avatar ${you ? "you" : ""}`} aria-hidden="true">{name.charAt(0)}</span>;
}

/** "+3 IP" in green, "-1 IP" in red: the points a moment was worth to this manager. */
function Points({ headline, lang }: { headline: string; lang?: string }) {
  const tone = headline.startsWith("+") ? "up" : headline.startsWith("-") ? "down" : "flat";
  return <span className={`points-chip ${tone}`} lang={lang}>{headline}</span>;
}

type Settled = Extract<ClashState, { phase: "verdict" | "chosen" }>;

/**
 * The group screen's body at match time t (#89). Normally the pitch and the lower third. A close call takes the
 * whole screen over: the two pitches, then the verdict; then it hands back to the pitch with the chosen card.
 * The pitch stays mounted underneath, and one live region announces the verdict and the cards, not the pitches.
 */
export function GroupStage({ view, cues, t, names, judge, pitch, clubs }: {
  view: GroupView; cues: readonly Cue[]; t: number; names: Map<string, string>; judge: string | null;
  pitch: ReactNode; clubs?: Map<string, string>; // a club's full name -> its short name, for code's stat line
}) {
  const { clash, card } = view;
  const pitches = clash?.phase === "pitches" ? clash : null;
  const verdict = clash?.phase === "verdict" ? clash : null;
  const after = clash?.phase === "chosen" ? clash : null;
  const reason = hostReason(cues, card?.moment_id ?? clash?.cue.moment_id, t);
  return (
    <>
      {/* The close call draws its own pitch; the verdict sits over this one, dimmed (#194) */}
      <div className="stage-main" hidden={pitches !== null}>{pitch}</div>
      {pitches && <ClashTakeover key={pitches.cue.cue_id} clash={pitches} names={names} judge={judge} pitch={pitch} />}
      <div className="group-live" aria-live="polite">
        {verdict && <VerdictTakeover key={verdict.cue.cue_id} verdict={verdict} reason={reason} names={names} judge={judge} />}
        {!pitches && !verdict && <Settle after={after} card={card} reason={reason} names={names} judge={judge} clubs={clubs} />}
      </div>
    </>
  );
}

/** After the hand-back: the chosen card with the verdict line, or the winner's pitch when no card of theirs is up. */
function Settle({ after, card, reason, names, judge, clubs }: {
  after: Settled | null; card: Cue | null; reason: Cue | undefined; names: Map<string, string>; judge: string | null; clubs?: Map<string, string>;
}) {
  const manager = card?.payload.manager;
  const chosen = card !== null && (!manager || !after || manager === after.winner);
  if (after && !chosen) return <VerdictCard key={`${after.cue.cue_id}-verdict`} verdict={after} reason={reason} names={names} judge={judge} />;
  return <LowerThird key={card?.cue_id ?? "idle"} card={card} reason={reason} names={names} judge={judge} verdict={after} clubs={clubs} />;
}

/** A close call takes the group screen over: the moment, then the two friends' pitches for it, large, side by side,
 * versus (#89). `pitch` is the moment on the pitch, shown above the two (#194). */
function ClashTakeover({ clash, names, judge, pitch }: {
  clash: Extract<ClashState, { phase: "pitches" }>; names: Map<string, string>; judge: string | null; pitch: ReactNode;
}) {
  const [a, b] = clash.contenders;
  const side = (c: Contender | undefined) => {
    if (!c) return null;
    const name = names.get(c.manager) ?? c.manager;
    return (
      <div className="contender">
        <Avatar name={name} you={c.manager === judge} />
        <div className="lt-meta">
          <span className="who">{name}</span>
          {/* Who wrote this pitch: the agent, or code's plain line (#138) */}
          {c.pitch_source && <SourceMark cue={clash.cue} by={c.pitch_source === "manager" ? "ai" : "code"} />}
        </div>
        {pointsOf(c.pitch) && <span className="bid">{pointsOf(c.pitch)}</span>}
        <p>{c.pitch}</p>
      </div>
    );
  };
  return (
    <section className="takeover clash-takeover" aria-label="Close call">
      <div className="takeover-head">
        <span className="mark host">{clash.cue.payload.headline}</span>
        <span>The host is choosing whose moment to show</span>
      </div>
      <div className="clash-pitch">{pitch}</div>
      <div className="contenders">
        {side(a)}
        <span className="versus">vs</span>
        {side(b)}
      </div>
    </section>
  );
}

/** The verdict, large, with the host's reason, for a moment before the screen goes back to the pitch. */
function VerdictTakeover({ verdict, reason, names, judge }: {
  verdict: Settled; reason: Cue | undefined; names: Map<string, string>; judge: string | null;
}) {
  const name = names.get(verdict.winner) ?? verdict.winner;
  const loser = names.get(verdict.loser) ?? verdict.loser;
  return (
    <section className="takeover verdict-takeover">
      <Avatar name={name} you={verdict.winner === judge} />
      <span className="mark host">Verdict</span>
      <h2><VerdictWords winner={name} loser={loser} /></h2>
      {reason?.payload.body && <p className="host-line"><span className="host-lead">Host</span> {reason.payload.body}</p>}
      {/* The losing friend's phone gets a note (#89); the room sees that it does */}
      <p className="lost-note">
        {verdict.loser === judge ? "Your phone" : `${loser}'s phone`} gets a note: the moment lost a close call, and it
        stays in the feed.
      </p>
    </section>
  );
}

/**
 * Code's plain stat card arrives as one string (backend app/cues.py `stat_headline`, and session.py `_stat_headline`
 * in a gameweek): "Kestlebury Athletic v Saltmere Rovers: Wide: Malik Tennant, xG 0.16". The lower third shows the
 * event and the player as its title ("Wide: Malik Tennant") and the fixture and the numbers as a small line under it
 * ("KES v SAL · xG 0.16", with `clubs` mapping each club's full name to its short one) (#194). A string that does not
 * fit the pattern is the title whole.
 */
export function statLine(headline: string, clubs: Map<string, string> = new Map()): { title: string; detail?: string } {
  const fixture = /^([^:]+?) v ([^:]+?): (.+)$/.exec(headline);
  const rest = fixture?.[3] ?? headline;
  const xg = /^(.+), (xG \d+(?:\.\d+)?)$/.exec(rest);
  const short = (club: string) => clubs.get(club) ?? club;
  const teams = fixture?.[1] && fixture[2] ? `${short(fixture[1])} v ${short(fixture[2])}` : undefined;
  const detail = [teams, xg?.[2]].filter((d): d is string => Boolean(d));
  return { title: xg?.[1] ?? rest, ...(detail.length > 0 ? { detail: detail.join(" · ") } : {}) };
}

/** The group screen's lower third: whose moment it is, what it meant to them, and the host's call. After a close
 * call it carries the verdict line. */
export function LowerThird({ card, reason, names, judge, verdict, clubs }: {
  card: Cue | null; reason: Cue | undefined; names: Map<string, string>; judge: string | null;
  verdict?: Settled | null; clubs?: Map<string, string>;
}) {
  if (!card) return null; // #194: no placeholder sentence; the pitch carries the screen until the first moment
  const manager = card.payload.manager ?? "";
  const name = names.get(manager);
  const own = name ? card.payload.headline.replace(`${name}: `, "") : card.payload.headline;
  // Every card's headline goes through the stat line: code's fixture and numbers move to a grey line (#194)
  const head = statLine(own, clubs);
  const points = pointsOf(own) ?? pointsOf(card.payload.body ?? "");
  // A headline that is only the points: the body becomes the headline, the points stand at the right
  const onlyPoints = points !== undefined && head.title.trim() === points;
  const title = onlyPoints && card.payload.body ? card.payload.body : head.title;
  const body = onlyPoints ? undefined : card.payload.body;
  return (
    <section className={`lower-third ${card.source}`}>
      {name ? <Avatar name={name} you={manager === judge} /> : <span className="avatar stat" aria-hidden="true">!</span>}
      <div className="lt-body">
        {verdict && <VerdictLine verdict={verdict} names={names} />}
        <div className="lt-meta">
          <span className="who">{card.kind === "stat" ? "Key moment" : name}</span>
          <SourceMark cue={card} />
          {/* Who decided the screen, beside who wrote the line (#184): shown even when the host's reason is missing */}
          <DecidedMark decider={decidedBy(card, reason, verdict?.cue)} />
        </div>
        <h2>{title}</h2>
        {head.detail && <p className="lt-detail">{head.detail}</p>}
        {body && <p>{body}</p>}
        {reason?.payload.body && (
          <p className="host-line">
            <span className="host-lead">Host</span> {reason.payload.body}
          </p>
        )}
      </div>
      {points && <span className="lt-points">{points.replace(/ IP$/, "")}<small>IP</small></span>}
    </section>
  );
}

/**
 * "+6 IP": the points figure a line is certainly about, which the lower third and a close call's claims show large
 * (#194). The group cue carries no points field, so only two cases count: code's plain line ("+8 IP for Priya from
 * this moment (goal).") and a line holding exactly one signed figure. A line with two figures ("Leo drops -2 IP, as
 * she gains +6 IP") or only an unsigned one ("6 IP") gets none; the line itself still says it.
 */
export function pointsOf(text: string): string | undefined {
  const plain = /^([+\-\u2212]\d+(?:[.,]\d+)?) IP for .+ from this moment \(.+\)\.$/.exec(text.trim());
  if (plain?.[1]) return `${plain[1]} IP`;
  const signed = [...text.matchAll(/(?:^|[\s(])([+\-\u2212]\d+(?:[.,]\d+)?) IP\b/g)].map((m) => m[1]);
  return signed.length === 1 ? `${signed[0]} IP` : undefined;
}

/** "Jo's moment, over Priya's", with the winner's name in the accent and never the loser's (#194). */
function VerdictWords({ winner, loser }: { winner: string; loser: string }) {
  const words = verdictText(winner, loser);
  const lead = winner === "You" ? "Your" : `${winner}'s`;
  return <><span className="winner">{lead}</span>{words.slice(lead.length)}</>;
}

/** Who decided the group screen: code on a plain stat card, code's scorer on a clear call, the host agent on a close
 * one, or the fallback. */
const DECIDED: Partial<Record<Cue["source"], string>> = {
  code: "Automatic", scorer: "Scorer's call", host: "Host's call", fallback: "Fallback",
};

function DecidedMark({ decider }: { decider: Cue["source"] }) {
  const label = DECIDED[decider];
  if (!label) return null;
  return <span className={`mark decided ${decider}`}>{label}</span>;
}

function VerdictLine({ verdict, names }: { verdict: Settled; names: Map<string, string> }) {
  const winner = names.get(verdict.winner) ?? verdict.winner;
  const loser = names.get(verdict.loser) ?? verdict.loser;
  return (
    <p className="verdict-line">
      <span className="verdict-band">Verdict</span> {verdictText(winner, loser)}
    </p>
  );
}

function VerdictCard({ verdict, reason, names, judge }: {
  verdict: Settled; reason: Cue | undefined; names: Map<string, string>; judge: string | null;
}) {
  const name = names.get(verdict.winner) ?? verdict.winner;
  const won = verdict.cue.payload.contenders?.find((c) => c.manager === verdict.winner);
  const pitch = won?.pitch ?? "";
  // The pitch's own writer, not the clash's source, which says who decided (#138)
  const by: Author | undefined = won?.pitch_source ? (won.pitch_source === "manager" ? "ai" : "code") : undefined;
  return (
    <section className="lower-third host">
      <Avatar name={name} you={verdict.winner === judge} />
      <div className="lt-body">
        <VerdictLine verdict={verdict} names={names} />
        <div className="lt-meta">
          <span className="who">{name}</span>
          <SourceMark cue={verdict.cue} by={by} />
          <DecidedMark decider={verdict.cue.source} />
        </div>
        <h2>{pitch}</h2>
        {reason?.payload.body && (
          <p className="host-line">
            <span className="host-lead">Host</span> {reason.payload.body}
          </p>
        )}
      </div>
    </section>
  );
}

/** A phone's note that its owner's moment lost the group screen (#89): shown here, not on the big screen. */
function LostScreenNote({ cue, ownerLang, names, fixture }: {
  cue: Cue; ownerLang: string; names: Map<string, string>; fixture?: string;
}) {
  const note = lostScreenNote(cue, ownerLang, names);
  return (
    <li className="feed-card lost_screen" lang={note.lang}>
      <div className="fc-head">
        <span className="minute-pill">{minuteLabel(cue.show_at)}</span>
        <span className="fc-title">{note.headline}</span>
        <FixtureTag text={fixture} lang={phoneLang(ownerLang)} />
      </div>
      <p className="fc-body">{note.body}</p>
      {note.translation && <p className="translation" lang="en">{note.translation}</p>}
    </li>
  );
}

export interface MomentTitle {
  what: string; // "Save", "Goal"
  who?: string;
}

/** In a gameweek, which fixture a feed card is from and its score then: "KES 1-0 RAV" (#94). */
function FixtureTag({ text, lang }: { text?: string; lang: PhoneLang }) {
  if (!text) return null;
  return <span className="fixture-tag" title={words(lang).scoreThen}>{text}</span>;
}

/** Code's plain casual line, "+8.5 IP for you from that moment." ("+2,5 IP para ti por esa jugada."), says only
 * the points the card's head already shows, so the feed leaves it out (#194). Any other line is kept. */
export function repeatsPoints(line: string): boolean {
  return /^[+\-\u2212]?\d+(?:[.,]\d+)? IP (?:for you from that moment|para ti por esa jugada)\.$/.test(line.trim());
}

/** One card in a phone's feed, like a live-ticker card: minute, what happened, what it meant to you. `fixture` is
 * its fixture tag in a gameweek. */
export function FeedCard({ cue, style, moment, ownerLang = "en", names = new Map(), fixture }: {
  cue: Cue; style: Style; moment?: MomentTitle; ownerLang?: string; names?: Map<string, string>; fixture?: string;
}) {
  if (cue.kind === "lost_screen") return <LostScreenNote cue={cue} ownerLang={ownerLang} names={names} fixture={fixture} />;
  const { headline, body, body_en, evidence, lang } = cue.payload;
  const summary = cue.kind === "summary";
  const recap = cue.kind === "recap_ready";
  const phone = phoneLang(ownerLang);
  const w = words(phone);
  const phoneAttr = langAttr(phone, phone);
  const englishAttr = langAttr("en", phone); // backend data the phone shows as written: en on a Spanish phone
  const title = summary || recap ? cardTitle(cue, phone) : { text: moment?.what ?? momentWhat("other", phone), lang: phone };
  return (
    <li className={`feed-card ${cue.kind} ${cue.source}`}>
      <div className="fc-head">
        <span className="minute-pill">{minuteLabel(cue.show_at)}</span>
        <span className="fc-title" lang={langAttr(title.lang, phone)}>
          {title.text}
          {!summary && !recap && moment?.who && <small>{moment.who}</small>}
        </span>
        <FixtureTag text={fixture} lang={phone} />
        {!summary && !recap && <Points headline={headline} lang={phoneAttr} />}
      </div>
      {recap && cue.payload.recap_url ? (
        <a className="recap-button" href={cue.payload.recap_url} target="_blank" rel="noreferrer" lang={phoneAttr}>
          <span aria-hidden="true">▶</span> {watchRecap(phone)}
        </a>
      ) : (
        body && !repeatsPoints(body) && <p className="fc-body" lang={lang}>{body}</p>
      )}
      {body_en && lang !== "en" && !repeatsPoints(body_en) && <p className="translation" lang={englishAttr}>{body_en}</p>}
      {(cue.source !== "code" || cue.payload.rulebook?.length) && (
        <div className="fc-foot">
          <SourceMark cue={cue} lang={phone} />
          {cue.payload.rulebook && cue.payload.rulebook.length > 0 && (
            <span className="rulebook" title={w.marks.ruleTitle}>
              <span className="mark rule">{w.marks.rule}</span>{" "}
              <span lang={englishAttr}>{cue.payload.rulebook.map((r) => r.title).join(" · ")}</span>
            </span>
          )}
        </div>
      )}
      {style === "analyst" && evidence && evidence.length > 0 && (
        <details>
          <summary>{w.evidence(evidence.length)}</summary>
          <ul className="evidence">
            {evidence.map((e) => (
              <li key={e.id}>
                <span lang={englishAttr}>{e.label}</span> <b>{decimal(e.value, phone)}</b>
              </li>
            ))}
          </ul>
        </details>
      )}
    </li>
  );
}

/** `unit="hidden"` keeps " IP" for screen readers but not on screen, where the card's heading names the unit once. */
export function LeagueTable({ rows, moves, names, judge, lang = "en", unit = "shown" }: {
  rows: TableRow[]; moves: Map<string, number>; names: Map<string, string>; judge: string | null; lang?: PhoneLang;
  unit?: "shown" | "hidden";
}) {
  const w = words(lang);
  return (
    <ol className="league" aria-label={w.table} style={{ height: `${rows.length * 3}rem` }}>
      {rows.map((r) => {
        const move = moves.get(r.manager) ?? 0;
        const name = names.get(r.manager) ?? r.manager;
        return (
          // Rows are placed by rank, so a rank change slides the row to its new place.
          <li key={r.manager} className={r.manager === judge ? "you" : undefined}
              style={{ transform: `translateY(${(r.rank - 1) * 3}rem)` }}>
            <span className="rank">{r.rank}</span>
            <Avatar name={name} you={r.manager === judge} />
            <span className="name">{name}</span>
            {r.delta ? <span className={`delta ${r.delta > 0 ? "up" : "down"}`}>{r.delta > 0 ? "+" : ""}{decimal(r.delta, lang)}</span> : <span />}
            <span className="points">{decimal(r.points, lang)}<small className={unit === "hidden" ? "visually-hidden" : undefined}> IP</small></span>
            <span className={`move ${move > 0 ? "up" : move < 0 ? "down" : ""}`}
                  aria-label={move > 0 ? w.moved.up : move < 0 ? w.moved.down : undefined}>
              {move > 0 ? "▲" : move < 0 ? "▼" : ""}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

export function tableFrom(managers: readonly Manager[]): TableRow[] {
  const sorted = [...managers].sort((a, b) => b.points - a.points || a.id.localeCompare(b.id));
  return sorted.map((m, i) => ({ manager: m.id, points: m.points, rank: i + 1 }));
}
