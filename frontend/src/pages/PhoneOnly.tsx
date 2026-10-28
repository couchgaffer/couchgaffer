// The phone view on its own, for a real phone (the QR code, #17). It also takes the group screen's cues, which
// carry the live table, so its Table tab keeps up.
import { useMemo, useRef, useState } from "react";

import { PhoneApp } from "../components/PhoneApp";
import { readFragment, setStyle, type Style } from "../lib/api";
import { groupScreen, type Slot } from "../lib/cues";
import { langAttr, phoneLang, words, type PhoneLang } from "../lib/phoneText";
import { matchState } from "../lib/state";
import { useMatch } from "../lib/useMatch";

export function PhoneOnly({ session, manager }: { session: string; manager: string }) {
  const { token } = useMemo(() => readFragment(location.hash), []);
  const screens = useMemo(() => [`phone:${manager}`, "group"], [manager]);
  const { store, t, now, connection } = useMatch(session, screens, token);
  const [style, setLocalStyle] = useState<Style | null>(null);
  const me = store.league?.managers.find((m) => m.id === manager);
  // The group screen's card, held for its dwell as the group screen holds it, so in a gameweek the strip marks the
  // fixture the big screen is on (#94), not the newest card's while an older one is still up
  const slot = useRef<Slot | null>(null);
  const shown = groupScreen(store.cues.get("group") ?? [], t, now, slot.current);
  slot.current = shown.slot;

  // Until the league arrives the phone does not know its friend's language; the browser's is the best guess.
  const lang = phoneLang(me?.lang ?? navigator.language);
  if (!token || connection === "refused") return <Ended lang={lang} />;
  if (!me) return <main className="notice" lang={langAttr(lang, lang)}><p>{words(lang).connecting}</p></main>;
  const changeStyle = (s: Style) => {
    setLocalStyle(s);
    setStyle(session, token, s).catch(() => setLocalStyle(null));
  };
  return (
    <main className="phone-only">
      <PhoneApp manager={me} cues={store.cues.get(`phone:${manager}`) ?? []} style={style ?? me.style}
                onStyle={me.is_bot ? undefined : changeStyle} match={matchState(store, t, shown.card ?? shown.clash?.cue)} judge={manager} />
    </main>
  );
}

/** The match is over. In the friend's language when the phone already knew whose it was (#127); a link opened
 * after its match ended never learns that, so it reads in the browser's language. */
export function Ended({ lang }: { lang: PhoneLang }) {
  const w = words(lang).ended;
  return (
    <main className="notice" lang={langAttr(lang, lang)}>
      <h1>{w.title}</h1>
      <p>{w.body}</p>
      {/* The start screen; `/` is the two-way choice (#196) */}
      <a className="btn acc" href="/?watch">{w.action}</a>
    </main>
  );
}
