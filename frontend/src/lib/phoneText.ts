// The words a phone writes itself, in its friend's language (#119, #127). The backend keeps writing English titles
// ("Full time" is how recap/plan.py finds the full-time summary), so the phone translates from the cue kind. The
// group screen is English and calls the same components with no language, so it reads exactly as before.
import type { Style } from "./api";
import type { Scoreboard, TickerItem } from "./match";
import type { Cue, MatchEvent } from "./messages";

export type PhoneLang = "en" | "es";

/** "es" and "es-ES" are Spanish; anything else is English. */
export function phoneLang(lang: string | undefined): PhoneLang {
  return /^es(?:-|$)/i.test(lang ?? "") ? "es" : "en";
}

export type MomentKind = "goal" | "save" | "chance" | "yellow" | "red" | "other";
export type PhoneTab = "you" | "live" | "table";

interface Words {
  moment: Record<MomentKind, string>;
  saved: (keeper: string | undefined, shooter: string | undefined) => string;
  summary: Record<string, string>; // the backend's English summary title -> this language's
  recapReady?: string; // none: the backend's English title
  watchRecap: string;
  phoneOf: (name: string) => string; // the phone's accessible name, from the backend's name ("You" for the judge)
  you: string; // the judge's manager, whom the backend names "You" (app/session.py), standing alone as a name
  ordinal: (n: number) => string;
  tabs: Record<PhoneTab, string>;
  sections: string;
  styleGroup: string;
  style: Record<Style, string>;
  styleHint: Record<Style, string>;
  emptyFeed: { lead: string; body: (whose: string | null) => string }; // whose: null is the viewer's own squad
  keyMoments: string;
  ticker: Record<TickerItem["kind"], string>;
  denies: (shooter: string) => string; // the rest of a ticker's save line, after the keeper's name
  tickerEmpty: string;
  phase: Record<Scoreboard["phase"], string>; // the compact scoreboard's clock when the ball is not in play
  score: string;
  pitch: string;
  pitchOf: (home: string, away: string) => string;
  tag: { goal: string; shot: string; save: string; sprint: string; card: (colour: string | undefined) => string };
  event?: Partial<Record<MatchEvent["type"], string>>; // none: the event type as the contract names it
  table: string;
  moved: { up: string; down: string };
  evidence: (n: number) => string;
  marks: { ai: string; aiTitle: string; fallback: string; fallbackTitle: string; rule: string; ruleTitle: string };
  ended: { title: string; body: string; action: string };
  connecting: string;
  banner: { half_time: string; full_time: string }; // the pitch badge, shorter than the ticker on a Spanish phone
  // A gameweek (#94): the strip of every fixture's score, a ticker item's fixture, a feed card's fixture tag
  strip: { label: string; onScreen: string };
  fixture: (home: string, away: string) => string;
  scoreThen: string;
}

const WORDS: Record<PhoneLang, Words> = {
  en: {
    moment: { goal: "Goal", save: "Save", chance: "Chance", yellow: "Yellow card", red: "Red card", other: "Key moment" },
    saved: (keeper, shooter) => `${keeper ?? "The keeper"} denies ${shooter ?? ""}`,
    summary: {},
    watchRecap: "Watch your recap",
    phoneOf: (name) => (name === "You" ? "Your phone" : `${name}'s phone`),
    you: "You",
    ordinal: (n) => `${n}${({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[n] ?? "th"}`,
    tabs: { you: "For you", live: "Live", table: "Table" },
    sections: "Sections",
    styleGroup: "How you like it",
    style: { casual: "Casual", analyst: "Analyst" },
    styleHint: { casual: "One plain line a moment", analyst: "The numbers, and the evidence behind them" },
    emptyFeed: {
      lead: "Your moments land here.",
      body: (whose) => `When something happens to ${whose === null ? "your" : `${whose}'s`} squad, this phone explains what it means, a few seconds before the big screen shows it.`, // prettier-ignore
    },
    keyMoments: "Key moments",
    ticker: { goal: "Goal", save: "Save", yellow_card: "Yellow card", red_card: "Red card", kickoff: "Kick-off",
              half_time: "Half time", full_time: "Full time" }, // prettier-ignore
    denies: (shooter) => ` denies ${shooter}`,
    tickerEmpty: "Key moments will appear here as the match plays.",
    phase: { pre: "Kick-off soon", live: "", half_time: "HT", full_time: "FT" },
    score: "Score",
    pitch: "Pitch",
    pitchOf: (home, away) => `Pitch: ${home} against ${away}`,
    tag: { goal: "goal", shot: "shot", save: "save", sprint: "sprint", card: (colour) => `${colour} card` },
    table: "Mini-league table",
    moved: { up: "moved up", down: "moved down" },
    evidence: (n) => `The numbers behind it (${n})`,
    marks: {
      ai: "AI", aiTitle: "Written by an AI agent. Every number in it was checked by code.",
      fallback: "Plain stat", fallbackTitle: "The agent missed its deadline or its checks; code wrote this.",
      rule: "Rulebook", ruleTitle: "Retrieved from the league rulebook, a Microsoft Foundry IQ knowledge base",
    }, // prettier-ignore
    ended: {
      title: "This watch party has ended",
      body: "Matches last about three minutes, and a phone link works only while its match is on.",
      action: "Start a new watch party",
    },
    connecting: "Connecting…",
    banner: { half_time: "Half time", full_time: "Full time" },
    strip: { label: "Gameweek scores", onScreen: "On the big screen" },
    fixture: (home, away) => `${home} v ${away}`,
    scoreThen: "The score in that match at the time",
  },
  es: {
    moment: { goal: "Gol", save: "Parada", chance: "Ocasión", yellow: "Tarjeta amarilla", red: "Tarjeta roja", other: "Jugada clave" },
    saved: (keeper, shooter) => `${keeper ?? "El portero"} para el disparo${shooter ? ` de ${shooter}` : ""}`,
    summary: { "Half time": "Descanso", "Full time": "Final del partido" }, // as the backend's Spanish summary says it
    recapReady: "Tu resumen está listo",
    watchRecap: "Ver tu resumen",
    phoneOf: (name) => (name === "You" ? "Tu teléfono" : `Teléfono de ${name}`),
    you: "Tú",
    ordinal: (n) => `${n}.º`, // as the backend's Spanish writes a place (agents/checks.py)
    tabs: { you: "Para ti", live: "En directo", table: "Clasificación" },
    sections: "Secciones",
    styleGroup: "Cómo lo prefieres",
    style: { casual: "Sencillo", analyst: "Analista" },
    styleHint: { casual: "Una frase sencilla por jugada", analyst: "Los números y la evidencia que hay detrás" },
    emptyFeed: {
      lead: "Tus jugadas aparecen aquí.",
      body: (whose) => `Cuando pase algo en ${whose === null ? "tu equipo" : `el equipo de ${whose}`}, este teléfono te explica qué significa, unos segundos antes de que lo muestre la pantalla grande.`, // prettier-ignore
    },
    keyMoments: "Jugadas clave",
    ticker: { goal: "Gol", save: "Parada", yellow_card: "Tarjeta amarilla", red_card: "Tarjeta roja",
              kickoff: "Saque inicial", half_time: "Descanso", full_time: "Fin del partido" }, // prettier-ignore
    denies: (shooter) => ` para el disparo de ${shooter}`,
    tickerEmpty: "Las jugadas clave aparecerán aquí durante el partido.",
    phase: { pre: "Empieza pronto", live: "", half_time: "Descanso", full_time: "Final" },
    score: "Marcador",
    pitch: "Campo",
    pitchOf: (home, away) => `Campo: ${home} contra ${away}`,
    tag: {
      goal: "gol", shot: "disparo", save: "parada", sprint: "sprint",
      card: (colour) => (colour === "red" ? "tarjeta roja" : colour === "yellow" ? "tarjeta amarilla" : "tarjeta"),
    }, // prettier-ignore
    event: {
      kickoff: "saque inicial", pass: "pase", carry: "conducción", dribble: "regate", pressure: "presión",
      tackle: "entrada", interception: "intercepción", possession_change: "cambio de posesión", shot: "disparo",
      save: "parada", goal: "gol", foul: "falta", card: "tarjeta", corner: "córner", free_kick: "tiro libre",
      offside: "fuera de juego", substitution: "cambio", half_time: "descanso", full_time: "final",
    }, // prettier-ignore
    table: "Clasificación de la miniliga",
    moved: { up: "sube", down: "baja" },
    evidence: (n) => `Los números que hay detrás (${n})`,
    marks: {
      ai: "IA", aiTitle: "Lo escribió un agente de IA. El código comprobó cada número.",
      fallback: "Dato simple", fallbackTitle: "El agente no llegó a tiempo o no pasó las comprobaciones; lo escribió el código.",
      rule: "Reglamento", ruleTitle: "Sacado del reglamento de la liga, una base de conocimiento de Microsoft Foundry IQ",
    }, // prettier-ignore
    ended: {
      title: "Esta sesión ha terminado",
      body: "Los partidos duran unos tres minutos, y el enlace del teléfono solo funciona mientras se juega su partido.",
      action: "Empezar una sesión nueva",
    },
    connecting: "Conectando…",
    banner: { half_time: "Descanso", full_time: "Final" },
    strip: { label: "Resultados de la jornada", onScreen: "En la pantalla grande" },
    fixture: (home, away) => `${home} contra ${away}`,
    scoreThen: "El marcador de ese partido en ese momento",
  },
};

/** Every word this phone writes itself, in its language. */
export function words(lang: PhoneLang): Words {
  return WORDS[lang];
}

export function momentWhat(kind: MomentKind, lang: PhoneLang): string {
  return WORDS[lang].moment[kind];
}

export function savedBy(keeper: string | undefined, shooter: string | undefined, lang: PhoneLang): string {
  return WORDS[lang].saved(keeper, shooter);
}

/** A manager's name as this phone shows it: the backend names the judge's manager "You", which a Spanish phone
 * writes "Tú" where the name stands alone (the top bar, the table). Sentences built around a name keep the
 * backend's, so they can say "para ti" (cues.ts, lost-screen note). Every other name is a name. */
export function shownName(name: string, lang: PhoneLang): string {
  return name === "You" ? WORDS[lang].you : name;
}

/** An event type as a pitch chip names it: the contract's own name in English. */
export function eventWord(type: MatchEvent["type"], lang: PhoneLang): string {
  return WORDS[lang].event?.[type] ?? type;
}

/** A number as this language writes it: 0.31 in English, 0,31 in Spanish (the backend's Spanish does the same). */
export function decimal(n: number, lang: PhoneLang, digits?: number): string {
  const text = digits === undefined ? String(n) : n.toFixed(digits);
  return lang === "es" ? text.replace(".", ",") : text;
}

/** The lang attribute for text in textLang on a phone in phoneLang: none on an English phone, so it reads as before;
 * on a Spanish phone, Spanish text says es and English text says en, so a screen reader switches voice. */
export function langAttr(textLang: PhoneLang, phone: PhoneLang): PhoneLang | undefined {
  return phone === "en" && textLang === "en" ? undefined : textLang;
}

export interface Title {
  text: string;
  lang: PhoneLang; // the language the text is in, which is English when the phone had no words for it
}

/** A summary or recap card's title on this phone. A title the phone has no words for is shown as the backend wrote
 * it, in English, and says so, so a screen reader on a Spanish phone does not read English as Spanish. */
export function cardTitle(cue: Cue, lang: PhoneLang): Title {
  const { headline } = cue.payload;
  const words = WORDS[lang];
  const translated = cue.kind === "recap_ready" ? words.recapReady : words.summary[headline];
  if (translated === undefined) return { text: headline, lang: "en" };
  return { text: translated, lang };
}

export function watchRecap(lang: PhoneLang): string {
  return WORDS[lang].watchRecap;
}
