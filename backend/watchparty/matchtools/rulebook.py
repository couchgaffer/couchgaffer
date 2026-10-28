"""The league rulebook in a Foundry IQ knowledge base (#18), queried with agentic retrieval.

Keyless both ways: the app asks with its managed identity (Search Index Data Reader), and the search
service plans its sub-queries with our gpt-5.4-mini deployment through its own identity. The knowledge
base is loaded by tools/foundry_iq.py from watchparty/knowledge.py.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx
from azure.core.exceptions import AzureError

log = logging.getLogger(__name__)
API_VERSION = "2026-08-01-preview"  # knowledge bases with LLM query planning
KNOWLEDGE_BASE = "league-rulebook"
KNOWLEDGE_SOURCE = "league-knowledge-ks"
MAX_ENTRIES = 4


class Rulebook(Protocol):
    async def explain(self, question: str) -> list[dict[str, str]]:
        """Rulebook entries (id, title, content) that answer the question, best first."""
        ...


def search_token() -> Callable[[], Awaitable[str]]:
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    token = get_bearer_token_provider(DefaultAzureCredential(), "https://search.azure.com/.default")

    async def get() -> str:  # the credential caches tokens; the thread hop only matters on refresh
        return await asyncio.to_thread(token)

    return get


class FoundryIQ:
    """The app's one client for the knowledge base (#35).

    - A lookup gives up timeout_s after it was asked, slot and token included (#42); the service is asked to
      stop by itself soon after.
    - At most two run at once across the app: Basic allows two semantic queries at a time per unit.
    - Any failure, from a sign-in failure (#46) or a 429 to an odd response, is logged by its type and gives no
      entries, so the agent still writes its summary from the match tools.
    """

    def __init__(self, endpoint: str, token: Callable[[], Awaitable[str]], timeout_s: float = 8,
                 http: httpx.AsyncClient | None = None) -> None:  # fmt: skip
        self.url = f"{endpoint.rstrip('/')}/knowledgebases/{KNOWLEDGE_BASE}/retrieve?api-version={API_VERSION}"
        self.token, self.timeout_s = token, timeout_s
        self.http = http or httpx.AsyncClient(timeout=timeout_s)  # one connection pool, not a handshake a lookup
        self.slots = asyncio.Semaphore(2)

    async def explain(self, question: str) -> list[dict[str, str]]:
        body = {
            "messages": [{"role": "user", "content": [{"type": "text", "text": question}]}],
            "knowledgeSourceParams": [{"knowledgeSourceName": KNOWLEDGE_SOURCE, "kind": "searchIndex"}],
            "outputMode": "extractiveData",  # passages, not an answer: our agents write the words
            "retrievalReasoningEffort": {"kind": "low"},
            "maxRuntimeInSeconds": 10,  # the service's minimum; our own limit is shorter
        }
        try:
            async with asyncio.timeout(self.timeout_s), self.slots:  # waiting for a slot and the token count too
                headers = {"Authorization": f"Bearer {await self.token()}"}
                response = await self.http.post(self.url, json=body, headers=headers)
            response.raise_for_status()
            return parse(response.json())
        except (TimeoutError, httpx.HTTPError, AzureError, ValueError, KeyError, TypeError, AttributeError) as error:
            log.warning("Foundry IQ lookup gave no entries: %s", type(error).__name__)
            return []


def parse(result: dict[str, Any]) -> list[dict[str, str]]:
    """The retrieved passages, best first: a JSON-encoded list in the response's text, whose ref_ids the
    references map to our document keys. A passage without a key, or a second one for the same document
    (two sub-queries can find it), is left out. An odd shape raises, for the caller to treat as no entries."""
    keys = {str(r["id"]): r["docKey"] for r in result.get("references", []) if r.get("docKey")}
    entries: dict[str, dict[str, str]] = {}
    for message in result.get("response", []):
        for content in message.get("content", []):
            if content.get("type") != "text":
                continue
            for item in json.loads(content["text"] or "[]"):
                key = keys.get(str(item.get("ref_id")))
                if key and key not in entries and item.get("content"):
                    entries[key] = {"id": key, "title": item.get("title", ""), "content": item["content"]}
    return list(entries.values())[:MAX_ENTRIES]
