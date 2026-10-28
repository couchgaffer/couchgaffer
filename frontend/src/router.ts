// A few routes, so a tiny router: the path picks the page; history changes re-render the app.
import { useEffect, useState } from "react";

export type Route =
  | { page: "landing" }
  | { page: "watch-now" }
  | { page: "judges" }
  | { page: "watch"; session: string }
  | { page: "phone"; session: string; manager: string }
  | { page: "league-home" }
  | { page: "league"; league: string }
  | { page: "missing" };

const ID = "([A-Za-z0-9_-]{8,64})";
// A league's id as the store makes it (lg_ and 12 hex digits; lg_demo); the server serves the app for these (#179)
export const LEAGUE_PATH = /^\/league\/(lg_(?:demo|[0-9a-f]{12}))$/;

/** The Watch now chooser lives at `/?watch` (#196): the server already serves `/` with any query, so it needs no new
 * server route; the landing page is `/` itself. */
export const WATCH_NOW = "/?watch";

export function parse(path: string, search = ""): Route {
  if (path === "/") return new URLSearchParams(search).has("watch") ? { page: "watch-now" } : { page: "landing" };
  if (path === "/judges") return { page: "judges" }; // the server serves the app for this exact path only (#92)
  if (path === "/league" || path === "/league/join") return { page: "league-home" }; // the join link carries ?code=
  const league = LEAGUE_PATH.exec(path);
  if (league?.[1]) return { page: "league", league: league[1] };
  const watch = new RegExp(`^/watch/${ID}/?$`).exec(path);
  if (watch?.[1]) return { page: "watch", session: watch[1] };
  const phone = new RegExp(`^/phone/${ID}/(mgr_[1-5])/?$`).exec(path);
  if (phone?.[1] && phone[2]) return { page: "phone", session: phone[1], manager: phone[2] };
  return { page: "missing" };
}

export function navigate(to: string): void {
  history.pushState(null, "", to);
  dispatchEvent(new PopStateEvent("popstate"));
}

export function useRoute(): Route {
  const [where, setWhere] = useState({ path: location.pathname, search: location.search ?? "" });
  useEffect(() => {
    const update = () => setWhere({ path: location.pathname, search: location.search });
    addEventListener("popstate", update);
    return () => removeEventListener("popstate", update);
  }, []);
  return parse(where.path, where.search);
}
