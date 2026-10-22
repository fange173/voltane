"""
stats.py — 概览、能耗、花费、里程、日历、月报、年报
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import my_vehicle_ids, require_vehicle
from app.auth import get_current_user
from app.constants import EXPENSE_CATEGORIES
from app.database import Expense, Record, User, Vehicle, get_db, localnow
from app.engine.aggregate import (
    avg_daily_km,
    data_span,
    delivery_index,
    energy_totals,
    latest_energy,
    mileage,
    month_bounds,
    months_between,
    odo_observations,
    period_report,
    rough,
    segment_dict,
    since_last_full,
)
from app.insights import latest_odo_of, pending_items
from app.routers.expenses import collect_reminders
from app.routers.records import _attach_users, _badges
from app.services import expense_dict, iso, load_expenses, load_ledger, record_dict, vehicle_dict

router = APIRouter(prefix="/api/stats", tags=["stats"])

MAINT = ("maintenance", "repair", "tires")


def _today() -> date:
    return localnow().date()


def resolve_range(rng: str, d_from: Optional[date], d_to: Optional[date], span: Optional[tuple]) -> tuple:
    """返回 (d0, d1, prev_d0, prev_d1)；prev 用于和上一周期对比。"""
    t = _today()
    if rng == "month":
        d0, d1 = month_bounds(t.year, t.month)
        p1 = d0 - timedelta(days=1)
        return d0, d1, p1.replace(day=1), p1
    if rng == "30d":
        d0 = t - timedelta(days=29)
        return d0, t, d0 - timedelta(days=30), d0 - timedelta(days=1)
    if rng == "year":
        return date(t.year, 1, 1), date(t.year, 12, 31), date(t.year - 1, 1, 1), date(t.year - 1, 12, 31)
    if rng == "custom" and d_from and d_to:
        return d_from, d_to, None, None
    if span:
        return span[0], max(span[1], t), None, None
    return t, t, None, None


def _spark(segs, n=10) -> list:
    pts = [s for s in segs if s.status == "ok" and s.kind == "drive" and not s.excluded and s.per100 is not None]
    return [{"ts": iso(s.end_ts), "per100": round(s.per100, 2), "quality": s.quality} for s in pts[-n:]]


def _history(led, v: Vehicle, exps) -> Optional[dict]:
    """「提车至今」卡片：总里程、总花费、粗略能耗。没有提车记录时改为「记账以来」，
    记账起点之前的历史汇总单独列出，不混进每公里成本（那段里程未知）。"""
    di = delivery_index(led)
    if di is None:
        return None
    start = led.points[di]
    d0 = start.ts.date()
    has_delivery = start.rec.snapshot_tag == "delivery"
    before_cents = 0
    for sm in led.summaries:
        if sm.end < d0:
            d = led.summary_diffs.get(sm.id, {})
            before_cents += (d.get("fuel_cents") or 0) + (d.get("elec_cents") or 0)
    t = _today()
    rep = period_report(led, exps, d0, t, v.initial_odometer_km)
    out = {
        "since": d0.isoformat(),
        "days": (t - d0).days,
        "mileage": rep["mileage"],
        "total_cents": rep["total_cents"],
        "energy_cents": rep["energy_cents"],
        "total_cost_per_km": rep["total_cost_per_km"],
        "energy_cost_per_km": rep["energy_cost_per_km"],
        "has_summary": bool(led.summaries),
        "scope": "delivery" if has_delivery else "ledger",
        "summary_before_cents": before_cents,
    }
    if led.params.has_fuel:
        out["fuel"] = rough(led, "fuel", di)
    if led.params.has_elec:
        out["elec"] = rough(led, "elec", di)
    return out


@router.get("/overview")
def overview(vehicle_id: int, range: str = "month", user: User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    v, role = require_vehicle(db, user, vehicle_id)
    led = load_ledger(db, v)
    exps = load_expenses(db, v.id)
    d0, d1, p0, p1 = resolve_range(range, None, None, data_span(led, exps))
    rep = period_report(led, exps, d0, d1, v.initial_odometer_km)
    prev = period_report(led, exps, p0, p1, v.initial_odometer_km) if p0 else None
    latest = {}
    if led.params.has_fuel:
        latest["fuel"] = latest_energy(led, "fuel")
    if led.params.has_elec:
        latest["elec"] = latest_energy(led, "elec")
        latest["elec_totals"] = energy_totals(led.elec_segments, d0, d1)
    combined = [s for s in led.combined_segments if s.status == "ok" and s.elec_kwh is not None]
    if combined:
        latest["combined"] = segment_dict(combined[-1], led.params.kwh_per_l)
    in_progress = db.scalars(select(Record).where(Record.vehicle_id == v.id, Record.status == "in_progress",
                                                  Record.deleted_at.is_(None)).order_by(Record.occurred_at.desc()))
    recent = list(db.scalars(select(Record).where(Record.vehicle_id == v.id, Record.deleted_at.is_(None),
                                                  Record.status == "done")
                             .order_by(Record.occurred_at.desc(), Record.id.desc()).limit(4)))
    recent_exp = list(db.scalars(select(Expense).where(Expense.vehicle_id == v.id, Expense.deleted_at.is_(None))
                                 .order_by(Expense.occurred_at.desc()).limit(4)))
    badges = _badges(led)
    items = [{"type": "record", **record_dict(r), "badges": badges.get(r.id, [])} for r in recent]
    items += [{"type": "expense", **expense_dict(e)} for e in recent_exp]
    items.sort(key=lambda x: (x["occurred_at"], x["id"]), reverse=True)
    items = items[:4]
    _attach_users(db, items)
    reminders = [r for r in collect_reminders(db, user, v.id) if r["status"] != "inactive"][:3]
    obs = odo_observations(led, exps)
    return {
        "vehicle": vehicle_dict(v, role),
        "range": {"key": range, "from": d0.isoformat(), "to": d1.isoformat()},
        "period": rep,
        "prev": prev,
        "latest": latest,
        "spark": {"fuel": _spark(led.fuel_segments), "elec": _spark(led.elec_segments)},
        "since_last_full": since_last_full(led) if led.params.has_fuel else None,
        "history": _history(led, v, exps),
        "in_progress": [record_dict(r) for r in in_progress],
        "pending": pending_items(db, v, user, led)[:5],
        "reminders": reminders,
        "recent": items,
        "latest_odo": latest_odo_of(obs, v),
        "daily_km": avg_daily_km(obs, _today()),
        "record_count": len(led.points),
        "k": led.calib.k,
    }


@router.get("/segments")
def segments(vehicle_id: int, energy: str = "elec", user: User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    v, _ = require_vehicle(db, user, vehicle_id)
    led = load_ledger(db, v)
    segs = {"fuel": led.fuel_segments, "elec": led.elec_segments, "combined": led.combined_segments}.get(energy)
    if segs is None:
        raise HTTPException(400, "energy 不正确")
    return [segment_dict(s, led.params.kwh_per_l) for s in reversed(segs)]


def _months(d0: date, d1: date) -> list:
    return [month_bounds(y, m) for y, m in months_between(d0, d1)]


@router.get("/energy")
def energy(vehicle_id: int, date_from: Optional[date] = None, date_to: Optional[date] = None,
           user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    v, _ = require_vehicle(db, user, vehicle_id)
    led = load_ledger(db, v)
    exps = load_expenses(db, v.id)
    span = data_span(led, exps)
    if not span:
        return {"months": [], "segments": {}, "charging": None, "calibration": None}
    d0 = date_from or span[0]
    d1 = date_to or max(span[1], _today())
    months = []
    for m0, m1 in _months(d0, d1):
        rep = period_report(led, exps, m0, m1, v.initial_odometer_km)
        months.append({
            "month": m0.strftime("%Y-%m"),
            "mileage": rep["mileage"],
            "fuel": rep["fuel"],
            "elec": rep["elec"],
            "combined": rep["combined"],
            "energy_cost_per_km": rep["energy_cost_per_km"],
        })
    in_range = lambda s: d0 <= s.end_ts.date() <= d1
    segs = {
        "fuel": [segment_dict(s) for s in led.fuel_segments if in_range(s)],
        "elec": [segment_dict(s) for s in led.elec_segments if in_range(s)],
        "combined": [segment_dict(s, led.params.kwh_per_l) for s in led.combined_segments if in_range(s)],
    }
    totals = {
        "fuel": energy_totals(led.fuel_segments, d0, d1) if led.params.has_fuel else None,
        "elec": energy_totals(led.elec_segments, d0, d1) if led.params.has_elec else None,
    }
    charging = None
    if led.params.has_elec:
        by_type = defaultdict(lambda: {"kwh": 0.0, "cents": 0, "count": 0})
        soc_start = [0] * 10
        for p in led.points:
            r = p.rec
            if r.kind != "charge" or not d0 <= r.ts.date() <= d1:
                continue
            t = by_type[r.charge_type or "home"]
            t["kwh"] += r.charge_kwh or 0
            t["cents"] += r.paid_cents or 0
            t["count"] += 1
            if r.soc_before is not None:
                soc_start[min(int(r.soc_before // 10), 9)] += 1
        types = []
        for k, t in by_type.items():
            types.append({"type": k, "kwh": round(t["kwh"], 1), "cents": t["cents"], "count": t["count"],
                          "avg_price": round(t["cents"] / 100 / t["kwh"], 3) if t["kwh"] > 0 else None})
        types.sort(key=lambda x: -x["kwh"])
        c = led.calib
        charging = {
            "types": types,
            "soc_start": soc_start,
            "k": c.k, "k_source": c.source, "k_ac": c.k_ac, "k_dc": c.k_dc, "capacity_est": c.capacity_est,
            "samples": [{"ts": iso(ts), "k": round(k, 4), "capacity_est": round(k * 90, 1), "type": ct}
                        for _, k, ct, ts in sorted(c.samples, key=lambda s: s[3])],
        }
    return {"from": d0.isoformat(), "to": d1.isoformat(), "months": months, "segments": segs,
            "totals": totals, "charging": charging, "energy_type": v.energy_type}


def _cost_vehicles(db: Session, user: User, vehicle_id: Optional[int]) -> list[Vehicle]:
    if vehicle_id:
        v, _ = require_vehicle(db, user, vehicle_id)
        return [v]
    return list(db.scalars(select(Vehicle).where(Vehicle.id.in_(my_vehicle_ids(db, user)))))


@router.get("/cost")
def cost(vehicle_id: Optional[int] = None, date_from: Optional[date] = None, date_to: Optional[date] = None,
         user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    vehicles = _cost_vehicles(db, user, vehicle_id)
    ctx = []
    spans = []
    for v in vehicles:
        led = load_ledger(db, v)
        exps = load_expenses(db, v.id)
        ctx.append((v, led, exps))
        s = data_span(led, exps)
        if s:
            spans.append(s)
    if not spans:
        return {"months": [], "categories": [], "total_cents": 0}
    d0 = date_from or min(s[0] for s in spans)
    d1 = date_to or max(max(s[1] for s in spans), _today())
    months = []
    cum = 0
    for m0, m1 in _months(d0, d1):
        row = {"month": m0.strftime("%Y-%m"), "fuel": 0, "elec": 0, "maintenance": 0, "insurance": 0, "other": 0,
               "summary": 0, "mileage": 0.0}
        for v, led, exps in ctx:
            rep = period_report(led, exps, m0, m1, v.initial_odometer_km)
            row["fuel"] += rep["fuel_cents"]
            row["elec"] += rep["elec_cents"]
            row["summary"] += rep["summary_cents"]
            row["mileage"] += rep["mileage"]
            for cat, c in rep["categories"].items():
                key = "maintenance" if cat in MAINT else ("insurance" if cat == "insurance" else "other")
                row[key] += c
        row["total"] = row["fuel"] + row["elec"] + row["maintenance"] + row["insurance"] + row["other"]
        cum += row["total"]
        row["cumulative"] = cum
        row["cost_per_km"] = round(row["total"] / 100 / row["mileage"], 3) if row["mileage"] >= 1 else None
        months.append(row)
    cats = defaultdict(lambda: {"cents": 0, "count": 0})
    places = defaultdict(lambda: {"count": 0, "qty": 0.0, "cents": 0, "kind": None})
    prices = {"fuel": [], "elec": []}
    total_km = 0.0
    from app.engine.aggregate import cash_and_qty

    for v, led, exps in ctx:
        cq = cash_and_qty(led, exps, d0, d1)
        total_km += period_report(led, exps, d0, d1, v.initial_odometer_km)["mileage"]
        cats["fuel"]["cents"] += cq["fuel_cents"]
        cats["fuel"]["count"] += cq["fuel_count"]
        cats["elec"]["cents"] += cq["elec_cents"]
        cats["elec"]["count"] += cq["elec_count"]
        for p in led.points:
            r = p.rec
            if not d0 <= r.ts.date() <= d1:
                continue
            if r.kind in ("fuel", "charge"):
                qty = (r.fuel_l if r.kind == "fuel" else r.charge_kwh) or 0
                if r.paid_cents and qty > 0 and "paid_cents" not in r.estimated_fields:
                    prices["fuel" if r.kind == "fuel" else "elec"].append({
                        "ts": iso(r.ts), "price": round(r.paid_cents / 100 / qty, 3),
                        "group": r.fuel_grade if r.kind == "fuel" else (r.charge_type or "home"),
                        "vehicle_id": v.id})
                if r.place_name:
                    pl = places[(r.kind, r.place_name)]
                    pl["count"] += 1
                    pl["qty"] += qty
                    pl["cents"] += r.paid_cents or 0
                    pl["kind"] = r.kind
        for e in exps:
            if d0 <= e.ts.date() <= d1:
                cats[e.category]["cents"] += e.amount_cents
                cats[e.category]["count"] += 1
    payers = _payer_totals(db, [v.id for v, _, _ in ctx], d0, d1)
    total = sum(c["cents"] for c in cats.values())
    cat_list = []
    labels = {"fuel": "加油", "elec": "充电", **EXPENSE_CATEGORIES}
    for k, c in cats.items():
        if c["cents"] <= 0 and c["count"] == 0:
            continue
        cat_list.append({"key": k, "label": labels.get(k, k), "cents": c["cents"], "count": c["count"],
                         "share": round(c["cents"] / total, 3) if total else None})
    cat_list.sort(key=lambda x: -x["cents"])
    place_list = [{"name": name, "kind": kind, "count": p["count"],
                   "avg_price": round(p["cents"] / 100 / p["qty"], 3) if p["qty"] > 0 else None,
                   "cents": p["cents"]} for (kind, name), p in places.items()]
    place_list.sort(key=lambda x: -x["count"])
    return {
        "from": d0.isoformat(), "to": d1.isoformat(), "months": months, "categories": cat_list,
        "total_cents": total, "mileage": round(total_km, 1),
        "cost_per_km": round(total / 100 / total_km, 3) if total_km >= 1 else None,
        "payers": payers, "places": place_list[:20], "prices": prices,
    }


def _payer_totals(db: Session, vids: list, d0: date, d1: date) -> list:
    from app.services import users_map

    lo = datetime.combine(d0, datetime.min.time())
    hi = datetime.combine(d1 + timedelta(days=1), datetime.min.time())
    tot = defaultdict(int)
    for pid, cents in db.execute(select(Record.payer_user_id, Record.paid_cents).where(
            Record.vehicle_id.in_(vids), Record.deleted_at.is_(None), Record.status == "done",
            Record.occurred_at >= lo, Record.occurred_at < hi)):
        tot[pid] += cents or 0
    for pid, cents in db.execute(select(Expense.payer_user_id, Expense.amount_cents).where(
            Expense.vehicle_id.in_(vids), Expense.deleted_at.is_(None),
            Expense.occurred_at >= lo, Expense.occurred_at < hi)):
        tot[pid] += cents or 0
    umap = users_map(db, tot.keys())
    out = [{"user": umap.get(k), "cents": c} for k, c in tot.items() if c]
    out.sort(key=lambda x: -x["cents"])
    return out


@router.get("/mileage")
def mileage_stats(vehicle_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    v, _ = require_vehicle(db, user, vehicle_id)
    led = load_ledger(db, v)
    exps = load_expenses(db, v.id)
    obs = odo_observations(led, exps)
    span = data_span(led, exps)
    if not span:
        return {"months": [], "points": []}
    months = []
    by_month = {}
    for m0, m1 in _months(span[0], max(span[1], _today())):
        km = mileage(obs, m0, m1, v.initial_odometer_km)
        by_month[(m0.year, m0.month)] = km
        months.append({"month": m0.strftime("%Y-%m"), "km": km, "days": (m1 - m0).days + 1})
    for m in months:
        y, mo = map(int, m["month"].split("-"))
        m["last_year"] = by_month.get((y - 1, mo))
    pts = []
    for t, o in obs:
        if not pts or pts[-1]["odo"] != o:
            pts.append({"ts": iso(t), "odo": o})
    total_days = max((obs[-1][0].date() - obs[0][0].date()).days, 1) if len(obs) > 1 else None
    return {
        "months": months,
        "points": pts,
        "daily_avg": round((obs[-1][1] - obs[0][1]) / total_days, 1) if total_days else None,
        "recent_daily": avg_daily_km(obs, _today()),
        "latest_odo": latest_odo_of(obs, v),
    }


def _sentence(rep: dict, prev: Optional[dict], v: Vehicle) -> str:
    """用模板拼一句月度总结。"""
    parts = []
    km = rep["mileage"]
    if not km and not rep["total_cents"]:
        return "这个月还没有记录。"
    if prev and prev["mileage"]:
        ch = (km - prev["mileage"]) / prev["mileage"]
        if abs(ch) >= 0.05:
            parts.append(f"比上月{'多' if ch > 0 else '少'}跑了 {abs(ch) * 100:.0f}%")
        else:
            parts.append("里程和上月差不多")
    else:
        parts.append(f"本月行驶 {km:,.0f} 公里")
    cpk = rep["energy_cost_per_km"]
    if cpk is not None:
        pcpk = prev["energy_cost_per_km"] if prev else None
        if pcpk:
            trend = "降到" if cpk < pcpk - 0.005 else ("升到" if cpk > pcpk + 0.005 else "保持在")
            parts.append(f"每公里能源成本{trend} ¥{cpk:.2f}")
        else:
            parts.append(f"每公里能源成本 ¥{cpk:.2f}")
    if v.energy_type in ("phev", "erev", "bev") and rep.get("home_share") is not None:
        parts.append(f"家充占比 {rep['home_share'] * 100:.0f}%")
    return "，".join(parts) + "。"


def _month_report(v: Vehicle, led, exps, y: int, m: int) -> dict:
    d0, d1 = month_bounds(y, m)
    p1 = d0 - timedelta(days=1)
    p0 = p1.replace(day=1)
    rep = period_report(led, exps, d0, d1, v.initial_odometer_km)
    prev = period_report(led, exps, p0, p1, v.initial_odometer_km)
    return {"month": f"{y:04d}-{m:02d}", "report": rep, "prev": prev, "sentence": _sentence(rep, prev, v)}


def _parse_month(month: Optional[str]) -> tuple:
    if not month:
        t = _today()
        return t.year, t.month
    try:
        y, m = map(int, month.split("-"))
        date(y, m, 1)
        return y, m
    except ValueError as exc:
        raise HTTPException(400, "月份格式应为 YYYY-MM") from exc


@router.get("/calendar")
def calendar(vehicle_id: int, month: Optional[str] = None, user: User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    v, _ = require_vehicle(db, user, vehicle_id)
    y, m = _parse_month(month)
    d0, d1 = month_bounds(y, m)
    lo = datetime.combine(d0, datetime.min.time())
    hi = datetime.combine(d1 + timedelta(days=1), datetime.min.time())
    recs = list(db.scalars(select(Record).where(Record.vehicle_id == v.id, Record.deleted_at.is_(None),
                                                Record.occurred_at >= lo, Record.occurred_at < hi)
                           .order_by(Record.occurred_at)))
    exps_rows = list(db.scalars(select(Expense).where(Expense.vehicle_id == v.id, Expense.deleted_at.is_(None),
                                                      Expense.occurred_at >= lo, Expense.occurred_at < hi)
                                .order_by(Expense.occurred_at)))
    led = load_ledger(db, v)
    badges = _badges(led)
    days: dict = defaultdict(lambda: {"items": [], "cents": 0, "kinds": []})
    items = [{"type": "record", **record_dict(r), "badges": badges.get(r.id, [])} for r in recs]
    items += [{"type": "expense", **expense_dict(e)} for e in exps_rows]
    _attach_users(db, items)
    for it in items:
        day = it["occurred_at"][:10]
        d = days[day]
        d["items"].append(it)
        d["cents"] += (it.get("paid_cents") or 0) if it["type"] == "record" else it["amount_cents"]
        k = it["kind"] if it["type"] == "record" else "expense"
        if k not in d["kinds"]:
            d["kinds"].append(k)
    for d in days.values():
        d["items"].sort(key=lambda x: x["occurred_at"])
    exps = load_expenses(db, v.id)
    return {"month": f"{y:04d}-{m:02d}", "days": days, "report": _month_report(v, led, exps, y, m)}


@router.get("/monthly-report")
def monthly_report(vehicle_id: int, month: Optional[str] = None, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    v, _ = require_vehicle(db, user, vehicle_id)
    y, m = _parse_month(month)
    return _month_report(v, load_ledger(db, v), load_expenses(db, v.id), y, m)


@router.get("/yearly-report")
def yearly_report(vehicle_id: int, year: Optional[int] = None, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    v, _ = require_vehicle(db, user, vehicle_id)
    year = year or _today().year
    led = load_ledger(db, v)
    exps = load_expenses(db, v.id)
    d0, d1 = date(year, 1, 1), date(year, 12, 31)
    rep = period_report(led, exps, d0, d1, v.initial_odometer_km)
    months = []
    for m in range(1, 13):
        m0, m1 = month_bounds(year, m)
        r = period_report(led, exps, m0, m1, v.initial_odometer_km)
        months.append({"month": m0.strftime("%Y-%m"), "mileage": r["mileage"], "total_cents": r["total_cents"],
                       "energy_cents": r["energy_cents"], "fuel": r["fuel"], "elec": r["elec"]})
    best = max(months, key=lambda x: x["mileage"]) if months else None
    lo = datetime.combine(d0, datetime.min.time())
    hi = datetime.combine(d1 + timedelta(days=1), datetime.min.time())
    top_exp = db.scalar(select(Expense).where(Expense.vehicle_id == v.id, Expense.deleted_at.is_(None),
                                              Expense.occurred_at >= lo, Expense.occurred_at < hi)
                        .order_by(Expense.amount_cents.desc()).limit(1))
    top_rec = db.scalar(select(Record).where(Record.vehicle_id == v.id, Record.deleted_at.is_(None),
                                             Record.occurred_at >= lo, Record.occurred_at < hi)
                        .order_by(Record.paid_cents.desc()).limit(1))
    top = None
    if top_exp and (not top_rec or (top_exp.amount_cents or 0) >= (top_rec.paid_cents or 0)):
        top = {"type": "expense", **expense_dict(top_exp)}
    elif top_rec:
        top = {"type": "record", **record_dict(top_rec)}
    return {"year": year, "report": rep, "months": months,
            "best_month": best if best and best["mileage"] > 0 else None, "top_item": top,
            "years": sorted({p.ts.year for p in led.points} | {e.ts.year for e in exps} | {year})}
