"""Does PUBLISH count every subscriber on this Redis, whichever shard its channel lands on (#144)?

shared.py decides whether another server is listening from the count PUBLISH returns. Azure Managed Redis with
the EnterpriseCluster policy answers through a proxy in front of its shards, and nothing we found documents
whether that count covers every shard. This subscribes one connection to each of several channels whose names
hash to slots spread over the whole range (and to one channel in the app's own {wp}:inbox: form), two
connections to one more, and none to a control channel. For each channel it then checks, through the proxy:
PUBSUB NUMSUB is the number subscribed, PUBLISH returns that number, and every subscriber receives the message.
The control channel must read 0 both ways, so a tool that cannot see a 0 cannot pass.

What it can and cannot prove: Azure does not say how many shards a size has or which slots each holds, and a
small size may have one shard, in which case every channel shares it and a pass says nothing about other shards.
The app's own channels all carry the {wp} hash tag, so they share one slot anyway. The acceptance check for the
app is tools/check_replicas.py on the live replicas; this one is the diagnostic that names a short count.

It fails closed: any short count, missing message, error or timeout exits 1, and it prints PASS only when every
check held. It signs in as shared.py does (a rediss:// URL with no password uses Microsoft Entra ID through
DefaultAzureCredential, so locally your az login), so it also proves that sign-in. scripts/check-redis-publish.sh
builds the URL for the live Redis. From backend/:

  uv run python -m tools.check_redis_publish rediss://<host>:10000
"""

import asyncio
import secrets
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from redis.asyncio import Redis
from redis.crc import key_slot

from watchparty.app.shared import inbox, partial_client

SLOTS = 16384
BUCKETS = 8  # channels spread over eight equal slot ranges
RECEIVE_S = 5.0
COMMAND_S = 5.0


@dataclass
class Channel:
    name: str
    subscribers: int
    listeners: list[Any] = field(default_factory=list)  # one pubsub per subscriber connection

    @property
    def slot(self) -> int:
        return int(key_slot(self.name.encode()))


def spread_channels(nonce: str) -> list[Channel]:
    """One channel per slot range, the app's inbox form, one with two subscribers, and the control with none."""
    channels, i = [], 0
    for bucket in range(BUCKETS):
        while True:
            name = f"{{wpcheck-{nonce}-{i}}}:ch"
            i += 1
            if key_slot(name.encode()) * BUCKETS // SLOTS == bucket:
                channels.append(Channel(name, 1))
                break
    channels.append(Channel(inbox(f"check-{nonce}"), 1))
    channels.append(Channel(f"{{wpcheck-{nonce}-two}}:ch", 2))
    channels.append(Channel(f"{{wpcheck-{nonce}-none}}:ch", 0))
    return channels


async def subscribed(client: Redis, name: str) -> Any:
    """A pubsub on its own connection, returned only once the server has confirmed the subscription."""
    pubsub = client.pubsub()
    await pubsub.subscribe(name)
    while True:
        message = await asyncio.wait_for(pubsub.get_message(timeout=RECEIVE_S), RECEIVE_S + 1)
        if message is None:
            raise TimeoutError(f"no subscribe confirmation on {name}")
        if message["type"] == "subscribe":
            return pubsub


async def received(pubsub: Any, payload: str) -> bool:
    """Whether this subscriber gets the payload within RECEIVE_S."""
    loop = asyncio.get_running_loop()
    end = loop.time() + RECEIVE_S
    while (left := end - loop.time()) > 0:
        message = await asyncio.wait_for(pubsub.get_message(timeout=left), left + 1)
        if message is not None and message["type"] == "message" and message["data"] == payload:
            return True
    return False


async def check_channel(publisher: Redis, channel: Channel, nonce: str) -> list[str]:
    """The problems with one channel; empty when NUMSUB, PUBLISH and delivery all agree with its subscribers."""
    problems = []
    label = f"{channel.name} (slot {channel.slot})"
    numsub = dict(await publisher.pubsub_numsub(channel.name)).get(channel.name)
    payload = f"check-{nonce}-{channel.slot}"
    count = await publisher.publish(channel.name, payload)
    got = [await received(listener, payload) for listener in channel.listeners]
    print(f"{label}: subscribers {channel.subscribers}, NUMSUB {numsub}, PUBLISH {count}, "
          f"received {sum(got)}/{len(got)}")  # fmt: skip
    if numsub != channel.subscribers:
        problems.append(f"{label}: PUBSUB NUMSUB said {numsub}, {channel.subscribers} subscribed")
    if count != channel.subscribers:
        problems.append(f"{label}: PUBLISH returned {count}, {channel.subscribers} subscribed")
    if not all(got):
        problems.append(f"{label}: {got.count(False)} of {len(got)} subscribers did not receive the message")
    return problems


async def check(connect: Callable[[float | None], Redis]) -> list[str]:
    """Every problem found; empty only when every channel's counts and deliveries held."""
    nonce = secrets.token_hex(4)
    channels = spread_channels(nonce)
    clients: list[Redis] = []
    publisher = connect(COMMAND_S)
    clients.append(publisher)
    try:
        for channel in channels:
            for _ in range(channel.subscribers):
                client = connect(None)  # its own connection, as each app server's inbox is
                clients.append(client)
                channel.listeners.append(await subscribed(client, channel.name))
        problems = []
        for channel in channels:
            problems += await check_channel(publisher, channel, nonce)
        return problems
    finally:
        for channel in channels:
            for listener in channel.listeners:
                await listener.aclose()
        for client in clients:
            await client.aclose()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m tools.check_redis_publish <redis url>", file=sys.stderr)
        return 2
    try:
        problems = asyncio.run(check(partial_client(argv[1])))
    except Exception as exc:  # any failure to connect, sign in or read is a failed check, never a pass
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    for problem in problems:
        print(f"FAIL: {problem}", file=sys.stderr)
    if problems:
        return 1
    print("PASS: every PUBLISH and PUBSUB NUMSUB counted every subscriber, on every slot range")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
