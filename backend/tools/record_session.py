"""Plays a demo session with code fallbacks and writes its recap bundle, for rendering recaps locally (#15).

uv run python -m tools.record_session ../recordings/input.json.gz
docker compose run --rm recap --local /data/input.json.gz /data/out
"""

import asyncio
import gzip
import json
import sys
from pathlib import Path

from watchparty.app.session import Session
from watchparty.recap.bundle import make_bundle
from watchparty.sim import DEMO_SEED


async def no_sleep(_: float) -> None:
    await asyncio.sleep(0)


async def main(out: Path) -> None:
    session = Session(DEMO_SEED, "mgr_1", "casual", "en", sleep=no_sleep)
    await session.run()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(gzip.compress(json.dumps(make_bundle(session)).encode()))
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
