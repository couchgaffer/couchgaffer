"""The generated models must agree with the JSON Schemas in contracts/."""

import importlib
import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

EXAMPLES = Path(__file__).parents[2] / "contracts" / "examples"
MODELS = {
    "match-meta": "MatchMeta",
    "gameweek": "Gameweek",
    "league": "League",
    "match-event": "MatchEvent",
    "match-frame": "MatchFrame",
    "stake": "Stake",
    "bid": "Bid",
    "host-decision": "HostDecision",
    "cue": "Cue",
    "ws-message": "WsMessage",
}


def model_for(schema: str) -> type[BaseModel]:
    module = importlib.import_module(f"watchparty.models.{schema.replace('-', '_')}_schema")
    model: type[BaseModel] = getattr(module, MODELS[schema])
    return model


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*.json")), ids=lambda p: p.name)
def test_every_example_round_trips_through_its_model(path: Path) -> None:
    example = json.loads(path.read_text())
    model = model_for(path.name.split(".")[0])
    dumped = model.model_validate(example).model_dump(mode="json", by_alias=True, exclude_unset=True)
    assert dumped == example


def test_every_schema_has_a_model() -> None:
    schemas = {p.name.removesuffix(".schema.json") for p in (EXAMPLES.parent / "schemas").glob("*.schema.json")}
    assert schemas - {"common"} == set(MODELS)


def test_models_reject_unknown_fields() -> None:
    example = json.loads((EXAMPLES / "cue.group.json").read_text())
    with pytest.raises(ValidationError):
        model_for("cue").model_validate({**example, "html": "<script>"})


def test_models_enforce_patterns() -> None:
    example = json.loads((EXAMPLES / "cue.group.json").read_text())
    with pytest.raises(ValidationError):
        model_for("cue").model_validate({**example, "screen": "phone:mgr_6"})


# Gameweek (#94): the generated models enforce the new patterns and the elsewhere fact, not only the JSON Schema.
@pytest.mark.parametrize("fixture_id", ["fx_0", "fx_10", "fx_a", "fixture_1"])
def test_models_refuse_a_bad_fixture_id(fixture_id: str) -> None:
    for name in ["match-event", "match-frame", "cue", "stake", "bid", "host-decision"]:
        example = json.loads((EXAMPLES / f"{name}.gameweek.json").read_text())
        with pytest.raises(ValidationError):
            model_for(name).model_validate({**example, "fixture_id": fixture_id})


@pytest.mark.parametrize("player", ["p_co_9", "p_cors_9", "p_COR_9", "p_cor_100", "p_cor_"])
def test_models_refuse_a_bad_player_id(player: str) -> None:
    example = json.loads((EXAMPLES / "match-event.gameweek.json").read_text())
    with pytest.raises(ValidationError):
        model_for("match-event").model_validate({**example, "player": player})


def test_models_accept_club_and_side_player_ids() -> None:
    example = json.loads((EXAMPLES / "match-event.gameweek.json").read_text())
    for player in ["p_hol_9", "p_home_9", "p_away_11"]:
        assert model_for("match-event").model_validate({**example, "player": player}).model_dump()["player"] == player


BAD_ELSEWHERE = [{"points": None}, {"fixtures": ["fx_1"]}, {"evidence_ids": []}, {"player": "p_kes_9"}]


@pytest.mark.parametrize("change", BAD_ELSEWHERE)
def test_models_refuse_a_bad_elsewhere_fact(change: dict[str, object]) -> None:
    example = json.loads((EXAMPLES / "stake.gameweek.json").read_text())
    fact = {k: v for k, v in {**example["facts"][-1], **change}.items() if v is not None}
    assert fact["kind"] == "elsewhere"
    model_for("stake").model_validate({**example, "facts": [example["facts"][-1]]})  # the unbroken fact passes
    with pytest.raises(ValidationError):
        model_for("stake").model_validate({**example, "facts": [fact]})


def test_gameweek_model_refuses_two_fixtures() -> None:
    example = json.loads((EXAMPLES / "gameweek.json").read_text())
    with pytest.raises(ValidationError):
        model_for("gameweek").model_validate({**example, "fixtures": example["fixtures"][:2]})
