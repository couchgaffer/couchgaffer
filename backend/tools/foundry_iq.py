"""Loads the league rulebook into Foundry IQ (#18), then asks it one question. Idempotent; run as an owner
with the Search Service Contributor and Search Index Data Contributor roles (infra/main.bicep), after every
deploy into a new resource group or search region. From backend/:

  uv run python -m tools.foundry_iq https://<search>.search.windows.net https://<foundry>.openai.azure.com
"""

import asyncio
import json
import sys
from typing import Any

import httpx

from watchparty.knowledge import documents
from watchparty.matchtools.rulebook import API_VERSION, KNOWLEDGE_BASE, KNOWLEDGE_SOURCE, FoundryIQ, search_token

INDEX = "league-knowledge"
MODEL = "gpt-5.4-mini"  # plans the sub-queries, through the search service's own identity

FIELDS = ["id", "title", "content", "kind"]
INDEX_DEFINITION: dict[str, Any] = {
    "name": INDEX,
    "fields": [
        {"name": "id", "type": "Edm.String", "key": True, "filterable": True, "retrievable": True},
        {"name": "kind", "type": "Edm.String", "filterable": True, "retrievable": True},
        {"name": "title", "type": "Edm.String", "searchable": True, "retrievable": True},
        {"name": "content", "type": "Edm.String", "searchable": True, "retrievable": True},
    ],
    "semantic": {
        "defaultConfiguration": "league",
        "configurations": [
            {
                "name": "league",
                "prioritizedFields": {
                    "titleField": {"fieldName": "title"},
                    "prioritizedContentFields": [{"fieldName": "content"}],
                },
            }
        ],
    },
}
KNOWLEDGE_SOURCE_DEFINITION: dict[str, Any] = {
    "name": KNOWLEDGE_SOURCE,
    "kind": "searchIndex",
    "description": "The league rulebook: impact point rules and why they exist, the squads, and a glossary.",
    "searchIndexParameters": {"searchIndexName": INDEX, "sourceDataFields": [{"name": f} for f in FIELDS]},
}


def knowledge_base(foundry: str) -> dict[str, Any]:
    """The knowledge base, planning its queries with the model on this Foundry resource."""
    return {
        "name": KNOWLEDGE_BASE,
        "description": "The rulebook of an invented, free-to-play fantasy-style mini-league.",
        "retrievalInstructions": (
            "Prefer rule entries for questions about points; squads for who owns or captains whom."
        ),
        "knowledgeSources": [{"name": KNOWLEDGE_SOURCE}],
        "models": [
            {
                "kind": "azureOpenAI",
                "azureOpenAIParameters": {"resourceUri": foundry, "deploymentId": MODEL, "modelName": MODEL},
            }
        ],
        "outputMode": "extractiveData",
        "retrievalReasoningEffort": {"kind": "low"},
    }


async def upload(http: httpx.AsyncClient, base: str, version: str, docs: list[dict[str, Any]]) -> None:
    """Upload every document. A 207 means some failed: name them and exit non-zero (#38)."""
    body = {"value": [d | {"@search.action": "mergeOrUpload"} for d in docs]}
    r = await http.post(f"{base}/indexes/{INDEX}/docs/index?{version}", json=body)
    print(f"uploaded {len(docs)} documents: {r.status_code}", r.text[:300] if r.is_error else "")
    r.raise_for_status()
    if failed := [f"{d['key']} ({d.get('errorMessage')})" for d in r.json()["value"] if not d["status"]]:
        raise SystemExit(f"{len(failed)} of {len(docs)} documents failed: {', '.join(failed)}")


async def main(endpoint: str, foundry: str) -> None:
    token = search_token()
    base, version = endpoint.rstrip("/"), f"api-version={API_VERSION}"
    async with httpx.AsyncClient(timeout=60, headers={"Authorization": f"Bearer {await token()}"}) as http:
        r = await http.put(f"{base}/indexes/{INDEX}?{version}", json=INDEX_DEFINITION)
        print(f"PUT indexes/{INDEX}: {r.status_code}", r.text[:300] if r.is_error else "")
        r.raise_for_status()
        await upload(http, base, version, documents())
        for path, body in [(f"knowledgesources/{KNOWLEDGE_SOURCE}", KNOWLEDGE_SOURCE_DEFINITION),
                           (f"knowledgebases/{KNOWLEDGE_BASE}", knowledge_base(foundry.rstrip("/")))]:  # fmt: skip
            r = await http.put(f"{base}/{path}?{version}", json=body)
            print(f"PUT {path}: {r.status_code}", r.text[:300] if r.is_error else "")
            r.raise_for_status()
    await asyncio.sleep(5)  # let the new documents reach the index
    question = "Why is a save against a big chance worth more than an easy one?"
    entries = await FoundryIQ(base, token).explain(question)
    print(f"\nasked: {question}\n" + json.dumps(entries, indent=2)[:1500])
    verify(entries)


def verify(entries: list[dict[str, str]]) -> None:
    """The load is done only when the knowledge base answers. A lookup that fails (401, 403, 404, a timeout)
    gives no entries rather than raising, so check for the rule the question is about (#42)."""
    if not any(e["id"] == "rule-save-points" for e in entries):
        raise SystemExit("the knowledge base did not return rule-save-points: check the roles and the index")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
