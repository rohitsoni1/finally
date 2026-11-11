import json

import pytest

from app.llm.parsing import LLMParseError, normalize_ticker, parse_llm_output
from app.llm.schema import LLMResponse


def test_full_valid_response():
    raw = json.dumps({
        "message": "Bought.",
        "trades": [{"ticker": "AAPL", "side": "buy", "quantity": 10}],
        "watchlist_changes": [{"ticker": "PYPL", "action": "add"}],
    })
    r = parse_llm_output(raw)
    assert r.message == "Bought."
    assert r.trades[0].ticker == "AAPL" and r.trades[0].side == "buy" and r.trades[0].quantity == 10
    assert r.watchlist_changes[0].action == "add"


def test_message_only_defaults_to_empty_lists():
    r = parse_llm_output('{"message": "Hello"}')
    assert r.trades == [] and r.watchlist_changes == []


def test_null_lists_treated_as_empty():
    r = parse_llm_output('{"message": "Hi", "trades": null, "watchlist_changes": null}')
    assert r.trades == [] and r.watchlist_changes == []


def test_code_fenced_json():
    r = parse_llm_output('```json\n{"message": "fenced", "trades": []}\n```')
    assert r.message == "fenced"


def test_json_embedded_in_prose():
    r = parse_llm_output('Sure! Here you go: {"message": "ok"} hope that helps')
    assert r.message == "ok"


def test_case_normalisation_of_items():
    r = parse_llm_output(json.dumps({
        "message": "m",
        "trades": [{"ticker": " aapl ", "side": "BUY", "quantity": "2.5"}],
        "watchlist_changes": [{"ticker": "pypl", "action": "Remove"}],
    }))
    assert r.trades[0].ticker == "AAPL" and r.trades[0].side == "buy" and r.trades[0].quantity == 2.5
    assert r.watchlist_changes[0].ticker == "PYPL" and r.watchlist_changes[0].action == "remove"


def test_invalid_items_dropped_valid_kept():
    r = parse_llm_output(json.dumps({
        "message": "m",
        "trades": [
            {"ticker": "AAPL", "side": "hold", "quantity": 1},
            {"ticker": "MSFT"},
            "garbage",
            {"ticker": "NVDA", "side": "sell", "quantity": 3},
        ],
        "watchlist_changes": {"not": "a list"},
    }))
    assert [t.ticker for t in r.trades] == ["NVDA"]
    assert r.watchlist_changes == []


@pytest.mark.parametrize("raw", [
    "", "   ", None, "not json at all", "[1, 2, 3]", '{"trades": []}',
    '{"message": ""}', '{"message": 42}', '{"message": "x"',
])
def test_unusable_output_raises(raw):
    with pytest.raises(LLMParseError):
        parse_llm_output(raw)


def test_normalize_ticker():
    assert normalize_ticker(" brk.b ") == "BRK.B"
    assert normalize_ticker("") is None
    assert normalize_ticker("WAYTOOLONGTICKER") is None
    assert normalize_ticker("AA PL") is None
    assert normalize_ticker(None) is None


def test_schema_json_schema_has_required_fields():
    schema = LLMResponse.model_json_schema()
    assert "message" in schema["required"]
    assert set(schema["properties"]) == {"message", "trades", "watchlist_changes"}
