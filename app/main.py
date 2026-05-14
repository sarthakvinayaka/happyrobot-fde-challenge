from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as redis
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from app.call_processing import process_happyrobot_call
from app.carrier_verify import (
    FMCSAConfigurationError,
    FMCSAUpstreamError,
    verify_mc_carrier,
)
from app.config import settings
from app.database import Base, engine, get_db
from app.metrics_service import build_metrics_bundle
from app.load_search import build_load_pitch, search_loads as search_loads_query
from app.models import Call, Load
from app.schemas import (
    CallRead,
    CarrierVerifyResponse,
    LoadCreate,
    LoadRead,
    ProcessCallRequest,
    ProcessCallResponse,
    SearchLoadSummary,
)


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
    """Require ``X-API-Key`` unless ``SKIP_API_KEY_AUTH`` is set (dev only) or route is public.

    Set env ``SKIP_API_KEY_AUTH=true`` to turn off key checks entirely (e.g. quick ngrok demos).
    **Do not use on a public production URL.** When auth is on, ``GET /``, ``/docs``,
    ``/openapi.json``, ``/redoc``, ``/dashboard``, and ``/v1/dashboard`` are exempt;
    other **``/v1/*``** routes need a key.
    """

    _PUBLIC_GET_PATHS = frozenset(
        {
            "/",
            "/docs",
            "/openapi.json",
            "/redoc",
            "/dashboard",
            "/v1/dashboard",
        }
    )

    def _exempt_from_api_key(self, request: Request) -> bool:
        if request.method != "GET":
            return False
        path = request.url.path
        norm = path.rstrip("/") or "/"
        return norm in self._PUBLIC_GET_PATHS

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)
        if settings.skip_api_key_auth:
            return await call_next(request)
        if self._exempt_from_api_key(request):
            return await call_next(request)
        expected = settings.api_key.strip()
        if not expected:
            return JSONResponse(
                {
                    "detail": "API_KEY is not configured on the server.",
                    "error_code": "API_KEY_NOT_CONFIGURED",
                },
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        got = (request.headers.get("X-API-Key") or "").strip()
        if got != expected:
            return JSONResponse(
                {
                    "detail": "Invalid or missing API key. Send header X-API-Key.",
                    "error_code": "INVALID_API_KEY",
                },
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        return await call_next(request)


v1 = APIRouter(prefix="/v1", tags=["v1"])


@v1.get("/health")
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


@v1.get("/metrics")
def metrics_json(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Aggregated call metrics for dashboards and external monitoring."""
    return build_metrics_bundle(db)


@v1.get("/search-loads", response_model=list[SearchLoadSummary])
def search_loads_endpoint(
    db: Session = Depends(get_db),
    origin: str | None = Query(default=None, description="Substring match on origin (case-insensitive)"),
    destination: str | None = Query(default=None, description="Substring match on destination (case-insensitive)"),
    equipment_type: str | None = Query(default=None, description="Exact equipment type (case-insensitive)"),
    earliest_pickup: str | None = Query(
        default=None,
        description="Pickup filter: YYYY-MM-DD compares lexically on stored pickup string prefix, else substring.",
    ),
    latest_pickup: str | None = Query(
        default=None,
        description="Pickup upper bound (same rules as earliest_pickup).",
    ),
    min_rate: float | None = Query(default=None, ge=0),
    max_rate: float | None = Query(default=None, ge=0),
    min_miles: int | None = Query(default=None, ge=0),
    max_miles: int | None = Query(default=None, ge=0),
    mode: str = Query(
        default="short",
        description="Pitch verbosity: 'short' (~200 chars) or 'detailed' (adds truncated notes when present).",
    ),
) -> list[SearchLoadSummary]:
    """Return candidate loads plus a speakable pitch line for the voice agent (call before /process-call).

    With **no** filter query params set, results are the earliest pickups first, limited to 20 rows.

    **HappyRobot usage:** Call this from an HTTP tool **after** the agent collects lane hints (origin,
    destination, equipment, optional rate/miles windows). Map tool query params from the conversation,
    then have the agent read ``pitch_text`` (and optionally ``load_id``) for one or more rows before
    asking if the carrier is interested.
    """
    pitch_mode = "detailed" if mode.strip().lower() == "detailed" else "short"
    rows = search_loads_query(
        db,
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
    return [
        SearchLoadSummary(
            load_id=r.load_id,
            origin=r.origin,
            destination=r.destination,
            equipment_type=r.equipment_type,
            pickup_datetime=r.pickup_datetime,
            delivery_datetime=r.delivery_datetime,
            miles=r.miles,
            loadboard_rate=float(r.loadboard_rate),
            notes=(r.notes or "").strip(),
            pitch_text=build_load_pitch(r, pitch_mode),
        )
        for r in rows
    ]


@v1.get("/loads", response_model=list[LoadRead])
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


@v1.post("/loads", response_model=LoadRead, status_code=201)
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


@v1.post("/process-call", response_model=ProcessCallResponse)
async def process_call_webhook(
    payload: ProcessCallRequest,
    request: Request,
    db: Session = Depends(get_db),
    debug: bool = Query(
        False,
        description="Include internal decision path (mc_valid, last_offer, bounds, …).",
    ),
) -> ProcessCallResponse | JSONResponse:
    """Classify the call, persist a row, and return the next conversational branch.

    **HappyRobot usage:** Call this from an HTTP tool **at the end of each negotiation turn** (and
    once after interest is confirmed if you send no counters yet). Send the **full transcript so far**,
    ``mc_number``, ``interested_load_id`` for the pitched load, ``counter_offers`` as the cumulative
    list of carrier numeric offers (newest last), optional ``current_round`` equal to
    ``len(counter_offers)``, and ``carrier_interested`` when the carrier explicitly said yes/no to
    proceeding. Branch the workflow on ``outcome`` and ``next_action`` in the JSON body (this
    endpoint usually returns **HTTP 200** even for business endings like ``no-interest``).
    """
    redis_client = getattr(request.app.state, "redis", None)
    body, _call, dbg = await process_happyrobot_call(
        transcript=payload.transcript,
        mc_number=payload.mc_number,
        interested_load_id=payload.interested_load_id,
        counter_offers=payload.counter_offers,
        final_agreed_price=payload.final_agreed_price,
        carrier_interested=payload.carrier_interested,
        interested_reason=payload.interested_reason,
        current_round=payload.current_round,
        db=db,
        redis=redis_client,
    )
    if debug:
        return JSONResponse({"debug": dbg, **body})
    return ProcessCallResponse(**body)


@v1.get("/calls", response_model=list[CallRead])
def list_calls(
    db: Session = Depends(get_db),
    outcome: str | None = Query(default=None, description="Filter by outcome (exact match)"),
) -> list[Call]:
    stmt = select(Call).order_by(Call.timestamp.desc())
    if outcome:
        stmt = stmt.where(Call.outcome == outcome.strip())
    return list(db.scalars(stmt).all())


@v1.get(
    "/verify-carrier/mc/{mc_number}",
    response_model=CarrierVerifyResponse,
    response_model_exclude_none=True,
)
async def verify_carrier_mc(mc_number: str, request: Request) -> CarrierVerifyResponse:
    """FMCSA-backed MC/docket check (cached in Redis when available).

    **HappyRobot usage:** Call this from an HTTP tool **right after** the agent captures the MC or
    docket number verbally (normalize digits in the tool URL path). If ``valid`` is false, have the
    agent politely end or correct; if true, continue to load search / pitch.

    On errors, the response body includes ``detail`` with ``message`` and ``error_code`` for branching.
    """
    redis_client = getattr(request.app.state, "redis", None)
    try:
        return await verify_mc_carrier(mc_number, redis_client)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": str(e),
                "error_code": "INVALID_MC_INPUT",
            },
        ) from e
    except FMCSAConfigurationError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": "FMCSA key is not configured. Obtain a free key at https://mobile.fmcsa.dot.gov/",
                "error_code": "FMCSA_NOT_CONFIGURED",
            },
        ) from None
    except FMCSAUpstreamError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"message": e.message, "error_code": "FMCSA_UPSTREAM_ERROR"},
        ) from e


def _streamlit_dashboard_redirect() -> RedirectResponse:
    url = settings.dashboard_entry_url.strip()
    if not url:
        url = "http://127.0.0.1:8501"
    return RedirectResponse(url=url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@v1.get("/dashboard")
def v1_dashboard_redirect() -> RedirectResponse:
    """Same as ``GET /dashboard`` for clients that wrongly prefix ``/v1``."""
    return _streamlit_dashboard_redirect()


app = FastAPI(title="Freight Loads API", version="1.0.0", lifespan=lifespan)

app.add_middleware(RequireApiKeyMiddleware)
app.add_middleware(
    CORSMiddleware,
    # Wildcard allows browser or server-side tools (e.g. HappyRobot cloud) to call a public HTTPS API.
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(v1)


@app.get("/")
def root() -> dict[str, str]:
    """Service index; HTTP API lives under `/v1`."""
    return {
        "service": "Freight Loads API",
        "api_prefix": "/v1",
        "docs": "/docs",
        "openapi": "/openapi.json",
        "health": "/v1/health",
        "metrics": "/v1/metrics",
        "search_loads": "/v1/search-loads",
        "dashboard_redirect": "/dashboard",
        "dashboard_redirect_v1": "/v1/dashboard",
    }


@app.get("/dashboard")
def dashboard_redirect() -> RedirectResponse:
    """Send browsers to the Streamlit app (path depends on reverse proxy / baseUrlPath)."""
    return _streamlit_dashboard_redirect()
