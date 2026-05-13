"""Unit tests for parse_call_intent."""

from app.call_intent import parse_call_intent


def test_parse_load_id_ld_style():
    p = parse_call_intent("We're looking at LD-ABC123 for tomorrow")
    assert p.load_id == "LD-ABC123"


def test_parse_load_id_load_prefix():
    p = parse_call_intent("Quote on LOAD-XYZ99 please")
    assert p.load_id == "LOAD-XYZ99"


def test_parse_prices_dollars_and_dollar_sign():
    p = parse_call_intent("They said 2800 dollars and later $2,850 for the lane")
    assert 2800.0 in p.price_mentions
    assert 2850.0 in p.price_mentions


def test_parse_empty():
    p = parse_call_intent("")
    assert p.load_id is None
    assert p.price_mentions == []
