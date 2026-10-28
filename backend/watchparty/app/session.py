"""One viewer's match (#9): simulator → points → moment workflow → cues, streamed to every screen.

Screens run playback_delay_ms behind the stream, so a moment's stat card, explanations and group
decision arrive before the moment is shown.
"""

import asyncio
import copy
import dataclasses
import functools
import logging
import secrets
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from functools import partial
from typing import Any

import httpx
import openai
from agent_framework import Workflow
from agent_framework.exceptions import ChatClientException
from azure.core.exceptions import AzureError
from opentelemetry import trace

from watchparty.agents.base import HostAgent, ManagerAgent, SummaryAgent, pitch_source
from watchparty.agents.foundry import FailedChecks, one_line, tool_failures
from watchparty.agents.scorer import Ledger
from watchparty.agents.summary import PHASES, fallback_summary, summary_problems
from watchparty.app.cues import HEADLINE_MAX, Cues, clash_cues, phone_headline, points_headline, stat_headline
from watchparty.app.workflow import MomentInput, build, fell_back
from watchparty.matchtools.view import Lookups, MatchView
from watchparty.points import PointsEngine, Standings, load_league
from watchparty.sim import Match, play, simulate
from watchparty.sim.gameweek import Gameweek, built
from watchparty.telemetry import span as traced

tracer = trace.get_tracer("watchparty.session")
# A simulated match is deterministic and only ever read (the engine, views and recap bundle read its events
# and frames), so sessions on the same seed share one: most play the demo. Up to 14 MB each otherwise (#49).
simulated = functools.lru_cache(maxsize=4)(simulate)
log = logging.getLogger(__name__)
Message = dict[str, Any]
AGENTS = ("manager_agent", "host_agent", "summary_agent")
NOT_PLAYED = "This gameweek changed. Start it again from the league page."  # a league match that cannot start
# What a late or reconnecting screen is sent (#25): the whistles and cards, every shot (the scoreboard counts the
# goals and sums xG over the shots; in a random match some come in ordinary play, #41, #70) and every key moment's
# events.
KEPT = ("kickoff", "half_time", "full_time", "card", "shot")
# session id, token, view, manager -> the match tools over MCP, for that manager's agent
Connect = Callable[[str, str, str, str], AbstractAsyncContextManager[Any]]


class ToolsUnavailable(Exception):
    """The match tools could not be opened for a summary agent (#84)."""


# The model call failing: the chat client wraps what the OpenAI SDK raises, and the token and transport errors
# beneath it. Anything else is our own code, and must not read as a Foundry problem.
MODEL_OR_API = (ChatClientException, openai.OpenAIError, AzureError, httpx.HTTPError)


def _cause(exc: Exception, elapsed_s: float) -> str:
    if isinstance(exc, TimeoutError):
        return f"timeout after {elapsed_s:.1f} s"
    if isinstance(exc, FailedChecks):
        return "failed the checks twice: " + "; ".join(one_line(p) for p in exc.problems)
    after = f"after {elapsed_s:.1f} s: {type(exc).__name__} {one_line(str(exc))}"
    if isinstance(exc, ToolsUnavailable):
        return f"match tools error {after}"
    if isinstance(exc, MODEL_OR_API):
        return f"model or API error {after}"
    return f"unexpected summary error {after}"


def why_summary_fell_back(exc: Exception, elapsed_s: float, failed_tools: Sequence[str] = ()) -> str:
    """The reason one summary went out plain, for the logs: the smoke counts fallbacks but not their causes (#84).

    failed_tools: tool calls that failed during the run. The model saw them only as "Error: Function failed.",
    so they come first: they are what broke, whatever the run then ended on."""
    cause = _cause(exc, elapsed_s)
    if not failed_tools:
        return cause
    return f"match tools error ({'; '.join(failed_tools)}), then {cause}"


def ws(kind: str, data: dict[str, Any]) -> Message:
    return {"v": 1, "type": kind, "data": data}


@dataclasses.dataclass(frozen=True)
class Armed:
    """One "simulate failure" press: its arm id, and the one manager whose agent it skips (None: every agent)."""

    arm: int
    manager: str | None

    def decision(self) -> dict[str, Any]:
        """For the decision of the moment that spent it (host-decision.schema.json knocked_out)."""
        return {"arm": self.arm} | ({"manager": self.manager} if self.manager else {})


@dataclasses.dataclass
class MomentState:
    """One key moment from its arrival until it is decided (#149). Moments can be in flight together, so each holds
    its own stakes and bids: a bid finds its moment by its moment id, never by which moment came last."""

    moment_id: str
    stakes: dict[str, dict[str, Any]]  # by manager
    deadline: float  # time.monotonic() by which it must be decided: before the screens show it (#26)
    armed: Armed | None  # the chaos flag: the "simulate failure" press this moment runs under, if any
    spends: bool  # whether it will use that press up, so a moment arriving meanwhile does not run under it too
    turn: asyncio.Future[None]  # its arbiter turn, ended once its decision has gone out
    after: asyncio.Future[None] | None  # the earlier moment's turn, which its scorer waits for
    bids: dict[str, dict[str, Any]] = dataclasses.field(default_factory=dict)  # by manager, declines left out


@dataclasses.dataclass
class Charged:
    """A manager, host or summary agent as a session calls it: each call marks the match paid as it starts (#72)."""

    agent: Any
    pay: Callable[[], None]

    async def bid(self, stake: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        self.pay()
        return await self.agent.bid(stake, context)  # type: ignore[no-any-return]

    async def decide(self, moment: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
        self.pay()
        return await self.agent.decide(moment, candidates)  # type: ignore[no-any-return]

    async def summarise(self, manager: dict[str, Any], phase: str, tools: Any, served: dict[str, dict[str, Any]],
                        names: list[str], rulebook: Any = None, **gameweek: bool) -> dict[str, Any]:  # fmt: skip
        self.pay()
        return await self.agent.summarise(manager, phase, tools, served, names, rulebook,  # type: ignore[no-any-return]
                                          **gameweek)  # fmt: skip


class Session:
    def __init__(
        self, seed: int, manager: str, style: str, lang: str, *, speed: float = 30,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        manager_agent: ManagerAgent | None = None, host_agent: HostAgent | None = None,
        manager_deadline_s: float = 4.0, host_deadline_s: float = 2.5, decision_budget_s: float = 4.5,
        summary_agent: SummaryAgent | None = None, mcp_connect: Connect | None = None, summary_deadline_s: float = 40.0,
        on_full_time: Callable[["Session"], Awaitable[None]] | None = None, abandon_after_s: float = 30,
        gameweek: bool = False, league: dict[str, Any] | None = None,
        on_played: Callable[["Session"], Awaitable[None]] | None = None,
        on_kickoff: Callable[["Session"], Awaitable[bool]] | None = None,
        on_released: Callable[["Session"], Awaitable[None]] | None = None,
    ) -> None:  # fmt: skip
        self.id, self.phone_token = secrets.token_urlsafe(16), secrets.token_urlsafe(16)
        self.speed, self.sleep, self.on_full_time = speed, sleep, on_full_time
        if league is not None and gameweek:
            raise ValueError("a stored league plays a single match")
        # A stored league's gameweek (#178): its squads are fixed as the match starts (on_kickoff), its results are
        # recorded at full time, before the recaps (on_played), and a match that never starts, is abandoned or fails
        # lets go of the gameweek (on_released, from here or from the store's drop)
        self.on_played, self.on_kickoff, self.on_released = on_played, on_kickoff, on_released
        self.on_abandoned: Callable[[], None] | None = None  # the store refunds an unpaid agent slot (#50, #72)
        self.agents_on = any((manager_agent, host_agent, summary_agent))
        self.called_agent = False  # set as the first agent call starts (#72)
        manager_agent, host_agent, summary_agent = map(self._charged, (manager_agent, host_agent, summary_agent))
        self.summary_agent, self.mcp_connect, self.summary_deadline_s = summary_agent, mcp_connect, summary_deadline_s
        self.abandon_after_s, self.abandoned = abandon_after_s, False
        # A gameweek (#94) plays as one stream too: its meta is the `gameweek` message's, its events and frames are
        # every fixture's, merged on one clock (sim/gameweek.py).
        self.gameweek: Gameweek | None = built(seed, speed) if gameweek else None
        if self.gameweek is None:
            self.match = simulated(seed, speed)
        else:
            self.match = Match(self.gameweek.meta, self.gameweek.events, self.gameweek.frames)
        if league is None:
            league = load_league() if self.gameweek is None else load_league("league_gw_v1.json")
        self.league = copy.deepcopy(league)  # a stored league's (#178), or today's fixed one
        self.judge = manager
        for m in self.league["managers"]:
            m["is_bot"] = m["id"] != manager
            if m["id"] == manager:
                m["style"], m["lang"] = style, lang
        # Whoever the judge plays is "You" this session; the agents address that name as the viewer.
        next(m for m in self.league["managers"] if m["id"] == manager)["display_name"] = "You"
        # by fixture id; a single match is one fixture with none
        metas = [self.match.meta] if self.gameweek is None else [f.meta for f in self.gameweek.fixtures]
        self.fixtures: dict[str | None, dict[str, Any]] = {meta.get("fixture_id"): meta for meta in metas}
        if self.gameweek is None:
            self.engine = PointsEngine(self.match.meta, self.league)
            self.engines: dict[str | None, PointsEngine] = {None: self.engine}
        else:  # one engine a fixture, all on one table: starting points counted once (#150)
            standings = Standings(self.league)
            self.engines = {f: PointsEngine(meta, self.league, standings=standings, fixture_id=f)
                            for f, meta in self.fixtures.items()}  # fmt: skip
            self.engine = self.engines["fx_1"]  # for its table(), which is the gameweek's: the Standings is shared
        # Every club and player of every fixture: six clubs and 66 players in a gameweek
        self.names = {p["id"]: p["name"] for meta in metas for s in ("home", "away") for p in meta[s]["players"]}
        self.managers = {m["id"]: m for m in self.league["managers"]}
        clubs = [meta[s]["name"] for meta in metas for s in ("home", "away")]
        self.allowed_names = [*self.names.values(), *clubs, *(m["display_name"] for m in self.managers.values())]
        self.climaxes = {e["moment_id"]: e for e in self.match.events if e.get("climax")}
        # A gameweek's fixtures, as play reaches them: the latest frame of each, the whistles each has blown, and
        # those that are over before full time (a failure, or the session ending)
        self.latest_frames: dict[str, dict[str, Any]] = {}
        self.blown: dict[str, dict[str, int]] = {phase: {} for phase in PHASES}  # phase -> fixture -> t
        self.phased: set[str] = set()  # the gameweek's own half time and full time, once each
        self.stopped: set[str] = set()
        self.failing: set[str] = set()  # failed, its whistle not yet blown
        self.next_seq = len(self.match.events)  # for a whistle the session blows itself: past every event's seq
        self.ledger, self.cues = Ledger(), Cues()
        self.subscribers: defaultdict[str, set[asyncio.Queue[Message | None]]] = defaultdict(set)
        self.cues_only: set[asyncio.Queue[Message | None]] = set()  # subscribers that take their cues alone
        self.key_events: list[dict[str, Any]] = []  # what a late screen needs (KEPT)
        self.latest_frame: dict[str, Any] | None = None
        self.last_seq = 0  # of the last event sent
        self.log: list[Message] = []  # every cue and decision: the recap job's edit list (#15)
        self.stakes_log: list[list[dict[str, Any]]] = []  # every key moment's stakes, for the match tools
        self.views: dict[str, MatchView] = {}  # frozen at half time and full time
        self.lookups = Lookups()  # this match's Foundry IQ budget and answers, across all its views (#34)
        self.summary_tasks: list[asyncio.Task[None]] = []
        # Summaries have no latency limit, but they share the model with the live bids: at most three at once,
        # so the half-time burst does not push second-half bids past their deadline.
        self.summary_slots = asyncio.Semaphore(3)
        self.stat_cue: dict[tuple[str, str], str] = {}  # (moment, screen) -> cue_id it upgrades
        self.moments: dict[str, MomentState] = {}  # key moments in flight, by moment id (#149)
        self.moment_tasks: list[asyncio.Task[None]] = []
        self.last_turn: asyncio.Future[None] | None = None  # the latest moment's arbiter turn
        self.armed: Armed | None = None  # the last "simulate failure" press not yet spent (#90)
        self.arms = 0  # presses so far: each gets the next arm id
        self.task: asyncio.Task[None] | None = None
        self.finished_at: float | None = None
        # Bids and host share one budget from the moment's arrival, under the screens' 5 s playback delay (#26,
        # docs/decisions/0003-timing.md).
        self.decision_budget_s = decision_budget_s
        self.deadlines = manager_deadline_s, host_deadline_s
        self.agents = manager_agent, host_agent
        self.workflow = self._workflow()
        # One workflow cannot run twice at once (agent-framework refuses a second run), so each moment in flight
        # takes an idle one, and another is built when none is idle (#149).
        self.idle = [self.workflow]

    def _workflow(self) -> Workflow:
        return build(self.league["managers"], *self.agents, self.ledger, self._on_bid, *self.deadlines, self._turn)

    async def _turn(self, moment_id: str) -> None:
        """Until the moment that arrived before this one has ended its turn. Shielded: a moment cancelled while it
        waits must not cancel the turn later moments wait on."""
        after = self.moments[moment_id].after
        if after is not None:
            await asyncio.shield(after)

    def _charged(self, agent: Any) -> Any:
        return None if agent is None else Charged(agent, partial(setattr, self, "called_agent", True))

    @property
    def paid(self) -> bool:
        """This match has started a model call or a Foundry IQ lookup, so abandoning it gives nothing back (#72)."""
        return self.called_agent or self.lookups.used > 0

    def run_on_code(self) -> None:
        """No agents for this match: plain stats, the scorer's picks and plain summaries (#22, #39)."""
        self.agents, self.summary_agent, self.agents_on = (None, None), None, False
        self.workflow = self._workflow()
        self.idle = [self.workflow]

    # --- screens --------------------------------------------------------------------------------------

    def subscribe(self, screen: str, cues_only: bool = False) -> asyncio.Queue[Message | None]:
        """A screen's queue, starting with the match so far: the whistles and key moments, where everyone stands
        now, and the screen's own cues and the decisions. A phone that scans the QR code late, or a screen that
        reconnects, picks up at once (#25). Bounded by the match itself (about 3,500 messages). None in the
        queue means the session is over and gone: close the screen.

        cues_only: the screen's own cues and nothing else, for a page that has the match from another screen;
        the judge's page would otherwise take every frame six times (#49)."""
        q: asyncio.Queue[Message | None] = asyncio.Queue()
        if cues_only and self.gameweek is not None:
            # First on every socket of a gameweek: a screen files whatever comes before it as a single match (#164)
            q.put_nowait(ws("gameweek", self.match.meta))
        if cues_only:
            self.cues_only.add(q)
            for entry in self.log:
                if entry.get("screen") == screen:
                    q.put_nowait(ws("cue", entry))
            self.subscribers[screen].add(q)
            return q
        q.put_nowait(ws("meta" if self.gameweek is None else "gameweek", self.match.meta))
        q.put_nowait(ws("league", self.league))
        for event in self.key_events:
            q.put_nowait(ws("event", event))
        if self.gameweek is not None:  # every fixture's pitch, not only the one that moved last
            for frame in self.latest_frames.values():
                q.put_nowait(ws("frame", frame))
        elif self.latest_frame is not None:
            q.put_nowait(ws("frame", self.latest_frame))
        for entry in self.log:
            if "chosen_manager" in entry:
                q.put_nowait(ws("decision", entry))
            elif entry.get("screen") == screen:
                q.put_nowait(ws("cue", entry))
        self.subscribers[screen].add(q)
        return q

    def unsubscribe(self, screen: str, q: asyncio.Queue[Message | None]) -> None:
        self.subscribers[screen].discard(q)
        self.cues_only.discard(q)

    def close_screens(self) -> None:
        """The session is being dropped: every screen still connected is closed (#33)."""
        for queues in self.subscribers.values():
            for q in queues:
                q.put_nowait(None)

    def connections(self) -> int:
        return sum(len(qs) for qs in self.subscribers.values())

    def _send(self, message: Message, screen: str | None = None) -> None:
        """To one screen's queues (its cues), or the match to every screen that takes it."""
        targets = [screen] if screen else list(self.subscribers)
        for s in targets:
            for q in self.subscribers[s]:
                if screen or q not in self.cues_only:
                    q.put_nowait(message)

    def _cue(self, cue: dict[str, Any]) -> dict[str, Any]:
        """A cue about a gameweek's moment names its fixture; one about the whole gameweek (the half-time and
        full-time table, a summary) names none."""
        if fixture := self.climaxes.get(cue.get("moment_id", ""), {}).get("fixture_id"):
            cue["fixture_id"] = fixture
        self.log.append(cue)
        self._send(ws("cue", cue), cue["screen"])
        return cue

    def live_view(self) -> MatchView:
        """In a gameweek, every fixture's meta and engine, on the one table (#152)."""
        t = max(e.t for e in self.engines.values())
        if self.gameweek is None:
            view = MatchView(self.match.meta, self.league, self.engine, self.match.events, self.stakes_log, t)
        else:
            engines = {str(f): e for f, e in self.engines.items()}
            view = MatchView(self.match.meta, self.league, self.engine, self.match.events, self.stakes_log, t,
                             fixtures=list(self.fixtures.values()), engines=engines)  # fmt: skip
        view.lookups = self.lookups
        return view

    def view(self, at: str | None = None) -> MatchView | None:
        """The match tools' view: live, or frozen at "half_time" or "full_time" once that whistle has gone."""
        return self.live_view() if at is None else self.views.get(at)

    def _frozen(self, t: int) -> MatchView:
        """The view the summaries are written from, frozen at t."""
        return self.live_view().frozen(t)

    def set_style(self, style: str) -> None:
        """The judge's style toggle: their next explanations and summaries follow it (rule 4.1(e))."""
        if style not in ("analyst", "casual"):
            raise ValueError("style must be analyst or casual")
        self.managers[self.judge]["style"] = style

    def simulate_failure(self, manager: str | None = None) -> int:
        """The live failure-recovery beat. With no manager, the next moment with agents in it falls back to code.
        With one, only that manager's agent is skipped, on the next moment it bids in; the others and the host
        run as usual (#90). Pressing again replaces what is armed. Returns the press's arm id, which comes back
        on the decision of the moment that spends it."""
        if manager is not None and manager not in self.managers:
            raise ValueError("no such manager in this league")
        self.arms += 1
        self.armed = Armed(self.arms, manager)
        return self.arms

    @property
    def chaos(self) -> str | None:
        """What is armed: "all", one manager's id, or None."""
        return None if self.armed is None else self.armed.manager or "all"

    @staticmethod
    def _spent(armed: Armed, bids: dict[str, Any]) -> bool:
        """Whether a moment with these bids uses up `armed`: everyone's needs any bid, one manager's needs theirs."""
        return bool(bids) if armed.manager is None else armed.manager in bids

    @staticmethod
    def _will_spend(armed: Armed, stakes: dict[str, dict[str, Any]]) -> bool:
        """_spent, known on arrival: a manager the press skips gets the plain stat, which is a bid unless the moment
        is worth 0 IP to them (plain_bid)."""
        bidders = {m for m, s in stakes.items() if s["above_threshold"] and s["delta_points"]}
        return bool(bidders) if armed.manager is None else armed.manager in bidders

    # --- the match ------------------------------------------------------------------------------------

    def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self.run())

    async def run(self) -> None:
        """Plays the match to full time, then the recaps. A match nobody watches stops after abandon_after_s
        with no recap; a match that fails ends on every screen. Either way the seat is freed (#22)."""
        if not await self._kicked_off():
            self._not_played()
            return
        watchdog = asyncio.create_task(self._watch_screens())
        completed = False
        try:
            async for message in play(self.match, self.speed, self.sleep):
                if self.abandoned:
                    break
                if message["type"] == "meta":
                    continue  # each screen got it on subscribing
                if self.gameweek is not None:
                    self._on_gameweek(message)
                    continue
                self._send(message)
                if message["type"] == "frame":
                    self.latest_frame = message["data"]
                    continue
                self._on_event(message["data"])
            await self._unless_abandoned(asyncio.gather(*self.moment_tasks), watchdog)
            if not self.abandoned:  # the full-time summaries are the recap captions (#15)
                await self._unless_abandoned(asyncio.gather(*self.summary_tasks), watchdog)
                completed = not self.abandoned
        except Exception:
            log.exception("match %s failed; ending it on every screen", self.id)
            self._end_screens()
        finally:
            watchdog.cancel()
            for task in (*self.moment_tasks, *self.summary_tasks):
                task.cancel()  # a no-op for the ones already done
            self.finished_at = time.monotonic()
        if self.abandoned:
            self._end_screens()  # a screen that comes back sees the match ended, not a frozen pitch (#70)
        if self.abandoned and self.on_abandoned:
            self.on_abandoned()
        # the recaps still go out either way; an unrecorded gameweek stays open for its creator to record
        await self._league_hook(
            self.on_played if completed else self.on_released,
            "record its league's results" if completed else "release its league's gameweek",
        )
        if completed and self.on_full_time:
            await self.on_full_time(self)
        self.drop_frames()

    async def _kicked_off(self) -> bool:
        """A stored league's match starts only if its gameweek is still this session's and its squads are still the
        picks (#178); otherwise it would play a match that cannot count, or overwrite a later pick."""
        if self.on_kickoff is None:
            return True
        try:
            return await self.on_kickoff(self)
        except Exception:
            log.exception("match %s could not fix its league's squads; not playing it", self.id)
            return False

    def _not_played(self) -> None:
        """Every screen is told the match will not be played, gets the whistle, and is closed."""
        for screen in ("group", f"phone:{self.judge}"):
            self._cue(self.cues.make(screen, "stat", "code", 0, {"headline": NOT_PLAYED}))
        self._end_screens()
        self.finished_at = time.monotonic()
        self.close_screens()

    async def _league_hook(self, hook: Callable[["Session"], Awaitable[None]] | None, what: str) -> None:
        if hook is None:
            return
        try:
            await hook(self)
        except Exception:  # the match goes on, or its recaps do
            log.exception("match %s could not %s", self.id, what)

    def drop_frames(self) -> None:
        """The recap bundle has its frames now; a lingering session keeps its link, not the frames (#49): about
        14 MB a match, 42 MB a gameweek. Copies: the shared match and gameweek (cached) are never changed."""
        self.match = dataclasses.replace(self.match, frames=[])
        if self.gameweek is not None:
            fixtures = [dataclasses.replace(m, frames=[]) for m in self.gameweek.fixtures]
            self.gameweek = dataclasses.replace(self.gameweek, frames=[], fixtures=fixtures)

    @staticmethod
    async def _unless_abandoned(work: Awaitable[Any], watchdog: asyncio.Task[None]) -> None:
        """Waits for the last decisions or summaries, unless the last screen leaves first: then they are cancelled,
        so nobody's match spends on models or a recap (#44). The watchdog only ends when it abandons the match."""
        future = asyncio.ensure_future(work)
        await asyncio.wait({future, watchdog}, return_when=asyncio.FIRST_COMPLETED)
        if future.done():
            future.result()  # a failure ends the match on every screen, as before
        else:
            future.cancel()

    def _on_event(self, event: dict[str, Any]) -> None:
        self._fixture_event(event)
        if event["type"] in PHASES:
            self._phase(event["type"], event["t"])

    def _fixture_event(self, event: dict[str, Any]) -> None:
        """One event of one fixture: its points, and the key moment it may end."""
        self.last_seq = event["seq"]
        if event["type"] in KEPT or event.get("moment_id"):
            self.key_events.append(event)
        stakes = self.engines[event.get("fixture_id")].feed(event)
        if not stakes:
            return
        if stakes[0]["moment_id"] in self.moments:
            # Its bids, turn and state would be the other's (#149). Ids are unique per match and per gameweek
            # (sim/gameweek.py renumbers them), so this is a fault: it costs this moment, not the match.
            log.warning("moment %s of match %s skipped: one with its id is in flight", stakes[0]["moment_id"], self.id)
            return
        self.stakes_log.append(stakes)
        self._stat_cues(event, stakes)
        moment = self._arrive(stakes, time.monotonic() + self.decision_budget_s)
        self.moment_tasks.append(asyncio.create_task(self._key_moment(moment)))

    def _phase(self, phase: str, t: int) -> None:
        """Half time or full time: the view the summaries are written from, the table, and the summaries."""
        self.views[phase] = self._frozen(t)
        table = {"headline": PHASES[phase], "table": self.engine.table()}  # matches the summaries
        self._cue(self.cues.make("group", "table", "code", t, table))
        self.summary_tasks.append(asyncio.create_task(self._summaries(phase)))

    # --- a gameweek (#94) -----------------------------------------------------------------------------

    def _on_gameweek(self, message: Message) -> None:
        """One message of a gameweek's merged stream. A fixture whose handling raises stops alone; a failure in
        the gameweek's own half time or full time costs its table and summaries, and play goes on."""
        data = message["data"]
        fixture = data["fixture_id"]
        if fixture in self.stopped:
            return  # over: nothing later from it reaches a screen
        if fixture in self.failing:
            # Its whistle takes the place of its next event, seq and all: seq stays strictly increasing across
            # the gameweek (match-event.schema.json), a few match seconds after the failure at most
            if message["type"] == "event":
                self.failing.discard(fixture)
                try:
                    self._whistle(fixture, data["t"], data["seq"])
                except Exception:  # it is over either way: the others play on
                    log.exception("the whistle for fixture %s of match %s failed", fixture, self.id)
                    self.stopped.add(fixture)
                for phase in PHASES:
                    self._phase_if_due(phase, data["t"])
            return
        self._send(message)
        if message["type"] == "frame":
            self.latest_frame = self.latest_frames[fixture] = data
            return
        try:
            self._fixture_event(data)
        except Exception:
            log.exception("fixture %s of match %s failed; it stops, the others play on", fixture, self.id)
            self._stop(fixture, data)
            return
        if data["type"] in PHASES:
            self.blown[data["type"]][fixture] = data["t"]
            self._phase_if_due(data["type"], data["t"])

    def _phase_if_due(self, phase: str, t: int) -> None:
        """The gameweek's half time or full time, once, as soon as every fixture still playing has blown it; with
        every fixture stopped, at once, so the table and the summaries still come."""
        blown = self.blown[phase]
        if phase in self.phased or any(f not in blown for f in self.fixtures if f not in self.stopped):
            return
        self.phased.add(phase)
        try:
            self._phase(phase, t)
        except Exception:  # the table or summaries are lost, not the three fixtures still playing
            log.exception("the gameweek's %s of match %s failed; play goes on", PHASES[phase].lower(), self.id)

    def _stop(self, fixture: str, event: dict[str, Any]) -> None:
        """A fixture whose handling raised: its screens get its full-time whistle in place of its next event, its
        points so far stay in the table, and the gameweek's whistles stop waiting for it then (#94, failure of one
        fixture). Its moments still in flight go to no screen."""
        if event["type"] != "full_time":
            self.failing.add(fixture)
            return
        self.stopped.add(fixture)  # the screens have its whistle already
        for phase in PHASES:
            self._phase_if_due(phase, event["t"])

    def _over(self, moment_id: str) -> bool:
        """Whether the moment's fixture failed or stopped: its decision and cues would land after its whistle."""
        fixture = self.climaxes[moment_id].get("fixture_id")
        return fixture in self.stopped or fixture in self.failing

    def _whistle(self, fixture: str, t: int = 0, seq: int | None = None) -> None:
        """A full-time whistle the session blows for one fixture, which is then over. Without a seq, past every
        event of the gameweek: the session is ending, so none follows it."""
        frame = self.latest_frames.get(fixture)
        t = max(t, frame["t"] if frame else 0)
        if seq is None:
            seq, self.next_seq = self.next_seq, self.next_seq + 1
        whistle = {"seq": seq, "t": t, "type": "full_time", "team": "home", "x": 0.5, "y": 0.5,
                   "fixture_id": fixture}  # fmt: skip
        self.stopped.add(fixture)
        self.key_events.append(whistle)
        self._send(ws("event", whistle))

    def _end_screens(self) -> None:
        """Tells every screen the match is over, as a full-time whistle where it stopped. Once: a match abandoned
        during its full-time summaries already has its whistle. In a gameweek, each fixture still playing gets its
        own: a whistle without a fixture is no fixture's."""
        if self.gameweek is not None:
            for fixture in self.fixtures:
                if fixture is not None and fixture not in self.stopped and fixture not in self.blown["full_time"]:
                    self._whistle(fixture)
            return
        last = self.key_events[-1] if self.key_events else {"seq": 0, "t": 0}
        if last.get("type") == "full_time":
            return
        t = max(last["t"], self.latest_frame["t"] if self.latest_frame else 0)
        # Past every event sent, not just the last key one: screens drop a seq they have seen as a repeat (#49)
        whistle = {"seq": self.last_seq + 1, "t": t, "type": "full_time", "team": "home", "x": 0.5, "y": 0.5}
        self.key_events.append(whistle)
        self._send(ws("event", whistle))

    async def _watch_screens(self) -> None:
        """Stops a match once no screen has watched it for abandon_after_s: it would only spend on models."""
        idle_since: float | None = None
        while True:
            await asyncio.sleep(min(1.0, self.abandon_after_s / 4))
            if self.connections():
                idle_since = None
            elif idle_since is None:
                idle_since = time.monotonic()
            elif time.monotonic() - idle_since >= self.abandon_after_s:
                log.info("match %s abandoned: no screen for %.0f s", self.id, self.abandon_after_s)
                self.abandoned = True
                return

    async def _key_moment(self, moment: MomentState) -> None:
        """Decides one key moment by its own deadline, started as it arrives (#149). Real moments are 10 s apart
        and a decision takes at most 4.5 s, so they rarely overlap; when they do, the later one's bids still get
        their full time, and its turn with the scorer and host comes once the earlier one is decided (#26)."""
        # A span of its own: matches start from the WebSocket handler, which is not traced, so without it a
        # failure here would have no span to land on (#111).
        with traced(tracer, "key_moment", {"session_id": self.id, "moment_id": moment.moment_id}) as span:
            try:
                await self._decide(moment)
            except Exception as exc:  # the moment keeps its stat card; the match and its recap go on (#49)
                fell_back(f"moment {moment.moment_id}", exc, span)

    def _stat_cues(self, climax: dict[str, Any], stakes: list[dict[str, Any]]) -> None:
        mid, t = climax["moment_id"], climax["t"]
        group = self.cues.make("group", "stat", "code", t, {"headline": self._stat_headline(climax)}, mid)
        self.stat_cue[(mid, "group")] = self._cue(group)["cue_id"]
        for stake in stakes:
            if stake["delta_points"]:
                screen = f"phone:{stake['manager']}"
                lang = self.managers[stake["manager"]]["lang"]  # each phone in its own manager's language (#108)
                payload = {"headline": points_headline(stake["delta_points"], lang), "manager": stake["manager"]}
                self.stat_cue[(mid, screen)] = self._cue(self.cues.make(screen, "stat", "code", t, payload, mid))[
                    "cue_id"
                ]
        goal = climax["type"] == "shot" and climax["outcome"] == "goal"
        if goal or any(s["rank_before"] != s["rank_after"] for s in stakes):
            rows = {s["manager"]: s["delta_points"] for s in stakes}
            table = [r | {"delta": rows[r["manager"]]} for r in self.engine.table()]
            self._cue(
                self.cues.make("group", "table", "code", t, {"headline": "Mini-league table", "table": table}, mid)
            )

    def _stat_headline(self, climax: dict[str, Any]) -> str:
        """The group's plain stat card. In a gameweek it names both clubs, so the room knows which match it is;
        their short names when the full ones would not fit the card."""
        headline = stat_headline(climax, self.names)
        fixture = climax.get("fixture_id")
        if fixture is None:
            return headline
        home, away = self.fixtures[fixture]["home"], self.fixtures[fixture]["away"]
        named = f"{home['name']} v {away['name']}: {headline}"
        return named if len(named) <= HEADLINE_MAX else f"{home['short_name']} v {away['short_name']}: {headline}"

    def _names_for(self, moment_id: str) -> list[str]:
        """The names a moment's lines may use: in a gameweek, its own fixture's two clubs and 22 players and the
        managers, so no line about it names a player from another match. A single match's: all of them."""
        fixture = self.climaxes[moment_id].get("fixture_id")
        if fixture is None:
            return self.allowed_names
        meta = self.fixtures[fixture]
        own = [meta[side]["name"] for side in ("home", "away")]
        own += [p["name"] for side in ("home", "away") for p in meta[side]["players"]]
        return [*own, *(m["display_name"] for m in self.managers.values())]

    def _gameweek_context(self, moment_id: str) -> tuple[dict[str, Any], ...]:
        """What a manager agent knows of the gameweek as a moment is decided: every fixture's clubs and score, its
        players, and which fixture the moment is in. Empty for a single match."""
        if self.gameweek is None:
            return ()
        here = self.climaxes[moment_id].get("fixture_id")
        out = []
        for fixture, meta in self.fixtures.items():
            engine = self.engines[fixture]
            goals = {side: sum(n for p, n in engine.goals.items() if engine.side_of[p] == side)
                     for side in ("home", "away")}  # fmt: skip
            out.append({"fixture_id": fixture, "home": meta["home"]["name"], "away": meta["away"]["name"],
                        "home_goals": goals["home"], "away_goals": goals["away"], "this_moment": fixture == here,
                        "players": frozenset(engine.side_of)})  # fmt: skip
        return tuple(out)

    def _on_bid(self, bid: dict[str, Any]) -> None:
        """A manager's explanation goes to their phone as soon as it is ready. A declined bid sends nothing and is
        not a candidate for the screen (#88)."""
        moment = self.moments.get(bid["moment_id"])
        if bid["status"] == "declined" or moment is None:  # None: no longer in flight
            return
        if self._over(bid["moment_id"]):  # a gameweek fixture that failed meanwhile
            return
        moment.bids[bid["manager"]] = bid
        mid, screen, stake = bid["moment_id"], f"phone:{bid['manager']}", moment.stakes[bid["manager"]]
        cited = {e["id"]: e for e in stake["evidence"]}
        angle = next((f for f in stake.get("facts") or [] if f["id"] == bid.get("angle")), None)
        shown = list(dict.fromkeys([*bid["evidence_ids"], *(angle["evidence_ids"] if angle else [])]))
        lang = self.managers[bid["manager"]]["lang"]  # the same language as the stat card it replaces (#108)
        payload: dict[str, Any] = {
            "headline": phone_headline(stake, lang), "body": bid["explanation"], "lang": bid["lang"],
            "style": bid["style"], "manager": bid["manager"],
            # the analyst's evidence drawer: what the text cites, and the fact it was written around (#88)
            "evidence": [cited[i] for i in shown if i in cited],
        }  # fmt: skip
        if bid.get("explanation_en"):
            payload["body_en"] = bid["explanation_en"]
        source = "fallback" if bid["status"] == "fallback" else "manager"
        replaces = self.stat_cue.get((mid, screen))
        self._cue(self.cues.make(screen, "explanation", source, self.climaxes[mid]["t"], payload, mid, replaces))

    async def _summaries(self, phase: str) -> None:
        await asyncio.gather(*(self._summary(m, phase) for m in self.league["managers"]))

    async def _ask(self, agent: SummaryAgent, connect: Connect, manager: dict[str, Any], phase: str) -> dict[str, Any]:
        async with AsyncExitStack() as stack:
            try:
                tools = await stack.enter_async_context(connect(self.id, self.phone_token, phase, manager["id"]))
            except Exception as exc:  # told apart from a model error in the logs (#84)
                raise ToolsUnavailable(f"{type(exc).__name__} {exc}") from exc
            view = self.views[phase]
            mine = view.rulebook.setdefault(manager["id"], {})  # what this manager's agent was given (#37)
            served = view.served_to(manager["id"])  # what this manager's agent was given (#54)
            gameweek = {"gameweek": True} if self.gameweek is not None else {}  # a single match's call is as before
            return await agent.summarise(manager, phase, tools, served, self.allowed_names, mine, **gameweek)

    async def _summary(self, manager: dict[str, Any], phase: str) -> None:
        """One manager's summary on their phone: the agent's if it passes every check, else plain from code. In a
        span of its own, so its fallback lands on it and the agent's own spans nest under it (#111)."""
        ids = {"session_id": self.id, "manager": manager["id"], "phase": phase}
        with traced(tracer, "summary", ids) as span:
            span.set_attribute("source", await self._write_summary(manager, phase, span))

    async def _write_summary(self, manager: dict[str, Any], phase: str, span: trace.Span) -> str:
        view = self.views[phase]
        answer = None
        if self.summary_agent is not None and self.mcp_connect is not None:
            async with self.summary_slots:  # the deadline starts once a slot is free
                start, failed_tools = time.monotonic(), list[str]()
                tool_failures.set(failed_tools)  # this summary's own task: each manager's are collected apart
                try:
                    ask = self._ask(self.summary_agent, self.mcp_connect, manager, phase)
                    answer = await asyncio.wait_for(ask, self.summary_deadline_s)
                except Exception as exc:  # timeout, model, network or tool error: the plain summary goes out
                    why = why_summary_fell_back(exc, time.monotonic() - start, failed_tools)
                    fell_back(f"{PHASES[phase].lower()} summary for {manager['id']}", exc, span, why)
                    answer = None
        mine, served = view.rulebook.get(manager["id"], {}), view.served_to(manager["id"])
        problems = (
            summary_problems(answer, manager, served, self.allowed_names, mine, self.gameweek is not None)
            if isinstance(answer, dict)
            else []
        )
        if problems:
            why = "failed the session's checks: " + "; ".join(one_line(p) for p in problems)
            what = f"{PHASES[phase].lower()} summary for {manager['id']}"
            fell_back(what, FailedChecks(problems), span, why)
        if not isinstance(answer, dict) or problems:
            answer = fallback_summary(view, manager, phase)
        fallback = answer.get("status") == "fallback"
        payload = {"headline": PHASES[phase], "body": answer["text"], "lang": manager["lang"],
                   "style": manager["style"], "manager": manager["id"],
                   "evidence": [served[i] for i in answer["evidence_ids"] if i in served]}  # fmt: skip
        if answer.get("text_en"):
            payload["body_en"] = answer["text_en"]
        if cited := [{"id": i, "title": mine[i]["title"]} for i in answer.get("rulebook_ids") or [] if i in mine]:
            payload["rulebook"] = cited[:5]  # grounded in the league rulebook, from Foundry IQ (#18)
        screen = f"phone:{manager['id']}"
        source = "fallback" if fallback else "manager"
        self._cue(self.cues.make(screen, "summary", source, view.t, payload))
        return source

    def send_recaps(self, links: dict[str, str]) -> None:
        """Each manager's recap video link, on their own phone (#15)."""
        t = self.views["full_time"].t
        for manager, url in links.items():
            payload = {"headline": "Your recap is ready", "recap_url": url, "manager": manager}
            self._cue(self.cues.make(f"phone:{manager}", "recap_ready", "code", t, payload))

    def _arrive(self, moment_stakes: list[dict[str, Any]], deadline: float) -> MomentState:
        """A key moment reaches the server: it takes the next arbiter turn and, unless a moment still in flight
        will use it up, runs under the press armed now. A press made after this is kept for a later moment."""
        mid = moment_stakes[0]["moment_id"]
        if mid in self.moments:  # its bids, turn and state would be the other's; ids are unique per match
            raise ValueError(f"moment {mid} is already in flight")
        stakes = {s["manager"]: s for s in moment_stakes}
        armed = self.armed
        if armed is not None and any(m.armed is armed and m.spends for m in self.moments.values()):
            armed = None  # an earlier moment in flight spends it: chaos hits one moment, not every one in flight
        spends = armed is not None and self._will_spend(armed, stakes)
        after, self.last_turn = self.last_turn, asyncio.get_running_loop().create_future()
        moment = MomentState(mid, stakes, deadline, armed, spends, self.last_turn, after)
        self.moments[mid] = moment
        return moment

    @staticmethod
    def _end_turn(moment: MomentState) -> None:
        """Lets the next moment take its turn, but never before the earlier one has ended its own: a moment that
        failed, or had no bids, can finish ahead of the one that arrived before it."""

        def end(_: object = None) -> None:
            if not moment.turn.done():
                moment.turn.set_result(None)

        if moment.after is None or moment.after.done():
            end()
        else:
            moment.after.add_done_callback(end)

    async def _run(self, moment: MomentInput) -> list[Any]:
        """The moment's workflow run, on a workflow no other moment is running."""
        workflow = self.idle.pop() if self.idle else self._workflow()
        outputs: list[Any] = (await workflow.run(moment)).get_outputs()
        # Only after a run that finished: one that raised or was cancelled leaves messages in flight, and
        # agent-framework refuses every later run on that workflow, so it is dropped.
        self.idle.append(workflow)
        return outputs

    async def decide(self, moment_stakes: list[dict[str, Any]], deadline: float | None = None) -> dict[str, Any] | None:
        """Run the moment workflow for one key moment, to be decided by `deadline` (time.monotonic())."""
        deadline = deadline if deadline is not None else time.monotonic() + self.decision_budget_s
        return await self._decide(self._arrive(moment_stakes, deadline))

    async def _decide(self, state: MomentState) -> dict[str, Any] | None:
        try:
            return await self._decided(state)
        finally:
            del self.moments[state.moment_id]  # its stakes and bids go with it (#149)
            self._end_turn(state)

    async def _decided(self, state: MomentState) -> dict[str, Any] | None:
        mid, armed = state.moment_id, state.armed
        summary = stat_headline(self.climaxes[mid], self.names)
        everyone = armed is not None and armed.manager is None
        target = armed.manager if armed is not None and armed.manager else ""
        first = next(iter(state.stakes.values()))
        moment = MomentInput(mid, first["t"], summary, state.stakes, state.deadline, everyone, self._names_for(mid),
                             self.names, session_id=self.id, knocked_out=target,
                             gameweek=self._gameweek_context(mid))  # fmt: skip
        ids = {"moment_id": mid, "chaos": everyone, "knocked_out": target, "session_id": self.id}
        with traced(tracer, "moment", ids):
            outputs = await self._run(moment)
        result: dict[str, Any] | None = outputs[0] if outputs else None
        if result is None or self._over(mid):  # over: a gameweek fixture that failed while this moment was decided
            return None
        if fixture := self.climaxes[mid].get("fixture_id"):
            result["fixture_id"] = fixture
        if armed is not None and self._spent(armed, state.bids):
            result["knocked_out"] = armed.decision()  # the judge's page clears its label on this arm id alone
            if self.armed is armed:
                self.armed = None  # a press made while this moment ran has its own arm id and stays
        self.log.append(result)  # already in the ledger: the workflow's last step records it (#149)
        self._send(ws("decision", result))
        bid, name = state.bids[result["chosen_manager"]], self.managers[result["chosen_manager"]]["display_name"]
        # The pitch, never the phone text: that speaks to its own manager as "you", and on the shared screen "you"
        # is everyone (#138). The pitch is English (#49) and checked as a line for the room. body_source says who
        # wrote it, so the card's label describes its own line, not the phone's.
        payload = {"headline": f"{name}: {moment.summary}", "body": bid["pitch_to_host"],
                   "body_source": pitch_source(bid), "manager": result["chosen_manager"],
                   "style": bid["style"]}  # fmt: skip
        # A simulated failure still shows as one on the group card; the decision itself says "scorer" on a clear
        # call, since no model decided it (#86). A knockout of one agent (#90) shows as one only when that agent's
        # plain stat is the card on the group screen.
        knocked = everyone or result["chosen_manager"] == target
        source = "fallback" if knocked else result["source"]
        self._cue(self.cues.make("group", "explanation", source, moment.t, payload, mid,
                                 self.stat_cue.get((mid, "group"))))  # fmt: skip
        if result["source"] == "host":
            reason = {
                "headline": f"Host: showing {name}",
                "body": result["reason"],
                "manager": result["chosen_manager"],
            }
            self._cue(self.cues.make("group", "host_reason", "host", moment.t, reason, mid))
        names = {m: v["display_name"] for m, v in self.managers.items()}
        for cue in clash_cues(self.cues, result, moment.t, names, self.speed):  # a close call (#89)
            self._cue(cue)
        return result


@dataclasses.dataclass
class Start:
    """One match started in the last hour, for the hourly limits."""

    when: float
    client: str
    session_id: str
    agents: bool  # it counts against the agent quota; an abandoned match that never called an agent gives it back


class SessionStore:
    """Live sessions on a public demo with no sign-in (rule 4.8), capped well below the model quota.

    - At most max_sessions running at once and per_client from one address.
    - At most per_client_hour matches an hour from one address.
    - Past global_agent_hour matches an hour across everyone, new matches run on code alone: the site stays
      up and free, and the model bill has a ceiling.

    The hourly limits count matches that started (a screen connected), not sessions created: a session nobody
    opens calls no model, and must not spend the hour for everyone else (#39).

    A session that never starts frees its seat after 30 s. A finished one lingers for its recap link, and
    when too many linger the oldest finished ones go first.
    """

    def __init__(self, max_sessions: int = 20, per_client: int = 4, linger_s: float = 600,
                 session_kwargs: dict[str, Any] | None = None, per_client_hour: int = 8,
                 global_agent_hour: int = 60, per_client_agent_hour: int = 4,
                 created_per_client_hour: int = 30) -> None:  # fmt: skip
        self.max_sessions, self.per_client, self.linger_s = max_sessions, per_client, linger_s
        self.per_client_hour, self.global_agent_hour = per_client_hour, global_agent_hour
        self.per_client_agent_hour, self.created_per_client_hour = per_client_agent_hour, created_per_client_hour
        self.session_kwargs = session_kwargs or {}
        self.sessions: dict[str, tuple[Session, str, float]] = {}  # id -> (session, client, created)
        self.started: deque[Start] = deque()  # matches started in the last hour
        self.created: deque[tuple[float, str]] = deque()  # (when, client): sessions created in the last hour
        self.background: set[asyncio.Task[None]] = set()  # releases of dropped league sessions, held until done

    def _reap(self) -> None:
        now = time.monotonic()
        for sid, (s, _, created) in list(self.sessions.items()):
            finished = s.finished_at is not None and now - s.finished_at > self.linger_s
            # A screen already subscribed is about to start it, however long the judge waited in the lobby (#44)
            unstarted = s.task is None and s.finished_at is None and now - created > 30 and not s.connections()
            if finished or unstarted:
                self._drop(sid)
        lingering = sorted((s.finished_at, sid) for sid, (s, _, _) in self.sessions.items() if s.finished_at)
        for _, sid in lingering[: max(0, len(self.sessions) - 3 * self.max_sessions + 1)]:
            self._drop(sid)
        self._last_hour()

    def _last_hour(self) -> None:
        now = time.monotonic()
        while self.started and now - self.started[0].when > 3600:
            self.started.popleft()
        while self.created and now - self.created[0][0] > 3600:
            self.created.popleft()

    def _drop(self, sid: str) -> None:
        session, _, _ = self.sessions.pop(sid)
        session.close_screens()
        if session.task is None and session.on_released is not None:  # never started: it lets go of its league (#178)
            task = asyncio.create_task(session._league_hook(session.on_released, "release its league's gameweek"))
            self.background.add(task)
            task.add_done_callback(self.background.discard)

    def create(self, seed: int, manager: str, style: str, lang: str, client: str, gameweek: bool = False,
               **league: Any) -> Session | None:  # fmt: skip
        """A new session, or None for busy. A client holds one unopened session at most: a new one replaces it, so
        a judge who refreshes the lobby is never refused, and nobody can hold seats without watching (#50)."""
        self._reap()
        for sid, (s, c, _) in list(self.sessions.items()):
            if c == client and s.task is None and s.finished_at is None and not s.connections():
                self._drop(sid)
        if sum(c == client for _, c in self.created) >= self.created_per_client_hour:
            return None
        running = [c for s, c, _ in self.sessions.values() if s.finished_at is None]
        if len(running) >= self.max_sessions or running.count(client) >= self.per_client:
            return None
        if self._started_by(client) >= self.per_client_hour:
            return None
        # one match, either way; `league` is a stored league and its on_played (#178)
        s = Session(seed, manager, style, lang, gameweek=gameweek, **league, **self.session_kwargs)
        now = time.monotonic()
        self.sessions[s.id] = (s, client, now)
        self.created.append((now, client))
        return s

    def start(self, session: Session) -> None:
        """The first screen connected: the match starts, and counts against the hour. Past either hourly limit it
        still plays, on code alone; the screen is already open, so refusing would only show it an error."""
        if session.task is not None or session.id not in self.sessions:
            return
        client = self.sessions[session.id][1]
        self._last_hour()
        agent_matches = [entry for entry in self.started if entry.agents]
        if (len(agent_matches) >= self.global_agent_hour or self._started_by(client) >= self.per_client_hour
                or sum(entry.client == client for entry in agent_matches) >= self.per_client_agent_hour):  # fmt: skip
            session.run_on_code()  # until the hour rolls on
        self.started.append(Start(time.monotonic(), client, session.id, session.agents_on))
        session.on_abandoned = partial(self.refund, session)
        session.start()

    def refund(self, session: Session) -> None:
        """An abandoned match that never called an agent gives its agent slot back: it cost nothing, so opening and
        closing matches must not use up the hour for every judge (#50). One that did keeps it, or watching each
        match up to a few paid calls and then leaving would never meet the agent limits (#72). The client's own
        hourly count stays charged."""
        if session.abandoned and not session.paid:
            for entry in self.started:
                if entry.session_id == session.id:
                    entry.agents = False

    def _started_by(self, client: str) -> int:
        return sum(entry.client == client for entry in self.started)

    def get(self, session_id: str) -> Session | None:
        entry = self.sessions.get(session_id)
        return entry[0] if entry else None
