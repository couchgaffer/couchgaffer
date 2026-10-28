// #208: a phone switches its own Live view between the gameweek's fixtures. One test per plan row; the taps
// themselves are checked in the browser run on #208.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { PhoneApp, type MatchState } from "../components/PhoneApp";
import { ScoreStrip } from "../components/ScoreStrip";
import type { Manager } from "./api";
import type { Cue, Gameweek, League, MatchFrame, WsMessage } from "./messages";
import { afterTap, phoneView, pinFor, shownFixture } from "./phoneFixture";
import { matchState } from "./state";
import { empty, receive } from "./useMatch";

const examples = import.meta.glob("../../../contracts/examples/*.json", { eager: true, import: "default" });
const example = <T,>(name: string): T => structuredClone(examples[`../../../contracts/examples/${name}`] as T);
const GW = example<{ data: Gameweek }>("ws-message.gameweek.json").data;
const LEAGUE = example<League>("league.json");
const CARD = example<Cue>("cue.gameweek.json"); // a group card: it puts its fixture on the big screen
const AT = CARD.show_at + 1_000;
const msg = (type: WsMessage["type"], data: unknown) => ({ v: 1, type, data }) as WsMessage;
const IDS = GW.fixtures.map((f) => f.fixture_id ?? "");

function gameweekAt(card: Cue = CARD): MatchState {
  const s = empty();
  const frame = (fixture: string): MatchFrame => {
    const meta = GW.fixtures.find((f) => f.fixture_id === fixture)!;
    return { t: AT - 1_000, fixture_id: fixture, possession: "home", ball: { x: 0.5, y: 0.5, z_m: 0 },
             players: [{ id: meta.home.players[0].id, x: 0.2, y: 0.5 }, { id: meta.away.players[0].id, x: 0.8, y: 0.5 }] }; // prettier-ignore
  };
  for (const m of [msg("gameweek", GW), msg("league", LEAGUE), ...IDS.map((f) => msg("frame", frame(f))), msg("cue", card)])
    receive(s, "group", true, m, 0);
  return matchState(s, AT);
}

const pitchOf = (m: MatchState) => `Pitch: ${m.meta?.home.name} against ${m.meta?.away.name}`;
const manager = { id: "mgr_1", display_name: "You", is_bot: false, style: "casual", lang: "en", squad: [], captain: "",
                  points: 0 } as unknown as Manager; // prettier-ignore
const phone = (match: MatchState) =>
  renderToStaticMarkup(<PhoneApp manager={manager} cues={[]} style="casual" match={match} judge="mgr_1" initialTab="live" />);

describe("a phone's own fixture (#208)", () => {
  const match = gameweekAt();
  const focus = match.focus!;
  const other = IDS.find((id) => id !== focus)!;
  const third = IDS.find((id) => id !== focus && id !== other)!;

  it("1: with no tap, Live shows the big screen's fixture, as before", () => {
    expect(shownFixture(match, null)).toBe(focus);
    expect(phoneView(match, null)).toBe(match);
    const html = phone(match);
    expect(html).toContain(pitchOf(match));
    expect(html.match(/aria-pressed="true"[^>]*>/g)).toHaveLength(1);
    expect(html).toMatch(new RegExp(`aria-current="true"><button type="button" class="pick" aria-pressed="true"`));
  });

  it("2: a tap on another fixture switches the pitch, score and key moments to it, and marks it pressed", () => {
    const pinned = afterTap(other, focus);
    expect(pinned).toBe(other);
    const view = phoneView(match, pinned);
    const fixture = match.fixtures!.find((f) => f.id === other)!;
    expect(view.meta).toBe(fixture.meta);
    expect(view.events).toBe(fixture.events);
    expect(view.frames).toBe(fixture.frames);
    expect(view.score).toBe(fixture.score);
    expect(view.fixtures).toBe(match.fixtures); // the strip still lists every fixture
    const strip = renderToStaticMarkup(
      <ScoreStrip fixtures={match.fixtures!} focus={focus} shown={shownFixture(match, pinned)} onPick={() => undefined} />,
    );
    const pressed = strip.match(/<li[^>]*>.*?<\/li>/g)!.map((li) => li.includes('aria-pressed="true"'));
    expect(pressed).toEqual(IDS.map((id) => id === other));
  });

  it("3: the phone stays on the tapped fixture when the big screen moves to a third", () => {
    const card = { ...CARD, cue_id: "cue_third", fixture_id: third };
    const moved = gameweekAt(card);
    expect(moved.focus).toBe(third);
    expect(shownFixture(moved, other)).toBe(other);
    expect(phoneView(moved, other).meta).toBe(moved.fixtures!.find((f) => f.id === other)!.meta);
  });

  it("4: a tap on the big screen's fixture follows the big screen again", () => {
    expect(afterTap(focus, focus)).toBeNull();
    expect(shownFixture(match, afterTap(focus, focus))).toBe(focus);
  });

  it("5: a single match has no strip and shows its one fixture", () => {
    const single = { ...match, gameweek: false };
    expect(shownFixture(single, other)).toBe(single.focus);
    expect(phoneView(single, other)).toBe(single);
    const one = renderToStaticMarkup(<ScoreStrip fixtures={match.fixtures!.slice(0, 1)} focus={focus} onPick={() => undefined} />);
    expect(one).toBe("");
  });

  it("6: the group screen's strip is plain scores, not buttons", () => {
    const strip = renderToStaticMarkup(<ScoreStrip fixtures={match.fixtures!} focus={focus} />);
    expect(strip).not.toMatch(/<button|aria-pressed|tabindex/);
  });

  it("7: every fixture on a phone is a native button, so Tab reaches it and Enter or Space presses it", () => {
    const buttons = phone(match).match(/<button type="button" class="pick"[^>]*>/g) ?? [];
    expect(buttons).toHaveLength(IDS.length);
    expect(buttons.join("")).not.toContain("tabindex");
  });

  it("unpins when the big screen moves onto the pinned fixture, and keeps the pin when it moves elsewhere", () => {
    expect(pinFor(other, other)).toBeNull();
    expect(pinFor(other, third)).toBe(other);
    expect(pinFor(null, focus)).toBeNull();
    // Then a later move of the big screen is followed, not held
    expect(shownFixture(gameweekAt({ ...CARD, cue_id: "cue_third", fixture_id: third }), pinFor(other, other))).toBe(third);
  });

  it("forgets a pin for a fixture the gameweek does not play", () => {
    expect(shownFixture(match, "fx_9")).toBe(focus);
  });
});
