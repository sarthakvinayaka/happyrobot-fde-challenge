"""FMCSA QCMobile carrier lookup by MC (interstate commerce) docket number."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.config import settings
from app.schemas import CarrierVerifyResponse

FMCSA_SERVICES_BASE = "https://mobile.fmcsa.dot.gov/qc/services/"
CACHE_KEY_PREFIX = "carrier:mc:v3:"
CACHE_TTL_SECONDS = 3600


class FMCSAConfigurationError(Exception):
    """Raised when FMCSA_WEB_KEY is missing."""


class FMCSAUpstreamError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def normalize_mc_docket(mc_number: str) -> str:
    s = mc_number.strip().upper().replace(" ", "")
    s = re.sub(r"^(MC|MX)[-]?", "", s, flags=re.IGNORECASE)
    return re.sub(r"\D", "", s)


def _walk_json(obj: Any) -> tuple[str | None, str | None]:
    """Return (allowToOperate, outOfService) as uppercased single-char strings if found.

    FMCSA QCMobile snapshots use ``allowedToOperate`` (not ``allowToOperate``). Some
    payloads use ``outOfService``; others expose an ``oosDate`` when authority is OOS.
    """
    allow_to: str | None = None
    oos: str | None = None
    stack: list[Any] = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                lk = k.lower()
                if lk in ("allowtooperate", "allowedtooperate") and v is not None and not isinstance(v, (dict, list)):
                    allow_to = str(v).strip().upper()[:1] or None
                elif lk == "outofservice" and v is not None and not isinstance(v, (dict, list)):
                    oos = str(v).strip().upper()[:1] or None
                elif lk == "oosdate" and v is not None and not isinstance(v, (dict, list)):
                    s = str(v).strip()
                    if s and s.lower() not in ("none", "null", "0000-00-00"):
                        oos = "Y"
                elif isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
    return allow_to, oos


def evaluate_carrier_payload(payload: dict[str, Any]) -> tuple[bool, str | None]:
    """
    Valid when FMCSA marks the carrier allowed to operate (``allowToOperate`` or
    ``allowedToOperate`` == ``Y``) and not out of service (``outOfService`` / ``oosDate``).
    """
    allow_to, oos = _walk_json(payload)
    oos_eff = oos or "N"
    if oos_eff == "Y":
        return False, "Out of service"
    if allow_to != "Y":
        return False, "Not authorized to operate"
    return True, None


def _fmcsa_404_is_bad_webkey(resp: httpx.Response) -> bool:
    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError):
        return False
    return isinstance(data, dict) and data.get("content") == "Webkey not found"


def build_fmcsa_docket_urls(docket_digits: str) -> tuple[str, str]:
    base = f"{FMCSA_SERVICES_BASE}carriers/docket-number/{docket_digits}"
    return base, f"{base}/"


async def fetch_carrier_from_fmcsa(docket_digits: str) -> tuple[int, dict[str, Any] | None]:
    if not settings.fmcsa_web_key.strip():
        raise FMCSAConfigurationError()

    url_plain, url_slash = build_fmcsa_docket_urls(docket_digits)
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url_plain, params={"webKey": settings.fmcsa_web_key})
        if resp.status_code == 404 and _fmcsa_404_is_bad_webkey(resp):
            raise FMCSAUpstreamError("Invalid FMCSA_WEB_KEY (FMCSA returned 'Webkey not found')")
        if resp.status_code == 404:
            resp = await client.get(url_slash, params={"webKey": settings.fmcsa_web_key})
        if resp.status_code == 404 and _fmcsa_404_is_bad_webkey(resp):
            raise FMCSAUpstreamError("Invalid FMCSA_WEB_KEY (FMCSA returned 'Webkey not found')")

    if resp.status_code == 404:
        return 404, None
    if resp.status_code == 401:
        raise FMCSAUpstreamError("FMCSA rejected the web key (401 Unauthorized)")
    if resp.status_code >= 500:
        raise FMCSAUpstreamError(f"FMCSA server error ({resp.status_code})")
    if resp.status_code >= 400:
        raise FMCSAUpstreamError(f"FMCSA request failed ({resp.status_code})")

    try:
        data = resp.json()
    except json.JSONDecodeError as e:
        raise FMCSAUpstreamError("FMCSA returned non-JSON body") from e

    if not isinstance(data, dict):
        raise FMCSAUpstreamError("Unexpected FMCSA JSON shape")

    content = data.get("content")
    if content == [] or content is None:
        return 404, None

    return 200, data


async def verify_mc_carrier(mc_number: str, redis: Any) -> CarrierVerifyResponse:
    digits = normalize_mc_docket(mc_number)
    if not digits:
        raise ValueError("Invalid MC number")

    if not settings.fmcsa_web_key.strip():
        raise FMCSAConfigurationError()

    cache_key = CACHE_KEY_PREFIX + digits

    if redis is not None:
        try:
            cached = await redis.get(cache_key)
        except Exception:
            cached = None
        if cached:
            try:
                env = json.loads(cached)
            except json.JSONDecodeError:
                env = None
            if isinstance(env, dict):
                if env.get("kind") == "not_found":
                    return CarrierVerifyResponse(valid=False, reason="Carrier not found")
                if env.get("kind") == "carrier":
                    payload = env.get("payload")
                    if isinstance(payload, dict):
                        ok, reason = evaluate_carrier_payload(payload)
                        if ok:
                            return CarrierVerifyResponse(valid=True, details=payload)
                        return CarrierVerifyResponse(valid=False, reason=reason or "Invalid carrier status")

    status, payload = await fetch_carrier_from_fmcsa(digits)

    if status == 404:
        if redis is not None:
            try:
                await redis.setex(cache_key, CACHE_TTL_SECONDS, json.dumps({"kind": "not_found"}))
            except Exception:
                pass
        return CarrierVerifyResponse(valid=False, reason="Carrier not found")

    assert payload is not None
    ok, reason = evaluate_carrier_payload(payload)

    if redis is not None:
        try:
            await redis.setex(
                cache_key,
                CACHE_TTL_SECONDS,
                json.dumps({"kind": "carrier", "payload": payload}, default=str),
            )
        except Exception:
            pass

    if ok:
        return CarrierVerifyResponse(valid=True, details=payload)
    return CarrierVerifyResponse(valid=False, reason=reason or "Invalid carrier status")
