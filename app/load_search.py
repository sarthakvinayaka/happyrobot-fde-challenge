"""Search loads for voice agent pitch (SQLAlchemy filters + speakable pitch lines)."""

from __future__ import annotations

import re
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Load

PitchMode = Literal["short", "detailed"]
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _any_filter_set(
    *,
    origin: str | None,
    destination: str | None,
    equipment_type: str | None,
    earliest_pickup: str | None,
    latest_pickup: str | None,
    min_rate: float | None,
    max_rate: float | None,
    min_miles: int | None,
    max_miles: int | None,
) -> bool:
    return any(
        x is not None and (not isinstance(x, str) or x.strip() != "")
        for x in (
            origin,
            destination,
            equipment_type,
            earliest_pickup,
            latest_pickup,
            min_rate,
            max_rate,
            min_miles,
            max_miles,
        )
    )


def _rate_phrase(rate: float) -> str:
    if abs(rate - round(rate)) < 1e-6:
        return str(int(round(rate)))
    return str(round(rate, 2))


def _tighten_datetime_speak(s: str) -> str:
    """Normalize AM/PM and light spacing for TTS (no heavy date parsing)."""
    t = re.sub(r"\s+", " ", s.strip())
    t = re.sub(r"\bam\b", "AM", t, flags=re.IGNORECASE)
    t = re.sub(r"\bp\.m\.\b|\bpm\b", "PM", t, flags=re.IGNORECASE)
    return t


def build_load_pitch(load: Load, mode: PitchMode = "short") -> str:
    """Concise speakable pitch; keep under ~200 chars in ``short`` mode."""
    rate = _rate_phrase(float(load.loadboard_rate))
    pu = _tighten_datetime_speak(load.pickup_datetime)
    de = _tighten_datetime_speak(load.delivery_datetime)
    base = (
        f"{load.equipment_type} from {load.origin} to {load.destination}, "
        f"pickup {pu}, delivery {de}, {load.miles} miles, posted {rate} dollars."
    )
    if mode == "detailed" and (load.notes or "").strip():
        note = (load.notes or "").strip().replace("\n", " ")
        if len(note) > 90:
            note = note[:87] + "…"
        base = f"{base} Notes: {note}"
    base = re.sub(r"\s+", " ", base).strip()
    if mode == "short" and len(base) > 200:
        base = base[:197].rstrip() + "…"
    return base


def search_loads(
    db: Session,
    *,
    origin: str | None = None,
    destination: str | None = None,
    equipment_type: str | None = None,
    earliest_pickup: str | None = None,
    latest_pickup: str | None = None,
    min_rate: float | None = None,
    max_rate: float | None = None,
    min_miles: int | None = None,
    max_miles: int | None = None,
    limit: int = 50,
    default_unfiltered_limit: int = 20,
) -> list[Load]:
    stmt = select(Load)
    if origin:
        stmt = stmt.where(func.lower(Load.origin).contains(origin.strip().lower()))
    if destination:
        stmt = stmt.where(func.lower(Load.destination).contains(destination.strip().lower()))
    if equipment_type:
        stmt = stmt.where(func.lower(Load.equipment_type) == equipment_type.strip().lower())
    if earliest_pickup and earliest_pickup.strip():
        ep = earliest_pickup.strip()
        if _ISO_DATE.match(ep):
            stmt = stmt.where(Load.pickup_datetime >= ep)
        else:
            stmt = stmt.where(func.lower(Load.pickup_datetime).contains(ep.lower()))
    if latest_pickup and latest_pickup.strip():
        lp = latest_pickup.strip()
        if _ISO_DATE.match(lp):
            stmt = stmt.where(Load.pickup_datetime <= lp)
        else:
            stmt = stmt.where(func.lower(Load.pickup_datetime).contains(lp.lower()))
    if min_rate is not None:
        stmt = stmt.where(Load.loadboard_rate >= min_rate)
    if max_rate is not None:
        stmt = stmt.where(Load.loadboard_rate <= max_rate)
    if min_miles is not None:
        stmt = stmt.where(Load.miles >= min_miles)
    if max_miles is not None:
        stmt = stmt.where(Load.miles <= max_miles)

    has_filters = _any_filter_set(
        origin=origin,
        destination=destination,
        equipment_type=equipment_type,
        earliest_pickup=earliest_pickup,
        latest_pickup=latest_pickup,
        min_rate=min_rate,
        max_rate=max_rate,
        min_miles=min_miles,
        max_miles=max_miles,
    )
    if not has_filters:
        stmt = stmt.order_by(Load.pickup_datetime.asc()).limit(min(default_unfiltered_limit, 100))
    else:
        stmt = stmt.order_by(Load.pickup_datetime.asc()).limit(min(limit, 100))
    return list(db.scalars(stmt).all())
