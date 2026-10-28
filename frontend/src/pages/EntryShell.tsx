// The entry pages' frame (#196): the top bar with the brand and its links, and a link that moves between the app's
// own pages without a reload. For judges draws the same bar itself, as it imports nothing but React.
import type { MouseEvent, ReactNode } from "react";

import "../entry.css";
import { navigate } from "../router";

/** A link to one of the app's pages, followed in place; a modified click (new tab) is left to the browser. */
export function Go({ to, className, current, children }: { to: string; className?: string; current?: boolean; children: ReactNode }) {
  const follow = (e: MouseEvent<HTMLAnchorElement>) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
    e.preventDefault();
    navigate(to);
  };
  return <a href={to} className={className} aria-current={current ? "page" : undefined} onClick={follow}>{children}</a>;
}

/** The same two links on every entry page, in the same place; the page they lead to is marked. For judges draws the
 * same markup itself. */
export function TopBar({ current, leagues = "Your leagues", judges = "For judges" }: {
  current?: "/league" | "/judges";
  leagues?: string;
  judges?: string;
}) {
  const links = [{ to: "/league", label: leagues }, { to: "/judges", label: judges }] as const;
  return (
    <header className="e-top">
      <Go to="/" className="e-brand"><i aria-hidden="true" />Couch Gaffer</Go>
      <nav aria-label="Pages">
        {links.map((l) => <Go key={l.to} to={l.to} current={current === l.to}>{l.label}</Go>)}
      </nav>
    </header>
  );
}
