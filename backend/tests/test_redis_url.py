"""The REDIS_URL the deployment hands the app, and the one scripts/check-redis-publish.sh builds, must reach the
network with Entra sign-in (#180). redis-py refuses a user name beside a credential provider with a DataError on
every connection, before any network, so a URL with one would leave every replica unready. Both shapes are read
from the files that build them, so a change there is tested here."""

import re
import socket
from pathlib import Path

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from watchparty.app.shared import partial_client

pytestmark = pytest.mark.anyio
ROOT = Path(__file__).resolve().parents[2]


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def bicep_url(host: str, port: int) -> str:
    """The REDIS_URL value in infra/main.bicep, with the host and port filled in."""
    template = re.findall(r"name: 'REDIS_URL', value: '([^']+)'", (ROOT / "infra/main.bicep").read_text())
    assert len(template) == 1
    url = str(template[0]).replace("${redis!.properties.hostName}", host).replace("${redisPort}", str(port))
    url = url.replace("${identity.properties.principalId}", "00000000-0000-0000-0000-0000000000a1")  # if it comes back
    assert "${" not in url, url  # any other interpolation, a user name for one, is not filled in here
    return url


def script_url(host: str, port: int) -> str:
    """The URL scripts/check-redis-publish.sh passes to the tools."""
    line = re.findall(r'^url="([^"]+)"$', (ROOT / "scripts/check-redis-publish.sh").read_text(), re.MULTILINE)
    assert len(line) == 1
    url = str(line[0]).replace("$host", host).replace("$PORT", str(port))
    url = url.replace("$me", "00000000-0000-0000-0000-0000000000a1")  # if a user name comes back
    assert "$" not in url, url
    return url


@pytest.mark.parametrize("build", [bicep_url, script_url])
async def test_the_deployed_redis_url_signs_in_with_entra_and_reaches_the_network(build: object) -> None:
    port = closed_port()
    url = build("127.0.0.1", port)  # type: ignore[operator]
    client = partial_client(url)(1.0)
    assert client.connection_pool.connection_kwargs.get("credential_provider") is not None  # Entra, not a password
    with pytest.raises(RedisConnectionError):  # the socket, not a DataError about the user name
        await client.ping()
    await client.aclose()
