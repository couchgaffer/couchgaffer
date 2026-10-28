// #89: a close call takes the group screen over (both pitches, versus, then the verdict), then hands back to the
// pitch with the chosen card; the losing friend's phone gets a lost-screen note. Built on contracts/examples.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { FeedCard, GroupStage } from "../components/Cards";
import { PhoneApp, type MatchState } from "../components/PhoneApp";
import type { Manager } from "./api";
import { PlaybackClock } from "./clock";
import { clashAt, groupScreen, handBackAt, lostScreenNote, phoneFeed, verdictText } from "./cues";
import type { Cue, WsMessage } from "./messages";
import { empty, receive } from "./useMatch";

const examples = import.meta.glob("../../../contracts/examples/cue.*.json", { eager: true, import: "default" });
const example = (name: string): Cue => structuredClone(examples[`../../../contracts/examples/${name}`] as Cue);

const CLASH = example("cue.clash.json"); // mgr_2 (Priya) and mgr_1 (Jo); the host picks mgr_1
const LOST = example("cue.lost-screen.json"); // on mgr_2's phone: the screen went to mgr_1
const SPEED = 30;
const DELAY = 5000;
const names = new Map([["mgr_1", "Jo"], ["mgr_2", "Priya"], ["mgr_3", "Sam"]]);
const verdictAt = CLASH.payload.verdict_at ?? 0;
const handBack = handBackAt(CLASH);
const PITCH = "<i>the live pitch</i>";

/** The winner's card for the same moment, as the backend puts on the group screen. */
const chosen: Cue = {
  cue_id: "c_29", seq: 29, screen: "group", moment_id: CLASH.moment_id, show_at: CLASH.show_at,
  min_dwell_ms: 6000, expires_at: CLASH.expires_at, kind: "explanation", source: "manager",
  payload: { headline: "Jo: +6 IP", body: "Captain saved, one off the bonus.", manager: "mgr_1" },
}; // prettier-ignore

const reason: Cue = {
  cue_id: "c_31", seq: 31, screen: "group", moment_id: CLASH.moment_id, show_at: CLASH.show_at, min_dwell_ms: 6000,
  expires_at: CLASH.expires_at, kind: "host_reason", source: "host",
  payload: { headline: "Host: showing Jo", body: "Jo has been overruled twice.", manager: "mgr_1" },
}; // prettier-ignore

const group = (cues: Cue[], t: number) =>
  renderToStaticMarkup(
    <GroupStage view={groupScreen(cues, t, 0, null)} cues={cues} t={t} names={names} judge={null}
                pitch={<i>the live pitch</i>} />,
  ); // prettier-ignore

/** The live region's content: what a screen reader would announce. */
const announced = (html: string) => /<div class="group-live" aria-live="polite">(.*)<\/div>$/.exec(html)?.[1] ?? "";

describe("a clash takes over the group screen (#89)", () => {
  it("shows both pitches large, versus, in place of the pitch, from show_at", () => {
    const view = groupScreen([CLASH, chosen], CLASH.show_at, 0, null);
    expect(view.clash?.phase).toBe("pitches");
    expect(view.card).toBeNull(); // the chosen card waits for the hand-back
    const html = group([CLASH, chosen], CLASH.show_at);
    expect(html).toContain("clash-takeover");
    expect(html).toContain('<span class="versus">vs</span>');
    expect(html).toContain("Priya&#x27;s keeper reaches the Wall tier.");
    expect(html).toContain("Jo&#x27;s captain saved, one off the bonus.");
    expect(html).toContain(`<div class="stage-main" hidden="">${PITCH}</div>`); // the pitch stays mounted, hidden
    expect(html).not.toContain("Verdict");
  });

  it("does not announce the pitches", () => {
    expect(group([CLASH, chosen], CLASH.show_at)).toContain('<div class="group-live" aria-live="polite"></div>');
  });

  it("switches to the verdict, large, with the host's reason, about one second later", () => {
    expect((verdictAt - CLASH.show_at) / SPEED).toBe(1000); // real ms at match speed
    expect(clashAt([CLASH], verdictAt - 1)?.phase).toBe("pitches");
    expect(groupScreen([CLASH, chosen, reason], verdictAt, 0, null).clash).toMatchObject({ phase: "verdict", winner: "mgr_1", loser: "mgr_2" });
    const html = group([CLASH, chosen, reason], verdictAt);
    expect(html).toContain("verdict-takeover");
    expect(html).not.toContain("clash-takeover");
    // #194: the winner's name is in its own span (the accent), so the sentence is read without tags
    expect(announced(html).replace(/<[^>]+>/g, "")).toContain("Jo&#x27;s moment, over Priya&#x27;s");
    expect(announced(html)).toContain('<span class="winner">Jo&#x27;s</span> moment, over Priya&#x27;s');
    expect(announced(html)).toContain("Jo has been overruled twice.");
    // #194: the pitch stays drawn behind the verdict, dimmed, instead of a black screen
    expect(html).toContain(`<div class="stage-main">${PITCH}</div>`);
  });

  it("hands back to the pitch with the chosen card and a verdict line", () => {
    expect(clashAt([CLASH], handBack - 1)?.phase).toBe("verdict");
    const view = groupScreen([CLASH, chosen], handBack, 0, null);
    expect(view.clash?.phase).toBe("chosen");
    expect(view.card?.cue_id).toBe(chosen.cue_id);
    const html = group([CLASH, chosen], handBack);
    expect(html).not.toContain("takeover");
    expect(html).toContain(`<div class="stage-main">${PITCH}</div>`);
    expect(html).toContain("Captain saved, one off the bonus.");
    expect(html).toContain("Jo&#x27;s moment, over Priya&#x27;s");
  });

  it("starts the chosen card's dwell at the hand-back, not under the takeover", () => {
    const during = groupScreen([CLASH, chosen], verdictAt, 500, null);
    const after = groupScreen([CLASH, chosen], handBack, 2000, during.slot);
    expect(after.slot?.since).toBe(2000);
  });

  it("shows the winner's pitch with the verdict when no card of theirs has arrived", () => {
    const html = group([CLASH], handBack);
    expect(html).toContain("Jo&#x27;s moment, over Priya&#x27;s");
    expect(html).toContain("Jo&#x27;s captain saved, one off the bonus.");
  });

  it("does not present another manager's card of the moment as the chosen one", () => {
    const loser = { ...chosen, cue_id: "c_28", payload: { ...chosen.payload, headline: "Priya: +6 IP", body: "Priya's line.", manager: "mgr_2" } };
    const html = group([CLASH, loser], handBack);
    expect(html).not.toContain("Priya&#x27;s line.");
    expect(html).toContain("Jo&#x27;s captain saved, one off the bonus.");
  });

  it("gives way to a later moment's card", () => {
    const later = { ...chosen, cue_id: "c_40", seq: 40, moment_id: "m_08", show_at: handBack + 30_000 };
    const view = groupScreen([CLASH, chosen, later], later.show_at, 99_000, null);
    expect(view.clash).toBeNull();
    expect(view.card?.cue_id).toBe(later.cue_id);
  });

  it("keeps its verdict over an earlier moment's card still dwelling", () => {
    const earlier = { ...chosen, cue_id: "c_20", seq: 20, moment_id: "m_06", show_at: CLASH.show_at - 60_000 };
    const up = groupScreen([earlier], earlier.show_at, 0, null);
    const view = groupScreen([earlier, CLASH], handBack, 1000, up.slot); // still inside its 6 s dwell
    expect(view.clash?.phase).toBe("chosen");
    expect(view.card).toBeNull();
    expect(group([earlier, CLASH], handBack)).toContain("Jo&#x27;s moment, over Priya&#x27;s");
  });

  it("matches the chosen card to the clash by moment, not by time", () => {
    const sameTimeOtherMoment = { ...chosen, cue_id: "c_27", moment_id: "m_99" };
    expect(groupScreen([CLASH, sameTimeOtherMoment], handBack, 0, null).card).toBeNull();
  });

  it("is gone at expires_at", () => {
    expect(clashAt([CLASH], CLASH.expires_at)).toBeNull();
  });

  it("ignores a clash that breaks the contract: one contender twice, a winner outside it, or times out of order", () => {
    const [a, b] = CLASH.payload.contenders!;
    const broken: Cue[] = [
      { ...CLASH, payload: { ...CLASH.payload, contenders: [a, { ...b, manager: a.manager }] } },
      { ...CLASH, payload: { ...CLASH.payload, winner: "mgr_3" } },
      { ...CLASH, payload: { ...CLASH.payload, verdict_at: CLASH.show_at - 1 } },
      { ...CLASH, payload: { ...CLASH.payload, verdict_at: CLASH.expires_at + 1 } },
    ];
    for (const c of broken) expect(clashAt([c], CLASH.show_at)).toBeNull();
  });

  it("needs verdict_at strictly after show_at and strictly before expires_at", () => {
    const atShow = { ...CLASH, payload: { ...CLASH.payload, verdict_at: CLASH.show_at } };
    const atExpiry = { ...CLASH, payload: { ...CLASH.payload, verdict_at: CLASH.expires_at } };
    expect(clashAt([atShow], CLASH.show_at)).toBeNull();
    expect(clashAt([atExpiry], CLASH.show_at)).toBeNull();
  });
});

describe("a clear winner (#89, regression)", () => {
  it("shows the card directly, with no takeover and no verdict", () => {
    const view = groupScreen([chosen], chosen.show_at, 0, null);
    expect(view.clash).toBeNull();
    expect(view.card?.cue_id).toBe(chosen.cue_id);
    const html = group([chosen], chosen.show_at);
    expect(html).toContain("Captain saved, one off the bonus.");
    expect(html).toContain(`<div class="stage-main">${PITCH}</div>`);
    expect(html).not.toContain("Verdict");
    expect(html).not.toContain("takeover");
  });
});

describe("a group screen that reloads during a clash (#89)", () => {
  const replay = (store: ReturnType<typeof empty>) =>
    [CLASH, chosen].forEach((c) => receive(store, "group", true, { v: 1, type: "cue", data: c } as WsMessage, 0));

  it("keeps the clash once when its history is sent again", () => {
    const store = empty();
    replay(store);
    replay(store); // a reconnect replays the history
    expect(store.cues.get("group")?.filter((c) => c.kind === "clash")).toHaveLength(1);
  });

  it("goes straight to the normal view with the chosen card after the hand-back, without the takeover", () => {
    const store = empty();
    replay(store);
    const clock = new PlaybackClock(SPEED, DELAY);
    clock.observe(handBack + DELAY * SPEED + 3000, 50_000); // a fresh page joins after the hand-back
    const t = clock.at(50_000);
    const view = groupScreen(store.cues.get("group") ?? [], t, 50_000, null);
    expect(view.clash?.phase).toBe("chosen");
    expect(view.card?.cue_id).toBe(chosen.cue_id);
    expect(group(store.cues.get("group") ?? [], t)).not.toContain("takeover");
  });
});

const manager = (id: string, lang: string): Manager => ({
  id, display_name: names.get(id) ?? id, is_bot: false, style: "casual", lang, squad: [], captain: "p_home_9",
  points: 0,
}) as unknown as Manager; // prettier-ignore

const match: MatchState = {
  meta: null, events: [], frames: [], t: CLASH.show_at, players: new Map(), managers: names, rows: [],
  moves: new Map(), score: { home: 0, away: 0, minute: 43, phase: "live", xg: { home: 0, away: 0 }, lastGoal: null },
} as unknown as MatchState; // prettier-ignore

const phone = (id: string, lang: string, cues: Cue[]) =>
  renderToStaticMarkup(<PhoneApp manager={manager(id, lang)} cues={cues} style="casual" match={match} judge={id} />);

describe("the lost-screen note on a phone (#89)", () => {
  it("lands in the loser's feed", () => {
    expect(phoneFeed([LOST], LOST.show_at, "mgr_2")).toEqual([LOST]);
    const html = phone("mgr_2", "en", [LOST]);
    expect(html).toContain("Shown on your phone");
    expect(html).toContain("The screen went to Jo: his captain&#x27;s save.");
  });

  it("never shows on the winner's own phone", () => {
    const misrouted = { ...LOST, screen: "phone:mgr_1" };
    expect(phoneFeed([misrouted], LOST.show_at, "mgr_1")).toEqual([]);
    expect(phone("mgr_1", "en", [misrouted])).not.toContain("Shown on your phone");
  });

  it("marks a points chip with its friend's language, so a screen reader says it in Spanish", () => {
    const card = { ...chosen, screen: "phone:mgr_4", payload: { headline: "+0,5 IP", body: "Media parada." } };
    expect(renderToStaticMarkup(<FeedCard cue={card} style="casual" ownerLang="es" />)).toContain('lang="es">+0,5 IP');
  });

  it("is a note, not a points card", () => {
    const html = renderToStaticMarkup(<FeedCard cue={LOST} style="casual" names={names} />);
    expect(html).toContain("feed-card lost_screen");
    expect(html).not.toContain("points-chip");
  });

  it("writes the note in Spanish for a Spanish friend", () => {
    const bare = { ...LOST, payload: { headline: "Shown on your phone", winner: "mgr_1" } };
    expect(lostScreenNote(bare, "es", names)).toEqual({
      lang: "es", headline: "Solo en tu teléfono", body: "La pantalla fue para Jo.", translation: undefined,
    });
    const html = phone("mgr_2", "es", [bare]);
    expect(html).toContain('lang="es"');
    expect(html).toContain("La pantalla fue para Jo.");
  });

  it("keeps the backend's English detail under the Spanish note", () => {
    const html = phone("mgr_2", "es", [LOST]); // the backend writes English, with no lang
    expect(html).toContain("La pantalla fue para Jo.");
    expect(html).toContain('<p class="translation" lang="en">The screen went to Jo: his captain&#x27;s save.</p>');
  });

  it("says you, not You, when the judge took the screen", () => {
    const you = new Map([["mgr_1", "You"]]);
    const bare = { ...LOST, payload: { headline: "Shown on your phone", winner: "mgr_1" } };
    expect(lostScreenNote(bare, "es", you).body).toBe("La pantalla fue para ti.");
    expect(lostScreenNote(bare, "en", you).body).toBe("The screen went to you.");
    expect([verdictText("You", "Leo"), verdictText("Priya", "You")]).toEqual(["Your moment, over Leo's", "Priya's moment, over yours"]);
  });

  it("keeps the backend's Spanish text as written, with the English under it", () => {
    const written = { ...LOST, payload: { headline: "Solo en tu teléfono", body: "La pantalla fue para Jo: la parada de su capitán.",
                                          body_en: "The screen went to Jo: his captain's save.", lang: "es", winner: "mgr_1" } }; // prettier-ignore
    const html = phone("mgr_2", "es", [written]);
    expect(html).toContain("La pantalla fue para Jo: la parada de su capitán.");
    expect(html).toContain("The screen went to Jo: his captain&#x27;s save.");
  });
});
