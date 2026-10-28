"""End-to-end browser check of every feature on a deployed site (#204): the real UI, driven as a judge would.

One line per case (`ok`, `skip <reason>` or `FAIL <reason>`), then a summary line; exit 1 on any FAIL. Failure
screenshots go to --out, with the QR code hidden (it carries the phone token). Nothing prints or keeps a phone or
member token: the watch page's address is never printed, and every failure reason is scrubbed.

A run starts at most two matches (cases 3 to 13 share one, 15 to 19 and 21 the league's) and creates at most one
league; the per-address agent limit is 4 matches an hour. Run after each deploy, from backend/:

  uv run --with playwright playwright install chromium        # once
  uv run --with playwright python -m tools.e2e_live https://couchgaffer.com
  uv run --with playwright python -m tools.e2e_live http://127.0.0.1:8000 --cases 1,2,14,20 --out /tmp/e2e

A server without agents or without a recap job fails the cases that need them; a local run passes
--allow-agents-off and --allow-no-recaps to accept that. A setup that fails (the Watch now start, the league) fails
every selected case that depends on it, with the setup's reason.
"""

import argparse
import itertools
import json
import re
import socket
import ssl
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import certifi
import httpx

TITLES = {
    1: "Landing", 2: "Watch now", 3: "Start", 4: "Group screen", 5: "Table and moments", 6: "Phone and tools",
    7: "Other phones", 8: "Knock out", 9: "Behind the scenes", 10: "Close call", 11: "Phone page", 12: "Summaries",
    13: "Recap", 14: "Ended", 15: "Friends", 16: "Join", 17: "Squad", 18: "League match", 19: "Season",
    20: "For judges", 21: "Replicas", 22: "Domain",
}  # fmt: skip
MATCH_CASES = frozenset(range(3, 14))  # one Watch now match
LEAGUE_CASES = frozenset({15, 16, 17, 18, 19, 21})  # one league and its gameweek's match
DOMAIN = "https://couchgaffer.com"
FIRST_CARD_S = 90  # the first key moment lands within about 10 s of kick-off at 30x
SPANISH_CARD_S = 150  # Leo's first card in Spanish: Leo has the fewest moments
FULL_TIME_S = 420  # three minutes of match, with room (as tools/smoke.py)
RECAP_S = 600  # after full time: the recap job renders the videos (#15)
SEASON_S = 60  # the result is recorded at full time, before the recaps
ENDED_S = 45  # an unknown match: the sockets give up and the page says it has ended
FRAME_MS = 3_000  # sim/engine.py FRAME_MS: one frame per fixture every 3 s of match time
PLAYING = "A friend is playing this gameweek right now"  # store/api.py PLAYING
VIEWPORT = {"width": 1280, "height": 800}
PHONE_VIEWPORT = {"width": 375, "height": 812}

# Records what the watch page showed over time, so a case can ask after the fact: a close call and its verdict,
# and whether every fixture reached full time. Polled, so it sees a takeover that lasts a few seconds.
OBSERVER = """
(() => {
  const seen = { clash: null, verdict: null };
  window.__e2e = seen;
  setInterval(() => {
    const clash = document.querySelector('.clash-takeover');
    if (clash && !seen.clash) seen.clash = [...clash.querySelectorAll('.contender .who')].map((e) => e.textContent);
    const verdict = document.querySelector('.verdict-takeover h2, .verdict-line');
    if (verdict && seen.clash && !seen.verdict) seen.verdict = verdict.textContent;
  }, 150);
})();
"""
# The QR code encodes the phone token. The page's CSP refuses an injected style sheet, so each one is hidden in
# place; the count lets a screenshot be skipped when hiding did not take.
HIDE_SECRETS = """() => {
  const codes = [...document.querySelectorAll('.qr')];
  codes.forEach((e) => { e.style.visibility = 'hidden'; });
  return codes.every((e) => getComputedStyle(e).visibility === 'hidden');
}"""


class Skip(Exception):
    pass


class Fail(Exception):
    pass


def expect(ok: bool, what: str) -> None:
    if not ok:
        raise Fail(what)


def scrub(text: str) -> str:
    """A failure reason that can be printed: its first line, with any URL fragment or token parameter cut out (the
    watch and phone pages carry the phone token in the fragment, and the sockets in ?token=)."""
    line = (text.strip().splitlines() or [""])[0]
    line = re.sub(r"#[^\s'\"]*", "#…", line)
    line = re.sub(r"/(watch|phone|ws)/[A-Za-z0-9_-]+(/[A-Za-z0-9_:-]+)?", r"/\1/…", line)
    line = re.sub(r"(token=|Bearer\s+)[^&\s'\"]+", r"\1…", line)
    return line[:300]


def parse_cases(spec: str | None) -> list[int]:
    """`--cases 1,3-5,20` as numbers, in order; every case when none is given."""
    if not spec:
        return sorted(TITLES)
    chosen: set[int] = set()
    for part in spec.split(","):
        lo, _, hi = part.strip().partition("-")
        span = range(int(lo), int(hi or lo) + 1)
        unknown = [n for n in span if n not in TITLES]
        if unknown:
            raise ValueError(f"no case {unknown[0]}: cases are 1 to {max(TITLES)}")
        chosen.update(span)
    return sorted(chosen)


def frame_gaps(frames: list[tuple[str, int]], step: int = FRAME_MS) -> dict[str, int]:
    """Each fixture's missing frame times between the first and the last one received, from (fixture, t) pairs."""
    times: dict[str, set[int]] = {}
    for fixture, t in frames:
        times.setdefault(fixture, set()).add(t)
    return {
        fixture: sum((b - a) // step - 1 for a, b in itertools.pairwise(sorted(seen)) if b - a > step)
        for fixture, seen in sorted(times.items())
    }


def club_names(lines: list[str]) -> list[list[str]]:
    """The strip's fixtures without their scores: "KES 1-0 RAV" gives ["KES", "RAV"]."""
    return sorted(re.sub(r"\d+-\d+", "", line).split() for line in lines)


def same_fixtures(group: list[str], phone: list[str]) -> None:
    """The phone shows the group screen's fixtures; an empty strip on either side is a failure, not a match."""
    expect(bool(group), "no fixtures in the group screen's strip")
    expect(bool(phone), "no fixtures in the phone's strip")
    expect(club_names(phone) == club_names(group), f"the phone shows {phone}, the group screen {group}")


def five_phones(tabs: list[str]) -> None:
    """The drawer's Whose phone list has a tab for each of the five friends."""
    expect(len(tabs) == 5 and len(set(tabs)) == 5, f"Whose phone lists {tabs}, not five friends")


def default_out() -> Path:
    """Failure screenshots go under the system temp folder, never into the checkout."""
    return Path(tempfile.gettempdir()) / "couch-gaffer-e2e"


class Run:
    def __init__(self, base: str, cases: list[int], out: Path, browser: Any, allow_agents_off: bool = False,
                 allow_no_recaps: bool = False) -> None:  # fmt: skip
        self.base, self.cases, self.out, self.browser = base, cases, out, browser
        self.allow_agents_off, self.allow_no_recaps = allow_agents_off, allow_no_recaps
        self.lines: dict[int, str] = {}  # each case's printed result, for the cases that depend on it
        self.match_error = "not reached"  # why there is no Watch now match, for cases 4 to 13
        self.league_error = "not reached"  # why there is no league match, for cases 19 and 21
        self.results: Counter[str] = Counter()
        self.health: dict[str, Any] = {}
        self.match: Any = None  # the Watch now page (cases 3 to 13)
        self.league_pages: list[Any] = []  # the creator's and the joiner's league pages (15 to 19, 21)
        self.league: dict[str, str] = {}  # its id, name and code; never a member token
        self.league_watch: Any = None  # the creator's watch page of the league match
        self.frames: list[tuple[str, int]] = []  # (fixture, t) of each frame on the league match's group socket
        self.phone_host: str | None = None  # the QR link's host (case 22)
        self.invite_host: str | None = None  # the invite link's host (case 22)
        self.contexts: list[Any] = []

    # --- plumbing ------------------------------------------------------------------------------------------------

    def context(self, viewport: dict[str, int] = VIEWPORT, clipboard: bool = False) -> Any:
        ctx = self.browser.new_context(viewport=viewport)
        if clipboard:
            ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=self.base)
        self.contexts.append(ctx)
        return ctx

    def case(self, n: int, body: Callable[[], Any], shots: Callable[[], list[Any]] = lambda: []) -> bool:
        """Runs one case if chosen, prints its line, and screenshots its pages on a failure. True when it passed."""
        if n not in self.cases:
            return False
        try:
            note = body()
            line, passed = "ok" + (f" ({note})" if note else ""), True
        except Skip as skip:
            line, passed = f"skip {skip}", False
            self.results["skip"] += 1
        except Exception as exc:  # a Fail, or Playwright's own timeout or error
            line, passed = f"FAIL {scrub(str(exc) or type(exc).__name__)}", False
            self.results["FAIL"] += 1
            self.screenshot(n, list({id(p): p for p in shots() if p is not None}.values()))  # each page once
        else:
            self.results["ok"] += 1
        self.lines[n] = line
        print(f"{n:>2} {TITLES[n]}: {line}", flush=True)
        return passed

    def screenshot(self, n: int, pages: list[Any]) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        for i, page in enumerate(pages):
            path = self.out / f"case-{n:02d}{'' if i == 0 else f'-{i + 1}'}.png"
            try:
                if not page.evaluate(HIDE_SECRETS):
                    print(f"   (no screenshot for case {n}: the QR code could not be hidden)", flush=True)
                    continue
                page.screenshot(path=str(path), full_page=True)
            except Exception as exc:  # a closed page has nothing to show; say so rather than pass in silence
                print(f"   (no screenshot for case {n}: {scrub(str(exc))})", flush=True)

    def needs(self, page: Any, why: str) -> Any:
        """The page a case runs on; without it the case fails with the reason its setup failed."""
        if page is None:
            raise Fail(why)
        return page

    def agents_needed(self, what: str) -> bool:
        """True when the agents are on. Off, a check that needs them fails unless --allow-agents-off was passed."""
        if self.health.get("agents"):
            return True
        expect(self.allow_agents_off, f"agents off: {what} needs them (pass --allow-agents-off for a local run)")
        return False

    def wait_for(self, page: Any, test: str, seconds: float, what: str) -> Any:
        """Waits for a JavaScript expression to be truthy; the failure says what did not happen in how long."""
        try:
            return page.wait_for_function(test, timeout=seconds * 1000).json_value()
        except Exception:
            raise Fail(f"{what} not seen within {seconds:.0f} s") from None

    def all_full_time(self, page: Any, seconds: float) -> None:
        """Every fixture at full time: each strip entry says FT, or the score bug does in a single match."""
        test = """() => {
          const when = [...document.querySelectorAll('.broadcast-bar .score-strip li .when')].map((e) => e.textContent);
          if (when.length) return when.every((w) => w === 'FT');
          return document.querySelector('.match-header.broadcast .clock')?.textContent === 'FT';
        }"""
        self.wait_for(page, test, seconds, "full time on every fixture")

    # --- the run ----------------------------------------------------------------------------------------------------

    def run(self) -> None:
        with httpx.Client(base_url=self.base, timeout=20) as http:
            self.health = http.get("/health").json()
        self.case(1, self.landing)
        self.case(2, self.watch_now, lambda: [self.match])
        if MATCH_CASES & set(self.cases):
            self.watch_match()
        self.case(14, self.ended)
        if LEAGUE_CASES & set(self.cases):
            self.league_flow()
        self.case(20, self.judges)
        self.case(22, self.domain)
        for ctx in self.contexts:
            ctx.close()

    # 1 -------------------------------------------------------------------------------------------------------------

    def landing(self) -> None:
        with httpx.Client(timeout=20) as http:
            head = http.get(self.base + "/")
        csp = head.headers.get("content-security-policy", "")
        expect("default-src 'self'" in csp, f"Content-Security-Policy: {csp or 'missing'}")
        expect(head.headers.get("x-content-type-options") == "nosniff", "X-Content-Type-Options is not nosniff")
        page = self.context().new_page()
        page.goto(self.base + "/")
        page.get_by_role("heading", name=re.compile("Watch the gameweek with your")).wait_for(timeout=15_000)
        for name in ("Watch now", "Play with your friends", "For judges"):
            expect(page.get_by_role("link", name=re.compile(name)).count() >= 1, f"no {name} link")
        page.close()

    # 2 -------------------------------------------------------------------------------------------------------------

    def watch_now(self) -> None:
        page = self.match = self.context().new_page()
        page.goto(self.base + "/?watch")
        start = page.get_by_role("button", name=re.compile("Start"))
        start.first.wait_for(timeout=15_000)
        expect(start.count() == 1, f"{start.count()} Start buttons")
        back, next_ = page.get_by_role("button", name="Back", exact=True), page.get_by_role("button", name="Next")
        expect(back.is_disabled(), "Back is not disabled on step 1")
        page.wait_for_function("() => !document.querySelector('.e-go button').disabled", timeout=15_000)  # the lobby
        next_.click()
        next_.click()
        expect(page.get_by_role("button", name="Next").count() == 0, "Next is still there on step 3")
        sentence = page.locator(".e-go .sum b")
        expect("a gameweek of three matches" in sentence.inner_text(), f"sentence: {sentence.inner_text()}")
        page.get_by_role("button", name="A single match").click()
        expect("a single match" in sentence.inner_text(), f"the sentence did not follow: {sentence.inner_text()}")
        page.get_by_role("button", name=re.compile("A gameweek")).click()  # back to the default for case 3
        expect("a gameweek of three matches" in sentence.inner_text(), "the sentence did not follow back")

    # 3 to 13 -------------------------------------------------------------------------------------------------------

    def watch_match(self) -> None:
        started = self.case(3, self.start, lambda: [self.match])
        if not started and 3 in self.cases:
            self.match_error = f"no match: case 3 {self.lines[3]}"
        if 3 not in self.cases:  # a later case needs the match even when Start is not one of the cases asked for
            try:
                self.start()
                started = True
            except Exception as exc:
                self.match_error = f"no match: Start failed: {scrub(str(exc) or type(exc).__name__)}"
                print(f"   ({self.match_error})", flush=True)
        if not started:
            self.match = None
        shots = lambda: [self.match]  # noqa: E731
        self.case(4, self.group_screen, shots)
        self.case(5, self.table_and_moments, shots)
        self.case(6, self.phone_and_tools, shots)
        self.case(7, self.other_phones, shots)
        self.case(8, self.knock_out, shots)
        self.case(9, self.behind_the_scenes, shots)
        self.case(11, self.phone_page, shots)
        reached = False
        if self.match is not None and ({10, 12, 13} & set(self.cases)):
            try:
                self.all_full_time(self.match, FULL_TIME_S)
                reached = True
            except Fail as exc:
                print(f"   (the match did not finish: {exc})", flush=True)
        self.case(10, lambda: self.close_call(reached), shots)
        self.case(12, lambda: self.summaries(reached), shots)
        self.case(13, lambda: self.recap(reached), shots)

    def start(self) -> None:
        page = self.match
        if page is None or "?watch" not in page.url:  # case 2 did not run, or failed: open Watch now here
            page = self.match = self.context().new_page()
            page.goto(self.base + "/?watch")
        page.context.add_init_script(OBSERVER)
        page.get_by_role("button", name="Start the watch party").click()
        page.wait_for_url(re.compile(r"/watch/[A-Za-z0-9_-]{8,64}"), timeout=30_000)
        page.evaluate(OBSERVER)  # the app navigated in place, so the init script has not run on this document
        strip = page.locator(".broadcast-bar .score-strip li")
        strip.first.wait_for(timeout=30_000)
        expect(strip.count() == 3, f"the bar shows {strip.count()} fixtures")
        expect(page.locator(".broadcast-bar .match-header .score").is_visible(), "no score bug")

    def group_screen(self) -> str:
        page = self.needs(self.match, self.match_error)
        test = """() => {
          const who = document.querySelector('.lower-third .who')?.textContent;
          return who && who !== 'Key moment' ? who : null;
        }"""
        friend = self.wait_for(page, test, FIRST_CARD_S, "a friend's card in the lower third")
        third = page.locator(".lower-third").first
        wrote = third.locator(".mark.ai, .mark.fallback")
        expect(wrote.count() >= 1, "the lower third does not say who wrote it")
        decided = third.locator(".mark.decided")
        expect(decided.count() == 1, "the lower third does not say who decided it")
        shirts = page.locator(".group-screen svg.pitch text.shirt").count()
        expect(shirts == 22, f"{shirts} numbered players on the pitch")
        return f"{friend}; written: {wrote.first.inner_text()}; decided: {decided.inner_text()}"

    def table_and_moments(self) -> None:
        page = self.needs(self.match, self.match_error)
        rows = page.locator(".rail ol.league li")
        expect(rows.count() == 5, f"the rail lists {rows.count()} friends")
        points = [r.locator(".points").inner_text() for r in rows.all()]
        expect(all(re.match(r"-?\d", p) for p in points), f"points: {points}")
        moments = ".rail ol.ticker li:not(.kickoff):not(.half_time):not(.full_time)"
        self.wait_for(page, f"() => document.querySelectorAll('{moments}').length > 0", FIRST_CARD_S, "a key moment")

    def open_tools(self, page: Any) -> Any:
        button = page.get_by_role("button", name="Phone and tools")
        if button.get_attribute("aria-expanded") != "true":
            button.click()
        drawer = page.locator("#tools")
        drawer.wait_for(timeout=5_000)
        return drawer

    def phone_and_tools(self) -> None:
        page = self.needs(self.match, self.match_error)
        drawer = self.open_tools(page)
        for tab in ("For you", "Live", "Table"):
            expect(drawer.get_by_role("tab", name=tab).count() == 1, f"no {tab} tab on the phone")
        expect(drawer.get_by_role("img", name="QR code to your phone").count() == 1, "no QR code")
        link = drawer.get_by_role("link", name="Open it here").get_attribute("href") or ""
        expect(link.startswith(self.base + "/phone/"), "the QR link does not lead to /phone/ on this site")
        self.phone_host = urlsplit(link).netloc
        expect(drawer.get_by_role("heading", name="Try a failure").count() == 1, "no Try a failure")
        page.keyboard.press("Escape")
        try:
            drawer.wait_for(state="detached", timeout=3_000)
        except Exception:
            raise Fail("Escape did not close the drawer") from None
        focused = page.evaluate("() => document.activeElement?.textContent ?? ''")
        expect(focused.strip() == "Phone and tools", f"focus went to {focused.strip()[:40]!r}, not the button")

    def other_phones(self) -> None:
        page = self.needs(self.match, self.match_error)
        drawer = self.open_tools(page)
        drawer.get_by_role("tab", name="Leo").click()
        phone = drawer.locator(".phone-app")
        expect(phone.get_attribute("lang") == "es", "Leo's phone is not in Spanish")
        expect(phone.get_by_role("tab", name="Para ti").count() == 1, "Leo's tabs are not in Spanish")
        test = "() => !!document.querySelector('#tools .feed-card .fc-body[lang=es] ~ .translation[lang=en]')"
        self.wait_for(page, test, SPANISH_CARD_S, "a Spanish card with English under it on Leo's phone")

    def knock_out(self) -> str:
        page = self.needs(self.match, self.match_error)
        drawer = self.open_tools(page)
        drawer.get_by_role("tab", name="You").click()
        button = drawer.get_by_role("button", name=re.compile("^Knock out"))
        try:
            button.wait_for(timeout=10_000)
            page.wait_for_function("() => { const b = [...document.querySelectorAll('#tools button')].find((b) => "
                                   "b.textContent.startsWith('Knock out')); return b && !b.disabled; }",
                                   timeout=30_000)  # fmt: skip
        except Exception:
            raise Fail("Knock out is not pressable (the match is not live)") from None
        with page.expect_response(lambda r: r.url.endswith("/chaos") and r.request.method == "POST") as answer:
            button.click()
        status = answer.value.status
        expect(200 <= status < 300, f"POST .../chaos answered {status}")
        knocked = drawer.get_by_role("button", name=re.compile("^Knocked out"))
        try:
            knocked.wait_for(timeout=5_000)
        except Exception:
            raise Fail("the button does not show the knocked-out state") from None
        page.keyboard.press("Escape")
        return f"POST .../chaos {status}"

    def behind_the_scenes(self) -> str:
        page = self.needs(self.match, self.match_error)
        page.get_by_role("button", name="Behind the scenes").click()
        trace = page.locator("aside.trace")
        trace.wait_for(timeout=5_000)
        decision = trace.locator("article.decision").first
        try:
            decision.wait_for(timeout=FIRST_CARD_S * 1000)
        except Exception:
            raise Fail(f"no decision within {FIRST_CARD_S} s") from None
        rows = decision.locator("tbody tr")
        expect(rows.count() >= 1, "a decision with no manager in it")
        decider = decision.locator(".tag").inner_text()  # read now: the decision is gone once the panel closes
        expect(decider != "", "no one is named as deciding")
        cells = [r.locator("td").all_inner_texts() for r in rows.all()]
        expect(all(re.fullmatch(r"-?\d+\.\d\d", c[1]) for c in cells), f"scores: {[c[1] for c in cells]}")
        expect(all(re.fullmatch(r"\d+%", c[2]) for c in cells), f"airtime: {[c[2] for c in cells]}")
        bids = trace.locator("tbody td q").count()
        if self.agents_needed("a quoted agent bid"):
            expect(bids > 0, "no agent bid quoted in any decision")
        page.keyboard.press("Escape")
        try:
            trace.wait_for(state="detached", timeout=3_000)
        except Exception:
            raise Fail("Escape did not close Behind the scenes") from None
        return f"{bids} bids quoted; decided by: {decider}"

    def phone_page(self) -> None:
        page = self.needs(self.match, self.match_error)
        drawer = self.open_tools(page)
        link = drawer.get_by_role("link", name="Open it here").get_attribute("href") or ""
        page.keyboard.press("Escape")
        group = page.locator(".broadcast-bar .score-strip .line").all_inner_texts()
        phone = self.context(PHONE_VIEWPORT).new_page()
        try:
            try:
                phone.goto(link)  # its path and fragment name the session, the friend and the token: never printed
            except Exception:
                raise Fail("the phone page did not open") from None
            phone.locator(".phone-only .phone-app").wait_for(timeout=20_000)
            phone.locator(".phone-only ol.feed").wait_for(timeout=10_000)
            mine = phone.locator(".score-strip .line").all_inner_texts()
            same_fixtures(group, mine)
            wide = phone.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
            expect(wide <= 0, f"scrolls sideways by {wide} px at 375 px")
        except Exception:
            self.screenshot(11, [phone])
            raise
        finally:
            phone.close()

    def close_call(self, reached: bool) -> str:
        page = self.needs(self.match, self.match_error)
        seen = page.evaluate("() => window.__e2e")
        if not seen or not seen.get("clash"):
            if not reached:
                raise Fail("the match did not reach full time, and no close call was seen")
            raise Skip("no close call this run" + ("" if self.health.get("agents") else " (agents off)"))
        claims, verdict = seen["clash"], seen.get("verdict") or ""
        expect(len(claims) == 2, f"a close call with {len(claims)} claims")
        expect(any(verdict.startswith(f"{c}'s") or verdict.startswith("Your") for c in claims),
               f"the verdict names no claimant: {verdict!r}")  # fmt: skip
        return f"{claims[0]} v {claims[1]}: {verdict}"

    def summaries(self, reached: bool) -> str:
        page = self.needs(self.match, self.match_error)
        expect(reached, "the match did not reach full time")
        drawer = self.open_tools(page)
        tabs = drawer.get_by_role("tablist", name="Whose phone").get_by_role("tab")
        names = [str(n) for n in tabs.all_inner_texts()]
        five_phones(names)
        counts: dict[str, int] = {}
        phones: set[str] = set()
        for i, name in enumerate(names):
            tabs.nth(i).click()
            # This phone's own: its tab selected and the phone app relabelled with its friend's name, so the
            # previous phone's cards cannot answer for it ("You" is the judge's own phone, under their own name)
            test = f"""() => {{
              const tab = document.querySelectorAll('#tools [role=tablist] [role=tab]')[{i}];
              const label = document.querySelector('#tools .phone-app')?.getAttribute('aria-label') ?? '';
              const own = {json.dumps(name)} === 'You' || label.includes({json.dumps(name)});
              const fresh = !{json.dumps(sorted(phones))}.includes(label);
              return tab?.getAttribute('aria-selected') === 'true' && own && fresh
                && document.querySelectorAll('#tools .feed-card.summary').length >= 2 ? label : null;
            }}"""
            phones.add(self.wait_for(page, test, 60, f"half-time and full-time summaries on {name}'s own phone"))
            counts[name] = drawer.locator(".feed-card.summary").count()
        expect(len(phones) == 5, f"{len(phones)} distinct phones read, not five")
        page.keyboard.press("Escape")
        return ", ".join(f"{k} {v}" for k, v in counts.items())

    def recap(self, reached: bool) -> str:
        page = self.needs(self.match, self.match_error)
        if self.health.get("recaps") is False and self.allow_no_recaps:
            raise Skip("no recap job: /health says recaps false (--allow-no-recaps)")
        expect(self.health.get("recaps") is not False, "no recap job: /health says recaps false "
               "(pass --allow-no-recaps for a local run)")  # fmt: skip
        expect(reached, "the match did not reach full time")
        drawer = self.open_tools(page)
        drawer.get_by_role("tab", name="You").click()
        test = "() => !!document.querySelector('#tools .recap-button')"
        self.wait_for(page, test, RECAP_S, "a recap link on your phone after full time")
        url = drawer.locator(".recap-button").get_attribute("href") or ""
        with httpx.Client(timeout=30, follow_redirects=True) as http:  # a signed link: never printed
            head = http.get(url, headers={"Range": "bytes=0-1023"})
        kind = head.headers.get("content-type", "")
        expect(head.status_code in (200, 206), f"the recap link answered {head.status_code}")
        expect(kind.startswith("video/"), f"the recap link is {kind or 'untyped'}, not video")
        return f"{head.status_code} {kind}"

    # 14 ------------------------------------------------------------------------------------------------------------

    def ended(self) -> None:
        page = self.context().new_page()
        try:
            page.goto(self.base + "/watch/e2e-no-such-match-204")
            try:
                page.get_by_role("heading", name="This watch party has ended").wait_for(timeout=ENDED_S * 1000)
            except Exception:
                raise Fail(f"no ended page within {ENDED_S} s") from None
            href = page.get_by_role("link", name="Start a new watch party").get_attribute("href")
            expect(href == "/?watch", f"the ended page links to {href}")
        except Exception:
            self.screenshot(14, [page])
            raise
        finally:
            page.close()

    # 15 to 19, 21 --------------------------------------------------------------------------------------------------

    def league_flow(self) -> None:
        shots = lambda: [*self.league_pages, self.league_watch]  # noqa: E731
        steps: list[tuple[int, Callable[[], Any]]] = [
            (15, self.friends), (16, self.join), (17, self.squads), (18, self.league_match),
        ]  # fmt: skip
        stopped: str | None = None
        for n, step in steps:  # each needs the one before: run it as setup when it was not asked for
            if stopped is not None:
                self.case(n, partial(self.not_reached, stopped))
                continue
            if n in self.cases:
                if not self.case(n, step, shots):
                    stopped = f"not reached: case {n} failed"
                continue
            try:
                step()
            except Exception as exc:
                stopped = f"not reached: league setup failed at case {n}: {scrub(str(exc) or type(exc).__name__)}"
                print(f"   ({stopped})", flush=True)
        if stopped is not None:
            self.league_watch, self.league_error = None, stopped
        reached = False
        if self.league_watch is not None and ({19, 21} & set(self.cases)):
            try:
                self.all_full_time(self.league_watch, FULL_TIME_S)
                reached = True
            except Fail as exc:
                print(f"   (the league match did not finish: {exc})", flush=True)
        self.case(21, lambda: self.replicas(reached), shots)
        self.case(19, lambda: self.season(reached), shots)

    def not_reached(self, why: str) -> None:
        raise Fail(why)

    def member_names(self, page: Any) -> list[str]:
        return [str(n) for n in page.locator(".e-mems li .who strong").all_inner_texts()]

    def friends(self) -> None:
        page = self.context(clipboard=True).new_page()
        self.league_pages = [page]
        page.goto(self.base + "/league")
        name = f"E2E {time.strftime('%d%b %H%M')}"
        page.get_by_label("Your name").fill("Ada")
        page.get_by_label("League name").fill(name)
        page.get_by_role("button", name="Create the league").click()
        page.wait_for_url(re.compile(r"/league/lg_[0-9a-f]{12}$"), timeout=20_000)
        code = page.locator(".e-code").inner_text().strip()
        expect(re.fullmatch(r"[A-Z0-9]{6}", code) is not None, f"join code {code!r}")
        page.get_by_role("button", name="Copy invite link").click()
        page.get_by_role("button", name="Link copied").wait_for(timeout=5_000)
        link = page.evaluate("() => navigator.clipboard.readText()")
        expect(link == f"{self.base}/league/join?code={code}", f"copied {link!r}")
        self.invite_host = urlsplit(link).netloc
        self.league = {"id": urlsplit(page.url).path.rsplit("/", 1)[1], "name": name, "code": code, "link": link}

    def join(self) -> None:
        expect(bool(self.league), "no league to join")
        page = self.context().new_page()
        self.league_pages.append(page)
        page.goto(self.league["link"])
        expect(page.get_by_label("Join code").input_value() == self.league["code"], "the link did not fill the code")
        page.get_by_label("Your name").fill("Bea")
        page.get_by_role("button", name="Join", exact=True).click()
        page.wait_for_url(re.compile(r"/league/lg_[0-9a-f]{12}$"), timeout=20_000)
        expect(page.url.endswith(self.league["id"]), "joined another league")
        creator = self.league_pages[0]
        creator.reload()
        for who in (creator, page):
            who.locator(".e-mems").wait_for(timeout=10_000)
            names = self.member_names(who)
            expect({"Ada", "Bea"} <= set(names), f"members: {names}")

    def pick(self, page: Any) -> tuple[bool, bool]:
        """Five players and a captain, saved. Says whether a fourth from one club and a sixth were refused."""
        picker = page.locator("form.e-picker")
        picker.wait_for(timeout=10_000)
        more = picker.get_by_role("button", name=re.compile("^Show all"))
        if more.count():
            more.click()
        add = picker.locator("tbody button.add:not(.out):not([disabled])")
        fourth_refused = False
        for _ in range(5):
            add.first.click()
            fourth_refused = fourth_refused or picker.locator(".full-club").count() > 0
        sixth_refused = picker.locator("tbody button.add:not(.out):not([disabled])").count() == 0
        picker.locator(".e-tray button[aria-label^='Make']").first.click()
        picker.get_by_role("button", name="Save my squad").click()
        page.get_by_text("Squad saved").wait_for(timeout=10_000)
        return fourth_refused, sixth_refused

    def squads(self) -> None:
        expect(len(self.league_pages) == 2, "no two members")
        for page in self.league_pages:
            fourth, sixth = self.pick(page)
            expect(fourth, "a fourth player from one club was not refused")
            expect(sixth, "a sixth player was not refused")
            status = page.locator(".e-mem.you .st").inner_text()
            expect(status == "Picked", f"own row says {status!r}, not Picked")

    def league_match(self) -> None:
        creator, joiner = self.league_pages
        creator.context.add_init_script(OBSERVER)
        creator.on("websocket", self.listen_for_frames)
        creator.get_by_role("button", name=re.compile(r"^Start gameweek \d+")).click()
        creator.wait_for_url(re.compile(r"/watch/[A-Za-z0-9_-]{8,64}"), timeout=30_000)
        self.league_watch = creator
        badge = creator.locator(".league-badge")
        try:
            badge.wait_for(timeout=20_000)
        except Exception:
            raise Fail("the group screen does not name the league") from None
        expect(badge.inner_text() == self.league["name"], f"the badge says {badge.inner_text()!r}")
        joiner.reload()
        joiner.get_by_role("button", name=re.compile(r"^Start gameweek \d+")).click()
        refusal = joiner.get_by_role("alert")
        try:
            refusal.wait_for(timeout=15_000)
        except Exception:
            raise Fail("a second start was not refused") from None
        expect(PLAYING in refusal.inner_text(), f"the refusal says {refusal.inner_text()!r}")
        expect("/league/" in joiner.url, "the second start opened a match")

    def listen_for_frames(self, ws: Any) -> None:
        if not re.search(r"/ws/[^/]+/group(\?|$)", ws.url):  # the phone sockets carry the token: not read at all
            return

        def received(payload: str | bytes) -> None:
            try:
                message = json.loads(payload)
            except ValueError:
                return
            if message.get("type") == "frame":
                self.frames.append((str(message["data"].get("fixture_id", "")), int(message["data"]["t"])))

        ws.on("framereceived", received)

    def replicas(self, reached: bool) -> str:
        self.needs(self.league_watch, self.league_error)
        expect(reached, "the league match did not reach full time")
        expect(len(self.frames) > 0, "no frames on the group screen")
        gaps = frame_gaps(self.frames)
        expect(not any(gaps.values()), f"frame times missing, by fixture: {gaps}")
        return f"{len(self.frames)} frames over {len(gaps)} fixtures, none missing"

    def season(self, reached: bool) -> str:
        self.needs(self.league_watch, self.league_error)
        expect(reached, "the league match did not reach full time")
        page = self.league_pages[0]
        page.goto(f"{self.base}/league/{self.league['id']}")
        deadline = time.monotonic() + SEASON_S
        rows: list[list[str]] = []
        while time.monotonic() < deadline:
            page.locator(".e-mems").wait_for(timeout=10_000)
            rows = [r.locator("td").all_inner_texts() for r in page.locator("table.e-season tbody tr").all()]
            if {"Ada", "Bea"} <= {r[1] for r in rows if len(r) > 2}:
                break
            page.wait_for_timeout(3_000)
            page.reload()
        named = {r[1]: r[2] for r in rows if len(r) > 2}
        expect({"Ada", "Bea"} <= set(named), f"the season table lists {sorted(named)} after {SEASON_S} s")
        expect(all(re.fullmatch(r"-?\d+(\.\d+)?", named[n]) for n in ("Ada", "Bea")), f"points: {named}")
        week = page.locator(".e-lp-head .gw").inner_text()
        expect(week == "Gameweek 2", f"the page shows {week!r}, not the next gameweek")
        expect(page.get_by_role("button", name="Start gameweek 2").count() == 1, "gameweek 2 cannot be started")
        return f"Ada {named['Ada']}, Bea {named['Bea']}; {week} open"

    # 20 ------------------------------------------------------------------------------------------------------------

    def judges(self) -> str:
        page = self.context().new_page()
        try:
            page.goto(self.base + "/judges")
            page.get_by_role("heading", name="Try it in six steps").wait_for(timeout=15_000)
            for title in ("How it covers the brief", "Does the multi-agent design earn its place?", "How it works",
                          "Microsoft technology", "All data is invented", "The gameweek"):  # fmt: skip
                expect(page.get_by_role("heading", name=title).count() >= 1, f"no section {title!r}")
            steps = page.locator("ol.e-try > li").count()
            expect(steps == 6, f"{steps} steps")
            hrefs = page.eval_on_selector_all("a[href]", "as => as.map((a) => a.getAttribute('href'))")
            anchors = [h for h in hrefs if h.startswith("#")]
            missing = [h for h in anchors if page.locator(f"[id='{h[1:]}']").count() == 0]
            expect(not missing, f"in-page links to nothing: {missing}")
            own = sorted({h.split("#")[0] for h in hrefs if h.startswith("/")})
            with httpx.Client(base_url=self.base, timeout=20) as http:
                broken = [h for h in own if http.get(h).status_code != 200]
            expect(not broken, f"links that do not answer 200: {broken}")
            return f"{len(anchors)} in-page and {len(own)} site links resolve"
        except Exception:
            self.screenshot(20, [page])
            raise
        finally:
            page.close()

    # 22 ------------------------------------------------------------------------------------------------------------

    def domain(self) -> str:
        if self.base != DOMAIN:
            raise Skip(f"the base is not {DOMAIN}")
        host = urlsplit(self.base).hostname or ""
        context = ssl.create_default_context(cafile=certifi.where())  # checks the chain and the host name
        with (
            socket.create_connection((host, 443), timeout=10) as raw,
            context.wrap_socket(raw, server_hostname=host) as tls,
        ):
            cert = tls.getpeercert() or {}
        days = (ssl.cert_time_to_seconds(str(cert["notAfter"])) - time.time()) / 86_400
        expect(days > 0, "the certificate has expired")
        expect(self.phone_host is not None and self.invite_host is not None, "cases 6 and 15 must run first")
        expect(self.phone_host == host, f"the QR link is on {self.phone_host}")
        expect(self.invite_host == host, f"the invite link is on {self.invite_host}")
        return f"certificate valid for {days:.0f} more days"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.e2e_live", description=__doc__.splitlines()[0])
    parser.add_argument("base", help="the deployed site, for example https://couchgaffer.com")
    parser.add_argument("--cases", help="which cases to run, for example 1,2,14 or 3-13 (default: all 22)")
    parser.add_argument("--out", type=Path, default=default_out(),
                        help=f"where failure screenshots go (default: {default_out()})")  # fmt: skip
    parser.add_argument(
        "--allow-agents-off",
        action="store_true",
        help="accept a server without agents: checks that need them pass instead of failing",
    )
    parser.add_argument(
        "--allow-no-recaps",
        action="store_true",
        help="accept a server without a recap job: the recap case skips instead of failing",
    )
    parser.add_argument("--headed", action="store_true", help="show the browser")
    args = parser.parse_args(argv)
    base = args.base.rstrip("/")
    try:
        cases = parse_cases(args.cases)
    except ValueError as exc:
        parser.error(str(exc))
    from playwright.sync_api import sync_playwright  # only here: the unit tests import this module without it

    start = time.monotonic()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        run = Run(base, cases, args.out, browser, args.allow_agents_off, args.allow_no_recaps)
        try:
            run.run()
        finally:
            browser.close()
    r = run.results
    print(f"e2e: {r['ok']} ok, {r['skip']} skip, {r['FAIL']} FAIL of {len(cases)} cases against {base} "
          f"in {time.monotonic() - start:.0f} s; agents {'on' if run.health.get('agents') else 'off'}")  # fmt: skip
    return 1 if r["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
