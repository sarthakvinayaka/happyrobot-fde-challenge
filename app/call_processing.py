"""HappyRobot POST /process-call: classify outcomes, persist `calls` rows."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.carrier_verify import (
    FMCSAConfigurationError,
    FMCSAUpstreamError,
    normalize_mc_docket,
    verify_mc_carrier,
)
from app.models import Call, Load
from app.schemas import CarrierVerifyResponse

OUTCOME_BOOKED = "booked"
OUTCOME_REJECTED_PRICE = "rejected-price"
OUTCOME_REJECTED_INVALID = "rejected-invalid"
OUTCOME_NO_INTEREST = "no-interest"

# Demo rule: last counter (or final verbal price) within this multiple of loadboard_rate → book at that price.
RATE_UPPER_MULTIPLIER = 1.1

MAX_COUNTER_ROUNDS = 3
SNIPPET_LEN = 500

NEXT_TRANSFER = "transfer_to_sales"
NEXT_END = "end_call"

_POS_PAT = re.compile(
    r"\b(?:great|good|excellent|yes|thanks|thank you|interested|deal|agree|awesome|perfect|wonderful|love|happy|fine|ok|okay|sure)\b",
    re.I,
)
_NEG_PAT = re.compile(
    r"\b(?:no|not|bad|terrible|awful|reject|rejected|cancel|never|angry|horrible|disappointed|unacceptable|ridiculous|worst|hate)\b",
    re.I,
)


def sentiment_from_transcript(transcript: str) -> str:
    """Lightweight lexicon + regex (no NLTK dependency)."""
    t = transcript.lower()
    pos = len(_POS_PAT.findall(t))
    neg = len(_NEG_PAT.findall(t))
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def _offers_payload(
    counter_offers: list[float],
    final_agreed_price: float | None,
    loadboard_rate: float | None,
    extracted: dict[str, Any],
) -> str:
    return json.dumps(
        {
            "counter_offers": counter_offers,
            "final_agreed_price": final_agreed_price,
            "loadboard_rate": loadboard_rate,
            "extracted": extracted,
        },
        default=str,
    )


def _public_body(
    *,
    outcome: str,
    agreed_price: float | None,
    next_action: str,
    sentiment: str,
) -> dict[str, Any]:
    ap = round(agreed_price, 2) if agreed_price is not None else None
    return {
        "outcome": outcome,
        "agreed_price": ap,
        "next_action": next_action,
        "sentiment": sentiment,
    }


async def process_happyrobot_call(
    *,
    transcript: str,
    mc_number: str,
    interested_load_id: str | None,
    counter_offers: list[float],
    final_agreed_price: float | None,
    db: Session,
    redis: Any,
) -> tuple[dict[str, Any], Call, dict[str, Any]]:
    snippet = transcript[:SNIPPET_LEN]
    sentiment = sentiment_from_transcript(transcript)
    mc_norm = normalize_mc_docket(mc_number)
    carrier_mc = mc_norm or mc_number.strip()
    load_id_key = (interested_load_id or "").strip() or None
    rounds = list(counter_offers[:MAX_COUNTER_ROUNDS])

    loadboard_rate: float | None = None
    load: Load | None = None
    outcome: str
    agreed_price: float | None = None
    next_action: str = NEXT_END
    last_offer: float | None = None
    upper_bound: float | None = None

    v: CarrierVerifyResponse | None = None

    try:
        v = await verify_mc_carrier(mc_number, redis)
    except (FMCSAConfigurationError, FMCSAUpstreamError, ValueError):
        outcome = OUTCOME_REJECTED_INVALID
    else:
        if not v.valid:
            outcome = OUTCOME_REJECTED_INVALID
        elif not load_id_key:
            outcome = OUTCOME_NO_INTEREST
        else:
            load = db.get(Load, load_id_key)
            if load is None:
                outcome = OUTCOME_REJECTED_INVALID
            else:
                loadboard_rate = float(load.loadboard_rate)
                upper_bound = loadboard_rate * RATE_UPPER_MULTIPLIER
                last_offer = rounds[-1] if rounds else None
                if last_offer is None and final_agreed_price is not None:
                    last_offer = float(final_agreed_price)

                if last_offer is None:
                    outcome = OUTCOME_NO_INTEREST
                elif last_offer <= upper_bound:
                    outcome = OUTCOME_BOOKED
                    agreed_price = last_offer
                    next_action = NEXT_TRANSFER
                else:
                    outcome = OUTCOME_REJECTED_PRICE

    mc_valid = bool(v and v.valid)

    extracted: dict[str, Any] = {
        "load_id": load_id_key,
        "agreed_price": round(agreed_price, 2) if agreed_price is not None else None,
        "carrier_mc": carrier_mc,
        "sentiment": sentiment,
    }

    debug: dict[str, Any] = {
        "mc_valid": mc_valid,
        "load_found": load is not None,
        "loadboard_rate": round(loadboard_rate, 2) if loadboard_rate is not None else None,
        "upper_bound_1_1x": round(upper_bound, 2) if upper_bound is not None else None,
        "counter_offers": [round(c, 2) for c in rounds],
        "last_offer": round(last_offer, 2) if last_offer is not None else None,
        "agreed_price": round(agreed_price, 2) if agreed_price is not None else None,
        "outcome": outcome,
        "sentiment": sentiment,
        "next_action": next_action,
    }

    call = Call(
        timestamp=datetime.now(timezone.utc),
        mc=carrier_mc,
        load_id=load_id_key,
        offers_json=_offers_payload(rounds, final_agreed_price, loadboard_rate, extracted),
        outcome=outcome,
        sentiment=sentiment,
        transcript_snippet=snippet,
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    body = _public_body(
        outcome=outcome,
        agreed_price=agreed_price,
        next_action=next_action,
        sentiment=sentiment,
    )
    return body, call, debug
