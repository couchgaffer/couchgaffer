// "For judges" (#92): what the demo is, a guided path through it, how it covers the brief, the evidence, the
// architecture and the Microsoft services. Static: it imports nothing that fetches, starts a session or opens a
// socket, so reading it costs no seat and no model call. Its styles are entry.css (#196), which the other entry pages
// import and the app bundles; it draws the entry pages' top bar itself with plain links.
import { useEffect } from "react";

type Row = readonly string[];

// Quoted from docs/evidence.md, word for word and in full; judges.test.tsx checks them against the file.
export const EVIDENCE_RUN = "Run on 27 Oct 2026, 04:51 UTC";
export const MODEL_ROWS: Row[] = [
  ["Explanations delivered in 4.5 s", "86% (38/44)", "93% (41/44)"],
  ["of which after the one retry", "6", "12"],
  ["Fell back to the plain line", "6", "3"],
  ["Screen picked by a model", "4", "19"],
  ["Screen picked by our scorer (clear winner, no host call)", "15", "n/a"],
  ["Screen pick fell back to code", "1", "1"],
  ["Decision latency p50 / p95 ms", "2916 / 4020", "1888 / 4332"],
  ["Decisions over 4.5 s", "1", "0"],
  ["Model calls", "75", "27"],
  ["of which cut off by the budget (no tokens reported)", "2", "0"],
  ["Tokens in / out", "at least 110365 / 8913", "83812 / 5933"],
  ["Cost, USD", "at least 0.1229", "0.0896"],
];
export const ARBITRATION_ROWS: Row[] = [
  ["Mean airtime spread (lower is fairer)", "0.40", "0.43"],
  ["Mean minimum airtime share", "2.5%", "2.3%"],
  ["Friends shut out (of 245)", "52", "59"],
  ["Reach, all friends", "42%", "42%"],
  ["Reach, worst friend", "22%", "19%"],
  ["Reach, Jo (mgr_1)", "39%", "46%"],
  ["Reach, Priya (mgr_2)", "53%", "52%"],
  ["Reach, Arjun (mgr_3)", "43%", "40%"],
  ["Reach, Leo (mgr_4)", "22%", "19%"],
  ["Reach, Sam (mgr_5)", "46%", "43%"],
  ["Screen shows the biggest swing", "98%", "100%"],
  ["Shown swing / biggest swing", "99.7%", "100.0%"],
];
export const CHECKER = "100 of 100 caught.";
export const WHERE_OURS_LOSES = [
  "Moments showing the biggest swing: ours 98.5%, greedy 100%.",
  "Shown swing as a share of the biggest: ours 99.7%, greedy 100.0%.",
  "Jo's reach: ours 39%, greedy 46%.",
  "Explanations delivered in the budget: ours 86%, one agent 93%.",
  "Explanations that fell back: ours 6, one agent 3.",
  "Decision latency, median: ours 2916 ms, one agent 1888 ms. At p95 ours is faster, 4020 ms against 4332 ms.",
  "Decisions over 4.5 s: ours 1, one agent 0.",
  "Model calls: ours 75, one agent 27.",
  "Model calls cut off by the budget: ours 2, one agent 0.",
  "More tokens: ours at least 110365 in and 8913 out, one agent 83812 in and 5933 out. None of the one agent's calls were cut off, so its figures are exact and ours are already higher.",
];
export const LIMITATIONS = [
  "The arbitration run does not include the host model's judgement; the model run does, on few moments.",
  "The model sample is small and one run: latency and delivery move with the service's load that hour.",
  "Latency is from a laptop; production calls run in-region.",
  "Tokens of a call cut off by the budget are not reported by the service: tokens and cost are then a lower bound, marked 'at least', and a cost difference against a lower bound is not listed as a loss.",
  "The one agent keeps any explanation that passes on its second attempt, which is generous to it: its single answer is otherwise all or nothing.",
  "The one agent writes no pitch, so its answer skips two checks our agents run on every bid: the angle check and the room check on the pitch (#138). Its explanations get the same text checks as ours. This exemption favours the baseline.",
  "Both baselines are ours to build; a stronger one-agent prompt might do better.",
  "Airtime is measured from five managers with fixed squads (league_v1); another league may differ.",
];

export const SECTIONS = [
  { id: "try", title: "Try it in six steps" },
  { id: "brief", title: "How it covers the brief" },
  { id: "evidence", title: "Does the multi-agent design earn its place?" },
  { id: "architecture", title: "How it works" },
  { id: "microsoft", title: "Microsoft technology" },
  { id: "data", title: "All data is invented" },
  { id: "gameweek", title: "The gameweek" },
] as const;

const STAGES: { stage: string; what: string; see: string }[] = [
  { stage: "1. Ingest", what: "A seeded simulator streams invented match events: passes, shots, tackles, pressure, cards.",
    see: "The pitch and Key moments on the group screen" },
  { stage: "2. Interpret", what: "Code computes the match stats, chance quality and each friend's impact points, with evidence for every number.",
    see: "The points card on each phone, the mini-league table" },
  { stage: "3. Explain", what: "One manager agent per friend says why the moment matters to that squad. Code refuses any number not in the evidence and any name not in the match.",
    see: "The phone in Phone and tools" },
  { stage: "4. Render", what: "Every output is a timed cue with a schema: one card at a time on the group screen, each phone its own feed.",
    see: "The group screen, the phones, the close-call takeover" },
  { stage: "5. Personalise", what: "By squad, by style (casual or analyst) and by language (English or Spanish).",
    see: "The style toggle on your phone; Leo's phone is in Spanish" },
]; // prettier-ignore

// The same steps as the diagram, in order: the diagram's text equivalent.
const FLOW = [
  "Simulator: an invented match from a seed",
  "Points engine (code): stakes, evidence and facts per friend",
  "Manager agents: one per friend with a stake, in parallel",
  "Checkers (code): numbers, names, language, tone",
  "Scorer and fairness ledger (code)",
  "Host agent: close calls only, with a reason",
  "Cues: the group screen and each phone",
];

const FLOW_TOP = 40;
const FLOW_STEP = 62;

function Diagram() {
  return (
    <figure className="arch">
      <svg viewBox={`0 0 320 ${FLOW_TOP + FLOW.length * FLOW_STEP}`} role="img" aria-labelledby="arch-title"
           aria-describedby="arch-text">
        <title id="arch-title">How one key moment flows, from the simulator to the screens</title>
        <defs>
          <marker id="arch-arrow" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0,0 L10,5 L0,10 z" className="arch-head" />
          </marker>
        </defs>
        <text x="160" y="22" className="arch-caption" textAnchor="middle">One key moment, 4.5 s budget</text>
        {FLOW.map((step, i) => {
          const y = FLOW_TOP + i * FLOW_STEP;
          const [name, detail] = step.split(": ");
          const kind = /agent/i.test(name ?? "") ? "agent" : "code";
          return (
            <g key={step} className={`arch-box ${kind}`}>
              <rect x="10" y={y} width="300" height="44" rx="10" />
              <text x="160" y={y + 19} textAnchor="middle" className="arch-name">{name}</text>
              {detail && <text x="160" y={y + 35} textAnchor="middle" className="arch-detail">{detail}</text>}
              {i < FLOW.length - 1 && (
                <line x1="160" y1={y + 44} x2="160" y2={y + FLOW_STEP - 4} markerEnd="url(#arch-arrow)" />
              )}
            </g>
          );
        })}
      </svg>
      <figcaption>
        Boxes with a lime outline are AI agents; grey boxes are code. Models write words and settle close calls; they never put a
        number on screen that code has not computed first.
      </figcaption>
    </figure>
  );
}

function Table({ label, head, rows }: { label: string; head: Row; rows: Row[] }) {
  return (
    <div className="table-scroll" role="region" aria-label={label} tabIndex={0}>
      <table>
        <thead><tr>{head.map((h) => <th key={h} scope="col">{h}</th>)}</tr></thead>
        <tbody>
          {rows.map(([first, ...rest]) => (
            <tr key={first}><th scope="row">{first}</th>{rest.map((c, i) => <td key={i}>{c}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Judges() {
  useEffect(() => {
    const before = document.title;
    document.title = "For judges · Couch Gaffer";
    return () => { document.title = before; };
  }, []);

  return (
    <div className="entry">
    <header className="e-top">
      <a className="e-brand" href="/"><i aria-hidden="true" />Couch Gaffer</a>
      <nav aria-label="Pages"><a href="/league">Your leagues</a><a href="/judges" aria-current="page">For judges</a></nav>
    </header>
    <main className="e-jd">
      <h1>For judges</h1>
      <p className="e-lead">
        Couch Gaffer is a watch party for five friends in a fantasy football mini-league, with invented matches on one
        shared screen: by default a gameweek of three matches at once, or a single match. Each friend's AI agent argues for the moments that matter to their squad; code ranks the
        arguments with a fairness ledger, and a host agent settles the close calls.
      </p>

      <nav aria-label="On this page">
        <ul className="toc">
          {SECTIONS.map((s) => <li key={s.id}><a href={`#${s.id}`}>{s.title}</a></li>)}
        </ul>
      </nav>

      <section id="try" aria-labelledby="try-h">
        <h2 id="try-h">Try it in six steps</h2>
        <p>No sign-in. A gameweek or a single match takes about three minutes.</p>
        <ol className="e-try">
          <li><span>
            <strong>Start the demo gameweek as Jo.</strong> On the first page, choose Watch now. Its three steps pick
            your manager (Jo), your phone (a style, and English) and the match ("A gameweek: three matches at once" and
            "The demo match"); each has that default, so you can press "Start the watch party" below them straight away.
            The other four friends are bots. For one match instead, choose "A single match" in the third step.
          </span></li>
          <li><span>
            <strong>Watch for a close call.</strong> When two friends' agents both have a strong case, the group screen
            gives way to their two pitches side by side, then the verdict (with the host's reason when the host
            decided), then the chosen moment. The friend who lost the screen gets a note on their phone. Your phone is
            in Phone and tools, at the top right; its tabs show any other friend's phone, and Leo's is in Spanish.
          </span></li>
          <li><span>
            <strong>Open Behind the scenes</strong>, at the top right. For each decision it lists every agent's bid,
            the scores with each friend's airtime and lost close calls, who decided (the scorer in code for a clear
            winner, the host agent for a close call, or the fallback) and, for a host or fallback decision, how long
            it took and, when the host decided, its reason.
          </span></li>
          <li><span>
            <strong>Knock out an agent.</strong> In Phone and tools, under "Try a failure", press "Knock out your agent", or
            open another friend's phone first and knock out theirs. The next time that agent bids, its phone gets a
            plain stat line written by code, and Behind the scenes marks that bid "plain stat". The other agents and
            the host carry on, and the match never waits.
          </span></li>
          <li><span>
            <strong>Stay to full time.</strong> At half time and full time each friend's phone gets a summary. It is
            written by a summary agent that calls the match tools (in a gameweek, across all three matches), or by
            code if that agent fails. On the public demo, within a few minutes of full time, each
            phone also gets a link to a recap video cut around that friend's players.
          </span></li>
          <li><span>
            <strong>Then play it as a league.</strong> On the first page, choose Play with your friends and start a
            league. Open its join link in a private window as a second friend and join. Each of you picks five players
            and a captain, then one of you presses "Start gameweek 1". At full time the season table on the league's
            page shows the result.
          </span></li>
        </ol>
        <p className="note">
          Each address gets up to 4 matches an hour with the AI agents, fewer when the demo is busy. Later matches run on plain stat lines from code,
          with no recap. A copy run locally without Azure settings has no agents and no recaps.
        </p>
        <p><a className="btn acc" href="/?watch">Open Watch now to start the demo</a></p>
      </section>

      <section id="brief" aria-labelledby="brief-h">
        <h2 id="brief-h">How it covers the brief</h2>
        <p>The brief names five stages. Each one is a part of the app you can watch.</p>
        <div className="table-scroll stack" role="region" aria-label="The five stages" tabIndex={0}>
          <table>
            <thead><tr><th scope="col">Stage</th><th scope="col">What does it</th><th scope="col">Where to see it</th></tr></thead>
            <tbody>
              {STAGES.map((s) => (
                <tr key={s.stage}><th scope="row">{s.stage}</th><td>{s.what}</td><td>{s.see}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
        <h3>Why several agents</h3>
        <p>
          The agents have competing interests and shared state. Each manager agent argues for its own friend. Code ranks
          the arguments with a fairness ledger, weighed by how often each friend has been on screen and how many close
          calls they have lost, and the host agent settles the close calls. Behind the scenes shows every argument and every decision as it happens. The next
          section measures whether that design is worth its cost.
        </p>
      </section>

      <section id="evidence" aria-labelledby="evidence-h">
        <h2 id="evidence-h">Does the multi-agent design earn its place?</h2>
        <p>
          Our agents are measured against two baselines. One agent that writes for every friend whose moment is worth points and picks the screen
          gets the same moments, the same 4.5 s budget and the same text checks on its explanations; it writes no pitch, so it skips our angle and room checks. A code rule that always shows the biggest
          swing needs no model, no budget and no checks, and is compared on who gets the screen. The figures below
          are quoted from the evidence report in the repository (<code>docs/evidence.md</code>). {EVIDENCE_RUN}. The model numbers vary from run to run.
        </p>
        <h3>With the model</h3>
        <p>Model gpt-5.4-mini for every agent call, 20 held-out moments, 44 explanations owed to each.</p>
        <details>
          <summary>
            Ours delivered 86% of explanations in time against 93% for one agent, and 6 against 3 fell back to the
            plain line. One decision of ours took over 4.5 s (none of the one agent's did), and ours was slower at
            the median (2916 ms against 1888 ms) with more model calls (75 against 27). Show the table.
          </summary>
          <Table label="With the model" head={["Measure", "ours", "one agent"]} rows={MODEL_ROWS} />
        </details>
        <p>The retry row does not compare like with like: our agents retry one friend's bid at a time, while the one agent retries its whole answer, so every owed friend whose line passes on that second attempt counts as retried.</p>
        <h3>Who gets the screen, without models</h3>
        <p>50 matches, 392 decided moments, 245 friend-matches with at least one eligible moment.</p>
        <details>
          <summary>
            The friend reached least got the screen in 22% of their moments with ours against 19% with the code rule,
            and ours showed the biggest swing in 98% of moments against 100%. Show the table.
          </summary>
          <Table label="Who gets the screen" head={["Measure", "ours", "greedy"]} rows={ARBITRATION_ROWS} />
        </details>
        <h3>Number checker</h3>
        <p>
          {CHECKER} Code wrote a false number into each line: a wrong value, a flipped sign, a wrong league total, or
          the true value in the wrong unit.
        </p>
        <h3>Where ours loses</h3>
        <ul>{WHERE_OURS_LOSES.map((l) => <li key={l}>{l}</li>)}</ul>
        <h3>Limitations</h3>
        <ul>{LIMITATIONS.map((l) => <li key={l}>{l}</li>)}</ul>
      </section>

      <section id="architecture" aria-labelledby="architecture-h">
        <h2 id="architecture-h">How it works</h2>
        <Diagram />
        <h3>The same flow, in words</h3>
        <ol id="arch-text" className="flow">{FLOW.map((step) => <li key={step}>{step}</li>)}</ol>
        <p>
          The screens run 5 s behind the stream, so each moment is decided before the screens reach it. A manager
          agent that misses its time falls back to a plain stat for that friend only, and a host that fails falls back
          to the scorer's pick. At full time the match goes on a queue, and a separate job renders one recap video per
          friend.
        </p>
      </section>

      <section id="microsoft" aria-labelledby="microsoft-h">
        <h2 id="microsoft-h">Microsoft technology</h2>
        <ul>
          <li>Microsoft Foundry models: <code>gpt-5.4-mini</code> for the manager, host and summary agents.</li>
          <li>Microsoft Agent Framework: the per-moment workflow, the agents and their function middleware.</li>
          <li>MCP: the match tools the summary agents call (standings, squad points, key moments, rules).</li>
          <li>Foundry IQ, on Azure AI Search: the league rulebook the summaries cite.</li>
          <li>
            Azure Container Apps for the app, and a Container Apps Job for the recaps, started by an Azure Storage
            queue; the videos are kept in Blob Storage.
          </li>
          <li>Managed identities: the app holds no keys.</li>
          <li>Azure Monitor (Log Analytics) for the logs.</li>
        </ul>
      </section>

      <section id="data" aria-labelledby="data-h">
        <h2 id="data-h">All data is invented</h2>
        <p>
          All match data is synthetic: the clubs, players, managers and the match itself are invented and generated in
          code. The demo has no sign-in and asks for no personal data. It is free to play, with no betting and no
          prizes.
        </p>
      </section>

      <section id="gameweek" aria-labelledby="gameweek-h">
        <h2 id="gameweek-h">The gameweek</h2>
        <p>
          Watch now starts a gameweek unless you choose otherwise: three matches among six invented clubs, kicking off
          together on one match clock. In the demo gameweek each friend's squad has players in all three matches, and
          in any gameweek one league table adds up their points from every match. Each match blows its own whistles, within half a match minute of the others;
          the half-time and full-time table and summaries come once, at the last whistle.
        </p>
        <ul>
          <li>
            A gameweek has 15 to 18 key moments, five or six per match, at least three match minutes apart, so each
            one is still decided on its own by the same agents, scorer and host, in the same 4.5 s.
          </li>
          <li>
            The group screen follows the decisions: it shows the match of the card on screen. The top bar
            shows all three scores and marks the one on the big screen. Each phone shows the same strip, and each card
            in its feed names its match and the score at that minute.
          </li>
          <li>
            If one match fails while it is being handled, that match alone gets a full-time whistle where it stopped;
            its points stay in the table and the other two play on.
          </li>
          <li>A friend's recap can mix moments from all three matches, each clip drawn from its own match.</li>
        </ul>
        <p>
          The single match is one choice away: pick "A single match" in Watch now's third step. The figures in the evidence section
          above come from single matches.
        </p>
      </section>
    </main>
    </div>
  );
}
