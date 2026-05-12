from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base, engine, get_db
from app.deps import require_api_key
from app.models import Load
from app.schemas import LoadCreate, LoadRead


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Freight Loads API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


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
