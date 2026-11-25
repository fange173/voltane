import csv
import io
from datetime import date

import pytest

from app.routers import stats
from tests.test_api import rec as post_record


def rec(c, **body):
    r = post_record(c, **body)
    assert r.status_code == 200, r.text
    return r.json()["record"]


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(stats, "_today", lambda: date(2026, 11, 25))


def expense(c, **body):
    r = c.post("/api/expenses", json={"vehicle_id": c.vid, "category": "parking", **body})
    assert r.status_code == 200, r.text
    return r.json()


def stat(c, path, **params):
    r = c.get(f"/api/stats/{path}", params={"vehicle_id": c.vid, **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_audit_partial_cash_and_quantity(vehicle):
    c = vehicle
    rec(c, kind="snapshot", occurred_at="2026-11-01T08:00", odometer_km=0)
    a = rec(c, kind="charge", occurred_at="2026-11-08T08:00", odometer_km=100, charge_kwh=10, amount_cents=1900)
    b = rec(c, kind="charge", occurred_at="2026-11-09T08:00", odometer_km=200, charge_kwh=10)
    rec(c, kind="charge", occurred_at="2026-11-10T08:00", odometer_km=300, amount_cents=0)
    expense(c, occurred_at="2026-11-12T08:00", amount_cents=2300)
    for path, p in [("monthly-report", {"month": "2026-11"}), ("yearly-report", {"year": 2026})]:
        data = stat(c, path, **p)
        r = data["report"]
        assert (r["total_cents"], r["other_cents"], r["energy_cost_missing"]) == (4200, 2300, 1)
        assert (r["elec_kwh"], r["elec_qty_missing"]) == (20, 1)
        assert r["energy_cost_per_km"] is None and r["total_cost_per_km"] is None
        assert "每公里能源成本" not in data.get("sentence", "")
    second = c.post("/api/vehicles", json={"name": "gltest_fleet", "energy_type": "ice"})
    assert second.status_code == 200, second.text
    other_vid = second.json()["id"]
    for day, odo in [(1, 0), (15, 200)]:
        rec(c, vehicle_id=other_vid, kind="snapshot", occurred_at=f"2026-11-{day:02d}", odometer_km=odo)
    expense(c, vehicle_id=other_vid, occurred_at="2026-11-15", amount_cents=700)
    for path, known in [(f"/api/stats/cost?vehicle_id={c.vid}", 4200), ("/api/stats/cost", 4900)]:
        response = c.get(path)
        assert response.status_code == 200, response.text
        d = response.json()
        assert d["energy_cost_missing"] == 1 and d["cost_per_km"] is None
        assert d["months"][-1]["cost_per_km"] is None
        assert d["months"][-1]["cumulative"] == known
        assert d["months"][-1]["cumulative_missing"] == 1
    assert stat(c, "energy")["months"][-1]["energy_cost_per_km"] is None
    for target in (a, b):
        r = c.put(f"/api/records/{target['id']}", json={
            **{k: target[k] for k in ("vehicle_id", "kind", "occurred_at", "version", "charge_kwh", "odometer_km")},
            "amount_cents": 0,
        })
        assert r.status_code == 200, r.text
    rep = stat(c, "monthly-report", month="2026-11")["report"]
    assert rep["elec_qty_missing"] == 1 and rep["energy_cost_per_km"] == 0
    assert rep["total_cost_per_km"] == 0.077


def test_audit_charge_average_only_pairs(vehicle):
    c = vehicle
    for day, qty, amount in [(1, 10, 1000), (2, None, 900), (3, 10, None)]:
        rec(c, kind="charge", occurred_at=f"2026-11-0{day}T08:00", charge_type="dc", charge_kwh=qty, amount_cents=amount)
    [dc] = stat(c, "energy")["charging"]["types"]
    assert (dc["count"], dc["kwh"], dc["cents"], dc["avg_price"]) == (3, 20, 1900, 1)
    [dc] = stat(c, "energy", date_from="2026-11-02")["charging"]["types"]
    assert dc["avg_price"] is None


@pytest.mark.parametrize("start,end,expected", [(0, 100, 0), (50, 150, .5)])
def test_audit_health_coverage_clips_before_ledger(vehicle, start, end, expected):
    c = vehicle
    rec(c, kind="fuel", occurred_at="2026-10-01T08:00", odometer_km=start, fuel_is_full=True, fuel_liters=8)
    rec(c, kind="snapshot", occurred_at="2026-10-02T08:00", odometer_km=100, snapshot_tag="ledger_start")
    rec(c, kind="fuel", occurred_at="2026-10-03T08:00", odometer_km=end, fuel_is_full=True, fuel_liters=8)
    rec(c, kind="snapshot", occurred_at="2026-10-04T08:00", odometer_km=200)
    d = c.get(f"/api/vehicles/{c.vid}/health").json()
    assert d["ledger_km"] == 100 and d["coverage"]["fuel"] == expected


def test_audit_year_top_includes_extra_and_excludes_unpaid_snapshot(vehicle):
    c = vehicle
    rec(c, kind="snapshot", occurred_at="2026-11-01T08:00", odometer_km=0)
    assert stat(c, "yearly-report", year=2026)["top_item"] is None
    fuel = rec(c, kind="fuel", occurred_at="2026-11-03T08:00", amount_cents=1000, extra_fee_cents=500, fuel_liters=2)
    expense(c, occurred_at="2026-11-06T08:00", amount_cents=1200)
    y = stat(c, "yearly-report", year=2026)
    assert y["report"]["total_cents"] == 2700
    top = y["top_item"]
    assert (top["type"], top["id"], top["amount_cents"], top["paid_cents"]) == ("record", fuel["id"], 1000, 1500)


@pytest.mark.parametrize("old,new,pct", [(1, 10, None), (100, 110, "10%")])
def test_audit_month_sentence_uses_comparison_threshold(vehicle, old, new, pct):
    c = vehicle
    for ts, odo in [("2026-10-01", 0), ("2026-10-25", old), ("2026-11-01", old), ("2026-11-25", old + new)]:
        rec(c, kind="snapshot", occurred_at=ts + "T00:00", odometer_km=odo)
    d = stat(c, "monthly-report", month="2026-11")
    assert d["prev"]["mileage"] == old and d["report"]["mileage"] == new
    assert (d["prev"]["from"], d["prev"]["to"], d["report"]["from"], d["report"]["to"]) == (
        "2026-10-01", "2026-10-25", "2026-11-01", "2026-11-25")
    assert ("本月行驶 10 公里" if pct is None else "比上月同期多跑了 10%") in d["sentence"]


def test_audit_cost_summary_cumulative_preserves_cent(vehicle):
    c = vehicle
    r = c.post("/api/summaries", json={"vehicle_id": c.vid, "start_date": "2026-01-31", "end_date": "2026-02-01",
                                      "mode": "extra", "fuel_cents": 101})
    assert r.status_code == 200, r.text
    d = stat(c, "cost", date_from="2026-01-01", date_to="2026-02-28")
    assert [m["total"] for m in d["months"]] == [50, 51]
    assert d["months"][-1]["cumulative"] == d["total_cents"] == 101
    assert stat(c, "cost", date_from="2026-02-01", date_to="2026-02-28")["total_cents"] == 51


def test_audit_records_page_does_not_lose_same_timestamp(vehicle):
    c = vehicle
    for i in range(1, 62):
        ts = f"2026-11-01T08:{max(0, i - 2):02d}:00"
        expense(c, occurred_at=ts, title=f"gltest_{i}", amount_cents=i)
    params = {"vehicle_id": c.vid, "limit": 60}
    a = c.get("/api/records", params=params).json()
    b = c.get("/api/records", params={**params, "cursor": a["next"]}).json()
    items = a["items"] + b["items"]
    assert (len(a["items"]), len(b["items"])) == (60, 1)
    assert len({i["id"] for i in items}) == 61
    assert sum(i["amount_cents"] for i in items) == 1891


def test_audit_records_cursor_handles_same_id_and_timestamp(vehicle):
    from app.database import Expense, Record, SessionLocal
    from datetime import datetime

    c = vehicle
    ts = datetime(2026, 11, 1, 8, 0, 58)
    with SessionLocal() as db:
        params = {"id": 2099000000, "vehicle_id": c.vid, "occurred_at": ts, "created_by": c.uid}
        db.add(Record(**params, kind="snapshot"))
        db.add(Expense(**params, category="parking", amount_cents=100))
        db.commit()
    params = {"vehicle_id": c.vid, "limit": 1}
    a = c.get("/api/records", params=params).json()
    b = c.get("/api/records", params={**params, "cursor": a["next"]}).json()
    assert {(it["type"], it["id"]) for it in a["items"] + b["items"]} == {
        ("expense", 2099000000), ("record", 2099000000),
    }
    assert b["next"] is None


def test_audit_deleted_record_detail_returns_after_restore(vehicle):
    c = vehicle
    item = rec(c, kind="snapshot", odometer_km=0)
    url = f"/api/records/{item['id']}"
    assert c.get(url).status_code == 200
    assert c.delete(url).status_code == 200
    assert c.get(url).status_code == 404
    assert c.post(url + "/restore").status_code == 200
    assert c.get(url).json()["id"] == item["id"]


def test_audit_expense_csv_retains_zero_odometer(vehicle):
    c = vehicle
    for i, odo in enumerate([0, None, 45.5], 1):
        expense(c, occurred_at=f"2026-11-0{i}T08:00", title=f"gltest_{i}", amount_cents=1200, odometer_km=odo)
    r = c.get(f"/api/export?vehicle_id={c.vid}&format=expenses_csv")
    assert r.status_code == 200, r.text
    rows = {row[2]: row for row in list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))[1:]}
    assert [rows[f"gltest_{i}"][4] for i in (1, 2, 3)] == ["0.0", "", "45.5"]
    assert all(row[3] == "12.00" for row in rows.values())
