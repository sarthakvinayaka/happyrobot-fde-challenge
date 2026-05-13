from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as redis
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.call_processing import process_happyrobot_call
from app.carrier_verify import (
    FMCSAConfigurationError,
    FMCSAUpstreamError,
    verify_mc_carrier,
)
from app.config import settings
from app.database import Base, engine, get_db
from app.metrics_service import build_metrics_bundle
from app.models import Call, Load
from app.schemas import CallRead, CarrierVerifyResponse, LoadCreate, LoadRead, ProcessCallRequest


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    app.state.redis = None
    client: redis.Redis | None = None
    try:
        client = redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
        )
        await client.ping()
        app.state.redis = client
        client = None
    except Exception:
        if client is not None:
            await client.aclose()
    yield
    if app.state.redis is not None:
        await app.state.redis.aclose()


class RequireApiKeyMiddleware(BaseHTTPMiddleware):
    """Require X-API-Key on every request (except CORS preflight)."""

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)
        expected = settings.api_key.strip()
        if not expected:
            return JSONResponse(
                {"detail": "API_KEY is not configured on the server."},
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        got = (request.headers.get("X-API-Key") or "").strip()
        if got != expected:
            return JSONResponse(
                {"detail": "Invalid or missing API key. Send header X-API-Key."},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        return await call_next(request)


app = FastAPI(title="Freight Loads API", version="1.0.0", lifespan=lifespan)

app.add_middleware(RequireApiKeyMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root() -> dict[str, str]:
    """Browser hits `/` by default; there is no HTML UI—use `/docs` for Swagger."""
    return {
        "service": "Freight Loads API",
        "docs": "/docs",
        "openapi": "/openapi.json",
        "health": "/health",
        "metrics": "/metrics",
        "dashboard": "/dashboard",
    }


@app.get("/health")
async def health(request: Request) -> dict[str, str]:
    out: dict[str, str] = {"status": "ok"}
    r = getattr(request.app.state, "redis", None)
    if r is None:
        out["redis"] = "disconnected"
    else:
        try:
            pong = await r.ping()
            out["redis"] = "ok" if pong else "error"
        except Exception:
            out["redis"] = "error"
    return out


@app.get("/metrics")
def metrics_json(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Aggregated call metrics for dashboards and external monitoring."""
    return build_metrics_bundle(db)


@app.get("/dashboard")
def dashboard_redirect() -> RedirectResponse:
    """Send browsers to the Streamlit app (path depends on reverse proxy / baseUrlPath)."""
    url = settings.dashboard_entry_url.strip()
    if not url:
        url = "http://127.0.0.1:8501"
    return RedirectResponse(url=url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@app.get("/loads", response_model=list[LoadRead])
def list_loads(
    db: Session = Depends(get_db),
    origin: str | None = Query(default=None, description="Filter by origin city (case-insensitive substring)"),
    equipment: str | None = Query(default=None, description="Filter by equipment type (case-insensitive exact)"),
) -> list[Load]:
    stmt = select(Load)
    if origin:
        stmt = stmt.where(func.lower(Load.origin).contains(origin.strip().lower()))
    if equipment:
        stmt = stmt.where(func.lower(Load.equipment_type) == equipment.strip().lower())
    return list(db.scalars(stmt).all())


@app.post("/loads", response_model=LoadRead, status_code=201)
def create_load(payload: LoadCreate, db: Session = Depends(get_db)) -> Load:
    row = Load(**payload.model_dump())
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Load with load_id '{payload.load_id}' already exists.",
        ) from None
    db.refresh(row)
    return row


@app.post("/process-call")
async def process_call_webhook(
    payload: ProcessCallRequest,
    request: Request,
    db: Session = Depends(get_db),
    debug: bool = Query(
        False,
        description="Include internal decision path (mc_valid, last_offer, bounds, …).",
    ),
) -> dict[str, Any]:
    redis_client = getattr(request.app.state, "redis", None)
    body, _call, dbg = await process_happyrobot_call(
        transcript=payload.transcript,
        mc_number=payload.mc_number,
        interested_load_id=payload.interested_load_id,
        counter_offers=payload.counter_offers,
        final_agreed_price=payload.final_agreed_price,
        db=db,
        redis=redis_client,
    )
    if debug:
        return {"debug": dbg, **body}
    return body


@app.get("/calls", response_model=list[CallRead])
def list_calls(
    db: Session = Depends(get_db),
    outcome: str | None = Query(default=None, description="Filter by outcome (exact match)"),
) -> list[Call]:
    stmt = select(Call).order_by(Call.timestamp.desc())
    if outcome:
        stmt = stmt.where(Call.outcome == outcome.strip())
    return list(db.scalars(stmt).all())


@app.get(
    "/verify-carrier/mc/{mc_number}",
    response_model=CarrierVerifyResponse,
    response_model_exclude_none=True,
)
async def verify_carrier_mc(mc_number: str, request: Request) -> CarrierVerifyResponse:
    redis_client = getattr(request.app.state, "redis", None)
    try:
        return await verify_mc_carrier(mc_number, redis_client)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except FMCSAConfigurationError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="FMCSA_WEB_KEY is not configured. Obtain a free key at https://mobile.fmcsa.dot.gov/",
        ) from None
    except FMCSAUpstreamError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=e.message) from e
