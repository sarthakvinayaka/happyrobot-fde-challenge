"""Pytest fixtures: in-memory DB, API key, FastAPI TestClient with dependency overrides."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Load


def _sample_load(
    load_id: str = "LD-HAPPY",
    *,
    rate: float = 2000.0,
    miles: int = 925,
) -> Load:
    return Load(
        load_id=load_id,
        origin="Chicago",
        destination="Dallas",
        pickup_datetime="May 14th at 8am",
        delivery_datetime="May 16th afternoon",
        equipment_type="dry van",
        loadboard_rate=rate,
        notes="",
        weight=1000,
        commodity_type="general",
        num_of_pieces=1,
        miles=miles,
        dimensions="53ft",
    )


@pytest.fixture
def db_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr("app.config.settings.api_key", "test", raising=False)

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    tc = TestClient(app)
    tc.headers["X-API-Key"] = "test"
    yield tc
    app.dependency_overrides.clear()


@pytest.fixture
def valid_carrier(monkeypatch: pytest.MonkeyPatch):
    from unittest.mock import AsyncMock

    from app.schemas import CarrierVerifyResponse

    mock = AsyncMock(return_value=CarrierVerifyResponse(valid=True, details={"docket": "123"}))
    monkeypatch.setattr("app.call_processing.verify_mc_carrier", mock)
    return mock
