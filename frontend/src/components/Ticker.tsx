// The match's key moments, newest first, as ticker cards: minute, what happened and who. In the phone's language
// on a phone (#127); the group screen passes none and stays English. In a gameweek (`fixtures`) it lists every
// fixture's moments, each labelled with its clubs (#94).
import { gameweekTicker, ticker, type TickerItem } from "../lib/match";
import type { MatchEvent, MatchMeta } from "../lib/messages";
import { decimal, words, type PhoneLang } from "../lib/phoneText";
import { Crest } from "./Crest";

const ICON: Record<TickerItem["kind"], string> = {
  goal: "⚽", save: "🧤", yellow_card: "🟨", red_card: "🟥", kickoff: "▶", half_time: "⏸", full_time: "⏹",
};

export function Ticker({ meta, events, t, names, limit, lang = "en", fixtures }: {
  meta: MatchMeta | null; events: MatchEvent[]; t: number; names: Map<string, string>; limit?: number; lang?: PhoneLang;
  fixtures?: { id: string; meta: MatchMeta | null; events: MatchEvent[] }[];
}) {
  const w = words(lang);
  const items = (fixtures ? gameweekTicker(fixtures, t) : ticker(events, t)).slice(0, limit);
  const metaOf = (item: TickerItem) => (item.fixture ? fixtures?.find((f) => f.id === item.fixture)?.meta : meta) ?? null;
  if (items.length === 0) return <p className="empty">{w.tickerEmpty}</p>;
  return (
    <ol className="ticker">
      {items.map((item) => {
        const match = metaOf(item);
        const club = match?.[item.side];
        const whistle = item.kind === "kickoff" || item.kind === "half_time" || item.kind === "full_time";
        return (
          <li key={`${item.fixture ?? ""}-${item.seq}-${item.kind}`} className={`ticker-item ${item.kind}`}>
            <span className="minute-pill">{item.minute}'</span>
            <span className="icon" aria-hidden="true">{ICON[item.kind]}</span>
            <span className="what">
              <strong>{w.ticker[item.kind]}</strong>
              {item.fixture && match && (
                <span className="fixture-label">{w.fixture(match.home.short_name, match.away.short_name)}</span>
              )}
              {!whistle && item.player && (
                <small>
                  {names.get(item.player)}
                  {item.kind === "save" && item.against ? w.denies(names.get(item.against) ?? "") : ""}
                  {item.xg !== undefined ? ` · xG ${decimal(item.xg, lang, 2)}` : ""}
                </small>
              )}
            </span>
            {!whistle && club && <Crest side={item.side} short={club.short_name} size={22} />}
          </li>
        );
      })}
    </ol>
  );
}
