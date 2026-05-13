"""Tests for POST /v1/process-call negotiation and payloads."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.schemas import CarrierVerifyResponse
from tests.conftest import _sample_load


@pytest.fixture
def mock_carrier_ok(monkeypatch: pytest.MonkeyPatch):
    m = AsyncMock(return_value=CarrierVerifyResponse(valid=True, details={}))
    monkeypatch.setattr("app.call_processing.verify_mc_carrier", m)
    return m


def test_process_call_booked_happy_path(client, db_session, mock_carrier_ok):
    db_session.add(_sample_load("LD-HAPPY", rate=2000.0))
    db_session.commit()

    r = client.post(
        "/v1/process-call",
        json={
            "transcript": "Yes we want it great thanks",
            "mc_number": "MC-123456",
            "interested_load_id": "LD-HAPPY",
            "counter_offers": [2000.0],
            "final_agreed_price": None,
            "current_round": 1,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["outcome"] == "booked"
    assert data["agreed_price"] == 2000.0
    assert data["next_action"] == "transfer_to_sales"
    assert data["sentiment"] == "positive"
    assert data.get("suggested_counter") is None
    assert "transfer" in (data.get("transfer_message") or "").lower()
    assert data.get("transfer_initiated") is True
    assert "Transfer was successful" in (data.get("transfer_status_message") or "")
    assert data.get("sentiment_warning") is False


def test_carrier_interested_false_forces_no_interest(client, db_session, mock_carrier_ok):
    db_session.add(_sample_load("LD-X", rate=1000.0))
    db_session.commit()

    r = client.post(
        "/v1/process-call",
        json={
            "transcript": "We are not taking this lane after all.",
            "mc_number": "MC-1",
            "interested_load_id": "LD-X",
            "counter_offers": [1000.0],
            "final_agreed_price": None,
            "carrier_interested": False,
            "current_round": 1,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["outcome"] == "no-interest"
    assert data["next_action"] == "end_call"
    assert data["agreed_price"] is None


def test_current_round_mismatch_returns_422(client, db_session, mock_carrier_ok):
    db_session.add(_sample_load("LD-R", rate=2000.0))
    db_session.commit()

    r = client.post(
        "/v1/process-call",
        json={
            "transcript": "offer",
            "mc_number": "MC-1",
            "interested_load_id": "LD-R",
            "counter_offers": [2000.0, 2100.0],
            "current_round": 1,
        },
    )
    assert r.status_code == 422


def test_too_many_counters_rejected_price(client, db_session, mock_carrier_ok):
    db_session.add(_sample_load("LD-M", rate=2000.0))
    db_session.commit()

    r = client.post(
        "/v1/process-call",
        json={
            "transcript": "countering again",
            "mc_number": "MC-1",
            "interested_load_id": "LD-M",
            "counter_offers": [2100.0, 2150.0, 2180.0, 2190.0],
            "final_agreed_price": None,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["outcome"] == "rejected-price"
    assert data["next_action"] == "end_call"


def test_three_counters_booked_when_last_acceptable(client, db_session, mock_carrier_ok):
    db_session.add(_sample_load("LD-3OK", rate=2000.0))
    db_session.commit()

    r = client.post(
        "/v1/process-call",
        json={
            "transcript": "third counter",
            "mc_number": "MC-1",
            "interested_load_id": "LD-3OK",
            "counter_offers": [2500.0, 2300.0, 2100.0],
            "current_round": 3,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["outcome"] == "booked"
    assert data["agreed_price"] == 2100.0
    assert data["rounds_used"] == 3


def test_three_counters_rejected_price_when_last_still_high(client, db_session, mock_carrier_ok):
    db_session.add(_sample_load("LD-3BAD", rate=2000.0))
    db_session.commit()

    r = client.post(
        "/v1/process-call",
        json={
            "transcript": "third counter",
            "mc_number": "MC-1",
            "interested_load_id": "LD-3BAD",
            "counter_offers": [2500.0, 2400.0, 2300.0],
            "current_round": 3,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["outcome"] == "rejected-price"
    assert data["next_action"] == "end_call"


def test_parse_load_and_price_from_transcript(client, db_session, mock_carrier_ok):
    db_session.add(_sample_load("LD-PARSE1", rate=2000.0))
    db_session.commit()

    r = client.post(
        "/v1/process-call",
        json={
            "transcript": "Calling about load LD-PARSE1, we can do at 2050 dollars",
            "mc_number": "MC-9",
            "interested_load_id": None,
            "counter_offers": [],
            "final_agreed_price": None,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["outcome"] == "booked"
    assert data["agreed_price"] == 2050.0


def test_negotiated_returns_suggested_counter(client, db_session, mock_carrier_ok):
    db_session.add(_sample_load("LD-N", rate=2000.0))
    db_session.commit()

    r = client.post(
        "/v1/process-call",
        json={
            "transcript": "still interested",
            "mc_number": "MC-1",
            "interested_load_id": "LD-N",
            "counter_offers": [2500.0],
            "final_agreed_price": None,
            "current_round": 1,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["outcome"] == "negotiated"
    assert data["next_action"] == "continue_negotiation"
    assert data["agreed_price"] is None
    assert "suggested_counter" in data
    assert isinstance(data["suggested_counter"], (int, float))
    assert data["suggested_counter"] <= 2200.0
    assert data.get("suggested_counter_reason")
    assert data.get("followup_needed") is True


def test_booked_negative_sentiment_sets_sentiment_warning(client, db_session, mock_carrier_ok):
    db_session.add(_sample_load("LD-SW", rate=2000.0))
    db_session.commit()

    r = client.post(
        "/v1/process-call",
        json={
            "transcript": "This is terrible but we will take 2000 dollars",
            "mc_number": "MC-1",
            "interested_load_id": "LD-SW",
            "counter_offers": [2000.0],
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["outcome"] == "booked"
    assert data.get("sentiment_warning") is True


def test_no_interest_positive_sentiment_followup(client, db_session, mock_carrier_ok):
    db_session.add(_sample_load("LD-SOFT", rate=2000.0))
    db_session.commit()

    r = client.post(
        "/v1/process-call",
        json={
            "transcript": "Thanks great talking but not interested in this one",
            "mc_number": "MC-1",
            "interested_load_id": "LD-SOFT",
            "counter_offers": [],
            "carrier_interested": False,
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["outcome"] == "no-interest"
    assert data.get("followup_needed") is True
