"""Connections, and migrations to head on start.

DATABASE_URL is a plain postgresql:// URL (asyncpg reads it, sslmode included). With DATABASE_AUTH=entra the URL
carries no password: each new connection asks the app's managed identity for a Microsoft Entra token and sends
that as the password, as Azure Database for PostgreSQL flexible server expects
(https://learn.microsoft.com/en-us/azure/postgresql/security/security-connect-with-managed-identity).
"""

import asyncio
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import asyncpg
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

TokenProvider = Callable[[], Awaitable[str]]
ENTRA_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"
MIGRATIONS = Path(__file__).parent / "migrations"
MIGRATION_LOCK = 960_096  # pg_advisory_xact_lock key: one replica migrates at a time


def plain_url(url: str) -> str:
    """asyncpg takes postgresql:// or postgres://; a SQLAlchemy-style driver suffix is dropped."""
    scheme, sep, rest = url.partition("://")
    if not sep or scheme.split("+")[0] not in ("postgresql", "postgres"):
        raise ValueError("DATABASE_URL must be a postgresql:// URL")
    return f"postgresql://{rest}"


def entra_token_provider() -> TokenProvider:
    """A token for the database from the app's managed identity (DefaultAzureCredential, as the agents use).
    The provider caches the token and renews it before it expires; only new connections need a fresh one."""
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    token = get_bearer_token_provider(DefaultAzureCredential(), ENTRA_SCOPE)

    async def provide() -> str:
        return await asyncio.to_thread(token)  # the credential's HTTP call is blocking

    return provide


def token_provider_from_env() -> TokenProvider | None:
    auth = os.environ.get("DATABASE_AUTH", "password")
    if auth not in ("password", "entra"):
        raise ValueError("DATABASE_AUTH must be password or entra")
    return entra_token_provider() if auth == "entra" else None


def make_engine(url: str, token_provider: TokenProvider | None = None) -> AsyncEngine:
    """An async engine whose every new connection takes its password from token_provider when one is given,
    otherwise from the URL. The pool recycles connections every 30 minutes, well inside a token's lifetime."""
    dsn = plain_url(url)

    async def connect() -> Any:
        password = await token_provider() if token_provider else None
        return await asyncpg.connect(dsn, password=password)

    return create_async_engine("postgresql+asyncpg://", async_creator=connect, pool_size=5, max_overflow=5,
                               pool_recycle=1800, pool_pre_ping=True)  # fmt: skip


def alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    return config


def _upgrade(connection: Connection, revision: str) -> None:
    connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK})
    config = alembic_config()
    config.attributes["connection"] = connection
    command.upgrade(config, revision)


async def migrate(engine: AsyncEngine, revision: str = "head") -> None:
    """Applies every migration not yet applied, in one transaction."""
    async with engine.begin() as connection:
        await connection.run_sync(_upgrade, revision)
