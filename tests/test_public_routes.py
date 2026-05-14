"""Public routes that skip API key (browser-friendly)."""


def test_root_and_docs_without_api_key(client):
    del client.headers["X-API-Key"]
    assert client.get("/").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_v1_still_requires_api_key(client):
    del client.headers["X-API-Key"]
    r = client.get("/v1/health")
    assert r.status_code == 401
    assert r.json().get("error_code") == "INVALID_API_KEY"
