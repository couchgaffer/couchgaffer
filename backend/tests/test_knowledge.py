import re

import pytest

from watchparty.knowledge import documents
from watchparty.points import RULES, load_league
from watchparty.sim import DEMO_SEED, simulate

DOCS = documents()
BY_ID = {d["id"]: d for d in DOCS}


def test_every_rule_squad_and_glossary_term_is_a_document_with_a_unique_id() -> None:
    assert len(BY_ID) == len(DOCS)
    assert {f"rule-{r}".replace("_", "-") for r in RULES["rules"]} <= set(BY_ID)
    assert {f"squad-{m['id']}".replace("_", "-") for m in load_league()["managers"]} <= set(BY_ID)
    assert {"glossary-ip", "glossary-wall-tier", "glossary-captain", "glossary-key-moment"} <= set(BY_ID)
    assert {d["kind"] for d in DOCS} == {"rule", "squad", "glossary"}
    assert all(re.fullmatch(r"[a-z0-9-]+", d["id"]) for d in DOCS)  # a valid search document key


def test_rules_state_the_numbers_of_rules_v1_and_why() -> None:
    goal = BY_ID["rule-goal"]["content"]
    assert f"{RULES['rules']['goal']['points']} IP" in goal
    save = BY_ID["rule-save-points"]["content"]
    assert "xG" in save
    assert "Why" in save


def test_squads_name_only_the_invented_players_and_the_captain() -> None:
    meta = simulate(DEMO_SEED).meta
    players = {p["name"] for side in ("home", "away") for p in meta[side]["players"]}
    for manager in load_league()["managers"]:
        doc = BY_ID[f"squad-{manager['id']}".replace("_", "-")]["content"]
        named = {name for name in players if name in doc}
        assert len(named) == len(manager["squad"])
        assert "captain" in doc


def test_nothing_real_or_betting_related_goes_into_the_knowledge_base() -> None:
    text = " ".join(d["title"] + " " + d["content"] for d in DOCS).lower()
    for banned in ("premier league", "bet", "money", "prize", "bonus", "clean sheet", "fantasy premier"):
        assert not re.search(rf"\b{banned}\b", text), banned


def test_the_index_and_knowledge_source_carry_every_document_field() -> None:
    from tools.foundry_iq import INDEX_DEFINITION, KNOWLEDGE_SOURCE_DEFINITION, knowledge_base

    fields = {f["name"] for f in INDEX_DEFINITION["fields"]}
    assert all(set(d) == fields for d in DOCS)
    sourced = {f["name"] for f in KNOWLEDGE_SOURCE_DEFINITION["searchIndexParameters"]["sourceDataFields"]}
    assert sourced == fields  # the rulebook tool reads id, title and content back from each passage
    definition = knowledge_base("https://example-foundry.openai.azure.com")
    assert definition["knowledgeSources"] == [{"name": KNOWLEDGE_SOURCE_DEFINITION["name"]}]
    assert definition["models"][0]["azureOpenAIParameters"]["resourceUri"] == "https://example-foundry.openai.azure.com"


@pytest.mark.anyio
async def test_a_load_with_one_bad_document_fails() -> None:
    import httpx

    from tools.foundry_iq import upload

    def answer(request: httpx.Request) -> httpx.Response:  # 207: the batch went through, one document did not
        return httpx.Response(207, json={"value": [{"key": "rule-goal", "status": True, "statusCode": 201},
                                                   {"key": "rule-x", "status": False, "statusCode": 400,
                                                    "errorMessage": "bad field"}]})  # fmt: skip

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        with pytest.raises(SystemExit, match="rule-x"):
            await upload(http, "https://search", "api-version=x", DOCS[:2])
