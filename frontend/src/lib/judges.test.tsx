// #92: the "For judges" page. Its sections, links, headings and diagram; its wording; the evidence it quotes,
// checked against docs/evidence.md; and that it is static, so reading it starts nothing on the server.
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import evidence from "../../../docs/evidence.md?raw";
import { App } from "../App";
import {
  ARBITRATION_ROWS, CHECKER, EVIDENCE_RUN, Judges, LIMITATIONS, MODEL_ROWS, SECTIONS, WHERE_OURS_LOSES,
} from "../pages/Judges";
import { Landing, Lobby } from "../pages/Lobby";
import judgesSource from "../pages/Judges.tsx?raw";
import { parse } from "../router";

const html = renderToStaticMarkup(<Judges />);

const decode = (s: string) =>
  s.replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
const text = decode(html.replace(/<[^>]+>/g, " "));

/** The rows of the markdown table under `heading` whose header row is `header`, as cells. */
function tableRows(heading: string, header: string): string[][] {
  const section = evidence.split(`\n## ${heading}\n`)[1]?.split("\n## ")[0] ?? "";
  const lines = section.split("\n");
  const start = lines.indexOf(header);
  expect(start, `${header} under ${heading}`).toBeGreaterThan(-1);
  const rows: string[][] = [];
  for (const line of lines.slice(start + 2)) {
    if (!line.startsWith("|")) break;
    rows.push(line.slice(1, -1).split("|").map((c) => c.trim()));
  }
  return rows;
}

const bullets = (heading: string) =>
  (evidence.split(`\n## ${heading}\n`)[1]?.split("\n## ")[0] ?? "")
    .split("\n").filter((l) => l.startsWith("- ")).map((l) => l.slice(2));

describe("the For judges page (#92)", () => {
  it("is linked from the landing page and Watch now (#196: from the top bar)", () => {
    for (const page of [<Landing />, <Lobby />]) expect(renderToStaticMarkup(page)).toContain('<a href="/judges">For judges</a>');
  });

  afterEach(() => vi.unstubAllGlobals());

  it("is what the app shows at /judges", () => {
    vi.stubGlobal("location", { pathname: "/judges" });
    expect(renderToStaticMarkup(<App />)).toBe(html);
  });

  it("has its own route, and only the exact path", () => {
    expect(parse("/judges")).toEqual({ page: "judges" });
    expect(parse("/judges/x")).toEqual({ page: "missing" });
    expect(parse("/judgesx")).toEqual({ page: "missing" });
  });

  it("renders every section, and each contents link lands on one", () => {
    for (const s of SECTIONS) {
      expect(html).toContain(`<section id="${s.id}"`);
      expect(html).toContain(`<a href="#${s.id}">${decode(s.title).replace(/'/g, "&#x27;")}</a>`);
      expect(html).toMatch(new RegExp(`<h2 id="${s.id}-h">[^<]*</h2>`));
    }
    const anchors = [...html.matchAll(/href="#([^"]+)"/g)].map((m) => m[1]);
    for (const id of anchors) expect(html).toContain(`id="${id}"`);
    const hrefs = [...html.matchAll(/href="([^"]+)"/g)].map((m) => m[1]);
    // #196: the brand, the top bar's two links (the same on every entry page), and the start button to Watch now
    expect(hrefs.filter((h) => !h?.startsWith("#"))).toEqual(["/", "/league", "/judges", "/?watch"]);
    expect(html).toContain('<a href="/judges" aria-current="page">For judges</a>');
  });

  it("has one h1 and never skips a heading level", () => {
    const levels = [...html.matchAll(/<h([1-6])[\s>]/g)].map((m) => Number(m[1]));
    expect(levels.filter((l) => l === 1)).toHaveLength(1);
    expect(levels[0]).toBe(1);
    levels.slice(1).forEach((level, i) => expect(level - (levels[i] ?? 0)).toBeLessThanOrEqual(1));
  });

  it("gives every link a name that says where it goes", () => {
    const names = [...html.matchAll(/<a [^>]*>(.*?)<\/a>/g)].map((m) => decode((m[1] ?? "").replace(/<[^>]+>/g, "")).trim());
    expect(names.length).toBeGreaterThan(SECTIONS.length);
    // #196: named for where they go: the brand to the landing page, the start button to Watch now
    const away = [...html.matchAll(/<a [^>]*href="\/(\?watch)?"[^>]*>(.*?)<\/a>/g)].map((m) => decode((m[2] ?? "").replace(/<[^>]+>/g, "")));
    expect(away).toEqual(["Couch Gaffer", "Open Watch now to start the demo"]);
    for (const name of names) {
      expect(name.length).toBeGreaterThan(3);
      expect(name.toLowerCase()).not.toMatch(/click here|^here$|^more$/);
    }
  });

  it("draws the architecture as a labelled image with the same steps in words", () => {
    expect(html).toMatch(/<svg [^>]*role="img" aria-labelledby="arch-title" aria-describedby="arch-text"/);
    expect(html).toContain('<title id="arch-title">How one key moment flows');
    // Written out here, not read from the page's own list, so the drawing and the words are each checked.
    const expected = ["Simulator", "Points engine (code)", "Manager agents", "Checkers (code)",
      "Scorer and fairness ledger (code)", "Host agent", "Cues"];
    const boxes = [...html.matchAll(/class="arch-name">([^<]*)<\/text>/g)].map((m) => m[1]);
    const words = [...(html.split('<ol id="arch-text"')[1]?.split("</ol>")[0] ?? "").matchAll(/<li>(.*?)<\/li>/g)]
      .map((m) => (m[1] ?? "").split(": ")[0]);
    expect(boxes).toEqual(expected);
    expect(words).toEqual(expected);
  });

  it("states the folded tables' headline numbers as they are in the tables", () => {
    const folds = html.split("<details>").slice(1).map((d) => d.split("</details>")[0] ?? "");
    expect(folds).toHaveLength(2);
    for (const fold of folds) {
      const summary = decode(fold.split("</summary>")[0] ?? "");
      const cells = decode(fold.split("</summary>")[1] ?? "").replace(/<[^>]+>/g, " ");
      const numbers = summary.match(/\d+(\.\d+)?%?/g) ?? [];
      expect(numbers.length).toBeGreaterThan(3);
      for (const n of numbers) expect(cells).toMatch(new RegExp(`(^|[^\\d.])${n.replace(".", "\\.")}(?![\\d])`));
      // "X against Y" must be ours then the baseline, on one row of the table
      const rows = [...(fold.split("</summary>")[1] ?? "").matchAll(/<tr>(.*?)<\/tr>/gs)].map((r) =>
        [...(r[1] ?? "").matchAll(/<t[dh][^>]*>(.*?)<\/t[dh]>/gs)].map((c) => decode(c[1] ?? "").replace(/<[^>]+>/g, "")),
      );
      const has = (cell: string | undefined, n: string) =>
        new RegExp(`(^|[^\\d.])${n.replace(".", "\\.")}(?![\\d])`).test(cell ?? "");
      for (const [, ours, theirs] of summary.matchAll(/(\d+(?:\.\d+)?%?)[^\d]{0,40}? against (\d+(?:\.\d+)?%?)/g)) {
        expect(rows.some((r) => has(r[1], ours ?? "") && has(r[2], theirs ?? "")), `${ours} against ${theirs}`).toBe(true);
      }
    }
  });

  it("quotes the evidence report's tables, losses and limitations word for word and in full", () => {
    expect(evidence).toContain(EVIDENCE_RUN);
    expect(text).toContain(EVIDENCE_RUN);
    expect(text).toContain(CHECKER);
    expect(MODEL_ROWS).toEqual(tableRows("Models", "| Measure | ours | one agent |"));
    expect(ARBITRATION_ROWS).toEqual(tableRows("Arbitration", "| Measure | ours | greedy |"));
    expect(WHERE_OURS_LOSES).toEqual(bullets("Where ours loses"));
    expect(LIMITATIONS).toEqual(bullets("Limitations"));
    expect(evidence).toContain(CHECKER);
    for (const line of [...WHERE_OURS_LOSES, ...LIMITATIONS]) expect(text).toContain(line);
    for (const row of [...MODEL_ROWS, ...ARBITRATION_ROWS]) for (const cell of row) expect(text).toContain(cell);
  });

  it("describes the gameweek as built, with the single match one choice away", () => {
    // #155: the placeholder from #92 is gone once the gameweek ships
    const gameweek = decode(html.split('<section id="gameweek"')[1]?.split("</section>")[0] ?? "");
    expect(gameweek).not.toMatch(/coming|not built yet|being built/i);
    expect(html).not.toContain('class="coming"');
    expect(gameweek).toContain("three matches among six invented clubs");
    expect(gameweek).toContain('"A single match"');
    const steps = decode(html.split('<section id="try"')[1]?.split("</section>")[0] ?? "");
    expect(steps).toContain('"A gameweek: three matches at once"'); // the lobby's default, as it is labelled there
    expect(steps).toContain('"A single match"');
  });

  it("writes plainly: no dashes, no other game's name, no real club or player", () => {
    expect(text).not.toMatch(/[–—]/);
    expect(text).not.toMatch(/\bFPL\b|Fantasy Premier League|Premier League/i);
    const real = ["Arsenal", "Chelsea", "Liverpool", "Manchester", "Tottenham", "Everton", "Newcastle", "Villa",
      "Haaland", "Salah", "Saka", "Palmer", "Kane", "Rashford"];
    for (const name of real) expect(text).not.toContain(name);
    expect(text).not.toMatch(/\b(robust|seamless|leverage|comprehensive|powerful|cutting-edge)\b/i);
  });

  it("is static: it imports nothing that fetches, starts a session or opens a socket", () => {
    // Every static import, over any number of lines, and any side-effect or dynamic import or require.
    const modules = [...judgesSource.matchAll(/\bfrom\s*["']([^"']+)["']/g)].map((m) => m[1]);
    expect(modules).toEqual(["react"]);
    expect(judgesSource).not.toMatch(/^\s*import\s*["']|\bimport\s*\(|\brequire\s*\(/m);
    expect(judgesSource).not.toMatch(/\bfetch\s*\(|WebSocket|EventSource|sendBeacon|XMLHttpRequest/);
  });
});
