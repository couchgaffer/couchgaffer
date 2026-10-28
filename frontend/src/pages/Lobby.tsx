// The landing page and Watch now (#196). The landing is one decision: Watch now, or Play with your friends. Watch now
// picks a manager, a phone and a match in three steps, each with a default, and one Start below them; then the watch
// party starts. No login (rule 4.8).
import { useEffect, useRef, useState, type KeyboardEvent } from "react";

import { ordinal } from "../components/PhoneApp";
import {
  createSession, fragment, getLobby, Refused, sessionRequest, type Format, type Lobby as LobbyData, type Roster,
  type SessionRequest, type Style,
} from "../lib/api";
import { navigate, WATCH_NOW } from "../router";
import { Go, TopBar } from "./EntryShell";

const LANGUAGES = [
  { code: "en", name: "English" },
  { code: "es", name: "Español" }, // #16: explanations in Spanish, with English copies
];
const FORMATS: { id: Format; name: string; said: string; inWords: string }[] = [
  { id: "gameweek", name: "A gameweek: three matches at once", said: "Three matches on one clock and one table.",
    inWords: "a gameweek of three matches" },
  { id: "single", name: "A single match", said: "One match on its own.", inWords: "a single match" },
]; // prettier-ignore
const SEEDS: { id: Seed; name: string; inWords: string }[] = [
  { id: "demo", name: "The demo match", inWords: "the demo match" },
  { id: "random", name: "A random match", inWords: "a random match" },
];
const STYLES: { id: Style; name: string; example: string }[] = [
  { id: "casual", name: "Casual", example: "Big save from your keeper, +3 IP!" },
  { id: "analyst", name: "Analyst", example: "Ashcombe stops a 0.31 xG chance: +3 IP, and you stay 2nd on 225." },
];

export type Seed = "demo" | "random";
export interface Choices {
  manager: string;
  style: Style;
  lang: string;
  format: Format; // the owner's call on #94: a gameweek unless chosen otherwise
  seed: Seed;
}
export const DEFAULTS: Choices = { manager: "mgr_1", style: "casual", lang: "en", format: "gameweek", seed: "demo" };

/** The session request for these choices: the body Start sends. A random match draws its seed from `draw`. */
export function requestFor(c: Choices, draw: () => number = Math.random): SessionRequest {
  const seed = c.seed === "demo" ? "demo" : Math.floor(draw() * 1_000_000);
  return sessionRequest({ seed, manager: c.manager, style: c.style, lang: c.lang, format: c.format });
}

/** The choices in words, beside Start: "Jo, casual, English, a gameweek of three matches, the demo match." */
export function choicesInWords(c: Choices, managerName: string): string {
  const style = c.style === "casual" ? "casual" : "analyst";
  const lang = LANGUAGES.find((l) => l.code === c.lang)?.name ?? c.lang;
  const format = FORMATS.find((f) => f.id === c.format)?.inWords ?? c.format;
  const seed = SEEDS.find((s) => s.id === c.seed)?.inWords ?? c.seed;
  return `${managerName}, ${style}, ${lang}, ${format}, ${seed}.`;
}

export const STEPS = ["Your manager", "Your phone", "The match"] as const;
export type Step = 1 | 2 | 3;

/** The stepper's foot: Back cannot go before step 1, and Next is gone on the last step, where Start is the way on. */
export const stepFoot = (step: Step) => ({ backDisabled: step === 1, next: step < STEPS.length });

/** The league and players the chosen format plays: a gameweek's own squads (league_gw_v1), or the single match's
 * as before. A server that sends no gameweek roster shows the single match's. */
export function rosterFor(lobby: LobbyData | null, format: Format): Roster | null {
  if (!lobby) return null;
  return format === "gameweek" && lobby.gameweek ? lobby.gameweek : lobby;
}

export interface StartError {
  text: string;
  offerSingle: boolean; // the gameweek was refused: a single match is one press away
}

/** What Watch now says when a start fails. A request the server refuses (422) fails the same way every time, so the
 * page says what it cannot do and offers what it can, never "try again". */
export function startError(e: unknown, format: Format): StartError {
  if (e instanceof Refused && format === "gameweek") {
    return { text: "This server cannot start a gameweek. It can start a single match.", offerSingle: true };
  }
  if (e instanceof Refused) return { text: "This server cannot start that kind of match.", offerSingle: false };
  return { text: e instanceof Error ? e.message : String(e), offerSingle: false };
}

/** `/`: the headline, one line, and two ways in. Nothing else. */
export function Landing() {
  return (
    <div className="entry">
      <TopBar />
      <main className="e-hero">
        <h1>Watch the gameweek with your <em>mini-league.</em></h1>
        <p className="e-lead">
          Five friends, one screen. Each of you has an AI agent that argues for your players; code picks a clear winner
          for the big screen, and an AI host settles close calls.
        </p>
        <nav className="e-menu" aria-label="Choose how to play">
          <Go to={WATCH_NOW}>
            <span><b>Watch now</b><small>You and four bots. About three minutes.</small></span>
            <i aria-hidden="true">&rarr;</i>
          </Go>
          <Go to="/league">
            <span><b>Play with your friends</b><small>Start or join a league, pick squads, watch together.</small></span>
            <i aria-hidden="true">&rarr;</i>
          </Go>
        </nav>
        <p className="fine">No app, no sign-in, free to play. All clubs, players and managers are invented.</p>
      </main>
    </div>
  );
}

/** Where an arrow key moves in a radio group of `n` from `i`, wrapping as native radios do; null for any other key. */
export function rovingIndex(key: string, i: number, n: number): number | null {
  if (key === "ArrowDown" || key === "ArrowRight") return (i + 1) % n;
  if (key === "ArrowUp" || key === "ArrowLeft") return (i - 1 + n) % n;
  if (key === "Home") return 0;
  if (key === "End") return n - 1;
  return null;
}

/** An arrow key in a radio group selects the option it moves to and focuses it; the group is one Tab stop. */
function roving(e: KeyboardEvent<HTMLButtonElement>, i: number, n: number, select: (j: number) => void) {
  const j = rovingIndex(e.key, i, n);
  if (j === null) return;
  e.preventDefault();
  select(j);
  (e.currentTarget.parentElement?.children[j] as HTMLElement | undefined)?.focus();
}

/** One group of buttons, one pressed. */
function Seg<T extends string>({ label, options, value, onPick }: {
  label: string;
  options: { id: T; name: string; lang?: string }[];
  value: T;
  onPick: (v: T) => void;
}) {
  const id = `seg-${label.toLowerCase().replace(/\W+/g, "-")}`;
  return (
    <div className="e-row">
      <span className="eyebrow" id={id}>{label}</span>
      <span className="seg" role="group" aria-labelledby={id}>
        {options.map((o) => (
          <button key={o.id} type="button" lang={o.lang} aria-pressed={value === o.id} onClick={() => onPick(o.id)}>{o.name}</button>
        ))}
      </span>
    </div>
  ); // prettier-ignore
}

/** `/?watch`: Watch now. `initial`, `initialFormat`, `initialStep`, `initialChoices` and `initialError` let a static
 * render show a state; the page itself passes none and loads. */
export function Lobby({ initial = null, initialFormat, initialStep = 1, initialChoices, initialError = null }: {
  initial?: LobbyData | null;
  initialFormat?: Format;
  initialStep?: Step;
  initialChoices?: Partial<Choices>;
  initialError?: StartError | null;
}) {
  const [lobby, setLobby] = useState<LobbyData | null>(initial);
  const [c, setC] = useState<Choices>({ ...DEFAULTS, ...(initialFormat ? { format: initialFormat } : {}), ...initialChoices });
  const [step, setStep] = useState<Step>(initialStep);
  const [error, setError] = useState<StartError | null>(initialError);
  const [starting, setStarting] = useState(false);
  const set = (more: Partial<Choices>) => setC((was) => ({ ...was, ...more }));

  const alert = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!initial) getLobby().then(setLobby, (e: Error) => setError({ text: e.message, offerSingle: false }));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => alert.current?.scrollIntoView?.({ block: "nearest" }), [error]); // below the fold on a phone

  // A step change by Back, Next or a tab moves focus to the new step's panel, so it never drops to the page when the
  // button pressed (Next on step 2) is gone on the next render.
  const pane = useRef<HTMLDivElement>(null);
  const moved = useRef(false);
  const go = (to: Step) => {
    moved.current = true;
    setStep(to);
  };
  useEffect(() => {
    if (moved.current) pane.current?.focus();
  }, [step]);

  const start = async (format: Format = c.format) => {
    setStarting(true);
    setError(null);
    try {
      const seat = await createSession(requestFor({ ...c, format }));
      navigate(`/watch/${seat.session_id}${fragment(seat.phone_token, seat.manager)}`);
    } catch (e) {
      setError(startError(e, format));
      setStarting(false);
    }
  };
  const startSingle = () => {
    set({ format: "single" });
    void start("single");
  };

  const shown = rosterFor(lobby, c.format);
  const managers = shown ? [...shown.league.managers].sort((a, b) => b.points - a.points) : [];
  const picked = shown?.league.managers.find((m) => m.id === c.manager);
  const shortName = (club: string) => Object.values(shown?.clubs ?? {}).find((k) => k.name === club)?.short_name ?? club;
  const foot = stepFoot(step);

  return (
    <div className="entry">
      <TopBar />
      <main className="e-col">
        <Go to="/" className="e-back">&larr; Back</Go>
        <h1>Watch now</h1>
        <div className="e-stepper">
          <div className="e-steps">
            {STEPS.map((name, i) => (
              <button key={name} type="button" aria-current={step === i + 1 ? "step" : undefined}
                      className={i + 1 < step ? "done" : undefined} onClick={() => go((i + 1) as Step)}>
                <b>{i + 1}</b>{name}
              </button>
            ))}
          </div>

          {step === 1 && (
            <div className="e-pane" ref={pane} tabIndex={-1} aria-label={STEPS[step - 1]}>
              {!lobby && !error && <p className="e-muted">Loading the league…</p>}
              <div className="e-mgrs" role="radiogroup" aria-label="Your manager">
                {managers.map((m, i) => (
                  <button key={m.id} type="button" role="radio" aria-checked={c.manager === m.id} className="e-mgr"
                          tabIndex={c.manager === m.id ? 0 : -1}
                          onKeyDown={(e) => roving(e, i, managers.length, (j) => set({ manager: managers[j]!.id, lang: managers[j]!.lang }))}
                          onClick={() => set({ manager: m.id, lang: m.lang })}>
                    {/* Picking this manager sets the phone's language to theirs: say so in words, not a bare code */}
                    <span className="nm">{m.display_name}<small>{ordinal(i + 1)}{m.lang === "es" && <> · phone in <span lang="es">Español</span></>}</small></span>
                    <span className="pts"><small>IP</small>{m.points}</span>
                  </button>
                ))}
              </div>
              {/* The chosen manager's five, on one fixed line under the list, so no row changes height */}
              {picked && (
                <p className="e-sq" aria-live="polite">
                  <span className="who">{picked.display_name}'s squad</span>
                  {picked.squad.map((p) => {
                    const player = shown?.players[p];
                    if (!player) return null;
                    return (
                      <span key={p} title={`${player.name}, ${player.club}${p === picked.captain ? ", captain: points count double" : ""}`}>
                        {player.name.split(" ").at(-1)}
                        {p === picked.captain && <abbr className="cap" title="Captain">C</abbr>}
                        <em>{shortName(player.club)}</em>
                      </span>
                    );
                  })}
                </p>
              )}
            </div>
          )}

          {step === 2 && (
            <div className="e-pane" ref={pane} tabIndex={-1} aria-label={STEPS[step - 1]}>
              <span className="eyebrow" id="style-label">How your phone talks</span>
              <div className="e-opts" role="radiogroup" aria-labelledby="style-label">
                {STYLES.map((s, i) => (
                  <button key={s.id} type="button" role="radio" aria-checked={c.style === s.id} className="e-opt"
                          tabIndex={c.style === s.id ? 0 : -1}
                          onKeyDown={(e) => roving(e, i, STYLES.length, (j) => set({ style: STYLES[j]!.id }))}
                          onClick={() => set({ style: s.id })}>
                    <b>{s.name}</b><q>{s.example}</q>
                  </button>
                ))}
              </div>
              <Seg label="Language" options={LANGUAGES.map((l) => ({ id: l.code, name: l.name, lang: l.code === "es" ? "es" : undefined }))}
                   value={c.lang} onPick={(lang) => set({ lang })} />
            </div>
          )}

          {step === 3 && (
            <div className="e-pane" ref={pane} tabIndex={-1} aria-label={STEPS[step - 1]}>
              <Seg label="Watch" options={FORMATS} value={c.format} onPick={(format) => set({ format })} />
              <Seg label="Match" options={SEEDS} value={c.seed} onPick={(seed) => set({ seed })} />
              <p className="e-said">
                {FORMATS.find((f) => f.id === c.format)?.said} The demo match is the same every time; a random one is new.
              </p>
            </div>
          )}

          <div className="e-pane-foot">
            <button type="button" className="btn line" disabled={foot.backDisabled} onClick={() => go((step - 1) as Step)}>Back</button>
            {foot.next && <button type="button" className="btn line" onClick={() => go((step + 1) as Step)}>Next</button>}
          </div>
        </div>

        <div className="e-go">
          <span className="sum">
            <b>{choicesInWords(c, picked?.display_name ?? "…")}</b> Each step has a default, so you can start now.
          </span>
          <button type="button" className="btn acc" onClick={() => void start()} disabled={!lobby || starting}>
            {starting ? "Starting…" : "Start the watch party"}
          </button>
        </div>
        {error && (
          <div role="alert" className="notice-line" ref={alert}>
            {error.text}
            {error.offerSingle && (
              <button type="button" className="btn line" onClick={startSingle} disabled={starting}>Start a single match</button>
            )}
          </div>
        )}
        <p className="fine">
          Each address gets 4 matches an hour with the AI agents, fewer when the demo is busy; later matches run on plain
          stat lines from code. Free to play: no betting, no prizes.
        </p>
      </main>
    </div>
  ); // prettier-ignore
}
