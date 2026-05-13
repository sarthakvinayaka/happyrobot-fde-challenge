"""Tests for GET /v1/search-loads."""

from tests.conftest import _sample_load


def test_search_loads_origin_filter(client, db_session):
    db_session.add(_sample_load("LD-A", rate=1000))
    db_session.add(_sample_load("LD-B", rate=2000))
    db_session.commit()

    r = client.get("/v1/search-loads", params={"origin": "chicago"})
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    assert all(
        "pitch_text" in row and "Chicago" in row["pitch_text"] and "notes" in row for row in data
    )


def test_search_loads_rate_bounds(client, db_session):
    db_session.add(_sample_load("LD-LOW", rate=500))
    db_session.add(_sample_load("LD-MID", rate=1500))
    db_session.commit()

    r = client.get("/v1/search-loads", params={"min_rate": 1000, "max_rate": 2000})
    ids = {row["load_id"] for row in r.json()}
    assert "LD-MID" in ids
    assert "LD-LOW" not in ids


def test_search_loads_no_filters_default_limit_and_pitch(client, db_session):
    for i in range(25):
        row = _sample_load(f"LD-BULK-{i}", rate=1000.0 + i)
        row.pickup_datetime = f"2025-05-{i + 1:02d} 08:00"
        db_session.add(row)
    db_session.commit()

    r = client.get("/v1/search-loads")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 20
    for row in data:
        assert row["load_id"]
        assert "pitch_text" in row
        assert len(row["pitch_text"]) <= 250
        assert "miles" in row["pitch_text"].lower() or "miles" in row["pitch_text"]


def test_search_loads_detailed_mode_adds_notes(client, db_session):
    ld = _sample_load("LD-NOTES", rate=1500.0)
    ld.notes = "Hazmat paperwork required at pickup dock seven."
    db_session.add(ld)
    db_session.commit()

    r_short = client.get("/v1/search-loads", params={"origin": "Chicago", "mode": "short"})
    r_det = client.get("/v1/search-loads", params={"origin": "Chicago", "mode": "detailed"})
    assert r_short.status_code == 200 and r_det.status_code == 200
    short_pt = r_short.json()[0]["pitch_text"]
    det_pt = r_det.json()[0]["pitch_text"]
    assert "Hazmat" not in short_pt
    assert "Notes" in det_pt and "Hazmat" in det_pt
