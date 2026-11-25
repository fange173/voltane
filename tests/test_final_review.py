import json


def test_import_is_atomic_and_undo_keeps_edits(vehicle):
    c = vehicle
    v = c.vid
    pay = {"category": "parking", "amount_cents": 500, "occurred_at": "2099-01-01T09:00"}
    data = {"app": "voltane", "records": [{"kind": "snapshot", "occurred_at": pay["occurred_at"]}],
            "expenses": [pay], "summaries": [{"start_date": "2098-01-01", "end_date": "2098-02-01"}],
            "reminders": [{"title": "gltest_年检", "due_date": "2099-04-01"}]}

    def load(payload):
        return c.post(f"/api/import?vehicle_id={v}", files={"file": ("snapshot.json", json.dumps(payload))})

    def dump():
        return c.get(f"/api/export?vehicle_id={v}&format=json").json()

    original = c.post("/api/expenses", json={**pay, "vehicle_id": v}).json()["expense"]["id"]
    assert load({**data, "expenses": [pay, {**pay, "amount_cents": -2}]}).status_code == 400
    assert not dump()["records"] and len(dump()["expenses"]) == 1
    batch = load(data).json()
    assert all(batch[k] == 1 for k in ("records", "expenses", "summaries", "reminders"))
    eid = next(e["id"] for e in dump()["expenses"] if e["id"] != original)
    assert c.put(f"/api/expenses/{eid}", json={**pay, "vehicle_id": v, "amount_cents": 700}).status_code == 200
    undo = c.delete(f"/api/import/{batch['batch']}?vehicle_id={v}").json()
    assert undo == {"records": 1, "expenses": 0, "summaries": 1, "reminders": 1, "skipped": 1}
    result = dump()
    assert {e["id"]: e["amount_cents"] for e in result["expenses"]} == {original: 500, eid: 700}
    assert not any(result[k] for k in ("records", "summaries", "reminders"))


def test_reminders_keep_real_sources(vehicle):
    c = vehicle
    v = {"vehicle_id": c.vid}
    p = {**v, "category": "insurance", "amount_cents": 500, "occurred_at": "2098-01-01T09:00"}
    exp = c.post("/api/expenses", json={**p, "period_end": "2099-01-01"}).json()["expense"]
    older = c.post("/api/expenses", json={**p, "period_end": "2098-06-01"}).json()
    assert older["insurance_reminder"]["due_date"] == "2099-01-01"
    assert c.put(f"/api/expenses/{exp['id']}", json=p).json()["insurance_reminder"]["due_date"] == "2098-06-01"
    c.delete(f"/api/expenses/{older['expense']['id']}")
    rem = next(r for r in c.get("/api/reminders", params=v).json() if r["template_key"] == "insurance")
    assert not rem["is_active"] and rem["due_date"] is None
    assert c.post(f"/api/expenses/{older['expense']['id']}/restore").status_code == 200
    assert c.put(f"/api/reminders/{rem['id']}", json={**v, "title": "人工止期", "template_key": "insurance", "due_date": "2099-12-31"}).status_code == 200
    c.delete(f"/api/expenses/{older['expense']['id']}")
    rem = next(r for r in c.get("/api/reminders", params=v).json() if r["template_key"] == "insurance")
    assert rem["is_active"] and rem["due_date"] == "2099-12-31"
    rid = c.post("/api/reminders", json={**v, "title": "保养", "due_odometer_km": 1000, "interval_km": 500}).json()["id"]
    assert c.post(f"/api/reminders/{rid}/complete", json={"done_at": "2099-01-01"}).status_code == 400
    assert next(r for r in c.get("/api/reminders", params=v).json() if r["id"] == rid)["due_odometer_km"] == 1000


def test_place_scope_change_preserves_old_name(vehicle):
    c = vehicle
    base = {"vehicle_id": c.vid, "name": "gltest_公共桩", "kind": "charge_station", "shared": True}
    pid = c.post("/api/places", json=base).json()["id"]
    rid = c.post("/api/records", json={"vehicle_id": c.vid, "kind": "charge", "occurred_at": "2099-01-01T09:00", "place_id": pid}).json()["record"]["id"]
    updated = c.put(f"/api/places/{pid}", json={**base, "shared": False, "name": "gltest_私人桩"}).json()
    assert updated["shared"] is False
    old = c.get(f"/api/records/{rid}").json()
    assert old["place_id"] is None and old["place_name"] == base["name"]
