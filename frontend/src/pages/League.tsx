// Play with your friends (#179, #196): start or join a league, pick a squad for the gameweek, see who has picked and
// the season, and start the watch party with the league. No login: a member's token is kept in this browser, one per
// league.
import { useEffect, useState, type ReactNode } from "react";

import { fragment, type Style } from "../lib/api";
import {
  cleanCode, codeFrom, codeOk, createLeague, forget, gameweekRoster, getLeague, getSeason, hasPicked, joinLeague,
  keptInTabOnly, LeagueError, leaguesAnswer, leagueWithCode, membership, pickSquad, remember, rememberedLeagues,
  shareLink, squadIssue, startLeagueSession,
  type GameRoster, type LeagueData, type LeagueSeat, type Membership, type Season, type SquadIssue,
} from "../lib/league";
import { LEAGUE_WORDS, type LeagueWords } from "../lib/leagueText";
import { phoneLang, type PhoneLang } from "../lib/phoneText";
import { navigate } from "../router";
import { Go, TopBar } from "./EntryShell";

const STYLES: Style[] = ["casual", "analyst"];
const LANGS: { code: PhoneLang; name: string }[] = [
  { code: "en", name: "English" },
  { code: "es", name: "Español" },
];

export type Action = "create" | "join" | "pick" | "start";

export interface Refusal {
  text: string;
  problems: string[]; // the server's reasons, shown as it sent them
}

/** True for FastAPI's refusal of `league_id` as a field it does not know: a server without league sessions (#178). */
const leagueIdUnknown = (detail: unknown) =>
  Array.isArray(detail)
  && detail.some((d: { type?: string; loc?: unknown[] }) => d.type === "extra_forbidden" && d.loc?.includes("league_id")); // prettier-ignore

/** What a screen says when the server refuses, in the member's language. The statuses the routes document get their
 * own sentence; a 422 lists the server's reasons; anything else says the status. */
export function refusal(e: unknown, action: Action, w: LeagueWords): Refusal {
  if (!(e instanceof LeagueError)) return { text: e instanceof Error ? e.message : String(e), problems: [] };
  if (e.status === 429) return { text: w.refused.slowDown, problems: [] };
  if (action === "join" && e.status === 404) return { text: w.refused.noCode, problems: [] };
  if (action === "join" && e.status === 409) return { text: w.refused.full, problems: [] };
  if (action === "start" && e.status === 422 && leagueIdUnknown(e.detail)) return { text: w.refused.cannotStart, problems: [] };
  if (action === "start" && e.status === 503) return { text: w.refused.busy, problems: [] };
  if (action === "start" && e.status === 400) return { text: w.refused.noLeagues, problems: [] };
  if (action === "start" && e.status === 404) return { text: w.refused.notYours, problems: [] };
  if (e.status === 422 || e.status === 409) return { text: w.refused.problems, problems: e.problems };
  return { text: w.refused.other(e.status), problems: [] };
}

function Refused({ refused }: { refused: Refusal | null }) {
  if (!refused) return null;
  return (
    <div role="alert" className="notice-line">
      <p>{refused.text}</p>
      {refused.problems.length > 0 && <ul>{refused.problems.map((p) => <li key={p}>{p}</li>)}</ul>}
    </div>
  );
}

/** The create body's member: what the form asked for and nothing else (MemberIn forbids extra fields). */
export const profileOf = (display_name: string, style: Style, lang: PhoneLang) => ({ display_name: display_name.trim(), style, lang });

// --- start or join: Play with your friends --------------------------------------------------------------------------

/** `/league` and the share link `/league/join?code=…`. `search` is the page's query string. */
export function LeagueHome({ search = "", initialLang = "en", initialPrefsOpen = true }: {
  search?: string;
  initialLang?: PhoneLang;
  initialPrefsOpen?: boolean;
}) {
  const [lang, setLang] = useState<PhoneLang>(initialLang);
  const [style, setStyle] = useState<Style>("casual");
  const [you, setYou] = useState("");
  const [leagueName, setLeagueName] = useState("");
  const [code, setCode] = useState(codeFrom(search));
  const [tab, setTab] = useState<"create" | "join">(codeFrom(search) !== "" ? "join" : "create"); // a share link opens the join form
  const [prefsOpen, setPrefsOpen] = useState(initialPrefsOpen);
  const [busy, setBusy] = useState<"create" | "join" | null>(null);
  const [refused, setRefused] = useState<Refusal | null>(null);
  const w = LEAGUE_WORDS[lang];
  const mine = rememberedLeagues();
  const already = codeOk(code) ? leagueWithCode(code) : null; // a share link for a league this browser is in

  const enter = async (action: "create" | "join") => {
    setBusy(action);
    setRefused(null);
    const me = profileOf(you, style, lang);
    try {
      const joined = action === "create" ? await createLeague(leagueName.trim(), me) : await joinLeague(cleanCode(code), me);
      // A join learns the league's name and code when its page loads (loadLeague)
      const name = action === "create" ? leagueName.trim() : undefined;
      remember(joined.league_id, { member_id: joined.member_id, token: joined.member_token, seat: joined.seat, lang, name,
                                   code: joined.join_code ?? (action === "join" ? cleanCode(code) : undefined) }); // prettier-ignore
      navigate(`/league/${joined.league_id}`);
    } catch (e) {
      setRefused(refusal(e, action, w));
      setBusy(null);
    }
  };

  const yourName = (
    <label className="e-field">{w.create.you}<input value={you} maxLength={20} required autoComplete="nickname" onChange={(e) => setYou(e.target.value)} /></label>
  ); // prettier-ignore
  const createForm = (
    <form className="e-form" aria-label={w.create.heading} onSubmit={(e) => { e.preventDefault(); void enter("create"); }}>
      {yourName}
      <label className="e-field">{w.create.league}<input value={leagueName} maxLength={40} required onChange={(e) => setLeagueName(e.target.value)} /></label>
      <div>
        <button type="submit" className="btn acc" disabled={busy !== null || !leagueName.trim() || !you.trim()}>
          {busy === "create" ? w.create.busy : w.create.button}
        </button>
      </div>
    </form>
  ); // prettier-ignore
  const joinForm = (
    <form className="e-form" aria-label={w.join.heading} onSubmit={(e) => { e.preventDefault(); void enter("join"); }}>
      {yourName}
      <label className="e-field">
        {w.join.code}
        <input className="code" value={code} maxLength={6} autoCapitalize="characters" autoComplete="off" spellCheck={false}
               onChange={(e) => setCode(e.target.value)} />
        <small>{code && !codeOk(code) ? w.join.badCode : w.join.codeHint}</small>
      </label>
      {already && (
        <p role="status" className="notice-line">
          {w.alreadyIn.text(already.name)} <a className="link" href={`/league/${already.league}`}>{w.alreadyIn.open}</a>
        </p>
      )}
      <div>
        <button type="submit" className="btn acc" disabled={busy !== null || !codeOk(code) || !you.trim()}>
          {busy === "join" ? w.join.busy : w.join.button}
        </button>
      </div>
    </form>
  ); // prettier-ignore

  return (
    <div className="entry">
      <TopBar current="/league" leagues={w.nav.leagues} judges={w.nav.judges} />
      <main className="e-lg" lang={lang}>
        <div className="e-col">
          <Go to="/" className="e-back">&larr; {w.back}</Go>
          <h1>{w.title}</h1>
          <p className="e-lead">{w.intro}</p>
          {mine.length > 0 && (
            <nav className="e-mine" aria-label={w.yours}>
              <span className="eyebrow">{w.yours}</span>
              {mine.map((m) => (
                <Go key={m.league} to={`/league/${m.league}`}>{m.name}<span>{m.code ? `${w.codeLabel} ${m.code}` : w.open}</span></Go>
              ))}
            </nav>
          )}
          <span className="seg" role="group" aria-label={w.title}>
            <button type="button" aria-pressed={tab === "create"} onClick={() => { setTab("create"); setRefused(null); }}>{w.create.heading}</button>
            <button type="button" aria-pressed={tab === "join"} onClick={() => { setTab("join"); setRefused(null); }}>{w.join.tab}</button>
          </span>
          {tab === "create" ? createForm : joinForm}
          <Refused refused={refused} />
          <p className="fine">
            {w.places} {w.prefs(w.style[style].toLowerCase(), LANGS.find((l) => l.code === lang)?.name ?? lang)}{" "}
            <button type="button" className="link" aria-expanded={prefsOpen} onClick={() => setPrefsOpen(!prefsOpen)}>{w.change}</button>
          </p>
          {prefsOpen && (
            <div className="e-prefs">
              <div className="e-row">
                <span className="eyebrow" id="pref-style">{w.create.style}</span>
                <span className="seg" role="group" aria-labelledby="pref-style">
                  {STYLES.map((s) => (
                    <button key={s} type="button" aria-pressed={style === s} onClick={() => setStyle(s)}>{w.style[s]}</button>
                  ))}
                </span>
              </div>
              <div className="e-row">
                <span className="eyebrow" id="pref-lang">{w.create.lang}</span>
                <span className="seg" role="group" aria-labelledby="pref-lang">
                  {LANGS.map((l) => (
                    <button key={l.code} type="button" lang={l.code} aria-pressed={lang === l.code} onClick={() => setLang(l.code)}>{l.name}</button>
                  ))}
                </span>
              </div>
            </div>
          )}
        </div>
        <aside className="e-how" aria-labelledby="how-h">
          <span className="eyebrow" id="how-h">{w.how.heading}</span>
          <ol>{w.how.steps.map(([title, more]) => <li key={title}><span>{title}<small>{more}</small></span></li>)}</ol>
        </aside>
      </main>
    </div>
  ); // prettier-ignore
}

// --- the league page ----------------------------------------------------------------------------------------------

export type PageState =
  | { kind: "loading" }
  | { kind: "not-member"; lang: PhoneLang }
  | { kind: "failed"; lang: PhoneLang; text: string }
  | { kind: "ready"; league: LeagueData; season: Season; member: Membership | null; tabOnly: boolean }; // null: read-only

const read = (league: string, token: string | null) => Promise.all([getLeague(league, token), getSeason(league, token)]);
const notFound = (e: unknown) => e instanceof LeagueError && e.status === 404;

/** Loads the league. With no token kept, a league anyone may read (the demo) shows read-only, and any other is not
 * this browser's. A kept token is dropped only when the league API answers and still refuses it for this league; a
 * server without leagues (every league path a 404) keeps it, and the page says leagues are not available. */
export async function loadLeague(league: string, fallbackLang: PhoneLang): Promise<PageState> {
  const member = membership(league);
  if (!member) {
    const [data, season] = await read(league, null).catch(() => [null, null] as const);
    if (data && season) return { kind: "ready", league: data, season, member: null, tabOnly: false };
    return { kind: "not-member", lang: fallbackLang };
  }
  const w = LEAGUE_WORDS[member.lang];
  try {
    const [data, season] = await read(league, member.token);
    const known = { ...member, name: data.name, ...(data.join_code ? { code: data.join_code } : {}) };
    if (known.name !== member.name || known.code !== member.code) remember(league, known); // a join learns them here
    return { kind: "ready", league: data, season, member: known, tabOnly: keptInTabOnly(league) };
  } catch (e) {
    if (notFound(e) && (await leaguesAnswer())) {
      forget(league);
      return { kind: "not-member", lang: member.lang };
    }
    if (notFound(e)) return { kind: "failed", lang: member.lang, text: w.leaguesDown };
    return { kind: "failed", lang: member.lang, text: refusal(e, "pick", w).text };
  }
}

const browserLang = (): PhoneLang => phoneLang(typeof navigator === "undefined" ? undefined : navigator.language);


/** `/league/<id>`. `initial` lets a static render show a state; the page itself passes none and loads. */
export function LeaguePage({ league, initial }: { league: string; initial?: PageState }) {
  const [state, setState] = useState<PageState>(initial ?? { kind: "loading" });
  const reload = () => void loadLeague(league, browserLang()).then(setState);
  useEffect(() => {
    if (!initial) reload();
  }, [league]); // eslint-disable-line react-hooks/exhaustive-deps

  if (state.kind === "loading") return <Center lang={browserLang()}><p>{LEAGUE_WORDS[browserLang()].loading}</p></Center>;
  if (state.kind === "not-member") return <NotMember w={LEAGUE_WORDS[state.lang]} lang={state.lang} />;
  if (state.kind === "failed") {
    return <Center lang={state.lang}><p role="alert" className="notice-line">{state.text}</p></Center>;
  }
  return <Board state={state} onChange={reload} />;
}

function Center({ lang, children }: { lang: PhoneLang; children: ReactNode }) {
  const w = LEAGUE_WORDS[lang];
  return (
    <div className="entry">
      <TopBar current="/league" leagues={w.nav.leagues} judges={w.nav.judges} />
      <main className="e-center" lang={lang}><div>{children}</div></main>
    </div>
  );
}

function NotMember({ w, lang }: { w: LeagueWords; lang: PhoneLang }) {
  return (
    <Center lang={lang}>
      <h1>{w.notMember.heading}</h1>
      <p>{w.notMember.body}</p>
      <Go className="btn acc" to="/league">{w.notMember.action}</Go>
    </Center>
  );
}

function Board({ state, onChange }: { state: Extract<PageState, { kind: "ready" }>; onChange: () => void }) {
  const { league, season, member, tabOnly } = state;
  const roster = gameweekRoster(league);
  const own = member ? league.seats.find((s) => s.member_id !== null && s.member_id === league.you) : undefined;
  const lang = phoneLang(own?.lang ?? member?.lang ?? browserLang());
  const w = LEAGUE_WORDS[lang];
  const [editing, setEditing] = useState(false);
  const [copied, setCopied] = useState<boolean | null>(null);
  const link = league.join_code ? shareLink(typeof location === "undefined" ? "" : location.origin, league.join_code) : null;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(link ?? "");
      setCopied(true);
    } catch {
      setCopied(false); // the link is then shown, to copy by hand
    }
  };

  return (
    <div className="entry">
      <TopBar current="/league" leagues={w.nav.leagues} judges={w.nav.judges} />
      <main className="e-lp" lang={lang}>
        <div>
          <div className="e-lp-head">
            <h1>{league.name}</h1>
            <span className="gw">{w.gameweek(league.gameweek.number)}</span>
          </div>
          {tabOnly && <p role="status" className="notice-line">{w.storageNote}</p>}
          {!own && <p role="status" className="notice-line">{w.readOnly}</p>}

          {own && member && !roster && (
            <section aria-label={w.squad.heading}>
              <span className="eyebrow">{w.squad.heading}</span>
              <p role="alert" className="notice-line">{w.noPlayers}</p>
            </section>
          )}
          {own && member && roster && (
            <section aria-label={own.squad && !editing ? w.squad.yours : w.squad.heading}>
              {own.squad && !editing ? (
                <>
                  <span className="eyebrow">{w.squad.yours} · {w.squad.saved}</span>
                  <SquadTray roster={roster} players={own.squad.players} captain={own.squad.captain} w={w} />
                  <div className="e-tray-meta">
                    <span>{w.squad.savedNote}</span>
                    <button type="button" className="btn line" onClick={() => setEditing(true)}>{w.squad.change}</button>
                  </div>
                </>
              ) : (
                <SquadPicker roster={roster} w={w} initial={own.squad}
                             onCancel={own.squad ? () => setEditing(false) : undefined}
                             save={(players, captain) => pickSquad(league.league_id, league.gameweek.number, member.token, players, captain)
                               .then(() => { setEditing(false); onChange(); })} />
              )}
            </section>
          )}
        </div>

        <aside className="e-side">
          {league.join_code && link && (
            <section aria-label={w.shareHeading}>
              <span className="eyebrow">{w.shareHeading}</span>
              <div className="e-invite">
                <div className="e-code" aria-label={`${w.codeLabel}: ${league.join_code}`}>{league.join_code}</div>
                <button type="button" className="btn line" onClick={() => void copy()}>{copied === true ? w.copied : w.copy}</button>
              </div>
              {/* The link is copied, never shown, unless the browser will not copy it: then it is there to copy by hand */}
              {copied === false && <p className="e-muted">{w.linkLabel}: {link}</p>}
            </section>
          )}

          <section aria-label={w.members}>
            <span className="eyebrow">{w.members}</span>
            <ul className="e-mems">
              {league.seats.map((s) => <SeatRow key={s.seat} seat={s} you={league.you} w={w} />)}
            </ul>
          </section>

          <SeasonTable season={season} you={league.you} w={w} />

          {own && member && <StartBar league={league.league_id} gameweek={league.gameweek.number} seat={own} token={member.token} w={w} />}
        </aside>
      </main>
    </div>
  ); // prettier-ignore
}

function SeatRow({ seat, you, w }: { seat: LeagueSeat; you: string | null; w: LeagueWords }) {
  const picked = hasPicked(seat, you);
  const mine = seat.member_id !== null && seat.member_id === you;
  const status = seat.member_id === null ? w.bot : picked === null ? w.unknown : picked ? w.picked : w.notPicked;
  return (
    <li className={`e-mem${mine ? " you" : ""}`}>
      <span className="who">
        <strong>{seat.display_name}</strong>
        {mine && <span className="tag">{w.you}</span>}
        {seat.member_id !== null && seat.is_bot && <span className="tag">{w.bot}</span>}
      </span>
      <span className={`st${picked ? " ok" : ""}`}>{status}</span>
    </li>
  );
}

/** A club's name as a slot and the "full" line show it: its first word ("Hollinsford" for "Hollinsford United"). */
const clubWord = (club: string) => club.split(" ")[0] ?? club;

/** The five places of a squad: each chosen player with position and club, the captain marked, the rest empty. With
 * `onCaptain`, a chosen player is a button that makes them captain. */
function SquadTray({ roster, players, captain, w, onCaptain }: {
  roster: GameRoster;
  players: string[];
  captain: string | null;
  w: LeagueWords;
  onCaptain?: (id: string) => void;
}) {
  const slots = Array.from({ length: roster.squadSize }, (_, i) => players[i] ?? null);
  return (
    <ul className="e-tray">
      {slots.map((id, i) => {
        if (!id) return <li key={`empty-${i}`}><span className="e-slot"><span className="num" aria-hidden="true">{i + 1}</span>{w.squad.empty}</span></li>;
        const p = roster.players[id];
        const isCaptain = id === captain;
        const inside = (
          <>
            <b><span className="nmx" title={p?.name}>{p?.name ?? id}</span>{isCaptain && <abbr className="cap" title={w.squad.captain}>C</abbr>}</b>
            <small>{p ? `${w.squad.positions[p.position] ?? p.position} · ${clubWord(p.club)}` : ""}</small>
          </>
        );
        const className = `e-slot full${isCaptain ? " c captain" : ""}`;
        return (
          <li key={id}>
            {onCaptain ? (
              <button type="button" className={className} aria-pressed={isCaptain} aria-label={w.squad.captainOf(p?.name ?? id)}
                      onClick={() => onCaptain(id)}>{inside}</button>
            ) : (
              <span className={className}>{inside}</span>
            )}
          </li>
        );
      })}
    </ul>
  ); // prettier-ignore
}

/** The plain reason a squad cannot be saved yet. */
export function issueText(issue: SquadIssue, w: LeagueWords): string {
  if (issue.kind === "count") return w.squad.count(issue.n, issue.size);
  if (issue.kind === "club") return w.squad.club(issue.club, issue.cap);
  return w.squad.pickCaptain;
}

export type PositionFilter = "all" | "GK" | "DEF" | "MID" | "FWD";
export const FILTERS: PositionFilter[] = ["all", "GK", "DEF", "MID", "FWD"];
export const ROWS_SHOWN = 10;

/** The table's rows for a position filter: the squad's own players first, then the rest in the route's order (club by
 * club), at most ROWS_SHOWN until Show all; `hidden` is how many more Show all reveals. */
export function pickerRows(roster: GameRoster, players: string[], filter: PositionFilter, all: boolean) {
  const matching = Object.entries(roster.players).filter(([, p]) => filter === "all" || p.position === filter);
  const ordered = [...matching.filter(([id]) => players.includes(id)), ...matching.filter(([id]) => !players.includes(id))];
  const rows = all ? ordered : ordered.slice(0, ROWS_SHOWN);
  return { rows, hidden: ordered.length - rows.length };
}

/** Five players from the gameweek's matches and a captain among them: five places above a table of every player
 * with Add, Remove or "club is full". The picker stops a sixth and a fourth from one club and says what is missing;
 * the server checks again, and its refusal is shown as it sent it. */
export function SquadPicker({ roster, w, initial, save, onCancel, initialAll = false }: {
  roster: GameRoster;
  w: LeagueWords;
  initial: { players: string[]; captain: string } | null;
  save: (players: string[], captain: string) => Promise<unknown>;
  onCancel?: () => void;
  initialAll?: boolean; // a static render can show every row
}) {
  const [players, setPlayers] = useState<string[]>(initial?.players ?? []);
  const [captain, setCaptain] = useState<string | null>(initial?.captain ?? null);
  const [saving, setSaving] = useState(false);
  const [refused, setRefused] = useState<Refusal | null>(null);
  const issue = squadIssue(roster, players, captain);
  const perClub = new Map<string, number>();
  for (const id of players) {
    const club = roster.players[id]?.clubId ?? id;
    perClub.set(club, (perClub.get(club) ?? 0) + 1);
  }
  const [filter, setFilter] = useState<PositionFilter>("all");
  // The squad goes first when the picker opens or the filter changes, never under the pointer while picking
  const [pinned, setPinned] = useState<string[]>(() => initial?.players ?? []);
  const [all, setAll] = useState(initialAll);
  const { rows, hidden } = pickerRows(roster, pinned, filter, all);

  const add = (id: string) => {
    if (players.length < roster.squadSize) setPlayers([...players, id]);
  };
  const remove = (id: string) => {
    setPlayers(players.filter((p) => p !== id));
    if (captain === id) setCaptain(null);
  };
  const submit = async () => {
    if (issue || !captain) return;
    setSaving(true);
    setRefused(null);
    try {
      await save(players, captain);
    } catch (e) {
      setRefused(refusal(e, "pick", w));
    }
    setSaving(false);
  };

  return (
    <form className="e-picker" aria-label={w.squad.heading} onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      <span className="eyebrow">{w.squad.heading}</span>
      <SquadTray roster={roster} players={players} captain={captain} w={w} onCaptain={setCaptain} />
      <div className="e-tray-meta">
        <span>
          {w.squad.progress(players.length, roster.squadSize, roster.maxPerClub)}
          {players.length > 0 && <> · {w.squad.tapCaptain}</>}
          {issue && <><br /><span role="status">{issueText(issue, w)}</span></>}
        </span>
        <span className="acts">
          {onCancel && <button type="button" className="btn line" onClick={onCancel}>{w.squad.cancel}</button>}
          <button type="submit" className="btn acc" disabled={issue !== null || saving}>{saving ? w.squad.saving : w.squad.save}</button>
        </span>
      </div>
      <Refused refused={refused} />
      <span className="seg e-filter" role="group" aria-label={w.squad.position}>
        {FILTERS.map((f) => (
          <button key={f} type="button" aria-pressed={filter === f} onClick={() => { setFilter(f); setAll(false); setPinned(players); }}>{w.squad.filters[f]}</button>
        ))}
      </span>
      <table className="e-pt">
        <thead><tr><th scope="col">{w.squad.player}</th><th scope="col">{w.squad.position}</th><th scope="col">{w.squad.clubHeading}</th><th scope="col"><span className="visually-hidden">{w.squad.add}</span></th></tr></thead>
        <tbody>
          {rows.map(([id, p]) => {
            const chosen = players.includes(id);
            const clubFull = !chosen && (perClub.get(p.clubId) ?? 0) >= roster.maxPerClub;
            return (
              <tr key={id} className={chosen ? "chosen" : undefined}>
                <td>{p.name}<small className="club-sm">{clubWord(p.club)}</small></td>
                <td>{w.squad.positions[p.position] ?? p.position}</td>
                <td>{p.club}</td>
                <td>
                  {chosen ? (
                    <button type="button" className="add out" aria-label={w.squad.removeOf(p.name)} onClick={() => remove(id)}>{w.squad.remove}</button>
                  ) : clubFull ? (
                    <span className="full-club">{w.squad.clubFull(clubWord(p.club))}</span>
                  ) : (
                    <button type="button" className="add" aria-label={w.squad.addOf(p.name)} disabled={players.length >= roster.squadSize}
                            onClick={() => add(id)}>{w.squad.add}</button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {hidden > 0 && <button type="button" className="btn line e-more" onClick={() => setAll(true)}>{w.squad.showAll(hidden)}</button>}
    </form>
  ); // prettier-ignore
}

function SeasonTable({ season, you, w }: { season: Season; you: string | null; w: LeagueWords }) {
  return (
    <section aria-label={w.season.heading}>
      <span className="eyebrow">{w.season.heading}</span>
      {season.played.length === 0 ? (
        <p className="e-muted">{w.season.empty}</p>
      ) : (
        <table className="e-season">
          <thead><tr><th scope="col">{w.season.rank}</th><th scope="col">{w.season.name}</th><th scope="col">{w.season.points}</th><th scope="col">{w.season.played}</th></tr></thead>
          <tbody>
            {season.table.map((r) => (
              <tr key={r.member_id} className={r.member_id === you ? "you" : undefined}>
                <td className="n">{r.rank}</td><td>{r.display_name}</td><td className="ip">{r.points}</td><td className="pl">{r.played}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  ); // prettier-ignore
}

function StartBar({ league, gameweek, seat, token, w }: { league: string; gameweek: number; seat: LeagueSeat; token: string; w: LeagueWords }) {
  const [starting, setStarting] = useState(false);
  const [refused, setRefused] = useState<Refusal | null>(null);
  const start = async () => {
    setStarting(true);
    setRefused(null);
    try {
      const made = await startLeagueSession(league, seat, token);
      navigate(`/watch/${made.session_id}${fragment(made.phone_token, made.manager)}`);
    } catch (e) {
      setRefused(refusal(e, "start", w));
      setStarting(false);
    }
  };
  return (
    <section>
      <button type="button" className="btn acc e-start" onClick={() => void start()} disabled={starting}>
        {starting ? w.start.busy : w.start.button(gameweek)}
      </button>
      {seat.squad === null && <p className="e-muted">{w.start.unpicked}</p>}
      <Refused refused={refused} />
    </section>
  ); // prettier-ignore
}
