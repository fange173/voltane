import pytest

from app.routers import records


@pytest.mark.parametrize("kind,level", [("fuel", "fuel_level_before_pct"), ("charge", "soc_before_pct")])
def test_partial_level_record_can_be_saved_and_edited(vehicle, kind, level):
    body = {"vehicle_id": vehicle.vid, "kind": kind, "occurred_at": "2026-08-10T09:00",
            "odometer_km": 100, level: 60}
    assert vehicle.post("/api/records", json=body).status_code == 200
    snap = {**body, "kind": "snapshot", "occurred_at": "2026-08-12T09:00", "odometer_km": 200, level: 40}
    result = vehicle.post("/api/records", json=snap)
    assert result.status_code == 200, result.text
    rid = result.json()["record"]["id"]
    edited = vehicle.put(f"/api/records/{rid}", json={**snap, "note": "已核对"})
    assert edited.status_code == 200, edited.text
    args = {"vehicle_id": vehicle.vid, "range": "all"}
    assert vehicle.get("/api/stats/overview", params=args).status_code == 200
    assert len(vehicle.get("/api/records", params=args).json()["items"]) == 2


def test_preview_failure_does_not_persist_mutation(vehicle, monkeypatch):
    body = {"vehicle_id": vehicle.vid, "kind": "snapshot", "occurred_at": "2026-08-10T09:00",
            "odometer_km": 100, "note": "原备注"}
    rid = vehicle.post("/api/records", json=body).json()["record"]["id"]

    def unavailable(*args):
        raise RuntimeError("preview unavailable")

    monkeypatch.setattr(records, "_segment_preview", unavailable)
    with pytest.raises(RuntimeError, match="preview unavailable"):
        vehicle.post("/api/records", json={**body, "occurred_at": "2026-08-12T09:00"})
    with pytest.raises(RuntimeError, match="preview unavailable"):
        vehicle.put(f"/api/records/{rid}", json={**body, "note": "修改"})
    items = vehicle.get("/api/records", params={"vehicle_id": vehicle.vid}).json()["items"]
    assert [(item["id"], item["note"]) for item in items] == [(rid, "原备注")]
