import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import phoneSource from "../components/PhoneApp.tsx?raw";
import indexHtml from "../../index.html?raw";
import { Landing, Lobby } from "../pages/Lobby";
import watchSource from "../pages/Watch.tsx?raw";

// The product is Couch Gaffer on every screen a judge sees (#117); "Mini-league" names only the league table.
describe("the brand", () => {
  it("is the page title", () => {
    expect(indexHtml).toContain("<title>Couch Gaffer</title>");
  });

  // #196: the name moved from the lobby's h1 to the entry pages' top bar; the landing's h1 is its headline
  it("heads the landing page and Watch now", () => {
    for (const page of [<Landing />, <Lobby />]) {
      expect(renderToStaticMarkup(page)).toContain('<a href="/" class="e-brand"><i aria-hidden="true"></i>Couch Gaffer</a>');
    }
  });

  it("leads the group screen and the phone", () => {
    for (const source of [watchSource, phoneSource]) {
      expect(source).toMatch(/className="brand"[^>]*>Couch Gaffer</);
    }
  });
});
