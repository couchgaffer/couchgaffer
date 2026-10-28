// "Behind the scenes": for each moment, every manager agent's bid, the scores and fairness ledger, who
// decided (scorer, host or fallback) and how long it took. The multi-agent design, shown as it runs.
import type { HostDecision } from "../lib/messages";

const DECIDER = { scorer: "Scorer (code): clear winner", host: "Host agent: close call", fallback: "Fallback" };

type Row = HostDecision["scores"][number];

/** How a bid was made, and who wrote the pitch quoted beside it: code's plain line can stand in for an agent's
 * pitch that failed its checks, while the agent's phone text is kept (#138). */
export function bidLabel(r: Pick<Row, "status" | "pitch_source">): string {
  if (r.status === "fallback") return "plain stat";
  return r.pitch_source === "code" ? `${r.status ?? "ok"}, plain pitch` : (r.status ?? "");
}

/** One unit for every time on the page (#194): seconds to a tenth. A time of zero (code's own plain stat) is not
 * shown, since it says nothing. */
export function seconds(ms: number): string {
  return `${(ms / 1000).toFixed(1)} s`;
}

export function Trace({ decisions, names, t, onClose }: {
  decisions: HostDecision[]; names: Map<string, string>; t: number; onClose: () => void;
}) {
  const due = decisions.filter((d) => d.t <= t).reverse();
  return (
    <aside className="trace" aria-label="Behind the scenes">
      <header>
        <h3>Behind the scenes</h3>
        <button type="button" className="btn line" onClick={onClose}>Close</button>
      </header>
      <p className="hint">
        Each manager agent bids for the group screen. Code scores the bids with a fairness ledger; the host agent
        settles only the close calls. If an agent misses its deadline, code falls back to a plain stat.
      </p>
      {due.length === 0 && <p className="hint">The first key moment will appear here.</p>}
      {due.map((d) => (
        <article key={d.moment_id} className={`decision ${d.source}`}>
          <h4>
            {d.moment_id.replace("m_", "Moment ")} · {Math.floor(d.t / 60_000) + 1}'
            <span className={`tag ${d.source === "host" ? "on" : ""}`}>{DECIDER[d.source]}</span>
            {d.latency_ms && d.source !== "scorer" ? <span className="ms">{seconds(d.latency_ms)}</span> : null}
          </h4>
          {d.reason && <p className="host-reason"><strong>Host:</strong> {d.reason}</p>}
          <table>
            <thead>
              <tr><th>Manager</th><th>Score</th><th>Airtime</th><th>Lost calls</th><th>Bid</th></tr>
            </thead>
            <tbody>
              {d.scores.map((r) => (
                <tr key={r.manager} className={r.manager === d.chosen_manager ? "chosen" : undefined}>
                  <td>{names.get(r.manager) ?? r.manager}{r.manager === d.chosen_manager ? " ✓" : ""}</td>
                  <td>{r.score.toFixed(2)}</td>
                  <td>{Math.round(r.airtime_share * 100)}%</td>
                  <td>{r.overruled_count}</td>
                  <td>
                    {r.pitch && <q>{r.pitch}</q>}
                    <span className={`bid ${r.status ?? ""}`}>
                      {bidLabel(r)}
                      {r.latency_ms ? `, ${seconds(r.latency_ms)}` : ""}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </article>
      ))}
    </aside>
  );
}
