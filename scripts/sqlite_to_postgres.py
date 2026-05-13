#!/usr/bin/env python3
"""Copy `loads` and `calls` from SQLite into Postgres (creates tables if missing).

Destination: ``DATABASE_URL`` must be a Postgres URL (e.g. postgresql+psycopg://...).

Usage::

    export DATABASE_URL=postgresql+psycopg://freight:YOURPASS@localhost:5432/freight
    cd /path/to/repo && PYTHONPATH=. python scripts/sqlite_to_postgres.py sqlite:///./data/loads.db

Requires: ``pip install -r requirements.txt -r requirements-prod.txt`` in your venv.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base, Call, Load


def main() -> None:
    sqlite_url = sys.argv[1] if len(sys.argv) > 1 else "sqlite:///./data/loads.db"
    pg_url = os.environ.get("DATABASE_URL", "").strip()
    if not pg_url or "postgresql" not in pg_url:
        print("Set DATABASE_URL to a Postgres URL (postgresql+psycopg://...).", file=sys.stderr)
        sys.exit(1)

    src_engine = create_engine(sqlite_url, connect_args={"check_same_thread": False})
    dst_engine = create_engine(pg_url, pool_pre_ping=True)
    Base.metadata.create_all(bind=dst_engine)

    SrcSession = sessionmaker(bind=src_engine)
    DstSession = sessionmaker(bind=dst_engine)

    with SrcSession() as src, DstSession() as dst:
        loads = list(src.scalars(select(Load)).all())
        for row in loads:
            src.expunge(row)
            dst.merge(row)
        dst.commit()

        calls = list(src.scalars(select(Call)).all())
        for row in calls:
            src.expunge(row)
            dst.merge(row)
        dst.commit()

    print(f"Migrated {len(loads)} loads and {len(calls)} calls.")


if __name__ == "__main__":
    main()
