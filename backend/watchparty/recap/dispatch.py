"""The app's side of the recaps (#15): at full time, hand the session to the recap job, then wait for the
five videos and put each manager's link on their phone. A failure costs only the link, never the match."""

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, Protocol

from watchparty.recap.bundle import gameweek_bundle, make_bundle

if TYPE_CHECKING:
    from watchparty.app.session import Session

log = logging.getLogger(__name__)


class Recaps(Protocol):
    async def submit(self, session_id: str, bundle: dict[str, Any]) -> None:
        """Store the bundle and queue the job."""
        ...

    async def links(self, session_id: str, managers: list[str]) -> dict[str, str] | None:
        """Every manager's recap link once all of them are rendered; None until then."""
        ...


def bundle_for(session: "Session") -> dict[str, Any]:
    """A single match's bundle (version 1), or a gameweek's (version 2, #151)."""
    if session.gameweek is None:
        return make_bundle(session)
    # The climaxes the session played: a fixture that stopped early never reached its later ones
    seen = [session.climaxes[stakes[0]["moment_id"]] for stakes in session.stakes_log]
    return gameweek_bundle(session_id=session.id, gameweek=session.gameweek, league=session.league,
                           climaxes=seen, stakes=session.stakes_log, log=session.log,
                           table=session.engine.table())  # fmt: skip


async def publish_recaps(recaps: Recaps, session: "Session", poll_s: float = 3, timeout_s: float = 600) -> None:
    if not session.agents_on:  # a render per code-only match had no cap; agent matches have the hourly one (#50)
        return
    managers = [m["id"] for m in session.league["managers"]]
    start = time.monotonic()
    try:
        await recaps.submit(session.id, bundle_for(session))
        # The job has the frames now; up to 14 MB a match, 42 MB a gameweek, otherwise held for the whole wait (#71)
        session.drop_frames()
        failed = 0
        while time.monotonic() - start < timeout_s:
            links, error = await _links(recaps, session.id, managers, quiet=failed > 0)
            failed += error
            if links:
                session.send_recaps(links)
                log.info("recaps for %s ready %.1f s after full time", session.id, time.monotonic() - start)
                return
            await asyncio.sleep(poll_s)
        log.warning("recaps for %s not ready after %.0f s, %d polls failed", session.id, timeout_s, failed)
    except Exception:
        log.exception("recaps for %s failed", session.id)


async def _links(
    recaps: Recaps, session_id: str, managers: list[str], quiet: bool
) -> tuple[dict[str, str] | None, bool]:
    """One poll, and whether it failed. A blob timeout costs that poll, not every phone's link (#71). The first
    failure is logged with its traceback, the rest at debug: a permanent one is one error, not 200 warnings."""
    try:
        return await recaps.links(session_id, managers), False
    except Exception:
        (log.debug if quiet else log.exception)("recap links for %s failed, polling again", session_id)
        return None, True
