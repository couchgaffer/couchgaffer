import json
from collections.abc import Callable
from typing import Any

import pytest

from tools import latency_test as lt
from tools.latency_fixtures import MOMENTS, league, meta
from watchparty.agents.foundry import foundry_client


def test_percentile_is_nearest_rank() -> None:
    values = [float(v) for v in range(1, 11)]
    assert lt.percentile(values, 50) == 5
    assert lt.percentile(values, 95) == 10
    assert lt.percentile([3.0], 95) == 3
    with pytest.raises(ValueError, match="no values"):
        lt.percentile([], 50)


def test_the_gate_judges_bids_by_the_production_rules() -> None:
    stake = next(s for m in MOMENTS for s in m.stakes if league()["managers"][0]["id"] == s["manager"])
    delta = next(e for e in stake["evidence"] if e["id"].endswith("_delta"))
    bid = {"urgency": 0.5, "explanation": f"{delta['value']:g} IP for you.", "explanation_en": "",
           "pitch_to_host": "Show it.", "evidence_ids": [delta["id"]]}  # fmt: skip
    assert lt._bid_ok(bid, stake)
    assert lt._bid_numbers_ok(bid, stake)
    assert not lt._bid_ok(bid | {"evidence_ids": []}, stake)  # #28: production rejects an uncited answer
    assert not lt._bid_numbers_ok(bid | {"explanation": f"{delta['value'] + 1:g} IP for you."}, stake)


def test_thirty_moments_with_one_to_three_managers_at_stake(contract_errors: Callable[[str, Any], list[str]]) -> None:
    assert len(MOMENTS) == 30
    assert len({m.moment_id for m in MOMENTS}) == 30
    managers = {m["id"] for m in league()["managers"]}
    for moment in MOMENTS:
        assert 1 <= len(moment.stakes) <= 3
        for stake in moment.stakes:
            assert contract_errors("stake", stake) == []
            assert stake["manager"] in managers
            assert stake["moment_id"] == moment.moment_id


def test_fixtures_use_only_invented_names_from_contracts() -> None:
    names = {p["name"] for side in ("home", "away") for p in meta()[side]["players"]}
    names |= {meta()[side]["name"] for side in ("home", "away")}
    for moment in MOMENTS:
        assert any(name in moment.summary for name in names)


def test_manager_request_carries_style_language_and_every_evidence_id() -> None:
    moment = MOMENTS[0]
    stake = moment.stakes[0]
    instructions, payload = lt.manager_request(moment, stake)
    manager = next(m for m in league()["managers"] if m["id"] == stake["manager"])
    assert manager["style"] in instructions
    assert manager["lang"] in instructions
    for item in stake["evidence"]:
        assert item["id"] in payload


@pytest.mark.parametrize("schema", [lt.MANAGER_SCHEMA, lt.HOST_SCHEMA, lt.BATCH_SCHEMA])
def test_output_schemas_are_strict(schema: dict[str, Any]) -> None:
    def check(node: dict[str, Any]) -> None:
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                check(child)
        if node.get("type") == "array":
            check(node["items"])

    check(schema["schema"])


class FakeResponses:
    """Stands in for client.responses: answers from the evidence it is sent."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        name = kwargs["text"]["format"]["name"]
        if name == "host_decision":
            first = json.loads(kwargs["input"])["candidates"][0]["id"]
            body = json.dumps({"chosen_manager": first, "reason": "Their turn on the big screen."})
        elif name == "bids":
            managers = json.loads(kwargs["input"])["managers"]
            bid = {"urgency": 0.5, "explanation": "Nice.", "explanation_en": "", "pitch_to_host": "Show it."}
            body = json.dumps({"bids": [{"manager_id": m["manager_id"], **bid,
                                         "evidence_ids": [m["EVIDENCE"][0]["id"]]} for m in managers]})  # fmt: skip
        else:
            cited = [json.loads(kwargs["input"])["EVIDENCE"][0]["id"]]
            body = json.dumps({"urgency": 0.7, "explanation": "Huge save for your keeper.", "explanation_en": "",
                               "pitch_to_host": "Show it.", "evidence_ids": cited})  # fmt: skip
        usage = type("Usage", (), {"input_tokens": 100, "output_tokens": 20})()
        return type("Response", (), {"output_text": body, "usage": usage})()


class FakeClient:
    def __init__(self) -> None:
        self.responses = FakeResponses()


@pytest.mark.anyio
async def test_run_model_records_every_call_and_checks_quality() -> None:
    client = FakeClient()
    model = lt.Candidate(deployment="fake-mini", reasoning=None)
    records = await lt.run_model(client, model, MOMENTS[:3])  # type: ignore[arg-type]
    managers = [r for r in records if r.kind == "manager"]
    assert len(managers) == sum(len(m.stakes) for m in MOMENTS[:3])
    assert len([r for r in records if r.kind == "host"]) == 3
    assert len([r for r in records if r.kind == "batch"]) == 3
    assert all(r.valid and r.numbers_ok and r.latency_ms >= 0 for r in managers)
    assert all(r.valid for r in records if r.kind == "batch")
    assert all(r.valid and r.numbers_ok for r in records if r.kind == "host")  # #58
    assert all(call["store"] is False for call in client.responses.calls)


def _rewrite(client: FakeClient, name: str, change: Callable[[Any], Any]) -> None:
    """Make every `name` answer the fake gives come out as change(parsed answer)."""
    original = client.responses.create

    async def create(**kwargs: Any) -> Any:
        response = await original(**kwargs)
        if kwargs["text"]["format"]["name"] == name:
            response.output_text = json.dumps(change(json.loads(response.output_text)))
        return response

    client.responses.create = create  # type: ignore[method-assign]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "change",
    [
        lambda d: {"chosen_manager": d["chosen_manager"]},  # no reason
        lambda d: d | {"reason": "   "},
        lambda d: d | {"chosen_manager": ["mgr_1"]},
        lambda d: [d],
        lambda d: "Show mine.",
    ],
)
async def test_a_malformed_host_answer_is_invalid_not_a_crash(change: Callable[[Any], Any]) -> None:
    client = FakeClient()
    _rewrite(client, "host_decision", change)
    records = await lt.run_model(client, lt.Candidate("fake", None), MOMENTS[:3])  # type: ignore[arg-type]
    hosts = [r for r in records if r.kind == "host"]
    assert len(hosts) == 3
    assert not any(r.valid for r in hosts)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "change",
    [
        lambda d: {"bids": d["bids"] + d["bids"][:1]},  # two bids for one manager
        lambda d: {"bids": d["bids"] + [d["bids"][0] | {"manager_id": "mgr_9"}]},  # one not at stake
        lambda d: {"bids": d["bids"] + ["Nice."]},
        lambda d: {"bids": [b | {"manager_id": [b["manager_id"]]} for b in d["bids"]]},
        lambda d: d["bids"],
    ],
)
async def test_a_malformed_batch_is_invalid_not_a_crash(change: Callable[[Any], Any]) -> None:
    client = FakeClient()
    _rewrite(client, "bids", change)
    records = await lt.run_model(client, lt.Candidate("fake", None), MOMENTS[:3])  # type: ignore[arg-type]
    batches = [r for r in records if r.kind == "batch"]
    assert len(batches) == 3
    assert not any(r.valid for r in batches)


@pytest.mark.anyio
async def test_a_bid_missing_a_field_is_invalid_not_a_crash() -> None:
    client = FakeClient()
    original = client.responses.create

    async def drop_field(**kwargs: Any) -> Any:
        response = await original(**kwargs)
        response.output_text = response.output_text.replace(', "explanation_en": ""', "")
        return response

    client.responses.create = drop_field  # type: ignore[method-assign]
    records = await lt.run_model(client, lt.Candidate("fake", None), MOMENTS[:1])  # type: ignore[arg-type]
    assert not any(r.valid for r in records if r.kind == "manager")


def test_summary_reports_p50_p95_and_quality() -> None:
    records = [
        lt.Record("m", "manager", latency_ms=float(ms), valid=True, numbers_ok=ms != 10, tokens_in=1, tokens_out=1)
        for ms in range(1, 11)
    ]
    row = lt.summarise(records)["m"]["manager"]
    assert (row["p50_ms"], row["p95_ms"], row["n"]) == (5, 10, 10)
    assert row["numbers_ok"] == 0.9


@pytest.mark.anyio
async def test_same_deployment_with_different_reasoning_is_reported_separately() -> None:
    assert lt.Candidate("gpt-5.4-mini", "none").label == "gpt-5.4-mini:none"
    assert lt.Candidate("gpt-4.1-mini", None).label == "gpt-4.1-mini"
    client = FakeClient()
    records = [
        r
        for effort in ("none", "low")
        for r in await lt.run_model(client, lt.Candidate("fake", effort), MOMENTS[:1])  # type: ignore[arg-type]
    ]
    assert set(lt.summarise(records)) == {"fake:none", "fake:low"}


def test_client_has_no_hidden_retries() -> None:
    """A timed-out call must count once at its deadline; SDK retries would hide it (and triple it)."""
    client = foundry_client("https://example.invalid/")
    assert client.max_retries == 0
    assert str(client.base_url) == "https://example.invalid/openai/v1/"
