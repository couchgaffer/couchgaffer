import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

SCHEMAS = Path(__file__).parents[2] / "contracts" / "schemas"

ClientFactory = Callable[[FastAPI], AbstractAsyncContextManager[httpx.AsyncClient]]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def client_for() -> ClientFactory:
    """An HTTP client that calls the app in-process, with no network."""

    @asynccontextmanager
    async def make(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield client

    return make


@pytest.fixture(scope="session")
def contract_errors() -> Callable[[str, Any], list[str]]:
    """Validate against the JSON Schema itself; pydantic models skip the schemas' if/then rules."""
    schemas = [json.loads(p.read_text()) for p in SCHEMAS.glob("*.schema.json")]
    registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas)
    validators = {s["$id"].removesuffix(".schema.json"): Draft202012Validator(s, registry=registry) for s in schemas}

    def errors(name: str, instance: Any) -> list[str]:
        """`name` is a schema ("stake") or a reference into one ("common.schema.json#/$defs/evidence")."""
        validator = Draft202012Validator({"$ref": name}, registry=registry) if "#" in name else validators[name]
        return [e.message for e in validator.iter_errors(instance)]

    return errors


def drain(queue: "asyncio.Queue[dict[str, Any] | None]") -> list[dict[str, Any]]:
    """Every message waiting on a screen's queue. None (the session closing the screen) is not expected."""
    got = [queue.get_nowait() for _ in range(queue.qsize())]
    assert None not in got, "the screen was closed"
    return [m for m in got if m is not None]
