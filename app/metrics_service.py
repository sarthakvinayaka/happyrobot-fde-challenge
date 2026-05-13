"""Aggregate metrics from `calls` for dashboards and GET /metrics."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Call


def _parse_offers(row: Call) -> dict[str, Any]:
    try:
        data = json.loads(row.offers_json)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _call_ts_utc(c: Call) -> datetime:
    """Normalize Call.timestamp to aware UTC for comparisons."""
    ts = c.timestamp
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def calls_in_window(rows: Sequence[Call], hours: int) -> list[Call]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    return [c for c in rows if _call_ts_utc(c) >= cutoff]


def build_metrics_for_calls(calls: Sequence[Call]) -> dict[str, Any]:
    """Compute dashboard metrics for an already-filtered list of Call rows."""
    total = len(calls)
    if total == 0:
        return {
            "total_calls": 0,
            "booked_pct": 0.0,
            "avg_negotiation_rounds": 0.0,
            "avg_rate_premium_pct": None,
            "sentiment_breakdown": {"positive": 0, "neutral": 0, "negative": 0},
            "outcomes_breakdown": {},
            "top_loads_by_bookings": [],
            "top_mcs_by_bookings": [],
        }

    booked = [c for c in calls if c.outcome == "booked"]
    booked_pct = round(100.0 * len(booked) / total, 2) if total else 0.0

    rounds_list: list[int] = []
    premiums: list[float] = []
    for c in calls:
        o = _parse_offers(c)
        cos = o.get("counter_offers") or []
        if isinstance(cos, list):
            rounds_list.append(len(cos))
        else:
            rounds_list.append(0)

    for c in booked:
        o = _parse_offers(c)
        ext = o.get("extracted") or {}
        lr = o.get("loadboard_rate")
        ap = ext.get("agreed_price")
        if isinstance(lr, (int, float)) and isinstance(ap, (int, float)) and lr > 0:
            premiums.append((float(ap) - float(lr)) / float(lr) * 100.0)

    avg_rounds = round(sum(rounds_list) / len(rounds_list), 3) if rounds_list else 0.0
    avg_premium = round(sum(premiums) / len(premiums), 3) if premiums else None

    sent = Counter(c.sentiment for c in calls)
    sentiment_breakdown = {
        "positive": int(sent.get("positive", 0)),
        "neutral": int(sent.get("neutral", 0)),
        "negative": int(sent.get("negative", 0)),
    }

    outcomes_breakdown = dict(Counter(c.outcome for c in calls))

    load_book = Counter(c.load_id for c in booked if c.load_id)
    mc_book = Counter(c.mc for c in booked)

    top_loads = [{"load_id": lid, "bookings": n} for lid, n in load_book.most_common(10)]
    top_mcs = [{"mc": mc, "bookings": n} for mc, n in mc_book.most_common(10)]

    return {
        "total_calls": total,
        "booked_pct": booked_pct,
        "avg_negotiation_rounds": avg_rounds,
        "avg_rate_premium_pct": avg_premium,
        "sentiment_breakdown": sentiment_breakdown,
        "outcomes_breakdown": outcomes_breakdown,
        "top_loads_by_bookings": top_loads,
        "top_mcs_by_bookings": top_mcs,
    }


def build_metrics_bundle(db: Session) -> dict[str, Any]:
    """Load all calls once, then slice 24h / 7d in Python (small/medium tables)."""
    rows = list(db.scalars(select(Call).order_by(Call.id.desc())).all())
    c24 = calls_in_window(rows, 24)
    c7d = calls_in_window(rows, 24 * 7)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "last_24h": build_metrics_for_calls(c24),
        "last_7d": build_metrics_for_calls(c7d),
    }
