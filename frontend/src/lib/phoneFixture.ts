// Which fixture a phone shows (#208). By default a phone follows the big screen's fixture; a tap on another fixture
// in its strip pins that one, and a tap on the big screen's fixture follows the big screen again. The group screen
// has no say: its fixture is the decision system's.
import type { MatchState } from "../components/PhoneApp";

/** The fixture this phone shows: the pinned one while the gameweek plays it, else the big screen's. */
export function shownFixture(match: MatchState, pinned: string | null): string | undefined {
  if (!match.gameweek || pinned === null) return match.focus;
  return match.fixtures?.some((f) => f.id === pinned) ? pinned : match.focus;
}

/** The pin once the big screen has moved: on the pinned fixture itself, the phone follows the big screen again. */
export function pinFor(pinned: string | null, focus: string | undefined): string | null {
  return pinned === focus ? null : pinned;
}

/** The pin after a tap on a fixture: the big screen's fixture means follow the big screen again. */
export function afterTap(tapped: string, focus: string | undefined): string | null {
  return tapped === focus ? null : tapped;
}

/** The match as this phone shows it: the shown fixture's meta, events, frames and score, everything else as is. */
export function phoneView(match: MatchState, pinned: string | null): MatchState {
  const id = shownFixture(match, pinned);
  if (id === match.focus) return match;
  const fixture = match.fixtures?.find((f) => f.id === id);
  if (!fixture) return match;
  return { ...match, meta: fixture.meta, events: fixture.events, frames: fixture.frames ?? [], score: fixture.score };
}
