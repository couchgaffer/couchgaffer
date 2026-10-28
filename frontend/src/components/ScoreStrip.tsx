// A gameweek's three scores in one strip above the pitch, the one on the big screen marked (#94). The group screen
// and every phone show it; a single match has one fixture and no strip. On the group screen they are plain scores
// (#207); on a phone (`onPick`) each is a button that switches the phone's own view to that fixture (#208).
import type { Scoreboard } from "../lib/match";
import type { MatchMeta } from "../lib/messages";
import { words, type PhoneLang } from "../lib/phoneText";
import { Crest } from "./Crest";

/** "KES 1-0 RAV": a fixture's short names and its score. */
export function scoreline(meta: MatchMeta, score: Pick<Scoreboard, "home" | "away">): string {
  return `${meta.home.short_name} ${score.home}-${score.away} ${meta.away.short_name}`;
}

export function ScoreStrip({ fixtures, focus, lang = "en", shown, onPick }: {
  fixtures: { id: string; meta: MatchMeta | null; score: Scoreboard }[];
  focus: string;
  lang?: PhoneLang;
  shown?: string; // the fixture this phone shows, marked pressed
  onPick?: (id: string) => void; // a phone's: a tap shows that fixture
}) {
  const w = words(lang);
  const listed = fixtures.filter((f): f is typeof f & { meta: MatchMeta } => f.meta !== null);
  if (listed.length < 2) return null;
  return (
    <ol className="score-strip" aria-label={w.strip.label}>
      {listed.map(({ id, meta, score }) => {
        const body = (
          <>
            <span className="teams">
              <Crest side="home" short={meta.home.short_name} size={18} />
              <span className="line">{scoreline(meta, score)}</span>
              <Crest side="away" short={meta.away.short_name} size={18} />
            </span>
            {/* A live fixture's minute is the bug's too; a fixture stopped alone keeps its HT or FT in view (#194) */}
            <span className={score.phase === "live" ? "when live" : "when"}>
              {score.phase === "live" ? `${score.minute}'` : w.phase[score.phase]}
            </span>
            {id === focus && <span className="on-screen">{w.strip.onScreen}</span>}
          </>
        );
        return (
          <li key={id} className={id === focus ? "focused" : undefined} aria-current={id === focus ? "true" : undefined}>
            {onPick ? (
              <button type="button" className="pick" aria-pressed={id === shown} onClick={() => onPick(id)}>{body}</button>
            ) : body}
          </li>
        );
      })}
    </ol>
  );
}
