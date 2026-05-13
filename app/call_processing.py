"""HappyRobot POST /v1/process-call: classify outcomes, persist `calls` rows."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.call_intent import parse_call_intent
from app.carrier_verify import (
    FMCSAConfigurationError,
    FMCSAUpstreamError,
    normalize_mc_docket,
    verify_mc_carrier,
)
from app.models import Call, Load
from app.schemas import CarrierVerifyResponse, NextAction, ProcessCallResponse

OUTCOME_BOOKED = "booked"
OUTCOME_NEGOTIATED = "negotiated"
OUTCOME_REJECTED_PRICE = "rejected-price"
OUTCOME_REJECTED_INVALID = "rejected-invalid"
OUTCOME_NO_INTEREST = "no-interest"

RATE_UPPER_MULTIPLIER = 1.1
MAX_COUNTER_ROUNDS = 3
SNIPPET_LEN = 500

NEXT_TRANSFER: NextAction = "transfer_to_sales"
NEXT_CONTINUE: NextAction = "continue_negotiation"
NEXT_END: NextAction = "end_call"

MSG_BOOKED = (
    "Great, I'll transfer you to a sales rep now to finalize the details. "
    "One moment while I connect you."
)
MSG_REJECT_PRICE = (
    "Thanks for calling in. I can't reach that rate today, but we'll keep you in mind for future loads."
)
MSG_REJECT_TOO_MANY = (
    "We've reached the limit on counter offers for this load. "
    "I can't extend further on this rate today—thank you for your time."
)
MSG_NEGOTIATE = (
    "That number is a bit above what we can book on this load right now. "
    "I can suggest a figure closer to the posted rate—let me know if that works for you."
)
MSG_NO_INTEREST = "Thanks for letting us know. If anything changes, you're welcome to call back anytime."
MSG_CARRIER_DECLINED = "Understood—no problem. Thanks for your time today."
MSG_INVALID = "I wasn't able to match that load or carrier record—please double-check the details and try again."

TRANSFER_STATUS_SPEC = (
    "Transfer was successful and now you can wrap up the conversation with the carrier."
)
SUGGESTED_COUNTER_REASON = (
    "Midpoint between posted rate and your last offer, capped at our posting ceiling (110% of posted rate)."
)

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


def _followup_needed(outcome: str, sentiment: str) -> bool:
    if sentiment != "positive":
        return False
    if outcome in (OUTCOME_NEGOTIATED, OUTCOME_REJECTED_PRICE):
        return True
    if outcome == OUTCOME_NO_INTEREST:
        return True
    return False


def _sentiment_warning(outcome: str, sentiment: str) -> bool:
    return outcome == OUTCOME_BOOKED and sentiment == "negative"


def _lane_pairs(db: Session) -> list[tuple[str, str]]:
    rows = db.execute(select(Load.origin, Load.destination)).all()
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for o, d in rows:
        key = (o, d)
        if key not in seen:
            seen.add(key)
            pairs.append((o, d))
    return pairs[:400]


def _offers_payload(
    counter_offers: list[float],
    final_agreed_price: float | None,
    loadboard_rate: float | None,
    extracted: dict[str, Any],
    *,
    negotiation_rounds_used: int,
    transcript_price_fallback: bool,
    extra: dict[str, Any] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "counter_offers": counter_offers,
        "final_agreed_price": final_agreed_price,
        "loadboard_rate": loadboard_rate,
        "extracted": extracted,
        "negotiation_rounds_used": negotiation_rounds_used,
        "transcript_price_fallback": transcript_price_fallback,
    }
    if extra:
        payload.update(extra)
    return json.dumps(payload, default=str)


def _transfer_message_for(
    outcome: str,
    *,
    too_many_rounds: bool = False,
) -> str | None:
    if outcome == OUTCOME_BOOKED:
        return MSG_BOOKED
    if outcome == OUTCOME_NEGOTIATED:
        return MSG_NEGOTIATE
    if outcome == OUTCOME_REJECTED_PRICE:
        return MSG_REJECT_TOO_MANY if too_many_rounds else MSG_REJECT_PRICE
    if outcome == OUTCOME_NO_INTEREST:
        return MSG_NO_INTEREST
    if outcome == OUTCOME_REJECTED_INVALID:
        return MSG_INVALID
    return None


def _suggested_counter_value(loadboard_rate: float, last_offer: float, upper_bound: float) -> float:
    mid = (loadboard_rate + last_offer) / 2.0
    return round(min(upper_bound, mid), 2)


def _response_dict(
    *,
    outcome: str,
    agreed_price: float | None,
    next_action: NextAction,
    sentiment: str,
    load_id: str | None,
    loadboard_rate: float | None,
    carrier_mc: str,
    counter_offers_echo: list[float] | None,
    rounds_used: int | None,
    carrier_interested_echo: bool | None,
    interested_reason_echo: str | None,
    suggested_counter: float | None = None,
    suggested_counter_reason: str | None = None,
    transfer_message: str | None = None,
    transfer_initiated: bool | None = None,
    transfer_status_message: str | None = None,
) -> dict[str, Any]:
    ap = round(agreed_price, 2) if agreed_price is not None else None
    body = ProcessCallResponse(
        outcome=outcome,
        agreed_price=ap,
        next_action=next_action,
        sentiment=sentiment,
        load_id=load_id,
        loadboard_rate=round(loadboard_rate, 2) if loadboard_rate is not None else None,
        carrier_mc=carrier_mc,
        counter_offers=counter_offers_echo,
        rounds_used=rounds_used,
        carrier_interested=carrier_interested_echo,
        interested_reason=interested_reason_echo,
        suggested_counter=round(suggested_counter, 2) if suggested_counter is not None else None,
        suggested_counter_reason=suggested_counter_reason,
        transfer_message=transfer_message,
        transfer_initiated=transfer_initiated,
        transfer_status_message=transfer_status_message,
        followup_needed=_followup_needed(outcome, sentiment),
        sentiment_warning=_sentiment_warning(outcome, sentiment),
    ).model_dump(exclude_none=True)
    return body


async def process_happyrobot_call(
    *,
    transcript: str,
    mc_number: str,
    interested_load_id: str | None,
    counter_offers: list[float],
    final_agreed_price: float | None,
    carrier_interested: bool | None,
    interested_reason: str | None,
    current_round: int | None,
    db: Session,
    redis: Any,
) -> tuple[dict[str, Any], Call, dict[str, Any]]:
    snippet = transcript[:SNIPPET_LEN]
    sentiment = sentiment_from_transcript(transcript)
    lane_pairs = _lane_pairs(db)
    parsed = parse_call_intent(transcript, lane_pairs=lane_pairs)

    mc_norm = normalize_mc_docket(mc_number)
    carrier_mc = mc_norm or mc_number.strip()

    effective_interested = carrier_interested
    if effective_interested is None:
        effective_interested = parsed.inferred_carrier_interested

    effective_load_id = (interested_load_id or "").strip() or None
    if not effective_load_id and parsed.load_id:
        effective_load_id = parsed.load_id.strip()

    raw_counters = list(counter_offers)
    transcript_price_used = False
    too_many_rounds = False

    load: Load | None = None
    loadboard_rate: float | None = None
    upper_bound: float | None = None
    outcome: str = OUTCOME_NO_INTEREST
    agreed_price: float | None = None
    next_action: NextAction = NEXT_END
    suggested_counter: float | None = None
    suggested_reason: str | None = None
    transfer_message: str | None = None
    transfer_initiated: bool | None = None
    transfer_status_message: str | None = None
    last_offer: float | None = None
    rounds_used = 0
    v: CarrierVerifyResponse | None = None

    def _extras_base() -> dict[str, Any]:
        return {
            "interested_reason": interested_reason,
            "parsed_lane_origin": parsed.lane_origin_hint,
            "parsed_lane_destination": parsed.lane_destination_hint,
            "inferred_carrier_interested": parsed.inferred_carrier_interested,
        }

    def persist_and_return(
        body: dict[str, Any],
        *,
        offers_extras: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], Call, dict[str, Any]]:
        extracted: dict[str, Any] = {
            "load_id": effective_load_id,
            "agreed_price": round(agreed_price, 2) if agreed_price is not None else None,
            "carrier_mc": carrier_mc,
            "sentiment": sentiment,
            "carrier_interested": carrier_interested,
            "interested_reason": interested_reason,
            "rounds_used": rounds_used,
        }
        extra = {**_extras_base(), **(offers_extras or {})}
        call = Call(
            timestamp=datetime.now(timezone.utc),
            mc=carrier_mc,
            load_id=effective_load_id,
            offers_json=_offers_payload(
                raw_counters,
                final_agreed_price,
                loadboard_rate,
                extracted,
                negotiation_rounds_used=rounds_used,
                transcript_price_fallback=transcript_price_used,
                extra=extra,
            ),
            outcome=body["outcome"],
            sentiment=sentiment,
            transcript_snippet=snippet,
        )
        db.add(call)
        db.commit()
        db.refresh(call)
        dbg = {
            "mc_valid": bool(v and v.valid),
            "load_found": load is not None,
            "loadboard_rate": round(loadboard_rate, 2) if loadboard_rate is not None else None,
            "upper_bound_1_1x": round(upper_bound, 2) if upper_bound is not None else None,
            "counter_offers": [round(c, 2) for c in raw_counters[:MAX_COUNTER_ROUNDS]],
            "last_offer": round(last_offer, 2) if last_offer is not None else None,
            "agreed_price": round(agreed_price, 2) if agreed_price is not None else None,
            "outcome": body["outcome"],
            "sentiment": sentiment,
            "next_action": body["next_action"],
            "rounds_used": rounds_used,
            "parsed": {
                "load_id": parsed.load_id,
                "prices": parsed.price_mentions,
                "lane_origin": parsed.lane_origin_hint,
                "lane_destination": parsed.lane_destination_hint,
            },
        }
        return body, call, dbg

    echo_counters = raw_counters[:] if raw_counters else None

    # --- Explicit decline (payload or transcript fallback) ---
    if effective_interested is False:
        decline_rounds = current_round if current_round is not None else min(len(raw_counters), MAX_COUNTER_ROUNDS)
        rounds_used = decline_rounds
        outcome = OUTCOME_NO_INTEREST
        next_action = NEXT_END
        transfer_message = MSG_CARRIER_DECLINED
        body = _response_dict(
            outcome=outcome,
            agreed_price=None,
            next_action=next_action,
            sentiment=sentiment,
            load_id=effective_load_id,
            loadboard_rate=None,
            carrier_mc=carrier_mc,
            counter_offers_echo=echo_counters,
            rounds_used=rounds_used,
            carrier_interested_echo=carrier_interested,
            interested_reason_echo=interested_reason,
            transfer_message=transfer_message,
        )
        return persist_and_return(body, offers_extras={"early_exit": "not_interested"})

    # --- Too many structured counter rounds ---
    if len(raw_counters) > MAX_COUNTER_ROUNDS:
        too_many_rounds = True
        rounds_used = current_round if current_round is not None else len(raw_counters)
        outcome = OUTCOME_REJECTED_PRICE
        next_action = NEXT_END
        transfer_message = _transfer_message_for(outcome, too_many_rounds=True)
        body = _response_dict(
            outcome=outcome,
            agreed_price=None,
            next_action=next_action,
            sentiment=sentiment,
            load_id=effective_load_id,
            loadboard_rate=None,
            carrier_mc=carrier_mc,
            counter_offers_echo=echo_counters,
            rounds_used=rounds_used,
            carrier_interested_echo=carrier_interested,
            interested_reason_echo=interested_reason,
            transfer_message=transfer_message,
        )
        return persist_and_return(body, offers_extras={"early_exit": "too_many_counters"})

    structured_rounds = len(raw_counters)
    rounds_used = current_round if current_round is not None else structured_rounds

    # --- FMCSA ---
    try:
        v = await verify_mc_carrier(mc_number, redis)
    except (FMCSAConfigurationError, FMCSAUpstreamError, ValueError):
        outcome = OUTCOME_REJECTED_INVALID
        next_action = NEXT_END
        transfer_message = _transfer_message_for(outcome)
        body = _response_dict(
            outcome=outcome,
            agreed_price=None,
            next_action=next_action,
            sentiment=sentiment,
            load_id=effective_load_id,
            loadboard_rate=None,
            carrier_mc=carrier_mc,
            counter_offers_echo=echo_counters,
            rounds_used=rounds_used,
            carrier_interested_echo=carrier_interested,
            interested_reason_echo=interested_reason,
            transfer_message=transfer_message,
        )
        return persist_and_return(body, offers_extras={"fmcsa_error": True})

    if not v.valid:
        outcome = OUTCOME_REJECTED_INVALID
        next_action = NEXT_END
        transfer_message = _transfer_message_for(outcome)
        body = _response_dict(
            outcome=outcome,
            agreed_price=None,
            next_action=next_action,
            sentiment=sentiment,
            load_id=effective_load_id,
            loadboard_rate=None,
            carrier_mc=carrier_mc,
            counter_offers_echo=echo_counters,
            rounds_used=rounds_used,
            carrier_interested_echo=carrier_interested,
            interested_reason_echo=interested_reason,
            transfer_message=transfer_message,
        )
        return persist_and_return(body)

    if not effective_load_id:
        outcome = OUTCOME_REJECTED_INVALID
        next_action = NEXT_END
        transfer_message = _transfer_message_for(outcome)
        body = _response_dict(
            outcome=outcome,
            agreed_price=None,
            next_action=next_action,
            sentiment=sentiment,
            load_id=None,
            loadboard_rate=None,
            carrier_mc=carrier_mc,
            counter_offers_echo=echo_counters,
            rounds_used=rounds_used,
            carrier_interested_echo=carrier_interested,
            interested_reason_echo=interested_reason,
            transfer_message=transfer_message,
        )
        return persist_and_return(body, offers_extras={"missing_load_id": True})

    load = db.get(Load, effective_load_id)
    if load is None:
        outcome = OUTCOME_REJECTED_INVALID
        next_action = NEXT_END
        transfer_message = _transfer_message_for(outcome)
        body = _response_dict(
            outcome=outcome,
            agreed_price=None,
            next_action=next_action,
            sentiment=sentiment,
            load_id=effective_load_id,
            loadboard_rate=None,
            carrier_mc=carrier_mc,
            counter_offers_echo=echo_counters,
            rounds_used=rounds_used,
            carrier_interested_echo=carrier_interested,
            interested_reason_echo=interested_reason,
            transfer_message=transfer_message,
        )
        return persist_and_return(body, offers_extras={"load_not_found": True})

    loadboard_rate = float(load.loadboard_rate)
    upper_bound = loadboard_rate * RATE_UPPER_MULTIPLIER

    use_counter_round_semantics = structured_rounds > 0
    last_offer = None

    if use_counter_round_semantics:
        last_offer = float(raw_counters[-1])
    else:
        if final_agreed_price is not None:
            last_offer = float(final_agreed_price)
        elif parsed.price_mentions:
            last_offer = float(max(parsed.price_mentions))
            transcript_price_used = True
            rounds_used = current_round if current_round is not None else 0

    if last_offer is None:
        outcome = OUTCOME_NO_INTEREST
        next_action = NEXT_END
        transfer_message = _transfer_message_for(outcome)
        body = _response_dict(
            outcome=outcome,
            agreed_price=None,
            next_action=next_action,
            sentiment=sentiment,
            load_id=effective_load_id,
            loadboard_rate=loadboard_rate,
            carrier_mc=carrier_mc,
            counter_offers_echo=echo_counters,
            rounds_used=rounds_used,
            carrier_interested_echo=carrier_interested,
            interested_reason_echo=interested_reason,
            transfer_message=transfer_message,
        )
        return persist_and_return(body, offers_extras={"no_numeric_offer": True})

    if last_offer <= upper_bound:
        outcome = OUTCOME_BOOKED
        agreed_price = last_offer
        next_action = NEXT_TRANSFER
        transfer_message = _transfer_message_for(outcome)
        transfer_initiated = True
        transfer_status_message = TRANSFER_STATUS_SPEC
        body = _response_dict(
            outcome=outcome,
            agreed_price=agreed_price,
            next_action=next_action,
            sentiment=sentiment,
            load_id=effective_load_id,
            loadboard_rate=loadboard_rate,
            carrier_mc=carrier_mc,
            counter_offers_echo=echo_counters,
            rounds_used=rounds_used,
            carrier_interested_echo=carrier_interested,
            interested_reason_echo=interested_reason,
            transfer_message=transfer_message,
            transfer_initiated=transfer_initiated,
            transfer_status_message=transfer_status_message,
        )
        return persist_and_return(body)

    if use_counter_round_semantics and structured_rounds < MAX_COUNTER_ROUNDS:
        outcome = OUTCOME_NEGOTIATED
        next_action = NEXT_CONTINUE
        suggested_counter = _suggested_counter_value(loadboard_rate, last_offer, upper_bound)
        suggested_reason = SUGGESTED_COUNTER_REASON
        transfer_message = _transfer_message_for(outcome)
        body = _response_dict(
            outcome=outcome,
            agreed_price=None,
            next_action=next_action,
            sentiment=sentiment,
            load_id=effective_load_id,
            loadboard_rate=loadboard_rate,
            carrier_mc=carrier_mc,
            counter_offers_echo=echo_counters,
            rounds_used=rounds_used,
            carrier_interested_echo=carrier_interested,
            interested_reason_echo=interested_reason,
            suggested_counter=suggested_counter,
            suggested_counter_reason=suggested_reason,
            transfer_message=transfer_message,
        )
        return persist_and_return(body)

    outcome = OUTCOME_REJECTED_PRICE
    next_action = NEXT_END
    transfer_message = _transfer_message_for(outcome, too_many_rounds=False)
    body = _response_dict(
        outcome=outcome,
        agreed_price=None,
        next_action=next_action,
        sentiment=sentiment,
        load_id=effective_load_id,
        loadboard_rate=loadboard_rate,
        carrier_mc=carrier_mc,
        counter_offers_echo=echo_counters,
        rounds_used=rounds_used,
        carrier_interested_echo=carrier_interested,
        interested_reason_echo=interested_reason,
        transfer_message=transfer_message,
    )
    return persist_and_return(body)
