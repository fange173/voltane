"""
services.py — 数据库记录与计算引擎之间的转换、计算缓存、序列化与动态日志
"""
from __future__ import annotations

import json
import threading
from collections import OrderedDict
from datetime import date, datetime
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import user_brief
from app.database import (
    ActivityLog,
    Expense,
    PeriodSummary,
    Record,
    SegmentMark,
    User,
    Vehicle,
)
from app.engine.aggregate import Exp
from app.engine.ledger import Ledger, build_ledger
from app.engine.types import Mark, Rec, Summary, VehicleParams

ENERGY_LABEL = {"ice": "燃油", "hev": "油混", "phev": "插混", "erev": "增程", "bev": "纯电"}


def params_for(v: Vehicle) -> VehicleParams:
    c = v.get_calc()
    return VehicleParams(
        energy_type=v.energy_type,
        tank_l=v.tank_capacity_l or None,
        battery_kwh=v.battery_usable_kwh or None,
        charge_limit=v.charge_limit_pct or 100,
        gauge_err_pct=float(c.get("gauge_err_pct", 5)),
        min_segment_km=float(c.get("min_segment_km", 30)),
        kwh_per_l=float(c.get("kwh_per_l", 8.9)),
        allow_gauge_anchor=bool(c.get("allow_gauge_anchor", True)),
    )


def rec_from_row(r: Record) -> Rec:
    return Rec(
        id=r.id,
        kind=r.kind,
        ts=r.occurred_at,
        odo=r.odometer_km,
        odo_estimated=bool(r.odometer_estimated or r.odometer_km is None),
        fuel_l=r.fuel_liters,
        fuel_full=bool(r.fuel_is_full),
        fuel_before_pct=r.fuel_level_before_pct,
        fuel_after_pct=r.fuel_level_after_pct,
        charge_kwh=r.charge_kwh,
        charge_type=r.charge_type,
        charge_full=bool(r.charge_to_limit),
        soc_before=r.soc_before_pct,
        soc_after=r.soc_after_pct,
        paid_cents=r.paid_cents if r.paid_cents is not None else (r.amount_cents or 0),
        extra_cents=r.extra_fee_cents or 0,
        missed_before=bool(r.missed_before),
        snapshot_tag=r.snapshot_tag,
        estimated_fields=frozenset(r.est_fields()),
        place_name=r.place_name,
        fuel_grade=r.fuel_grade,
        user_id=r.created_by,
    )


def summary_from_row(s: PeriodSummary) -> Summary:
    return Summary(
        id=s.id, start=s.start_date, end=s.end_date, fuel_l=s.fuel_liters, fuel_cents=s.fuel_cents,
        elec_kwh=s.elec_kwh, elec_cents=s.elec_cents, start_odo=s.start_odometer_km,
        end_odo=s.end_odometer_km, mode=s.mode or "extra", fuel_price=s.fuel_price, elec_price=s.elec_price,
    )


# ---------- 计算缓存：按 (vehicle_id, data_version) ----------

_cache_lock = threading.Lock()
_cache: "OrderedDict[tuple, Ledger]" = OrderedDict()
_CACHE_MAX = 32


def active_records(db: Session, vehicle_id: int) -> list[Record]:
    return list(db.scalars(
        select(Record).where(Record.vehicle_id == vehicle_id, Record.deleted_at.is_(None))
        .order_by(Record.occurred_at, Record.id)
    ))


def active_summaries(db: Session, vehicle_id: int) -> list[PeriodSummary]:
    return list(db.scalars(
        select(PeriodSummary).where(PeriodSummary.vehicle_id == vehicle_id, PeriodSummary.deleted_at.is_(None))
        .order_by(PeriodSummary.start_date)
    ))


def load_ledger(db: Session, v: Vehicle) -> Ledger:
    key = (v.id, v.data_version, v.updated_at)
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    rows = [r for r in active_records(db, v.id) if r.status == "done"]
    sums = [summary_from_row(s) for s in active_summaries(db, v.id)]
    marks = {
        (m.end_record_id, m.energy): Mark(m.tag, m.excluded)
        for m in db.scalars(select(SegmentMark).where(SegmentMark.vehicle_id == v.id))
    }
    led = build_ledger(params_for(v), [rec_from_row(r) for r in rows], sums, marks)
    with _cache_lock:
        _cache[key] = led
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return led


def bump(v: Vehicle) -> None:
    v.data_version = (v.data_version or 0) + 1


def load_expenses(db: Session, vehicle_id: int) -> list[Exp]:
    rows = db.scalars(
        select(Expense).where(Expense.vehicle_id == vehicle_id, Expense.deleted_at.is_(None))
    )
    return [Exp(e.id, e.category, e.occurred_at, e.amount_cents, e.odometer_km) for e in rows]


# ---------- 序列化 ----------


def users_map(db: Session, ids: Iterable) -> dict:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {u.id: user_brief(u) for u in db.scalars(select(User).where(User.id.in_(ids)))}


def iso(x) -> Optional[str]:
    if x is None:
        return None
    if isinstance(x, datetime):
        return x.isoformat(timespec="minutes")
    if isinstance(x, date):
        return x.isoformat()
    return str(x)


RECORD_FIELDS = [
    "id", "vehicle_id", "kind", "status", "odometer_km", "odometer_estimated", "source",
    "fuel_grade", "fuel_liters", "fuel_unit_price", "fuel_is_full", "fuel_level_before_pct",
    "fuel_level_after_pct", "charge_type", "charge_kwh", "charge_unit_price", "charge_minutes",
    "charge_to_limit", "energy_fee_cents", "service_fee_cents", "soc_before_pct", "soc_after_pct",
    "amount_cents", "paid_cents", "extra_fee_cents", "place_id", "place_name", "lat", "lng",
    "missed_before", "snapshot_tag", "payer_user_id", "note", "version", "client_id",
]


def record_dict(r: Record, umap: Optional[dict] = None) -> dict:
    d = {f: getattr(r, f) for f in RECORD_FIELDS}
    d["occurred_at"] = iso(r.occurred_at)
    d["estimated_fields"] = r.est_fields()
    d["deleted_at"] = iso(r.deleted_at)
    d["created_at"] = iso(r.created_at)
    d["updated_at"] = iso(r.updated_at)
    umap = umap or {}
    d["created_by"] = umap.get(r.created_by)
    d["updated_by"] = umap.get(r.updated_by)
    d["payer"] = umap.get(r.payer_user_id)
    return d


EXPENSE_FIELDS = [
    "id", "vehicle_id", "category", "title", "odometer_km", "amount_cents", "vendor",
    "payer_user_id", "note", "version", "client_id",
]


def expense_dict(e: Expense, umap: Optional[dict] = None) -> dict:
    d = {f: getattr(e, f) for f in EXPENSE_FIELDS}
    d["occurred_at"] = iso(e.occurred_at)
    d["period_start"] = iso(e.period_start)
    d["period_end"] = iso(e.period_end)
    try:
        d["sub_items"] = json.loads(e.sub_items or "[]")
    except ValueError:
        d["sub_items"] = []
    umap = umap or {}
    d["created_by"] = umap.get(e.created_by)
    d["payer"] = umap.get(e.payer_user_id)
    d["deleted_at"] = iso(e.deleted_at)
    return d


def summary_dict(s: PeriodSummary, diff: Optional[dict] = None) -> dict:
    return {
        "id": s.id, "vehicle_id": s.vehicle_id, "start_date": iso(s.start_date), "end_date": iso(s.end_date),
        "mode": s.mode, "start_odometer_km": s.start_odometer_km, "end_odometer_km": s.end_odometer_km,
        "fuel_liters": s.fuel_liters, "fuel_cents": s.fuel_cents, "fuel_price": s.fuel_price,
        "elec_kwh": s.elec_kwh, "elec_cents": s.elec_cents, "elec_price": s.elec_price,
        "note": s.note, "version": s.version, "diff": diff,
    }


def vehicle_dict(v: Vehicle, role: Optional[str] = None) -> dict:
    return {
        "id": v.id, "name": v.name, "plate": v.plate, "brand": v.brand, "model": v.model, "year": v.year,
        "energy_type": v.energy_type, "energy_label": ENERGY_LABEL.get(v.energy_type, v.energy_type),
        "tank_capacity_l": v.tank_capacity_l, "battery_usable_kwh": v.battery_usable_kwh,
        "charge_limit_pct": v.charge_limit_pct, "default_fuel_grade": v.default_fuel_grade,
        "default_charge_type": v.default_charge_type, "fuel_gauge_mode": v.fuel_gauge_mode,
        "fuel_gauge_bars": v.fuel_gauge_bars, "factory_fuel_l": v.factory_fuel_l,
        "record_style": v.record_style, "initial_odometer_km": v.initial_odometer_km,
        "delivery_date": iso(v.delivery_date), "purchase_price_cents": v.purchase_price_cents,
        "color": v.color, "calc_settings": v.get_calc(), "is_archived": v.is_archived,
        "has_fuel": v.energy_type in ("ice", "hev", "phev", "erev"),
        "has_elec": v.energy_type in ("phev", "erev", "bev"),
        "role": role, "data_version": v.data_version,
    }


# ---------- 动态 ----------


def log_activity(db: Session, vehicle_id: Optional[int], user: User, action: str, entity: str,
                 entity_id: Optional[int], summary: str) -> None:
    db.add(ActivityLog(vehicle_id=vehicle_id, user_id=user.id, action=action, entity=entity,
                       entity_id=entity_id, summary=summary))


def fmt_num(x, digits=2) -> str:
    if x is None:
        return "-"
    s = f"{x:.{digits}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def fuel_level_text(pct, v: Optional[Vehicle]) -> str:
    """按车辆的油表显示方式把油量百分比换成「40%」「19.2L」「3/8 格」。"""
    if pct is None:
        return "-"
    if v is not None and v.fuel_gauge_mode == "liters" and v.tank_capacity_l:
        return f"{fmt_num(pct / 100 * v.tank_capacity_l, 1)}L"
    if v is not None and v.fuel_gauge_mode == "bars" and v.fuel_gauge_bars:
        return f"{round(pct / 100 * v.fuel_gauge_bars)}/{v.fuel_gauge_bars} 格"
    return f"{fmt_num(pct, 0)}%"


CHARGE_LABEL = {"home": "家充", "public_ac": "公共慢充", "dc": "快充", "super": "快充", "swap": "换电"}


def record_summary_text(r: Record) -> str:
    if r.kind == "fuel":
        s = f"加油 {fmt_num(r.fuel_liters)}L" if r.fuel_liters else "加油"
        return s + ("（加满）" if r.fuel_is_full else "")
    if r.kind == "charge":
        label = CHARGE_LABEL.get(r.charge_type or "", "充电")
        return f"{label} {fmt_num(r.charge_kwh)}kWh" if r.charge_kwh else label
    return "状态快照"
