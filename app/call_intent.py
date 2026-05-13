"""Lightweight transcript parsing: load id, prices, lane hints, disinclination phrases."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_LOAD_ID_PAT = re.compile(r"\b((?:LD|LOAD)[-_]?[A-Z0-9]{3,32})\b", re.IGNORECASE)

_DISINTEREST_PAT = re.compile(
    r"\b(?:not\s+interested|no\s+thanks|no\s+thank\s+you|pass\s+on\s+this|pass\s+on\s+it|"
    r"not\s+for\s+us|i'?ll\s+pass|we'?ll\s+pass|don'?t\s+want|not\s+interested\s+in)\b",
    re.IGNORECASE,
)


@dataclass
class ParsedCallIntent:
    """Hints from transcript when structured request fields are missing."""

    load_id: str | None = None
    price_mentions: list[float] = field(default_factory=list)
    lane_origin_hint: str | None = None
    lane_destination_hint: str | None = None
    inferred_carrier_interested: bool | None = None


def _extract_prices(text: str) -> list[float]:
    found: list[float] = []
    patterns = (
        r"\$\s*([\d,]+(?:\.\d{1,2})?)",
        r"(?:\bat\b|\bfor\b|\babout\b|\baround\b)\s+\$?\s*([\d,]+(?:\.\d{1,2})?)\b",
        r"\b([\d,]+(?:\.\d{1,2})?)\s+dollars?\b",
    )
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            raw = m.group(1).replace(",", "")
            try:
                v = float(raw)
            except ValueError:
                continue
            if 50.0 <= v <= 500_000.0:
                found.append(round(v, 2))
    seen: set[float] = set()
    out: list[float] = []
    for p in found:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _lane_hints_from_transcript(
    transcript_lower: str,
    lane_pairs: list[tuple[str, str]],
) -> tuple[str | None, str | None]:
    """If a known origin/destination substring appears in the transcript, record first hits."""
    origin_hit: str | None = None
    dest_hit: str | None = None
    for o, d in lane_pairs:
        ol, dl = o.strip().lower(), d.strip().lower()
        if ol and ol in transcript_lower and origin_hit is None:
            origin_hit = o.strip()
        if dl and dl in transcript_lower and dest_hit is None:
            dest_hit = d.strip()
    return origin_hit, dest_hit


def parse_call_intent(
    transcript: str,
    *,
    lane_pairs: list[tuple[str, str]] | None = None,
) -> ParsedCallIntent:
    if not (transcript or "").strip():
        return ParsedCallIntent()

    tl = transcript.lower()
    load_id: str | None = None
    m = _LOAD_ID_PAT.search(transcript)
    if m:
        load_id = m.group(1).upper().replace("_", "-")

    prices = _extract_prices(transcript)

    inferred: bool | None = None
    if _DISINTEREST_PAT.search(transcript):
        inferred = False

    lo, ld = (None, None)
    if lane_pairs:
        lo, ld = _lane_hints_from_transcript(tl, lane_pairs)

    return ParsedCallIntent(
        load_id=load_id,
        price_mentions=prices,
        lane_origin_hint=lo,
        lane_destination_hint=ld,
        inferred_carrier_interested=inferred,
    )
