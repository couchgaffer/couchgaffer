// One manager's phone, as a live-match app: the scoreboard, then tabs. "For you" is their own feed in their
// style; "Live" the pitch and the key moments; "Table" the mini-league. The same app runs on a real phone (the QR
// code) and inside the phone frame on the judge's page. A Spanish friend's phone is Spanish throughout (#119, #127):
// its root says lang="es", and the English it shows as the backend wrote it says lang="en".
import { useEffect, useState } from "react";

import type { Manager, Style } from "../lib/api";
import { phoneFeed, type TableRow } from "../lib/cues";
import { afterTap, phoneView, pinFor, shownFixture } from "../lib/phoneFixture";
import { scoreboard, type Scoreboard } from "../lib/match";
import type { Cue, MatchEvent, MatchFrame, MatchMeta } from "../lib/messages";
import {
  decimal, langAttr, momentWhat, phoneLang, savedBy, shownName, words, type MomentKind, type PhoneLang, type PhoneTab,
} from "../lib/phoneText"; // prettier-ignore
import { Avatar, FeedCard, LeagueTable, type MomentTitle } from "./Cards";
import { LiveAction } from "./LiveAction";
import { MatchHeader } from "./MatchHeader";
import { scoreline, ScoreStrip } from "./ScoreStrip";
import { Ticker } from "./Ticker";

const TABS: PhoneTab[] = ["you", "live", "table"];

export interface MatchState {
  meta: MatchMeta | null;
  events: MatchEvent[];
  frames: MatchFrame[];
  t: number;
  score: Scoreboard;
  players: Map<string, string>; // player id -> name
  managers: Map<string, string>; // manager id -> display name
  rows: TableRow[];
  moves: Map<string, number>;
  // A gameweek (#94); absent or false for a single match
  gameweek?: boolean;
  focus?: string; // the fixture on the big screen, whose meta, events, frames and score are the ones above
  fixtures?: FixtureScore[]; // every fixture, in the strip's order
  allEvents?: MatchEvent[]; // every fixture's events
}

export interface FixtureScore {
  id: string;
  meta: MatchMeta | null;
  events: MatchEvent[];
  frames?: MatchFrame[]; // so a phone can show this fixture's pitch (#208)
  score: Scoreboard;
}

/** A feed card's fixture tag in a gameweek: "KES 1-0 RAV", the score in that fixture at the card's minute (#94). A
 * cue with no fixture_id is about the whole gameweek and gets none, the rule the group screen's focus follows too. */
export function fixtureTag(cue: Cue, match: MatchState): string | undefined {
  const fixture = match.gameweek ? match.fixtures?.find((f) => f.id === cue.fixture_id) : undefined;
  if (!fixture?.meta) return undefined;
  return scoreline(fixture.meta, scoreboard(fixture.events, cue.show_at));
}

/** What kind of key moment a climax event was. */
function momentKind(e: MatchEvent): MomentKind {
  if (e.type === "shot" && e.outcome === "goal") return "goal";
  if (e.type === "shot" && e.outcome === "saved") return "save";
  if (e.type === "shot") return "chance";
  if (e.type === "card") return e.outcome === "red" ? "red" : "yellow";
  return "other";
}

/** What each key moment was, from its climax, in the phone's language: "Goal, Dylan Crossfield" or "Save, Tom
 * Ashcombe denies Lasse Vikholm"; on a Spanish phone "Parada, Tom Ashcombe para el disparo de Lasse Vikholm". */
export function moments(events: MatchEvent[], players: Map<string, string>, lang: PhoneLang): Map<string, MomentTitle> {
  const out = new Map<string, MomentTitle>();
  for (const e of events) {
    if (!e.climax || !e.moment_id) continue;
    const name = (id?: string) => (id ? players.get(id) : undefined);
    const kind = momentKind(e);
    const who = kind === "save" ? savedBy(name(e.opponent), name(e.player), lang) : name(e.player);
    out.set(e.moment_id, { what: momentWhat(kind, lang), who });
  }
  return out;
}

export function PhoneApp({ manager, cues, style, onStyle, match, judge, initialTab = "you" }: {
  manager: Manager;
  cues: Cue[];
  style: Style;
  onStyle?: (s: Style) => void;
  match: MatchState;
  judge: string | null;
  initialTab?: PhoneTab; // the tab it opens on; the tests render each tab this way
}) {
  const [tab, setTab] = useState<PhoneTab>(initialTab);
  // A fixture tapped in the strip, or null to follow the big screen's (#208)
  const [pinned, setPinned] = useState<string | null>(null);
  // The big screen moved onto the pinned fixture: follow it again from here on
  useEffect(() => setPinned((p) => pinFor(p, match.focus)), [match.focus]);
  const shown = shownFixture(match, pinned);
  const view = phoneView(match, pinned);
  const following = shown === match.focus;
  const lang = phoneLang(manager.lang);
  const w = words(lang);
  const feed = phoneFeed(cues, match.t, manager.id);
  const titles = moments(match.allEvents ?? match.events, match.players, lang);
  const standing = match.rows.find((r) => r.manager === manager.id);
  const id = `phone-${manager.id}`;
  const me = shownName(manager.display_name, lang);
  const managers = new Map([...match.managers].map(([m, name]) => [m, shownName(name, lang)]));
  return (
    <section className={`phone-app ${style}`} aria-label={w.phoneOf(manager.display_name)} lang={langAttr(lang, lang)}>
      <div className="pa-top">
        <span className="brand">Couch Gaffer</span>
        <span className="me">
          <Avatar name={me} you={manager.id === judge} />
          {me}
          {standing && <span className="standing">{w.ordinal(standing.rank)} · {decimal(standing.points, lang)} IP</span>}
        </span>
      </div>
      {match.gameweek && match.fixtures && (
        <ScoreStrip fixtures={match.fixtures} focus={match.focus ?? ""} lang={lang} shown={shown}
                    onPick={(id) => setPinned(afterTap(id, match.focus))} />
      )}
      <MatchHeader meta={view.meta} score={view.score} names={match.players} variant="compact" lang={lang} />
      <div className="pills" role="tablist" aria-label={w.sections}>
        {TABS.map((t) => (
          <button key={t} type="button" role="tab" id={`${id}-${t}`} aria-selected={tab === t}
                  aria-controls={`${id}-${t}-panel`} onClick={() => setTab(t)}>
            {w.tabs[t]}
          </button>
        ))}
      </div>

      <div className="pa-panel" role="tabpanel" id={`${id}-${tab}-panel`} aria-labelledby={`${id}-${tab}`}>
        {tab === "you" && (
          <>
            <div className="style-row">
              {onStyle ? (
                <div className="segmented" role="group" aria-label={w.styleGroup}>
                  {(["casual", "analyst"] as const).map((s) => (
                    <button key={s} type="button" aria-pressed={style === s} onClick={() => onStyle(s)}>
                      {w.style[s]}
                    </button>
                  ))}
                </div>
              ) : (
                <span className="style-tag">{w.style[style]}</span>
              )}
              <span className="hint">{w.styleHint[style]}</span>
            </div>
            <ol className="feed" aria-live="polite" aria-relevant="additions">
              {feed.length === 0 && (
                <li className="empty">
                  <strong>{w.emptyFeed.lead}</strong> {w.emptyFeed.body(manager.id === judge ? null : manager.display_name)}
                </li>
              )}
              {feed.map((c) => (
                <FeedCard key={c.cue_id} cue={c} style={style} moment={c.moment_id ? titles.get(c.moment_id) : undefined}
                          ownerLang={manager.lang} names={match.managers} fixture={fixtureTag(c, match)} />
              ))}
            </ol>
          </>
        )}
        {tab === "live" && (
          <>
            <LiveAction meta={view.meta} frames={view.frames} events={view.events} t={match.t} score={view.score}
                        lang={lang} />
            <h3 className="section-title">{w.keyMoments}</h3>
            {/* Following the big screen, every fixture's moments; on a tapped fixture, that fixture's only */}
            <Ticker meta={view.meta} events={view.events} t={match.t} names={match.players} limit={12} lang={lang}
                    fixtures={match.gameweek ? match.fixtures?.filter((f) => following || f.id === shown) : undefined} />
          </>
        )}
        {tab === "table" && (
          <LeagueTable rows={match.rows} moves={match.moves} names={managers} judge={judge} lang={lang} />
        )}
      </div>
    </section>
  );
}

/** A place as the English pages write it: 1st, 2nd, 12th. A phone writes its own (#127). */
export function ordinal(n: number): string {
  return words("en").ordinal(n);
}
