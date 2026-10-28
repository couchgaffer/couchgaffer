// "Live action": the pitch in perspective, with an upright chip naming the latest key moment's players, as a
// broadcast app does. In the phone's language on a phone (#127); the group screen passes none.
import type { Scoreboard } from "../lib/match";
import { tags } from "../lib/match";
import type { MatchEvent, MatchFrame, MatchMeta } from "../lib/messages";
import { words, type PhoneLang } from "../lib/phoneText";
import { Pitch } from "./Pitch";

export function LiveAction({ meta, frames, events, t, score, lang = "en" }: {
  meta: MatchMeta | null; frames: MatchFrame[]; events: MatchEvent[]; t: number; score: Scoreboard; lang?: PhoneLang;
}) {
  const w = words(lang);
  const players = new Map(
    (meta ? [["home", meta.home] as const, ["away", meta.away] as const] : []).flatMap(([side, club]) =>
      club.players.map((p) => [p.id, { ...p, side }]),
    ),
  );
  const chips = tags(events, t, lang).flatMap((tag) => {
    const p = players.get(tag.player);
    return p ? [{ ...tag, ...p }] : [];
  });
  return (
    <div className="live-action">
      <div className="stands" aria-hidden="true" />
      <div className="perspective">
        <Pitch meta={meta} frames={frames} events={events} t={t} lang={lang} />
      </div>
      <div className="chips" aria-live="polite">
        {chips.map((c) => (
          <span key={c.player} className="chip">
            <span className={`shirt ${c.side}`}>{c.shirt}</span>
            <span>
              <strong>{c.label.charAt(0).toUpperCase() + c.label.slice(1)}</strong>
              <small>{c.name}</small>
            </span>
          </span>
        ))}
      </div>
      {score.phase === "half_time" && <span className="pitch-banner">{w.banner.half_time}</span>}
      {score.phase === "full_time" && <span className="pitch-banner">{w.banner.full_time}</span>}
    </div>
  );
}
