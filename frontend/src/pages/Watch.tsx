// The judge's page, laid out as a broadcast for a 1920 x 1080 recording (#194): one top bar (the brand, the three
// fixtures, the score bug and two buttons), a large pitch with the lower third over it, and a slim right rail with
// the mini-league and the key moments. The phone, the QR code and Try a failure sit in one drawer, Phone and tools,
// closed until its button opens it; Behind the scenes covers the page below the bar. It stacks on narrow screens.
import { useEffect, useMemo, useRef, useState } from "react";

import { GroupStage, LeagueTable } from "../components/Cards";
import { LiveAction } from "../components/LiveAction";
import { MatchHeader } from "../components/MatchHeader";
import { PhoneApp } from "../components/PhoneApp";
import { QrCode } from "../components/QrCode";
import { ScoreStrip } from "../components/ScoreStrip";
import { Ticker } from "../components/Ticker";
import { Trace } from "../components/Trace";
import { knockOut, readFragment, setStyle, stillArmed, type Knockout, type Style } from "../lib/api";
import { groupScreen, type Slot } from "../lib/cues";
import type { League } from "../lib/messages";
import { phoneUrl } from "../lib/qr";
import { matchState } from "../lib/state";
import { useMatch } from "../lib/useMatch";

const MANAGERS = ["mgr_1", "mgr_2", "mgr_3", "mgr_4", "mgr_5"];

/** The league's name for the top bar when the match is a stored league's (#179); the demo leagues show none. */
export function leagueName(league: Pick<League, "league_id" | "name"> | null): string | null {
  if (!league || league.league_id.startsWith("lg_demo")) return null;
  return league.name;
}

/** Which layer Escape closes: Behind the scenes, which covers the drawer, before the drawer; none when both are shut. */
export function escapeLayer(trace: boolean, tools: boolean): "trace" | "tools" | null {
  if (trace) return "trace";
  return tools ? "tools" : null;
}

/** `initialTools` lets a static render show the drawer open; the page itself passes nothing. */
export function Watch({ session, initialTools = false }: { session: string; initialTools?: boolean }) {
  const { token, manager: judge } = useMemo(() => readFragment(location.hash), []);
  const screens = useMemo(() => ["group", ...(token ? MANAGERS.map((m) => `phone:${m}`) : [])], [token]);
  const { store, t, now, connection } = useMatch(session, screens, token);
  const slot = useRef<Slot | null>(null);
  const [style, setLocalStyle] = useState<Style | null>(null);
  const [viewing, setViewing] = useState<string | null>(null);
  const [trace, setTrace] = useState(false);
  const [tools, setTools] = useState(initialTools);
  const [knockout, setKnockout] = useState<Knockout | null>(null);
  const toolsButton = useRef<HTMLButtonElement>(null);
  const traceButton = useRef<HTMLButtonElement>(null);
  const drawer = useRef<HTMLElement>(null);

  // The drawer takes focus when it opens.
  useEffect(() => {
    if (tools) drawer.current?.focus();
  }, [tools]);
  // Escape closes the topmost layer, Behind the scenes over the drawer, and gives focus back to its button.
  useEffect(() => {
    if (!escapeLayer(trace, tools)) return;
    const close = { trace: () => { setTrace(false); traceButton.current?.focus(); },
                    tools: () => { setTools(false); toolsButton.current?.focus(); } }; // prettier-ignore
    const onKey = (e: KeyboardEvent) => {
      const layer = e.key === "Escape" ? escapeLayer(trace, tools) : null;
      if (layer) close[layer]();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [trace, tools]);
  const closeTools = () => {
    setTools(false);
    toolsButton.current?.focus();
  };

  const managers = store.league?.managers ?? [];
  const groupCues = store.cues.get("group") ?? [];
  const shown = groupScreen(groupCues, t, now, slot.current);
  slot.current = shown.slot;
  // In a gameweek the pitch, header and score follow the card on screen (#94): decided here, in the same render
  const match = matchState(store, t, shown.card ?? shown.clash?.cue);
  const you = managers.find((m) => m.id === judge);
  const ordered = you ? [you, ...managers.filter((m) => m.id !== judge)] : managers;
  const phone = ordered.find((m) => m.id === viewing) ?? ordered[0];
  const league = leagueName(store.league);
  // Each club's short name by its full one, so code's stat line can say "KES v SAL" (#194)
  const clubs = new Map((match.fixtures ?? []).flatMap((f) => (f.meta ? [f.meta.home, f.meta.away] : []))
    .concat(match.meta ? [match.meta.home, match.meta.away] : []).map((c) => [c.name, c.short_name])); // prettier-ignore

  const changeStyle = (s: Style) => {
    if (!token) return;
    setLocalStyle(s);
    setStyle(session, token, s).catch(() => setLocalStyle(null));
  };
  const armed = stillArmed(knockout, store.decisions, t);
  const whose = (id: string) => (id === judge ? "your" : `${match.managers.get(id) ?? id}'s`);
  const fail = (manager: string) => {
    if (!token) return;
    knockOut(session, token, manager).then(setKnockout, () => undefined); // a failed press changes nothing shown
  };

  if (connection === "refused") {
    return (
      <>
        <header className="top"><a className="brand" href="/">Couch Gaffer</a></header>
        <main className="notice">
          <h1>This watch party has ended</h1>
          <p>Matches last about three minutes. Start a new one; it is free and needs no sign-in.</p>
          <a className="btn acc" href="/?watch">Start a new watch party</a>
        </main>
      </>
    );
  }

  return (
    <div className={`watch ${trace ? "with-trace" : ""} ${tools ? "with-tools" : ""}`}>
      <header className="broadcast-bar">
        <a className="brand" href="/">Couch Gaffer</a>
        <div className="bar-fixtures">
          {match.gameweek && <ScoreStrip fixtures={match.fixtures ?? []} focus={match.focus ?? ""} />}
        </div>
        <MatchHeader meta={match.meta} score={match.score} names={match.players} variant="broadcast" />
        <div className="bar-actions">
          {league && <span className="league-badge">{league}</span>}
          {connection !== "open" && (
            <span className="status" role="status">{connection === "retrying" ? "Reconnecting…" : "Connecting…"}</span>
          )}
          <button ref={traceButton} type="button" className="btn line" aria-expanded={trace} onClick={() => setTrace(!trace)}>
            Behind the scenes
          </button>
          {token && (
            <button ref={toolsButton} type="button" className="btn line" aria-expanded={tools}
                    aria-controls={tools ? "tools" : undefined} onClick={() => setTools(!tools)}>
              Phone and tools
            </button>
          )}
        </div>
      </header>

      <main className="stage">
        <section className="group-screen" aria-label="Group screen">
          <GroupStage view={shown} cues={groupCues} t={t} names={match.managers} judge={judge} clubs={clubs}
                      pitch={<LiveAction meta={match.meta} frames={match.frames} events={match.events} t={t} score={match.score} />} />
        </section>

        <aside className="rail">
          <section aria-labelledby="rail-table">
            <h2 id="rail-table" className="eyebrow">
              {match.gameweek ? "Mini-league · all three matches" : "Mini-league"}
            </h2>
            <LeagueTable rows={match.rows} moves={match.moves} names={match.managers} judge={judge} unit="hidden" />
          </section>
          <section aria-labelledby="rail-moments">
            <h2 id="rail-moments" className="eyebrow">Key moments</h2>
            <Ticker meta={match.meta} events={match.events} t={t} names={match.players} limit={5}
                    fixtures={match.gameweek ? match.fixtures : undefined} />
          </section>
        </aside>
      </main>

      {tools && token && phone && (
        <aside id="tools" className="drawer" aria-labelledby="tools-title" ref={drawer} tabIndex={-1}>
          <header>
            <h2 id="tools-title">Phone and tools</h2>
            <button type="button" className="btn line" onClick={closeTools}>Close</button>
          </header>
          <div className="drawer-body">
            <section className="grp" aria-labelledby="tools-phone">
              <h3 id="tools-phone" className="eyebrow">Whose phone</h3>
              <div className="pills switcher" role="tablist" aria-label="Whose phone">
                {ordered.map((m) => (
                  <button key={m.id} type="button" role="tab" aria-selected={m.id === phone.id} onClick={() => setViewing(m.id)}>
                    {m.id === judge ? "You" : m.display_name}
                  </button>
                ))}
              </div>
              <div className="device">
                <PhoneApp key={phone.id} manager={phone} cues={store.cues.get(`phone:${phone.id}`) ?? []}
                          style={phone.id === judge ? (style ?? phone.style) : phone.style}
                          onStyle={phone.id === judge && token ? changeStyle : undefined} match={match} judge={judge} />
              </div>
            </section>
            <section className="grp" aria-labelledby="tools-fail">
              <h3 id="tools-fail" className="eyebrow">Try a failure</h3>
              <p>
                {armed
                  ? `The next time ${whose(knockout.manager)} agent bids, that phone gets plain stats from code. The other agents carry on.`
                  : "Knock out one manager's agent, yours included. That phone falls back to code; the others carry on."}
              </p>
              <button type="button" className="btn line" onClick={() => fail(phone.id)}
                      disabled={match.score.phase !== "live" || armed}>
                {armed ? `Knocked out: ${whose(knockout.manager)} agent` : `Knock out ${whose(phone.id)} agent`}
              </button>
            </section>
            <section className="grp" aria-labelledby="tools-qr">
              <h3 id="tools-qr" className="eyebrow">Follow on your phone</h3>
              <div className="qr-row">
                <QrCode text={phoneUrl(location.origin, session, judge ?? "", token)} label="QR code to your phone" />
                <p>
                  Your own feed, in sync with this screen. No app, no sign-in.{" "}
                  <a href={phoneUrl(location.origin, session, judge ?? "", token)} target="_blank" rel="noreferrer">Open it here</a>
                </p>
              </div>
            </section>
          </div>
        </aside>
      )}

      {trace && <Trace decisions={store.decisions} names={match.managers} t={t} onClose={() => setTrace(false)} />}
    </div>
  );
}
