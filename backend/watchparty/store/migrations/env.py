"""Alembic environment. The app migrates through db.migrate, which hands a connection in; the alembic CLI (from
backend/: DATABASE_URL=... uv run alembic -c watchparty/store/alembic.ini revision -m "<name>") connects itself."""

import asyncio
import os

from alembic import context
from sqlalchemy import Connection

from watchparty.store.db import make_engine, token_provider_from_env
from watchparty.store.schema import Base

target_metadata = Base.metadata


def run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_from_env() -> None:
    engine = make_engine(os.environ["DATABASE_URL"], token_provider_from_env())
    async with engine.begin() as connection:
        await connection.run_sync(run)
    await engine.dispose()


given = context.config.attributes.get("connection")
if given is not None:
    run(given)
else:
    asyncio.run(run_from_env())
