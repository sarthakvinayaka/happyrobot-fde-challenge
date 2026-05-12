"""ASGI shim: run `uvicorn main:app` from the repo root (same app as `app.main:app`)."""

from app.main import app

__all__ = ["app"]
