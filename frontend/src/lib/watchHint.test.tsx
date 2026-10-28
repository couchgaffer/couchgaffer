// #92: the page says who decides what the group screen shows. A clear winner goes to the scorer in code; only a close
// call reaches the host agent (app/workflow.py, Decided and HostStep). Since #194 the group screen carries no hint
// line: each lower third names who decided it, and Behind the scenes says the rule in words.
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, expect, it, vi } from "vitest";

import { Trace } from "../components/Trace";
import { Watch } from "../pages/Watch";
import { empty, useMatch } from "./useMatch";

vi.mock("./useMatch", async (original) => ({ ...(await original<typeof import("./useMatch")>()), useMatch: vi.fn() }));

afterEach(() => vi.unstubAllGlobals());

it("says in Behind the scenes that code scores the bids and the host settles only close calls", () => {
  const html = renderToStaticMarkup(<Trace decisions={[]} names={new Map()} t={0} onClose={() => undefined} />);
  expect(html).toContain("Code scores the bids with a fairness ledger; the host agent\n        settles only the close calls.".replace(/\s+/g, " "));
});

it("keeps the group screen free of a hint line", () => {
  vi.stubGlobal("location", { hash: "", origin: "http://test" });
  vi.mocked(useMatch).mockReturnValue({ store: empty(), t: 0, now: 0, connection: "open" } as ReturnType<typeof useMatch>);
  const html = renderToStaticMarkup(<Watch session="sess_12345678" />);
  expect(html).not.toContain('class="hint"');
  expect(html).not.toContain("Code picks a clear winner");
});
