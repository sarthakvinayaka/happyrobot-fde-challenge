from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.carrier_verify import (
    FMCSAConfigurationError,
    FMCSAUpstreamError,
    verify_mc_carrier,
)
from app.config import settings
from app.database import Base, engine, get_db
from app.deps import require_api_key
from app.models import Load
from app.schemas import CarrierVerifyResponse, LoadCreate, LoadRead


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


app = FastAPI(title="Freight Loads API", version="1.0.0", lifespan=lifespan)

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


@app.get("/loads", response_model=list[LoadRead], dependencies=[Depends(require_api_key)])
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


@app.post("/loads", response_model=LoadRead, status_code=201, dependencies=[Depends(require_api_key)])
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


@app.get(
    "/verify-carrier/mc/{mc_number}",
    response_model=CarrierVerifyResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(require_api_key)],
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
