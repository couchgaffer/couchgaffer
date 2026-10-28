"""Shared live state in Redis, so the app runs on several replicas (#95). Off unless REDIS_URL is set.

Each session runs on the server that created it, its owner, exactly as on one server. Redis holds:
- the session registry: which server owns a session, and a hash of its phone token
- the limits, counted across every server: seats running, matches created and started in the last hour, the
  agent quota. Each check-and-take is one Lua script, so two servers cannot both take the last seat
- one inbox channel per server, which carries everything else

A screen that connects to a server other than the owner is served through the owner: the serving server asks
the owner, through the owner's inbox, to subscribe on its behalf. The owner calls Session.subscribe as for a
local screen (so the late joiner's replay, the connection cap and the abandoned-match watchdog work unchanged)
and publishes that queue, in order, to the serving server's inbox. The judge's style toggle and the failure
button reach the owner the same way. Nothing in Session changes.

Every server writes its heartbeat every HEARTBEAT_S. A server that misses OWNER_TTL_MS of them is gone: its
sessions stop counting against the limits, and its remote screens are shown the match ended and closed. "Gone" takes
two dead readings in a row, the second confirm_s after the first: a Redis restart or failover that loses the
heartbeat record reads the same as a crash until the owner's next heartbeat rewrites it, and a match still playing
must not be shown a full-time whistle (#129). confirm_s is heartbeat_s + command_timeout_s + margin_s (4.5 s by
default), the longest the owner can take to write its record again once Redis answers: a beat already in flight
fails within command_timeout_s, the next comes retry_s later (well under heartbeat_s), and a beat that had just
succeeded is followed by the next within heartbeat_s. Looking a session up for a new screen or an action waits
confirm_s once before answering that there is no such session, but only while this server's own heartbeat record
is missing too, the sign that Redis has just lost its data; otherwise it answers at once. That
includes a server that is still running but cut off from Redis for longer than OWNER_TTL_MS: its matches play on
for its own screens, but drop out of the shared counts (so the limits can be overshot by them) until its next
heartbeat lands and counts its started matches again. Its unopened sessions are not counted again. The
same test makes a server whose own record is gone for another reason wait confirm_s on every miss: its beats
failing while its reads succeed, once another server's admission has pruned the expired record, until a beat
lands. That server is already
counted as gone by the others; its lookups are slow until a beat lands, not wrong. A missing record has no age to
look at, and telling this case apart locally would also skip the wait after a real data loss whenever a beat had
failed during the outage.

A remote screen fails closed. The owner lists each screen it is serving in {wp}:subs:<owner>; when its pump stops
for any reason other than the screen leaving (a failed publish, nobody listening, start_match raising) it tells
the serving server and takes the screen off the list. The serving server checks the list every heartbeat, so a
screen whose pump is gone is closed even if that last message was lost; it waits for two misses in a row, so a
set that Redis lost (a restart or failover) and the next heartbeat rewrote closes nothing. Such a screen is
closed with 1012, not with a full-time whistle and 1000: the match goes on, and the client retries anything but
1000 and 1008.

The hooks: SessionStore's sessions, _reap and _drop (this module stands in for its create and start), and
four branches in main.create_app.
"""

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import secrets
import time
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any
from urllib.parse import urlsplit

from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError

from watchparty.app.session import Message, Session, SessionStore, ws

log = logging.getLogger(__name__)
PREFIX = "{wp}"  # one hash slot: each script reads and writes several keys (Azure's Enterprise cluster policy)
RUNNING, CREATED, STARTED, ALIVE = (f"{PREFIX}:{k}" for k in ("running", "created", "started", "alive"))
HEARTBEAT_S = 2.0
OWNER_TTL_MS = 8000  # four missed heartbeats
UNOPENED_MS = 30_000  # SessionStore drops a session nobody opens after 30 s
HOUR_MS = 3_600_000
ACK_S = 5.0  # how long a screen waits for the owner to take it on
COMMAND_TIMEOUT_S = 2.0
RETRY_S = 0.25  # after a failed heartbeat, the next attempt: a Redis that came back is told at once
MARGIN_S = 0.5  # on top of the worst case before a dead reading is confirmed
# How long the heartbeat keeps one of its entries that its list lacks. A create still waiting on its admission's
# answer when the list was taken gets it within COMMAND_TIMEOUT_S or fails, and the heartbeat's own script call
# has the same limit: twice that covers both. A match that ends this soon after starting keeps its seat that long.
GRACE_MS = int(2 * COMMAND_TIMEOUT_S * 1000)
ENTRA_SCOPE = "https://redis.azure.com/.default"

Relay = Callable[..., Awaitable[None]]  # main.relay, bound to its socket: (queue, close_code=...)
LOST = 1012  # "service restart": the screen's client reconnects, and is served again from the replay

# Running entries are "client\towner\tstate\tt", state "new" (created, no screen yet) or "run". Started entries
# are "t\tclient\tagents". Times are the Redis server's clock in ms, so every replica counts the same hour.
LUA_COMMON = """
local unpack = unpack or table.unpack
local function now_ms()
  local t = redis.call('TIME')
  return tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
end
local function split(s)
  local out = {}
  for part in string.gmatch(s, '([^\\t]*)\\t?') do
    if #out < 4 then table.insert(out, part) end
  end
  return out
end
local function live_owners(alive, now, me)
  local owners = {}
  local a = redis.call('HGETALL', alive)
  for i = 1, #a, 2 do
    if tonumber(a[i + 1]) > now then owners[a[i]] = true else redis.call('HDEL', alive, a[i]) end
  end
  owners[me] = true
  return owners
end
-- the last hour's started matches: in all, this client's, with agents, and this client's with agents
local function started_counts(started, now, client)
  local all, mine, agents, my_agents = 0, 0, 0, 0
  local s = redis.call('HGETALL', started)
  for i = 1, #s, 2 do
    local e = split(s[i + 1])
    if now - tonumber(e[1]) > 3600000 then
      redis.call('HDEL', started, s[i])
    else
      all = all + 1
      local with_agents = e[3] == '1'
      if e[2] == client then mine = mine + 1 end
      if with_agents then agents = agents + 1 end
      if with_agents and e[2] == client then my_agents = my_agents + 1 end
    end
  end
  return all, mine, agents, my_agents
end
"""

# KEYS running, created, started, alive. ARGV sid, client, owner, max_sessions, per_client, per_client_hour,
# created_per_client_hour. Returns {admitted, replaced sid, its owner, ...}: SessionStore.create's checks, in
# its order, across every server. The client's unopened session is replaced wherever it is (#50).
ADMIT = (
    LUA_COMMON
    + """
local now = now_ms()
local sid, client, me = ARGV[1], ARGV[2], ARGV[3]
local owners = live_owners(KEYS[4], now, me)
local replaced = {}
local total, mine = 0, 0
local r = redis.call('HGETALL', KEYS[1])
for i = 1, #r, 2 do
  local e = split(r[i + 1])
  local unopened = e[3] == 'new'
  if not owners[e[2]] or (unopened and now - tonumber(e[4]) > 30000) then
    redis.call('HDEL', KEYS[1], r[i])
  elseif unopened and e[1] == client then
    redis.call('HDEL', KEYS[1], r[i])
    table.insert(replaced, r[i])
    table.insert(replaced, e[2])
  else
    total = total + 1
    if e[1] == client then mine = mine + 1 end
  end
end
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now - 3600000)
local made = 0
for _, m in ipairs(redis.call('ZRANGE', KEYS[2], 0, -1)) do
  if string.sub(m, 1, #client + 1) == client .. '\\t' then made = made + 1 end
end
local _, started_by = started_counts(KEYS[3], now, client)
local refused = made >= tonumber(ARGV[7]) or total >= tonumber(ARGV[4]) or mine >= tonumber(ARGV[5])
  or started_by >= tonumber(ARGV[6])
if refused then return {0, unpack(replaced)} end
redis.call('HSET', KEYS[1], sid, client .. '\\t' .. me .. '\\tnew\\t' .. now)
redis.call('ZADD', KEYS[2], now, client .. '\\t' .. sid)
return {1, unpack(replaced)}
"""
)

# KEYS running, started. ARGV sid, client, owner, agents_on, per_client_hour, global_agent_hour,
# per_client_agent_hour. Returns 1 when the match must run on code alone: SessionStore.start's check.
START = (
    LUA_COMMON
    + """
local now = now_ms()
local sid, client = ARGV[1], ARGV[2]
local _, mine, agents, my_agents = started_counts(KEYS[2], now, client)
local code = agents >= tonumber(ARGV[6]) or mine >= tonumber(ARGV[5]) or my_agents >= tonumber(ARGV[7])
local with_agents = (ARGV[4] == '1' and not code) and '1' or '0'
redis.call('HSET', KEYS[2], sid, now .. '\\t' .. client .. '\\t' .. with_agents)
redis.call('HSET', KEYS[1], sid, client .. '\\t' .. ARGV[3] .. '\\trun\\t' .. now)
if code then return 1 end
return 0
"""
)

# KEYS started. ARGV sid. An abandoned match that never called an agent gives its agent slot back (#50, #72).
REFUND = (
    LUA_COMMON
    + """
local e = redis.call('HGET', KEYS[1], ARGV[1])
if not e then return 0 end
local p = split(e)
redis.call('HSET', KEYS[1], ARGV[1], p[1] .. '\\t' .. p[2] .. '\\t0')
return 1
"""
)

# KEYS alive, running. ARGV server, ttl_ms, grace_ms, then "sid\tclient\tstate" for each of its unfinished
# sessions. Marks the server alive, forgets its sessions that finished or were dropped, and counts its started ones
# again (after a Redis restart, say). An unopened one is not re-added: the admit script may have replaced it. An
# entry written less than grace_ms ago is kept even when the list lacks it: it was admitted or started after the
# list was taken, and deleting it would let the next admission overshoot the seats.
BEAT = (
    LUA_COMMON
    + """
local now = now_ms()
local me = ARGV[1]
redis.call('HSET', KEYS[1], me, now + tonumber(ARGV[2]))
local mine = {}
for i = 4, #ARGV do
  local e = split(ARGV[i])
  mine[e[1]] = e
end
local r = redis.call('HGETALL', KEYS[2])
for i = 1, #r, 2 do
  local e = split(r[i + 1])
  local young = now - tonumber(e[4]) < tonumber(ARGV[3])
  if e[2] == me and not mine[r[i]] and not young then redis.call('HDEL', KEYS[2], r[i]) end
end
for sid, e in pairs(mine) do
  if e[3] == 'run' then
    local old = redis.call('HGET', KEYS[2], sid)
    local t = old and split(old)[4] or tostring(now)
    redis.call('HSET', KEYS[2], sid, e[2] .. '\\t' .. me .. '\\trun\\t' .. t)
  end
end
return 1
"""
)

# KEYS alive. ARGV server. 1 while that server's heartbeat is current.
IS_ALIVE = (
    LUA_COMMON
    + """
local until_ms = redis.call('HGET', KEYS[1], ARGV[1])
if until_ms and tonumber(until_ms) > now_ms() then return 1 end
return 0
"""
)


def registry(sid: str) -> str:
    return f"{PREFIX}:session:{sid}"


def inbox(server: str) -> str:
    return f"{PREFIX}:inbox:{server}"


def serving(server: str) -> str:
    """The set of remote screens a server is pumping right now, by subscription id."""
    return f"{PREFIX}:subs:{server}"


def token_hash(token: str) -> str:
    """Redis keeps a hash of the phone token, not the token: a read of Redis does not hand out sessions."""
    return hashlib.sha256(token.encode()).hexdigest()


def unopened(session: Session) -> bool:
    """SessionStore.create's test for the session a client holds without watching (#50)."""
    return session.task is None and session.finished_at is None and not session.connections()


# A gameweek has under 10,000 events (about 4,700 for three fixtures), so a whistle numbered past this is past all
GAMEWEEK_SEQ_PAST = 1_000_000


class Remote:
    """A screen on this server for a session another server owns: its queue, and what it needs to end the match
    on screen if the owner goes away (Session._end_screens' whistle)."""

    def __init__(self, cues_only: bool) -> None:
        self.queue: asyncio.Queue[Message | None] = asyncio.Queue()
        self.ack: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self.cues_only, self.ended, self.close_code = cues_only, False, 1000
        self.last_seq, self.last_t, self.full_time = 0, 0, False
        # A gameweek's fixtures (#94), from its `gameweek` message, and those that have blown full time: each
        # fixture still playing gets its own whistle, since a screen keeps an event without a fixture for none.
        self.fixtures: list[str] = []
        self.finished: set[str] = set()

    def put(self, message: Message) -> None:
        data = message.get("data") or {}
        if message.get("type") == "gameweek":
            self.fixtures = [f.get("fixture_id", "") for f in data.get("fixtures", [])]
        if message.get("type") == "event":
            self.last_seq = max(self.last_seq, data.get("seq", 0))
            self.full_time = self.full_time or data.get("type") == "full_time"
            if data.get("type") == "full_time" and data.get("fixture_id"):
                self.finished.add(data["fixture_id"])
        if message.get("type") in ("event", "frame"):
            self.last_t = max(self.last_t, data.get("t", 0))
        self.queue.put_nowait(message)

    def end(self, owner_gone: bool = False, lost: bool = False) -> None:
        """Closes the screen once; when the owner is gone mid-match, after a full-time whistle where it stopped.
        lost: the owner stopped serving it while the match goes on, so it closes with LOST and reconnects."""
        if self.ended:
            return
        self.ended = True
        if lost:
            self.close_code = LOST
        if owner_gone and not self.cues_only:
            # Past any seq a gameweek can have: a screen that reconnected was replayed key events only, so the last
            # seq seen here may be below one it saw before, and a screen keeps one event per fixture and seq
            past = GAMEWEEK_SEQ_PAST if self.fixtures else self.last_seq
            for n, fixture in enumerate(self._playing(), start=1):
                whistle = {"seq": past + n, "t": self.last_t, "type": "full_time", "team": "home",
                           "x": 0.5, "y": 0.5} | ({"fixture_id": fixture} if fixture else {})  # fmt: skip
                self.queue.put_nowait(ws("event", whistle))
        self.queue.put_nowait(None)

    def _playing(self) -> list[str]:
        """What has not blown full time: a gameweek's fixtures, or the single match ("")."""
        if self.fixtures:
            return [f for f in self.fixtures if f not in self.finished]
        return [] if self.full_time else [""]


class Shared:
    """This server's view of the shared state. Built with a Redis URL; connects in start(), inside the app's loop."""

    def __init__(self, url: str, connect: Callable[[float | None], Redis] | None = None,
                 heartbeat_s: float = HEARTBEAT_S, owner_ttl_ms: int = OWNER_TTL_MS,
                 grace_ms: int = GRACE_MS, command_timeout_s: float = COMMAND_TIMEOUT_S,
                 retry_s: float = RETRY_S, margin_s: float = MARGIN_S) -> None:  # fmt: skip
        self.url, self._connect = url, connect or partial_client(url)
        self.heartbeat_s, self.owner_ttl_ms, self.grace_ms = heartbeat_s, owner_ttl_ms, grace_ms
        self.command_timeout_s, self.retry_s, self.margin_s = command_timeout_s, retry_s, margin_s
        self.id = secrets.token_hex(8)
        self.store = SessionStore()  # attach() hands over the app's
        self.max_connections = 0
        self._redis: Redis | None = None
        self.listening = False
        self.remotes: dict[str, Remote] = {}  # subscription id -> a screen here, on another server's session
        self.pumps: dict[str, tuple[asyncio.Task[None], str]] = {}  # subscription id -> (pump, server): a screen
        # elsewhere, on one of our sessions
        self.scripts: dict[str, Any] = {}
        self._inbox: Redis | None = None
        self.registered: set[str] = set()
        self.early_drops: dict[str, float] = {}  # drops that came before their session was held, by sid
        self.answers: dict[str, asyncio.Future[dict[str, Any]]] = {}  # forwarded actions awaiting the owner
        self.starting: set[str] = set()
        self.tasks: set[asyncio.Task[Any]] = set()

    # --- lifecycle ------------------------------------------------------------------------------------

    @property
    def redis(self) -> Redis:
        if self._redis is None:
            raise RedisConnectionError("shared state not started")
        return self._redis

    def attach(self, store: SessionStore, max_connections: int) -> None:
        self.store, self.max_connections = store, max_connections

    async def start(self) -> None:
        """Connects and starts the heartbeat and the inbox. Never raises: with Redis down the server stays up and
        unhealthy, refusing new matches, and keeps trying."""
        self._redis = self._connect(self.command_timeout_s)  # a command that cannot finish fails, never hangs a request
        self._inbox = self._connect(None)  # the inbox waits for messages as long as it takes
        self.scripts = {name: self._redis.register_script(src) for name, src in
                        (("admit", ADMIT), ("start", START), ("refund", REFUND), ("beat", BEAT),
                         ("alive", IS_ALIVE))}  # fmt: skip
        self._spawn(self._heartbeat())
        self._spawn(self._listen())

    async def close(self) -> None:
        """A graceful stop: every remote screen of ours is told the match ended, and our sessions stop counting."""
        for task in list(self.tasks):
            task.cancel()
        if self._redis is None:
            return
        with contextlib.suppress(RedisError, OSError):
            for sub, (_, reply) in list(self.pumps.items()):
                await self.redis.publish(inbox(reply), json.dumps({"sub": sub, "gone": True}))
            await self.redis.hdel(ALIVE, self.id)
            await self.scripts["beat"](keys=[ALIVE, RUNNING], args=[self.id, 0, 0])
            await self.redis.delete(serving(self.id))
            if self.registered:
                await self.redis.delete(*(registry(sid) for sid in self.registered))
        for pump, _ in self.pumps.values():
            pump.cancel()
        for remote in self.remotes.values():
            remote.end(owner_gone=True)
        await self.redis.aclose()
        if self._inbox is not None:
            await self._inbox.aclose()

    def _spawn(self, work: Awaitable[Any]) -> asyncio.Task[Any]:
        task = asyncio.ensure_future(work)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def ready(self) -> bool:
        """For /ready: Redis answers and our inbox is subscribed. Anything less must not take new traffic, or this
        server would run its own matches and limits apart from the others."""
        if self._redis is None or not self.listening:
            return False
        try:
            return bool(await asyncio.wait_for(self.redis.ping(), 1.0))
        except (RedisError, OSError, TimeoutError):
            return False

    @property
    def confirm_s(self) -> float:
        """How long after a dead reading the confirming one comes: the longest the owner can take to write its
        heartbeat record again once Redis answers (a failed beat's timeout, then a retry, or one whole period after
        a beat that succeeded), plus a margin."""
        return self.heartbeat_s + self.command_timeout_s + self.margin_s

    async def _heartbeat(self) -> None:
        while True:
            try:
                await self.beat()
            except (RedisError, OSError) as exc:
                log.warning("shared state heartbeat failed: %s", exc)
                await asyncio.sleep(min(self.heartbeat_s, self.retry_s))  # Redis may be back: say so soon
                continue
            await asyncio.sleep(self.heartbeat_s)

    async def beat(self) -> None:
        """Marks this server alive, reaps as SessionStore.create would, and republishes what it owns."""
        self.store._reap()
        sessions = {sid: (s, client) for sid, (s, client, _) in self.store.sessions.items()}
        mine = [f"{sid}\t{client}\t{'new' if s.task is None else 'run'}"
                for sid, (s, client) in sessions.items() if s.finished_at is None]  # fmt: skip
        await self.scripts["beat"](keys=[ALIVE, RUNNING], args=[self.id, self.owner_ttl_ms, self.grace_ms, *mine])
        # The serving set, rewritten whole: one Redis lost comes back, and a pump that stopped but could not take
        # itself off goes. A pump started since the list was taken may be missing until the next heartbeat; the
        # serving side waits for two misses.
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.delete(serving(self.id))
            if self.pumps:
                pipe.sadd(serving(self.id), *self.pumps)
            pipe.pexpire(serving(self.id), self.owner_ttl_ms * 2)
            await pipe.execute()
        async with self.redis.pipeline(transaction=False) as pipe:
            for sid, (s, _) in sessions.items():
                pipe.hset(registry(sid), mapping={"owner": self.id, "token": token_hash(s.phone_token)})
                pipe.pexpire(registry(sid), self.owner_ttl_ms * 2)
            for sid in self.registered - sessions.keys():
                pipe.delete(registry(sid))
            await pipe.execute()
        self.registered = set(sessions)

    # --- the limits -----------------------------------------------------------------------------------

    async def create(self, seed: int, manager: str, style: str, lang: str, client: str, gameweek: bool = False,
                     **league: Any) -> Session | None:  # fmt: skip
        """SessionStore.create, with its limits counted across every server. Raises RedisError when Redis is
        down: the caller answers busy rather than admit on this server's count alone."""
        store = self.store
        store._reap()
        sid = secrets.token_urlsafe(16)  # admitted before the match is simulated: a refusal costs nothing
        limits = (store.max_sessions, store.per_client, store.per_client_hour, store.created_per_client_hour)
        result = await self.scripts["admit"](keys=[RUNNING, CREATED, STARTED, ALIVE], args=[sid, client, self.id,
                                                                                          *limits])  # fmt: skip
        admitted, replaced = int(result[0]), result[1:]
        session = None
        if int(admitted):
            # Held at once, with no await since the admission: a concurrent create from this client may replace
            # it, and its drop must find it here, or it would stay held with no limit counting it.
            session = Session(seed, manager, style, lang, gameweek=gameweek, **league, **store.session_kwargs)
            session.id = sid
            store.sessions[sid] = (session, client, time.monotonic())
            if self.early_drops.pop(sid, None) is not None:
                store._drop(sid)  # replaced before this request got its answer back from Redis
        for old, owner in zip(replaced[::2], replaced[1::2], strict=True):
            await self._send(owner, {"op": "drop", "sid": old})
        if session is None:
            return None
        try:
            await self.redis.hset(registry(sid), mapping={"owner": self.id, "token": token_hash(session.phone_token)})
            await self.redis.pexpire(registry(sid), self.owner_ttl_ms * 2)
        except BaseException:
            store.sessions.pop(sid, None)  # nothing held unregistered; the admitted entry lapses as an unopened one
            raise
        self.registered.add(sid)
        return session

    async def start_match(self, session: Session) -> None:
        """SessionStore.start, with the hourly limits counted across every server."""
        if session.task is not None or session.id not in self.store.sessions or session.id in self.starting:
            return
        self.starting.add(session.id)
        try:
            client, store = self.store.sessions[session.id][1], self.store
            limits = (store.per_client_hour, store.global_agent_hour, store.per_client_agent_hour)
            on_code = await self.scripts["start"](keys=[RUNNING, STARTED], args=[session.id, client, self.id,
                                                  int(session.agents_on), *limits])  # fmt: skip
            if int(on_code):
                session.run_on_code()  # until the hour rolls on
            session.on_abandoned = partial(self._refund_later, session)
            session.start()
        finally:
            self.starting.discard(session.id)

    def _refund_later(self, session: Session) -> None:
        self._spawn(self.refund(session))

    async def refund(self, session: Session) -> None:
        if not session.abandoned or session.paid:
            return
        try:
            await self.scripts["refund"](keys=[STARTED], args=[session.id])
        except (RedisError, OSError) as exc:  # the slot stays spent until the hour rolls on
            log.warning("refund for match %s failed: %s", session.id, exc)

    # --- screens on another server --------------------------------------------------------------------

    async def _lookup(self, sid: str) -> dict[str, str] | None:
        """The session's owner and token hash, while that owner is alive. A Redis that has just lost its data has
        no registry entry or heartbeat for a live session until the owner's next heartbeat writes them again. A
        screen refused then is refused before accept, which the browser reports as 1006, and
        frontend/src/lib/socket.ts retries it, six tries over about 12 s; but a style or failure press refused then
        answers 404 and is not retried, so the press is lost. So while this server's own
        heartbeat record is missing too, a miss is looked at once more, confirm_s later. Otherwise it is an answer:
        no such session, at once, so that nobody can hold handshakes and requests open by asking for sessions that
        do not exist."""
        if (entry := await self._lookup_once(sid)) is not None:
            return entry
        if await self.redis.hexists(ALIVE, self.id):
            return None
        await asyncio.sleep(self.confirm_s)
        return await self._lookup_once(sid)

    async def _lookup_once(self, sid: str) -> dict[str, str] | None:
        found = await self.redis.hgetall(registry(sid))
        entry = {str(k): str(v) for k, v in found.items()}
        if not entry or not await self.alive(entry["owner"]):
            return None
        return entry

    async def alive(self, server: str) -> bool:
        return bool(int(await self.scripts["alive"](keys=[ALIVE], args=[server])))

    async def serve(self, websocket: Any, sid: str, screen: str, token: str | None, cues_only: bool,
                    relay: Relay, same_token: Callable[[str, str], bool]) -> None:  # fmt: skip
        """A screen for a session another server owns: refused as main.stream refuses one, else served through
        the owner until either side ends it."""
        try:
            entry = await self._lookup(sid)
        except (RedisError, OSError):
            await websocket.close(code=1011)  # shared state out of reach: the screen retries elsewhere
            return
        allowed = entry is not None and (
            screen == "group" or (token is not None and same_token(token_hash(token), entry["token"]))
        )
        if not allowed or entry is None:
            await websocket.close(code=1008)
            return
        sub, remote = secrets.token_hex(8), Remote(cues_only)
        self.remotes[sub] = remote
        try:
            ask = {"op": "subscribe", "sub": sub, "sid": sid, "screen": screen, "cues_only": cues_only,
                   "reply": self.id}  # fmt: skip
            if not await self._send(entry["owner"], ask) or not await asyncio.wait_for(remote.ack, ACK_S):
                await websocket.close(code=1008)  # owner gone, session dropped, or the screen cap reached
                return
            await websocket.accept()
            watch = self._spawn(self._watch_owner(entry["owner"], sub, remote))
            try:
                await relay(remote.queue, close_code=lambda: remote.close_code)
            finally:
                watch.cancel()
        except TimeoutError:
            await websocket.close(code=1011)
        finally:
            self.remotes.pop(sub, None)
            with contextlib.suppress(RedisError, OSError):
                await self._send(entry["owner"], {"op": "unsubscribe", "sub": sub})

    async def _watch_owner(self, owner: str, sub: str, remote: Remote) -> None:
        """Ends the screen once its owner stops beating (a crashed owner never says goodbye), or once the owner no
        longer serves it (its pump stopped, and the word may have been lost with it). Either takes two readings in
        a row, the second confirm_s after the first, so a heartbeat record or serving set that Redis lost has been
        written again by the owner's next heartbeat before it is taken for a crash (#129)."""
        dead = unlisted = 0
        while not remote.ended:
            await asyncio.sleep(self.confirm_s if dead or unlisted else self.heartbeat_s)
            with contextlib.suppress(RedisError, OSError):
                alive = await self.alive(owner)
                listed = alive and bool(await self.redis.sismember(serving(owner), sub))
                dead = 0 if alive else dead + 1
                unlisted = 0 if listed or not alive else unlisted + 1
                if dead >= 2:
                    remote.end(owner_gone=True)
                elif unlisted >= 2:
                    remote.end(lost=True)

    async def forward(self, sid: str, token: str, op: dict[str, Any],
                      same_token: Callable[[str, str], bool]) -> dict[str, Any] | None:  # fmt: skip
        """The judge's style toggle or failure button, for a session another server owns, and the owner's answer:
        {"arm": n} for a failure press (#90), {"error": ...} for one it refused, {"ok": True} for a style. None: no
        such session, or not this token (the caller answers 404, as for a local session). Raises RedisError when
        the owner does not answer within ACK_S, or when nobody listens on its inbox while its heartbeat is current:
        it is between inbox connections (_listen retries after 1 s), or it has just crashed and its record has not
        expired yet. Either way the session was there a moment ago, so the press is busy, not unknown."""
        entry = await self._lookup(sid)
        if entry is None or not same_token(token_hash(token), entry["token"]):
            return None
        req = secrets.token_hex(8)
        answer: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self.answers[req] = answer
        try:
            if not await self._send(entry["owner"], op | {"sid": sid, "req": req, "reply": self.id}):
                raise RedisConnectionError("the owner's inbox has no listener")
            got = await asyncio.wait_for(answer, ACK_S)
        except TimeoutError:
            raise RedisConnectionError("the owner did not answer") from None
        finally:
            self.answers.pop(req, None)
        return None if got.get("missing") else got

    async def _send(self, server: str, message: dict[str, Any]) -> bool:
        """To a server's inbox. False when nobody is listening there."""
        if server == self.id:
            self._on_message(message)
            return True
        return bool(await self.redis.publish(inbox(server), json.dumps(message)))

    # --- the inbox ------------------------------------------------------------------------------------

    async def _listen(self) -> None:
        if self._inbox is None:
            raise RedisConnectionError("shared state not started")
        while True:
            try:
                async with self._inbox.pubsub() as pubsub:
                    await pubsub.subscribe(inbox(self.id))
                    self.listening = True
                    async for message in pubsub.listen():
                        if message["type"] == "message":
                            self._handle(message["data"])
            except (RedisError, OSError) as exc:
                log.warning("shared state inbox lost: %s", exc)
            finally:
                self.listening = False
            await asyncio.sleep(1.0)

    def _handle(self, data: str) -> None:
        """One inbox message. A bad one is logged and skipped: it must not stop the inbox for everything else."""
        try:
            self._on_message(json.loads(data))
        except Exception:
            log.exception("shared state message skipped")

    def _on_message(self, message: dict[str, Any]) -> None:
        if "req" in message and "op" not in message:
            self._answered(message)
            return
        if "sub" in message and "op" not in message:
            self._for_remote(message)
            return
        handler = {"subscribe": self._subscribe, "unsubscribe": self._unsubscribe, "style": self._style,
                   "chaos": self._chaos, "drop": self._drop}.get(message.get("op", ""))  # fmt: skip
        if handler is not None:
            handler(message)

    def _for_remote(self, message: dict[str, Any]) -> None:
        """From an owner, for one of our screens: the answer to its subscribe, its messages, or its end."""
        remote = self.remotes.get(message["sub"])
        if remote is None:
            return
        if "ok" in message and not remote.ack.done():
            remote.ack.set_result(bool(message["ok"]))
        for m in message.get("msgs", []):
            remote.put(m)
        if message.get("end") or message.get("gone") or message.get("lost"):
            remote.end(owner_gone=bool(message.get("gone")), lost=bool(message.get("lost")))

    def _session(self, message: dict[str, Any]) -> Session | None:
        return self.store.get(message.get("sid", ""))

    def _subscribe(self, message: dict[str, Any]) -> None:
        session, sub, reply = self._session(message), message["sub"], message["reply"]
        if session is None or session.connections() >= self.max_connections:
            self._spawn(self._send(reply, {"sub": sub, "ok": False}))
            return
        queue = session.subscribe(message["screen"], cues_only=bool(message["cues_only"]))  # the seat, at once
        self.pumps[sub] = (self._spawn(self._pump(session, message["screen"], queue, sub, reply)), reply)

    async def _pump(self, session: Session, screen: str, queue: asyncio.Queue[Message | None], sub: str,
                    reply: str) -> None:  # fmt: skip
        """A remote screen's queue, in order, to the server that holds the socket. Ends when the session closes
        the screen, the screen leaves, or a publish fails or reaches nobody; in that last case the serving server
        is told, and in any case the screen leaves our serving set, so it is never left open and frozen."""
        delivered = False  # the screen was closed by the session, or it left: nothing more to tell
        try:
            await self.redis.sadd(serving(self.id), sub)
            if not await self._send(reply, {"sub": sub, "ok": True}):
                return
            await self.start_match(session)  # the hourly limits count it from here (#39)
            while True:
                batch = [await queue.get()]
                while not queue.empty():
                    batch.append(queue.get_nowait())
                end = None in batch
                msgs = [m for m in batch if m is not None]
                if not await self._send(reply, {"sub": sub, "msgs": msgs, "end": end}):
                    return
                if end:
                    delivered = True
                    return
        except asyncio.CancelledError:
            delivered = True  # the screen left, or this server is closing and has said so
            raise
        except Exception:
            log.exception("remote screen %s lost", sub)
        finally:
            session.unsubscribe(screen, queue)
            self.pumps.pop(sub, None)
            self._spawn(self._unlist(sub, reply, tell=not delivered))

    async def _unlist(self, sub: str, reply: str, tell: bool) -> None:
        """Best effort: the serving server closes the screen now; failing that, its next check of our serving
        set does, once the set no longer has it."""
        if tell:
            with contextlib.suppress(RedisError, OSError):
                await self._send(reply, {"sub": sub, "lost": True})
        with contextlib.suppress(RedisError, OSError):  # failing that, the next heartbeat rewrites the set without it
            await self.redis.srem(serving(self.id), sub)

    def _unsubscribe(self, message: dict[str, Any]) -> None:
        pump = self.pumps.pop(message["sub"], None)
        if pump is not None:
            pump[0].cancel()

    def _answered(self, message: dict[str, Any]) -> None:
        """The owner's answer to an action this server forwarded."""
        answer = self.answers.get(message["req"])
        if answer is not None and not answer.done():
            answer.set_result(message)

    def _answer(self, message: dict[str, Any], answer: dict[str, Any]) -> None:
        if "req" in message:
            self._spawn(self._send(message["reply"], answer | {"req": message["req"]}))

    def _style(self, message: dict[str, Any]) -> None:
        session = self._session(message)
        if session is None:
            self._answer(message, {"missing": True})
            return
        session.set_style(message["style"])
        self._answer(message, {"ok": True})

    def _chaos(self, message: dict[str, Any]) -> None:
        """A failure press from another server: every agent, or one manager's (#90). Its arm id goes back."""
        session = self._session(message)
        if session is None:
            self._answer(message, {"missing": True})
            return
        try:
            arm = session.simulate_failure(message.get("manager"))
        except ValueError as exc:
            self._answer(message, {"error": str(exc)})
            return
        self._answer(message, {"arm": arm})

    def _drop(self, message: dict[str, Any]) -> None:
        """Another server admitted this client's new session: their unopened one here goes, as on one server."""
        session = self._session(message)
        if session is None:  # its create may still be waiting on Redis's answer: it drops it once held
            now = time.monotonic()
            self.early_drops = {sid: t for sid, t in self.early_drops.items() if now - t < 60}
            self.early_drops[message.get("sid", "")] = now
            return
        if unopened(session):
            self.store._drop(session.id)


def partial_client(url: str) -> Callable[[float | None], Redis]:
    """A client for REDIS_URL. A rediss:// URL with no password signs in with Microsoft Entra ID: the managed
    identity on Azure (AZURE_CLIENT_ID picks which), the az login session locally. Azure Managed Redis takes
    Entra tokens and the client renews them before they expire (redis-entraid)."""

    def connect(timeout: float | None) -> Redis:
        parts = urlsplit(url)
        kwargs: dict[str, Any] = {"decode_responses": True, "health_check_interval": 30,
                                  "socket_connect_timeout": 2, "socket_timeout": timeout}  # fmt: skip
        if parts.scheme == "rediss" and not parts.password:
            from redis_entraid.cred_provider import create_from_default_azure_credential

            kwargs["credential_provider"] = create_from_default_azure_credential((ENTRA_SCOPE,))
            kwargs["protocol"] = 3  # redis-py re-authenticates a pub/sub connection on token renewal over RESP3 only
        return Redis.from_url(url, **kwargs)

    return connect


def shared_from_env() -> Shared | None:
    """Shared live state when REDIS_URL is set; otherwise one server holds everything, as before."""
    url = os.environ.get("REDIS_URL")
    return Shared(url) if url else None
