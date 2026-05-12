"""Populate SQLite `loads` table from data/sample_loads.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.database import SessionLocal, engine
from app.models import Base, Load


def main() -> None:
    json_path = ROOT / "data" / "sample_loads.json"
    raw = json.loads(json_path.read_text(encoding="utf-8"))
    Base.metadata.create_all(bind=engine)
    session: Session = SessionLocal()
    try:
        session.execute(delete(Load))
        for item in raw:
            session.add(Load(**item))
        session.commit()
        print(f"Seeded {len(raw)} loads from {json_path}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
