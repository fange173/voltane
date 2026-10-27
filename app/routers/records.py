"""
records.py — 加油 / 充电 / 快照记录、历史汇总、段标签、回收站
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.access import can_delete_entity, require_vehicle
from app.auth import get_current_user
from app.constants import CHARGE_TYPES, SEGMENT_TAGS, SNAPSHOT_TAGS, norm_charge_type
from app.database import PeriodSummary, Place, Record, SegmentMark, User, Vehicle, VehicleMember, get_db, utcnow
from app.engine.aggregate import avg_daily_km, odo_observations, segment_dict
from app.engine.ledger import equiv_per100
from app.insights import explain, latest_odo_of
from app.services import (
    active_summaries,
    bump,
    load_expenses,
    load_ledger,
    log_activity,
    record_dict,
    record_summary_text,
    summary_dict,
    users_map,
)

router = APIRouter(prefix="/api", tags=["records"])

TRASH_DAYS = 30


class RecordIn(BaseModel):
    vehicle_id: int
    kind: Literal["fuel", "charge", "snapshot"]
    status: Literal["done", "in_progress"] = "done"
    occurred_at: datetime
    odometer_km: Optional[float] = Field(None, ge=0, le=3_000_000)
    odometer_estimated: bool = False
    source: Literal["manual", "backfill", "import", "auto_fix"] = "manual"
    estimated_fields: list[str] = Field(default_factory=list)
    client_id: Optional[str] = Field(None, max_length=40)
    fuel_grade: Optional[str] = Field(None, max_length=16)
    fuel_liters: Optional[float] = Field(None, ge=0, le=500)
    fuel_unit_price: Optional[float] = Field(None, ge=0, le=100)
    fuel_is_full: bool = False
    fuel_level_before_pct: Optional[float] = Field(None, ge=0, le=100)
    fuel_level_after_pct: Optional[float] = Field(None, ge=0, le=100)
    charge_type: Optional[str] = None
    charge_kwh: Optional[float] = Field(None, ge=0, le=500)
    charge_unit_price: Optional[float] = Field(None, ge=0, le=20)
    charge_minutes: Optional[int] = Field(None, ge=0, le=7 * 24 * 60)
    charge_to_limit: bool = False
    energy_fee_cents: Optional[int] = Field(None, ge=0)
    service_fee_cents: Optional[int] = Field(None, ge=0)
    soc_before_pct: Optional[float] = Field(None, ge=0, le=100)
    soc_after_pct: Optional[float] = Field(None, ge=0, le=100)
    amount_cents: Optional[int] = Field(None, ge=0, le=10_000_000)
    extra_fee_cents: Optional[int] = Field(None, ge=0, le=10_000_000)
    place_id: Optional[int] = None
    place_name: Optional[str] = Field(None, max_length=64)
    lat: Optional[float] = Field(None, ge=-90, le=90)
    lng: Optional[float] = Field(None, ge=-180, le=180)
    missed_before: bool = False
    snapshot_tag: Optional[str] = None
    note: str = Field("", max_length=500)
    version: Optional[int] = None
    force: bool = False

    @field_validator("charge_type")
    @classmethod
    def _ct(cls, v):
        if v is not None and v not in CHARGE_TYPES:
            raise ValueError("充电方式不正确")
        return norm_charge_type(v)

    @field_validator("snapshot_tag")
    @classmethod
    def _tag(cls, v):
        if v is not None and v not in SNAPSHOT_TAGS:
            raise ValueError("快照标签不正确")
        return v

    @field_validator("estimated_fields")
    @classmethod
    def _est(cls, v):
        return [x for x in v if isinstance(x, str) and len(x) < 32][:20]


FIELDS_BY_KIND = {
    "fuel": {"fuel_grade", "fuel_liters", "fuel_unit_price", "fuel_is_full", "fuel_level_before_pct",
             "fuel_level_after_pct", "soc_before_pct"},
    "charge": {"charge_type", "charge_kwh", "charge_unit_price", "charge_minutes", "charge_to_limit",
               "energy_fee_cents", "service_fee_cents", "soc_before_pct", "soc_after_pct", "fuel_level_before_pct"},
    "snapshot": {"soc_before_pct", "fuel_level_before_pct", "snapshot_tag"},
}
KIND_SPECIFIC = set().union(*FIELDS_BY_KIND.values())
COMMON = {"occurred_at", "odometer_km", "odometer_estimated", "source", "amount_cents",
          "extra_fee_cents", "place_id", "place_name", "lat", "lng", "missed_before", "note",
          "status"}


def _check_kind(v: Vehicle, kind: str) -> None:
    if kind == "fuel" and v.energy_type == "bev":
        raise HTTPException(400, "纯电车不能记录加油")
    if kind == "charge" and v.energy_type in ("ice", "hev"):
        raise HTTPException(400, "这辆车不能记录充电")


def _resolve_place(db: Session, v: Vehicle, user: User, body: RecordIn) -> tuple:
    if body.place_id:
        p = db.get(Place, body.place_id)
        ok = p and ((p.vehicle_id == v.id) or (p.vehicle_id is None and p.owner_user_id == user.id))
        if not ok:
            raise HTTPException(400, "地点不存在")
        return p, p.name
    name = (body.place_name or "").strip() or None
    return None, name


def _fill(r: Record, body: RecordIn, v: Vehicle) -> None:
    data = body.model_dump()
    allowed = FIELDS_BY_KIND[body.kind]
    for f in KIND_SPECIFIC:
        setattr(r, f, data[f] if f in allowed else (False if f in ("fuel_is_full", "charge_to_limit") else None))
    for f in COMMON:
        if f in ("place_id", "place_name"):
            continue
        setattr(r, f, data[f])
    r.kind = body.kind
    # 车型不需要的字段一律清掉
    if v.energy_type in ("ice", "hev"):
        r.soc_before_pct = r.soc_after_pct = None
    if v.energy_type == "bev":
        r.fuel_level_before_pct = r.fuel_level_after_pct = None
    if r.odometer_km is None:
        r.odometer_estimated = True
    r.estimated_fields = json.dumps(sorted(set(body.estimated_fields)))
    r.note = (body.note or "").strip()


def _warnings(db: Session, v: Vehicle, r: Record) -> list[str]:
    """只提示不阻止：里程顺序矛盾、增量过大、数量超出容量、单价异常。"""
    out = []
    if r.odometer_km is not None and not r.odometer_estimated:
        base = select(Record).where(Record.vehicle_id == v.id, Record.deleted_at.is_(None), Record.id != (r.id or 0),
                                    Record.odometer_km.is_not(None), Record.odometer_estimated.is_(False))
        prev = db.scalar(base.where(Record.occurred_at <= r.occurred_at).order_by(Record.occurred_at.desc()).limit(1))
        nxt = db.scalar(base.where(Record.occurred_at > r.occurred_at).order_by(Record.occurred_at).limit(1))
        if prev and prev.odometer_km > r.odometer_km + 0.5:
            out.append(f"里程小于 {prev.occurred_at:%m-%d} 那条记录的 {prev.odometer_km:,.0f}")
        if nxt and nxt.odometer_km < r.odometer_km - 0.5:
            out.append(f"里程大于之后 {nxt.occurred_at:%m-%d} 那条记录的 {nxt.odometer_km:,.0f}")
        if prev and r.odometer_km - prev.odometer_km > 2000:
            out.append(f"距上次记录行驶了 {r.odometer_km - prev.odometer_km:,.0f}km，请确认里程")
    if r.kind == "fuel" and r.fuel_liters and v.tank_capacity_l and r.fuel_liters > v.tank_capacity_l * 1.05:
        out.append(f"加油量超过油箱容积 {v.tank_capacity_l:g}L")
    if r.kind == "fuel" and r.fuel_unit_price and not 4 <= r.fuel_unit_price <= 13:
        out.append("油价看起来不太对")
    if r.kind == "charge" and r.charge_unit_price and not 0.1 <= r.charge_unit_price <= 3.5:
        out.append("电价看起来不太对")
    if r.soc_before_pct is not None and r.soc_after_pct is not None and r.soc_after_pct < r.soc_before_pct:
        out.append("结束电量小于开始电量")
    return out


def _find_duplicate(db: Session, v: Vehicle, r: Record) -> Optional[Record]:
    """同一辆车、同类型、10 分钟内、里程差小于 1km、金额相同，视为可能重复。"""
    q = select(Record).where(
        Record.vehicle_id == v.id, Record.kind == r.kind, Record.deleted_at.is_(None),
        Record.occurred_at >= r.occurred_at - timedelta(minutes=10),
        Record.occurred_at <= r.occurred_at + timedelta(minutes=10),
    )
    for c in db.scalars(q):
        if c.id == r.id:
            continue
        same_odo = (c.odometer_km is None and r.odometer_km is None) or (
            c.odometer_km is not None and r.odometer_km is not None and abs(c.odometer_km - r.odometer_km) < 1)
        same_levels = r.kind != "snapshot" or (
            c.soc_before_pct == r.soc_before_pct and c.fuel_level_before_pct == r.fuel_level_before_pct)
        if same_odo and same_levels and (c.amount_cents or 0) == (r.amount_cents or 0):
            return c
    return None


def _segment_preview(db: Session, v: Vehicle, rid: int) -> list[dict]:
    """保存后提示：以这条记录结尾的段，以及和上一段相比的变化。"""
    led = load_ledger(db, v)
    out = []
    for energy, segs in (("combined", led.combined_segments), ("fuel", led.fuel_segments), ("elec", led.elec_segments)):
        valid = [s for s in segs if s.status == "ok" and s.kind == "drive"]
        for i, s in enumerate(valid):
            if s.end_id != rid:
                continue
            d = segment_dict(s, led.params.kwh_per_l)
            prev = valid[i - 1] if i > 0 else None
            d["prev_per100"] = round(prev.per100, 2) if prev and prev.per100 else None
            out.append(d)
    return out


def _touch_place(p: Optional[Place], r: Record) -> None:
    if not p:
        return
    p.use_count = (p.use_count or 0) + 1
    p.last_used_at = utcnow()
    if r.kind == "fuel" and r.fuel_unit_price:
        p.last_unit_price = r.fuel_unit_price
        p.default_fuel_grade = r.fuel_grade or p.default_fuel_grade
    if r.kind == "charge" and r.charge_unit_price:
        p.last_unit_price = r.charge_unit_price
        p.default_charge_type = r.charge_type or p.default_charge_type


@router.post("/records")
def create_record(body: RecordIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    v, _ = require_vehicle(db, user, body.vehicle_id, "editor")
    _check_kind(v, body.kind)
    if body.client_id:
        exist = db.scalar(select(Record).where(Record.client_id == body.client_id))
        if exist:
            if exist.vehicle_id != v.id:
                raise HTTPException(409, "client_id 冲突")
            return {"record": record_dict(exist, users_map(db, [exist.created_by])), "warnings": [], "replayed": True}
    r = Record(vehicle_id=v.id, created_by=user.id, updated_by=user.id, client_id=body.client_id)
    _fill(r, body, v)
    place, pname = _resolve_place(db, v, user, body)
    r.place_id, r.place_name = (place.id if place else None), pname
    if not body.force:
        dup = _find_duplicate(db, v, r)
        if dup:
            by = users_map(db, [dup.created_by]).get(dup.created_by)
            return JSONResponse(status_code=409, content={"detail": {
                "code": "duplicate", "record": record_dict(dup), "by": by,
                "message": f"可能重复：{by['name'] if by else '有人'} 已在 {dup.occurred_at:%H:%M} 记录过一条相同的{record_summary_text(dup)}",
            }})
    warnings = _warnings(db, v, r)
    db.add(r)
    _touch_place(place, r)
    if r.status == "done":
        bump(v)
    db.flush()
    log_activity(db, v.id, user, "create", "record", r.id,
                 ("开始充电" if r.status == "in_progress" else "记录了" + record_summary_text(r)))
    db.commit()
    preview = _segment_preview(db, v, r.id) if r.status == "done" else []
    return {"record": record_dict(r, users_map(db, [user.id])), "warnings": warnings, "segments": preview}


@router.put("/records/{rid}")
def update_record(rid: int, body: RecordIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    r = db.get(Record, rid)
    if not r or r.deleted_at:
        raise HTTPException(404, "记录不存在")
    v, _ = require_vehicle(db, user, r.vehicle_id, "editor")
    if body.vehicle_id != r.vehicle_id:
        raise HTTPException(400, "不能修改记录所属车辆")
    _check_kind(v, body.kind)
    if body.version is not None and body.version != r.version:
        umap = users_map(db, [r.created_by, r.updated_by])
        by = umap.get(r.updated_by)
        return JSONResponse(status_code=409, content={"detail": {
            "code": "conflict", "record": record_dict(r, umap),
            "message": f"这条记录刚被 {by['name'] if by else '别人'} 修改过，已显示最新内容",
        }})
    was_progress = r.status == "in_progress"
    _fill(r, body, v)
    place, pname = _resolve_place(db, v, user, body)
    if place and place.id != r.place_id:
        _touch_place(place, r)
    r.place_id, r.place_name = (place.id if place else None), pname
    r.updated_by = user.id
    r.version = (r.version or 1) + 1
    if r.snapshot_tag == "delivery" and r.odometer_km is not None and not r.odometer_estimated:
        v.initial_odometer_km = r.odometer_km
        v.delivery_date = r.occurred_at.date()
    warnings = _warnings(db, v, r)
    bump(v)
    text = ("结束充电，" + record_summary_text(r)) if (was_progress and r.status == "done") else "修改了" + record_summary_text(r)
    log_activity(db, v.id, user, "update", "record", r.id, text)
    db.commit()
    preview = _segment_preview(db, v, r.id) if r.status == "done" else []
    return {"record": record_dict(r, users_map(db, [r.created_by, user.id])), "warnings": warnings, "segments": preview}


@router.delete("/records/{rid}")
def delete_record(rid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    r = db.get(Record, rid)
    if not r or r.deleted_at:
        raise HTTPException(404, "记录不存在")
    v, role = require_vehicle(db, user, r.vehicle_id, "editor")
    if not can_delete_entity(role, user, r.created_by):
        raise HTTPException(403, "只能删除自己记的记录")
    r.deleted_at = utcnow()
    if r.snapshot_tag == "delivery":
        # 提车快照删了，起始里程也随之失效，否则首月里程仍按旧值计算
        v.initial_odometer_km = None
    bump(v)
    log_activity(db, v.id, user, "delete", "record", r.id, "删除了" + record_summary_text(r))
    db.commit()
    return {"ok": True}


@router.post("/records/{rid}/restore")
def restore_record(rid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    r = db.get(Record, rid)
    if not r or not r.deleted_at:
        raise HTTPException(404, "记录不在回收站")
    v, role = require_vehicle(db, user, r.vehicle_id, "editor")
    if not can_delete_entity(role, user, r.created_by):
        raise HTTPException(403, "只能恢复自己记的记录")
    r.deleted_at = None
    if r.snapshot_tag == "delivery" and r.odometer_km is not None:
        v.initial_odometer_km = r.odometer_km
    bump(v)
    log_activity(db, v.id, user, "restore", "record", r.id, "恢复了" + record_summary_text(r))
    db.commit()
    return {"ok": True}


@router.get("/records")
def list_records(vehicle_id: int, kind: Optional[str] = None, q: Optional[str] = None, mine: bool = False,
                 source: Optional[str] = None, anomaly: bool = False, before: Optional[datetime] = None,
                 date_from: Optional[date] = None, date_to: Optional[date] = None, limit: int = 50,
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """时间线：记录和费用合并，按时间倒序分页（before 为游标）。kind 可选 fuel/charge/snapshot/expense。"""
    from app.database import Expense
    from app.services import expense_dict

    v, _ = require_vehicle(db, user, vehicle_id)
    limit = min(max(limit, 1), 200)
    led = load_ledger(db, v)
    badge = _badges(led)
    items = []
    if kind in (None, "", "fuel", "charge", "snapshot", "estimated"):
        rq = select(Record).where(Record.vehicle_id == v.id, Record.deleted_at.is_(None))
        if kind in ("fuel", "charge", "snapshot"):
            rq = rq.where(Record.kind == kind)
        if kind == "estimated":
            rq = rq.where(or_(Record.odometer_estimated.is_(True), Record.source != "manual"))
        if source:
            rq = rq.where(Record.source == source)
        if mine:
            rq = rq.where(Record.created_by == user.id)
        if q:
            like = f"%{q.strip()}%"
            rq = rq.where(or_(Record.note.like(like), Record.place_name.like(like)))
        if before:
            rq = rq.where(Record.occurred_at < before)
        if date_from:
            rq = rq.where(Record.occurred_at >= datetime.combine(date_from, datetime.min.time()))
        if date_to:
            rq = rq.where(Record.occurred_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))
        rows = list(db.scalars(rq.order_by(Record.occurred_at.desc(), Record.id.desc()).limit(limit)))
        for r in rows:
            d = {"type": "record", **record_dict(r)}
            d["badges"] = badge.get(r.id, [])
            if anomaly and not any(b.get("anomaly") for b in d["badges"]):
                continue
            items.append(d)
    if kind in (None, "", "expense") and not anomaly and not source:
        eq = select(Expense).where(Expense.vehicle_id == v.id, Expense.deleted_at.is_(None))
        if mine:
            eq = eq.where(Expense.created_by == user.id)
        if q:
            like = f"%{q.strip()}%"
            eq = eq.where(or_(Expense.note.like(like), Expense.vendor.like(like), Expense.title.like(like)))
        if before:
            eq = eq.where(Expense.occurred_at < before)
        if date_from:
            eq = eq.where(Expense.occurred_at >= datetime.combine(date_from, datetime.min.time()))
        if date_to:
            eq = eq.where(Expense.occurred_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))
        for e in db.scalars(eq.order_by(Expense.occurred_at.desc(), Expense.id.desc()).limit(limit)):
            items.append({"type": "expense", **expense_dict(e)})
    items.sort(key=lambda x: (x["occurred_at"], x["id"]), reverse=True)
    items = items[:limit]
    _attach_users(db, items)
    next_cursor = items[-1]["occurred_at"] if len(items) == limit else None
    return {"items": items, "next": next_cursor}


def _attach_users(db: Session, items: list) -> None:
    """record_dict / expense_dict 未带用户时，批量补上记录人。"""
    from app.database import Expense

    rec_ids = [it["id"] for it in items if it["type"] == "record"]
    exp_ids = [it["id"] for it in items if it["type"] == "expense"]
    owners = {}
    if rec_ids:
        for rid, cb in db.execute(select(Record.id, Record.created_by).where(Record.id.in_(rec_ids))):
            owners[("record", rid)] = cb
    if exp_ids:
        for eid, cb in db.execute(select(Expense.id, Expense.created_by).where(Expense.id.in_(exp_ids))):
            owners[("expense", eid)] = cb
    umap = users_map(db, list(owners.values()))
    for it in items:
        it["created_by"] = umap.get(owners.get((it["type"], it["id"])))


def _badges(led) -> dict:
    """每条记录作为段终点时的能耗徽章。"""
    out: dict = {}
    for energy, segs in (("fuel", led.fuel_segments), ("elec", led.elec_segments)):
        for s in segs:
            if s.status not in ("ok",) and not s.anomaly:
                continue
            d = {"energy": energy, "per100": round(s.per100, 2) if s.per100 is not None else None,
                 "quality": s.quality, "kind": s.kind, "anomaly": s.anomaly, "tag": s.tag, "excluded": s.excluded,
                 "per_day": round(s.amount / s.days, 2) if s.kind == "idle" and s.amount and s.days > 0 else None}
            out.setdefault(s.end_id, []).append(d)
    # 综合段只在有异常时出徽章，数值用折合油耗
    for s in led.combined_segments:
        if s.anomaly:
            v = equiv_per100(s, led.params.kwh_per_l)
            out.setdefault(s.end_id, []).append({"energy": "combined", "per100": round(v, 2) if v is not None else None,
                                                 "quality": s.quality, "kind": s.kind, "anomaly": s.anomaly,
                                                 "tag": s.tag, "excluded": s.excluded, "per_day": None})
    return out


@router.get("/records/prefill")
def prefill(vehicle_id: int, kind: Literal["fuel", "charge", "snapshot"] = "fuel",
            user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """表单预填：上次里程和里程时间轴、同类上一条（「同上次」用）、常用地点排序、k、日均里程。"""
    v, _ = require_vehicle(db, user, vehicle_id)
    led = load_ledger(db, v)
    obs = odo_observations(led, load_expenses(db, v.id))
    last = db.scalar(select(Record).where(Record.vehicle_id == v.id, Record.kind == kind, Record.deleted_at.is_(None),
                                          Record.status == "done").order_by(Record.occurred_at.desc()).limit(1))
    last_soc = None
    last_fuel_pct = None
    for p in reversed(led.points):
        if last_soc is None and led.calib.k and p.elec[1] is not None:
            last_soc = round(p.elec[1].value / led.calib.k)
        if last_fuel_pct is None and v.tank_capacity_l and p.fuel[1] is not None:
            last_fuel_pct = round(p.fuel[1].value / v.tank_capacity_l * 100)
        if last_soc is not None and last_fuel_pct is not None:
            break
    places = _sorted_places(db, v, user, kind)
    in_progress = db.scalars(select(Record).where(Record.vehicle_id == v.id, Record.status == "in_progress",
                                                  Record.deleted_at.is_(None)).order_by(Record.occurred_at.desc()))
    last_obs = max(obs, key=lambda o: (o[1], o[0])) if obs else None
    return {
        "last_odo": latest_odo_of(obs, v),
        "last_odo_ts": last_obs[0].isoformat(timespec="minutes") if last_obs else None,
        # 按时间排好的里程读数，补录时用来找所选时间前后的读数
        "odo_points": [[t.isoformat(timespec="minutes"), o] for t, o in obs],
        "last": record_dict(last) if last else None,
        "last_soc": min(last_soc, 100) if last_soc is not None else None,
        "last_fuel_pct": min(last_fuel_pct, 100) if last_fuel_pct is not None else None,
        "k": led.calib.k,
        "k_source": led.calib.source,
        "daily_km": avg_daily_km(obs, datetime.now().date()),
        "places": places,
        "in_progress": [record_dict(r) for r in in_progress],
        "recent_notes": _recent_notes(db, v.id),
    }


def _recent_notes(db: Session, vid: int) -> list[str]:
    seen = []
    for (n,) in db.execute(select(Record.note).where(Record.vehicle_id == vid, Record.note != "",
                                                     Record.source == "manual", Record.deleted_at.is_(None))
                           .order_by(Record.id.desc()).limit(40)):
        n = n.strip()
        if n and len(n) <= 12 and n not in seen:
            seen.append(n)
        if len(seen) >= 5:
            break
    return seen


def _sorted_places(db: Session, v: Vehicle, user: User, kind: str) -> list[dict]:
    from app.routers.expenses import place_dict

    rows = list(db.scalars(select(Place).where(
        or_(Place.vehicle_id == v.id, (Place.vehicle_id.is_(None)) & (Place.owner_user_id == user.id)),
        Place.is_hidden.is_(False))))
    match = {"fuel": ("gas_station",), "charge": ("home_charger", "charge_station"), "snapshot": ()}[kind]
    rows.sort(key=lambda p: (p.kind not in match, p.sort_order, -(p.use_count or 0),
                             -(p.last_used_at.timestamp() if p.last_used_at else 0)))
    return [place_dict(p) for p in rows]


@router.get("/records/{rid}")
def get_record(rid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    r = db.get(Record, rid)
    if not r:
        raise HTTPException(404, "记录不存在")
    v, role = require_vehicle(db, user, r.vehicle_id)
    led = load_ledger(db, v)
    segs = []
    for energy, lst in (("combined", led.combined_segments), ("fuel", led.fuel_segments), ("elec", led.elec_segments)):
        for s in lst:
            if s.end_id == rid:
                d = segment_dict(s, led.params.kwh_per_l)
                d["explain"] = explain(led, s, v)
                segs.append(d)
    out = record_dict(r, users_map(db, [r.created_by, r.updated_by]))
    out["segments"] = segs
    out["can_delete"] = can_delete_entity(role, user, r.created_by) and not v.is_archived
    out["can_edit"] = role in ("owner", "editor") and not v.is_archived
    return out


@router.get("/trash")
def trash(vehicle_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.database import Expense
    from app.services import expense_dict

    v, _ = require_vehicle(db, user, vehicle_id)
    cutoff = utcnow() - timedelta(days=TRASH_DAYS)
    recs = db.scalars(select(Record).where(Record.vehicle_id == v.id, Record.deleted_at.is_not(None),
                                           Record.deleted_at > cutoff).order_by(Record.deleted_at.desc()))
    exps = db.scalars(select(Expense).where(Expense.vehicle_id == v.id, Expense.deleted_at.is_not(None),
                                            Expense.deleted_at > cutoff).order_by(Expense.deleted_at.desc()))
    items = [{"type": "record", **record_dict(r)} for r in recs] + [{"type": "expense", **expense_dict(e)} for e in exps]
    _attach_users(db, items)
    return items


# ---------- 历史 / 周期汇总 ----------


class SummaryIn(BaseModel):
    vehicle_id: int
    start_date: date
    end_date: date
    mode: Literal["extra", "total"] = "extra"
    start_odometer_km: Optional[float] = Field(None, ge=0)
    end_odometer_km: Optional[float] = Field(None, ge=0)
    fuel_liters: Optional[float] = Field(None, ge=0, le=100000)
    fuel_cents: Optional[int] = Field(None, ge=0)
    fuel_price: Optional[float] = Field(None, gt=0, le=100)
    elec_kwh: Optional[float] = Field(None, ge=0, le=1000000)
    elec_cents: Optional[int] = Field(None, ge=0)
    elec_price: Optional[float] = Field(None, gt=0, le=20)
    note: str = Field("", max_length=200)
    version: Optional[int] = None


@router.get("/summaries")
def list_summaries(vehicle_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    v, _ = require_vehicle(db, user, vehicle_id)
    led = load_ledger(db, v)
    return [summary_dict(s, led.summary_diffs.get(s.id)) for s in active_summaries(db, v.id)]


def _apply_summary(s: PeriodSummary, body: SummaryIn) -> None:
    if body.end_date < body.start_date:
        raise HTTPException(400, "结束日期早于开始日期")
    for k, val in body.model_dump(exclude={"vehicle_id", "version"}).items():
        setattr(s, k, val)


@router.post("/summaries")
def create_summary(body: SummaryIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    v, _ = require_vehicle(db, user, body.vehicle_id, "editor")
    s = PeriodSummary(vehicle_id=v.id, created_by=user.id, updated_by=user.id)
    _apply_summary(s, body)
    db.add(s)
    bump(v)
    db.flush()
    log_activity(db, v.id, user, "create", "summary", s.id, f"添加了 {s.start_date:%Y-%m-%d} 至 {s.end_date:%Y-%m-%d} 的汇总")
    db.commit()
    led = load_ledger(db, v)
    return summary_dict(s, led.summary_diffs.get(s.id))


@router.put("/summaries/{sid}")
def update_summary(sid: int, body: SummaryIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = db.get(PeriodSummary, sid)
    if not s or s.deleted_at:
        raise HTTPException(404, "汇总不存在")
    v, _ = require_vehicle(db, user, s.vehicle_id, "editor")
    if body.version is not None and body.version != s.version:
        raise HTTPException(409, "这条汇总刚被别人修改过，请刷新")
    _apply_summary(s, body)
    s.version += 1
    s.updated_by = user.id
    bump(v)
    log_activity(db, v.id, user, "update", "summary", s.id, "修改了历史汇总")
    db.commit()
    led = load_ledger(db, v)
    return summary_dict(s, led.summary_diffs.get(s.id))


@router.delete("/summaries/{sid}")
def delete_summary(sid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = db.get(PeriodSummary, sid)
    if not s or s.deleted_at:
        raise HTTPException(404, "汇总不存在")
    v, role = require_vehicle(db, user, s.vehicle_id, "editor")
    if not can_delete_entity(role, user, s.created_by):
        raise HTTPException(403, "只能删除自己添加的汇总")
    s.deleted_at = utcnow()
    bump(v)
    log_activity(db, v.id, user, "delete", "summary", s.id, "删除了历史汇总")
    db.commit()
    return {"ok": True}


# ---------- 段标签 ----------


class MarkIn(BaseModel):
    vehicle_id: int
    end_record_id: int
    energy: Literal["fuel", "elec", "combined"]
    tag: str = "normal"
    excluded: bool = False
    note: str = Field("", max_length=200)

    @field_validator("tag")
    @classmethod
    def _tag(cls, v):
        if v not in SEGMENT_TAGS:
            raise ValueError("标签不正确")
        return v


@router.patch("/segments")
def mark_segment(body: MarkIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """综合段与同区间的油段一一对应，标记综合段时油段一起标。"""
    v, _ = require_vehicle(db, user, body.vehicle_id, "editor")
    for energy in [body.energy] + (["fuel"] if body.energy == "combined" else []):
        m = db.get(SegmentMark, (v.id, body.end_record_id, energy))
        if body.tag == "normal" and not body.excluded:
            if m:
                db.delete(m)
            continue
        if not m:
            m = SegmentMark(vehicle_id=v.id, end_record_id=body.end_record_id, energy=energy)
            db.add(m)
        m.tag, m.excluded, m.note, m.updated_by = body.tag, body.excluded, body.note, user.id
    bump(v)
    log_activity(db, v.id, user, "update", "segment", body.end_record_id, "调整了一段能耗的标签")
    db.commit()
    return {"ok": True}
