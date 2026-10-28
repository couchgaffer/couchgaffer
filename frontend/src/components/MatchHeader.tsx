// The scoreboard, as a broadcast shows it: crests, the score, the live minute, each side's xG and the last
// scorer. "broadcast" for the group screen, "compact" for a phone, in the phone's language (#127).
import type { Scoreboard } from "../lib/match";
import type { MatchMeta } from "../lib/messages";
import { decimal, words, type PhoneLang } from "../lib/phoneText";
import { Crest } from "./Crest";

export function MatchHeader({ meta, score, names, variant, lang = "en" }: {
  meta: MatchMeta | null;
  score: Scoreboard;
  names: Map<string, string>; // player id -> name
  variant: "broadcast" | "compact";
  lang?: PhoneLang;
}) {
  const w = words(lang);
  if (!meta) return <header className={`match-header ${variant}`} aria-busy="true" />;
  const size = variant === "broadcast" ? 56 : 40;
  const live = score.phase === "live";
  return (
    <header className={`match-header ${variant}`} aria-label={w.score}>
      <div className="team home">
        <Crest side="home" short={meta.home.short_name} size={size} />
        <span className="team-text">
          <span className="team-name">{meta.home.name}</span>
          <span className="team-word" aria-hidden="true">{meta.home.name.split(" ")[0]}</span>
          <span className="xg">{decimal(score.xg.home, lang, 2)} xG</span>
        </span>
      </div>
      <div className="centre">
        <span className={`clock ${live ? "live" : ""}`}>
          {live && <span className="dot" aria-hidden="true" />}
          {live ? `${score.minute}'` : w.phase[score.phase]}
        </span>
        <span className="score" aria-live="polite">
          {score.home}<span className="dash">-</span>{score.away}
        </span>
        {score.lastGoal && (
          <span className="scorer">
            <span aria-hidden="true">⚽</span> {names.get(score.lastGoal.player) ?? ""} {score.lastGoal.minute}'
          </span>
        )}
      </div>
      <div className="team away">
        <Crest side="away" short={meta.away.short_name} size={size} />
        <span className="team-text">
          <span className="team-name">{meta.away.name}</span>
          <span className="team-word" aria-hidden="true">{meta.away.name.split(" ")[0]}</span>
          <span className="xg">{decimal(score.xg.away, lang, 2)} xG</span>
        </span>
      </div>
    </header>
  );
}
