// #127: the phone page's own notices, connecting and match ended, in the friend's language once the phone knows
// it, and in the browser's before then. useMatch is replaced, so the page renders each connection state.
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PhoneOnly } from "../pages/PhoneOnly";
import type { Connection } from "./socket";
import { useMatch, type MatchStore } from "./useMatch";

vi.mock("./useMatch", () => ({ useMatch: vi.fn() }));

const friend = (lang: string) => ({ id: "mgr_4", display_name: "Lucía", is_bot: false, style: "casual", lang });

function page(connection: Connection, me: ReturnType<typeof friend> | null, browser = "en-GB"): string {
  vi.stubGlobal("navigator", { language: browser });
  const store = { league: me ? { managers: [me] } : null, cues: new Map() } as unknown as MatchStore;
  vi.mocked(useMatch).mockReturnValue({ store, t: 0, now: 0, connection } as unknown as ReturnType<typeof useMatch>);
  return renderToStaticMarkup(<PhoneOnly session="sess_12345678" manager="mgr_4" />);
}

beforeEach(() => vi.stubGlobal("location", { hash: "#token=tok&manager=mgr_4" }));
afterEach(() => vi.unstubAllGlobals());

describe("the phone page's notices (#127)", () => {
  it("says the match has ended in Spanish on a Spanish friend's phone, whatever the browser's language", () => {
    const html = page("refused", friend("es"));
    expect(html).toContain('<main class="notice" lang="es"><h1>Esta sesión ha terminado</h1>');
    expect(html).toContain("mientras se juega su partido.");
    expect(html).toContain('<a class="btn acc" href="/?watch">Empezar una sesión nueva</a>'); // the start screen (#196)
  });

  it("says it in English on an English friend's phone, even in a Spanish browser", () => {
    const html = page("refused", friend("en"), "es-ES");
    expect(html).toContain('<main class="notice"><h1>This watch party has ended</h1>');
  });

  it("before the league arrives, connects in the browser's language", () => {
    expect(page("connecting", null, "es-ES")).toBe('<main class="notice" lang="es"><p>Conectando…</p></main>');
    expect(page("connecting", null, "en-GB")).toBe('<main class="notice"><p>Connecting…</p></main>');
  });

  it("before the league arrives, says the match ended in the browser's language", () => {
    expect(page("refused", null, "es")).toContain("<h1>Esta sesión ha terminado</h1>");
    expect(page("refused", null, "en-US")).toContain("<h1>This watch party has ended</h1>");
  });
});
