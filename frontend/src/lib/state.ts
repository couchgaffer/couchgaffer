// What every screen derives from the store at a match time: the score, the names, the table and its moves. In a
// gameweek the pitch, header and score are the focused fixture's, and the strip has every fixture's score (#94).
import { tableFrom } from "../components/Cards";
import type { FixtureScore, MatchState } from "../components/PhoneApp";
import { latestTable, rankMoves } from "./cues";
import { scoreboard } from "./match";
import type { Cue } from "./messages";
import { DEFAULT_FIXTURE, type MatchStore } from "./useMatch";

const CARD_KINDS = new Set<Cue["kind"]>(["stat", "explanation", "clash"]);

/** The fixtures in the order the screens list them: the gameweek message's order, else as they arrived. */
export function fixtureOrder(store: MatchStore): string[] {
  const ids = store.gameweek?.fixtures.map((f) => f.fixture_id ?? DEFAULT_FIXTURE) ?? [...store.fixtures.keys()];
  return ids.filter((id) => store.fixtures.has(id));
}

/**
 * The fixture the group screen shows at t (#94): the fixture of the card on screen; with none, of the latest due
 * group card; before the first, the first fixture. Nothing is kept between renders but the card on screen, so the
 * pitch never disagrees with the card, and a reload lands on the fixture of the card a fresh page shows (the newest
 * due one: a card's minimum dwell is the page's own real time, #89). A card naming a fixture this store does not
 * know, or in a gameweek naming none, is passed over.
 */
export function focusedFixture(store: MatchStore, t: number, onScreen?: Cue | null): string {
  // A single match's cues carry no fixture_id and belong to its one fixture. In a gameweek a cue without one is about
  // the whole gameweek, as a phone's summary is: it moves no focus here and gets no fixture tag (PhoneApp, fixtureTag).
  const fixture = (c: Cue) => c.fixture_id ?? (store.gameweek ? undefined : DEFAULT_FIXTURE);
  const known = (c: Cue) => store.fixtures.has(fixture(c) ?? "");
  if (onScreen && known(onScreen)) return fixture(onScreen) ?? DEFAULT_FIXTURE;
  const due = (store.cues.get("group") ?? []).filter((c) => CARD_KINDS.has(c.kind) && c.show_at <= t && known(c));
  const latest = due.reduce<Cue | null>(
    (best, c) => (!best || c.show_at > best.show_at || (c.show_at === best.show_at && c.seq > best.seq) ? c : best),
    null,
  );
  return (latest && fixture(latest)) ?? fixtureOrder(store)[0] ?? DEFAULT_FIXTURE;
}

/** The match state at t. `onScreen` is the group card or close call on screen, when the caller knows it. */
export function matchState(store: MatchStore, t: number, onScreen?: Cue | null): MatchState {
  const managers = store.league?.managers ?? [];
  const groupCues = store.cues.get("group") ?? [];
  const focus = focusedFixture(store, t, onScreen);
  const fixture = store.fixtures.get(focus);
  const events = fixture?.events ?? [];
  const metas = [...store.fixtures.values()].flatMap((f) => (f.meta ? [f.meta] : []));
  const fixtures: FixtureScore[] = fixtureOrder(store).map((id) => {
    const f = store.fixtures.get(id);
    return { id, meta: f?.meta ?? null, events: f?.events ?? [], frames: f?.frames ?? [], score: scoreboard(f?.events ?? [], t) };
  });
  return {
    meta: fixture?.meta ?? null,
    events,
    frames: fixture?.frames ?? [],
    t,
    score: scoreboard(events, t),
    players: new Map(metas.flatMap((m) => [m.home, m.away]).flatMap((side) => side.players.map((p) => [p.id, p.name]))),
    managers: new Map(managers.map((m) => [m.id, m.display_name])),
    rows: latestTable(groupCues, t) ?? tableFrom(managers),
    moves: rankMoves(groupCues, t),
    gameweek: store.gameweek !== null,
    focus,
    fixtures,
    allEvents: store.events,
  };
}
