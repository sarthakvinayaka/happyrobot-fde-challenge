"""Tests for API error bodies (e.g. error_code for tools)."""


def test_missing_api_key_returns_error_code(client):
    del client.headers["X-API-Key"]
    r = client.get("/v1/health")
    assert r.status_code == 401
    data = r.json()
    assert data.get("error_code") == "INVALID_API_KEY"
    assert "detail" in data
