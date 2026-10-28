// The design pass (#184): a lower third carries one label for who wrote it and one tag for who decided it, and the
// favicon is linked from the page's own origin. The built stylesheet's font urls and the CSP are checked in the live
// smoke on #184: Vitest serves a stylesheet imported with ?raw as an empty string.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { GroupStage, LeagueTable, LowerThird } from "../components/Cards";
import { Lobby } from "../pages/Lobby";
import indexHtml from "../../index.html?raw";
import type { TableRow } from "./cues";
import type { Cue, League } from "./messages";

const names = new Map([["mgr_1", "Jo"], ["mgr_2", "Priya"]]);
const card = (source: Cue["source"]): Cue => ({
  cue_id: "c_1", seq: 1, screen: "group", moment_id: "m_01", show_at: 0, min_dwell_ms: 6000, expires_at: 9000,
  kind: "explanation", source, payload: { headline: "Jo: +6 IP", body: "Captain saved.", manager: "mgr_1" },
}); // prettier-ignore

/** Every label shown on a card, in order: the text of each .mark, wherever it sits, the host's line included. */
const labels = (html: string) => [...html.matchAll(/<span class="mark [^"]*"[^>]*>([^<]*)<\/span>/g)].map((m) => m[1]?.replaceAll("&#x27;", "'"));

describe("who wrote a card and who decided it (#184)", () => {
  it("marks a lower third the host decided as the host's call, with the host's reason as a plain line", () => {
    const reason = { ...card("host"), kind: "host_reason", payload: { headline: "Host", body: "Jo is due." } } as Cue;
    const html = renderToStaticMarkup(<LowerThird card={card("host")} reason={reason} names={names} judge={null} />);
    expect(labels(html)).toEqual(["AI", "Host's call"]);
    expect(html).toContain('<span class="mark decided host">');
    expect(html).toContain("Jo is due.");
  });

  it("still says the host decided when the host's reason is missing", () => {
    const html = renderToStaticMarkup(<LowerThird card={card("host")} reason={undefined} names={names} judge={null} />);
    expect(labels(html)).toEqual(["AI", "Host's call"]);
  });

  it("marks a clear call as the scorer's, in code", () => {
    const html = renderToStaticMarkup(<LowerThird card={card("scorer")} reason={undefined} names={names} judge={null} />);
    expect(labels(html)).toEqual(["Scorer's call"]);
  });

  it("marks a fallback lower third as the fallback's", () => {
    const html = renderToStaticMarkup(<LowerThird card={card("fallback")} reason={undefined} names={names} judge={null} />);
    expect(labels(html)).toEqual(["Plain stat", "Fallback"]);
  });

  it("marks code's plain stat card as put up automatically, since no scorer or host has picked yet", () => {
    const stat = { ...card("code"), kind: "stat", payload: { headline: "Goal: Reds 1-0" } } as Cue;
    const html = renderToStaticMarkup(<LowerThird card={stat} reason={undefined} names={names} judge={null} />);
    expect(labels(html)).toEqual(["Automatic"]);
    expect(html).toContain('<span class="mark decided code">');
  });

  it("a knocked-out friend's card the host chose says the host decided, and the plain stat label says who wrote it", () => {
    const reason = { ...card("host"), kind: "host_reason", payload: { headline: "Host", body: "Jo is due." } } as Cue;
    const knocked = card("fallback");
    knocked.payload = { ...knocked.payload, body_source: "code" } as Cue["payload"];
    const html = renderToStaticMarkup(<LowerThird card={knocked} reason={reason} names={names} judge={null} />);
    expect(labels(html)).toEqual(["Plain stat", "Host's call"]);
  });

  it("after a close call the fallback settled, the chosen card says the fallback decided, not the host", () => {
    const verdict = { phase: "chosen" as const, cue: card("fallback"), winner: "mgr_1", loser: "mgr_2" };
    const html = renderToStaticMarkup(
      <LowerThird card={card("manager")} reason={undefined} names={names} judge={null} verdict={verdict} />,
    );
    expect(labels(html)).toEqual(["AI", "Fallback"]);
  });

  it("after a close call, the chosen card carries the verdict band as its title and says the host decided", () => {
    const verdict = { phase: "chosen" as const, cue: card("host"), winner: "mgr_1", loser: "mgr_2" };
    const html = renderToStaticMarkup(
      <LowerThird card={card("manager")} reason={undefined} names={names} judge={null} verdict={verdict} />,
    );
    expect(html).toContain('<span class="verdict-band">Verdict</span>');
    expect(labels(html)).toEqual(["AI", "Host's call"]);
  });

  it("the verdict card, shown when the winner has no card up, says the host decided even without a reason", () => {
    const clash = card("host");
    clash.kind = "clash";
    clash.payload = {
      headline: "Close call", manager: undefined,
      contenders: [{ manager: "mgr_1", pitch: "Jo's keeper saved it." }, { manager: "mgr_2", pitch: "Priya scored." }],
    } as Cue["payload"];
    const view = { clash: { phase: "chosen" as const, cue: clash, winner: "mgr_1", loser: "mgr_2" }, card: null, slot: null };
    const html = renderToStaticMarkup(
      <GroupStage view={view} cues={[]} t={0} names={names} judge={null} pitch={null} />,
    );
    expect(html).toContain("Jo&#x27;s keeper saved it.");
    expect(labels(html)).toContain("Host's call");
    view.clash.cue = { ...clash, source: "fallback" };
    const fellBack = renderToStaticMarkup(<GroupStage view={view} cues={[]} t={0} names={names} judge={null} pitch={null} />);
    expect(labels(fellBack)).toContain("Fallback");
    expect(labels(fellBack)).not.toContain("Host's call");
  });
});

describe("the mini-league table's unit (#184)", () => {
  const rows: TableRow[] = [{ manager: "mgr_1", points: 212, rank: 1 }];
  it("keeps \" IP\" in the markup for screen readers when the card's heading already names it on screen", () => {
    const html = renderToStaticMarkup(<LeagueTable rows={rows} moves={new Map()} names={names} judge={null} unit="hidden" />);
    expect(html).toContain('<small class="visually-hidden"> IP</small>');
  });

  it("shows \" IP\" by default", () => {
    const html = renderToStaticMarkup(<LeagueTable rows={rows} moves={new Map()} names={names} judge={null} />);
    expect(html).toContain("<small> IP</small>");
  });
});

const examples = import.meta.glob("../../../contracts/examples/league.json", { eager: true, import: "default" });
const LEAGUE = structuredClone(examples["../../../contracts/examples/league.json"] as League);

describe("the lobby's words (#184, Watch now since #196)", () => {
  const initial = { league: LEAGUE, clubs: {}, players: {} };
  const html = renderToStaticMarkup(<Lobby initial={initial} initialFormat="single" />);
  const steps = [2, 3].map((s) => renderToStaticMarkup(<Lobby initial={initial} initialStep={s as 2 | 3} />)).join("");

  // #196: the groups moved into steps 2 and 3, each named by the eyebrow beside it
  it("names each choice group by the words shown beside it", () => {
    for (const [id, words] of [["seg-language", "Language"], ["seg-watch", "Watch"], ["seg-match", "Match"]]) {
      expect(steps).toContain(`<span class="eyebrow" id="${id}">${words}</span>`);
      expect(steps).toContain(`role="group" aria-labelledby="${id}"`);
    }
    expect(steps).not.toMatch(/aria-label="(Language|Format|Match)"/);
  });

  // #196: a manager is a row now; the words sit beside their rank
  it("says a Spanish-speaking manager's phone is in Spanish, in words", () => {
    expect(html).toContain(' · phone in <span lang="es">Español</span>');
    expect(html).not.toContain(">ES<");
  });
});

const licences = import.meta.glob(["../fonts/OFL.txt", "../../../NOTICE.md"], { eager: true, query: "?raw", import: "default" });

describe("the self-hosted fonts' licence (#184)", () => {
  it("ships the OFL text beside the fonts with each font's copyright line, and NOTICE.md names them", () => {
    const ofl = (licences["../fonts/OFL.txt"] as string | undefined) ?? "";
    expect(ofl).toContain("SIL OPEN FONT LICENSE Version 1.1");
    expect(ofl).toContain("Copyright 2019 The Big Shoulders Project Authors");
    expect(ofl).toContain("Copyright 2020-2024 The Atkinson Hyperlegible Next Project Authors");
    const notice = (licences["../../../NOTICE.md"] as string | undefined) ?? "";
    expect(notice).toMatch(/Big Shoulders Display and Atkinson Hyperlegible Next .*\| OFL-1\.1/);
  });
});

describe("the favicon (#184)", () => {
  it("is linked from the page as an SVG at the page's own origin", () => {
    expect(indexHtml).toContain('<link rel="icon" type="image/svg+xml" href="/favicon.svg" />');
  });
});
