"""Leagues, squads, gameweek results and the season table in Postgres (#96). Imported only when DATABASE_URL is
set: without it the app runs today's fixed league and never loads this package."""

from collections.abc import Sequence

from watchparty.sim import Match
from watchparty.store.db import TokenProvider, make_engine, token_provider_from_env
from watchparty.store.rules import Fixtures
from watchparty.store.service import LeagueStore


def one_fixture(seed: int) -> Sequence[Match]:
    """Today's gameweek: one match on the seed. #94 swaps in three fixtures; the rules read the clubs from them."""
    from watchparty.app.session import simulated

    return [simulated(seed, 30)]


def open_store(url: str, token_provider: TokenProvider | None = None, fixtures: Fixtures = one_fixture) -> LeagueStore:
    return LeagueStore(make_engine(url, token_provider), fixtures)


__all__ = ["LeagueStore", "open_store", "token_provider_from_env"]
