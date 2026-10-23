"""
expenses.py — 费用、提醒、常用地点
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.access import can_delete_entity, my_vehicle_ids, require_vehicle
from app.auth import get_current_user
from app.constants import CATEGORY_TO_TEMPLATES, EXPENSE_CATEGORIES, PLACE_KINDS, REMINDER_TEMPLATES, TEMPLATE_BY_KEY
from app.database import Expense, Place, Record, Reminder, ReminderMute, User, Vehicle, VehicleMember, get_db, localnow, utcnow
from app.engine.aggregate import avg_daily_km, odo_observations
from app.engine.reminders import ReminderIn, evaluate, next_due
from app.insights import latest_odo_of
from app.services import expense_dict, iso, load_expenses, load_ledger, log_activity, users_map

router = APIRouter(prefix="/api", tags=["expenses"])


# ---------- 费用 ----------


class SubItem(BaseModel):
    name: str = Field(..., max_length=20)
    cents: int = Field(..., ge=0)


class ExpenseIn(BaseModel):
    vehicle_id: int
    category: str
    title: str = Field("", max_length=64)
    sub_items: list[SubItem] = Field(default_factory=list)
    occurred_at: datetime
    odometer_km: Optional[float] = Field(None, ge=0, le=3_000_000)
    amount_cents: int = Field(..., ge=0, le=100_000_000)
    vendor: str = Field("", max_length=64)
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    payer_user_id: Optional[int] = None
    note: str = Field("", max_length=500)
    client_id: Optional[str] = Field(None, max_length=40)
    version: Optional[int] = None

    @field_validator("category")
    @classmethod
    def _cat(cls, v):
        if v not in EXPENSE_CATEGORIES:
            raise ValueError("费用分类不正确")
        return v


def _apply_expense(e: Expense, body: ExpenseIn) -> None:
    for k in ("category", "title", "occurred_at", "odometer_km", "amount_cents", "vendor", "period_start",
              "period_end", "payer_user_id"):
        setattr(e, k, getattr(body, k))
    e.note = body.note.strip()
    e.sub_items = json.dumps([s.model_dump() for s in body.sub_items], ensure_ascii=False)


def _sync_insurance(db: Session, v: Vehicle, e: Expense, user: User) -> Optional[dict]:
    """记录保险时，按保险止期自动新建或更新「保险到期」提醒。"""
    if e.category != "insurance" or not e.period_end:
        return None
    rem = db.scalar(select(Reminder).where(Reminder.vehicle_id == v.id, Reminder.template_key == "insurance"))
    t = TEMPLATE_BY_KEY["insurance"]
    if not rem:
        rem = Reminder(vehicle_id=v.id, title=t["title"], template_key="insurance", interval_months=12,
                       lead_days=t["lead_days"], created_by=user.id)
        db.add(rem)
    rem.due_date = e.period_end
    rem.last_done_at = e.period_start or e.occurred_at.date()
    rem.is_active = True
    return {"title": rem.title, "due_date": iso(e.period_end)}


def _suggest_complete(db: Session, v: Vehicle, category: str) -> list[dict]:
    keys = CATEGORY_TO_TEMPLATES.get(category, ())
    if not keys or category == "insurance":
        return []
    rows = db.scalars(select(Reminder).where(Reminder.vehicle_id == v.id, Reminder.is_active.is_(True),
                                             Reminder.template_key.in_(keys)))
    return [{"id": r.id, "title": r.title} for r in rows]


@router.post("/expenses")
def create_expense(body: ExpenseIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    v, _ = require_vehicle(db, user, body.vehicle_id, "editor")
    if body.client_id:
        exist = db.scalar(select(Expense).where(Expense.client_id == body.client_id))
        if exist:
            return {"expense": expense_dict(exist), "replayed": True}
    e = Expense(vehicle_id=v.id, created_by=user.id, updated_by=user.id, client_id=body.client_id)
    _apply_expense(e, body)
    if e.payer_user_id is None:
        e.payer_user_id = user.id
    elif not db.get(VehicleMember, (v.id, e.payer_user_id)):
        raise HTTPException(400, "付款人不是这辆车的成员")
    db.add(e)
    db.flush()
    ins = _sync_insurance(db, v, e, user)
    label = EXPENSE_CATEGORIES[e.category]
    log_activity(db, v.id, user, "create", "expense", e.id, f"记了一笔{label} ¥{e.amount_cents / 100:,.2f}")
    db.commit()
    return {"expense": expense_dict(e, users_map(db, [user.id])), "insurance_reminder": ins,
            "suggest_complete": _suggest_complete(db, v, e.category)}


@router.get("/expenses/{eid}")
def get_expense(eid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    e = db.get(Expense, eid)
    if not e:
        raise HTTPException(404, "费用不存在")
    v, role = require_vehicle(db, user, e.vehicle_id)
    d = expense_dict(e, users_map(db, [e.created_by, e.payer_user_id]))
    d["can_delete"] = can_delete_entity(role, user, e.created_by)
    d["can_edit"] = role in ("owner", "editor") and not v.is_archived
    return d


@router.put("/expenses/{eid}")
def update_expense(eid: int, body: ExpenseIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    e = db.get(Expense, eid)
    if not e or e.deleted_at:
        raise HTTPException(404, "费用不存在")
    v, _ = require_vehicle(db, user, e.vehicle_id, "editor")
    if body.version is not None and body.version != e.version:
        raise HTTPException(409, "这笔费用刚被别人修改过，请刷新后再改")
    _apply_expense(e, body)
    e.version += 1
    e.updated_by = user.id
    ins = _sync_insurance(db, v, e, user)
    log_activity(db, v.id, user, "update", "expense", e.id, f"修改了一笔{EXPENSE_CATEGORIES[e.category]}")
    db.commit()
    return {"expense": expense_dict(e), "insurance_reminder": ins}


@router.delete("/expenses/{eid}")
def delete_expense(eid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    e = db.get(Expense, eid)
    if not e or e.deleted_at:
        raise HTTPException(404, "费用不存在")
    v, role = require_vehicle(db, user, e.vehicle_id, "editor")
    if not can_delete_entity(role, user, e.created_by):
        raise HTTPException(403, "只能删除自己记的费用")
    e.deleted_at = utcnow()
    log_activity(db, v.id, user, "delete", "expense", e.id, f"删除了一笔{EXPENSE_CATEGORIES[e.category]}")
    db.commit()
    return {"ok": True}


@router.post("/expenses/{eid}/restore")
def restore_expense(eid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    e = db.get(Expense, eid)
    if not e or not e.deleted_at:
        raise HTTPException(404, "费用不在回收站")
    v, role = require_vehicle(db, user, e.vehicle_id, "editor")
    if not can_delete_entity(role, user, e.created_by):
        raise HTTPException(403, "只能恢复自己记的费用")
    e.deleted_at = None
    db.commit()
    return {"ok": True}


# ---------- 提醒 ----------


class ReminderBody(BaseModel):
    vehicle_id: Optional[int] = None
    title: str = Field(..., min_length=1, max_length=64)
    template_key: str = Field("custom", max_length=24)
    due_date: Optional[date] = None
    due_odometer_km: Optional[float] = Field(None, ge=0)
    interval_months: Optional[int] = Field(None, ge=1, le=240)
    interval_km: Optional[float] = Field(None, ge=100, le=500000)
    lead_days: int = Field(30, ge=0, le=365)
    lead_km: float = Field(500, ge=0, le=50000)
    last_done_at: Optional[date] = None
    last_done_odometer_km: Optional[float] = None
    is_active: bool = True
    note: str = Field("", max_length=200)


class CompleteIn(BaseModel):
    done_at: date
    odometer_km: Optional[float] = Field(None, ge=0)
    expense_cents: Optional[int] = Field(None, ge=0)
    expense_category: Optional[str] = None
    vendor: str = Field("", max_length=64)
    note: str = Field("", max_length=200)


def _reminder_access(db: Session, user: User, r: Reminder, min_role: str = "viewer") -> None:
    if r.vehicle_id is None:
        if r.owner_user_id != user.id:
            raise HTTPException(404, "提醒不存在")
        return
    require_vehicle(db, user, r.vehicle_id, min_role)


def _vehicle_context(db: Session, v: Vehicle) -> tuple:
    led = load_ledger(db, v)
    obs = odo_observations(led, load_expenses(db, v.id))
    return latest_odo_of(obs, v), avg_daily_km(obs, localnow().date())


def reminder_dict(r: Reminder, ctx: Optional[tuple], muted: bool, vehicle: Optional[Vehicle] = None) -> dict:
    odo, daily = ctx if ctx else (None, None)
    ev = evaluate(ReminderIn(r.due_date, r.due_odometer_km, r.interval_months, r.interval_km, r.lead_days,
                             r.lead_km, r.last_done_at, r.last_done_odometer_km), localnow().date(), odo, daily)
    if not r.is_active:
        ev["status"] = "inactive"
    return {
        "id": r.id, "vehicle_id": r.vehicle_id, "vehicle_name": vehicle.name if vehicle else None,
        "personal": r.vehicle_id is None, "title": r.title, "template_key": r.template_key,
        "due_date": iso(r.due_date), "due_odometer_km": r.due_odometer_km, "interval_months": r.interval_months,
        "interval_km": r.interval_km, "lead_days": r.lead_days, "lead_km": r.lead_km,
        "last_done_at": iso(r.last_done_at), "last_done_odometer_km": r.last_done_odometer_km,
        "is_active": r.is_active, "note": r.note, "muted": muted, "current_odo": odo, **ev,
    }


def collect_reminders(db: Session, user: User, vehicle_id: Optional[int] = None) -> list[dict]:
    vids = [vehicle_id] if vehicle_id else my_vehicle_ids(db, user)
    if vehicle_id:
        require_vehicle(db, user, vehicle_id)
    q = select(Reminder).where(or_(Reminder.vehicle_id.in_(vids),
                                   (Reminder.vehicle_id.is_(None)) & (Reminder.owner_user_id == user.id)))
    rows = list(db.scalars(q))
    muted = set(db.scalars(select(ReminderMute.reminder_id).where(ReminderMute.user_id == user.id)))
    vehicles = {v.id: v for v in db.scalars(select(Vehicle).where(Vehicle.id.in_(vids)))}
    ctx = {vid: _vehicle_context(db, v) for vid, v in vehicles.items()}
    out = [reminder_dict(r, ctx.get(r.vehicle_id), r.id in muted, vehicles.get(r.vehicle_id)) for r in rows]
    order = {"overdue": 0, "soon": 1, "ok": 2, "inactive": 3}
    out.sort(key=lambda x: (order[x["status"]], x["next_date"] or "9999", x["km_left"] if x["km_left"] is not None else 1e9))
    return out


@router.get("/reminders/templates")
def reminder_templates():
    return REMINDER_TEMPLATES


@router.get("/reminders")
def list_reminders(vehicle_id: Optional[int] = None, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    return collect_reminders(db, user, vehicle_id)


@router.get("/reminders/due")
def due_count(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    items = [r for r in collect_reminders(db, user) if r["status"] in ("soon", "overdue") and not r["muted"]]
    return {"count": len(items), "items": items[:5]}


def _apply_reminder(r: Reminder, body: ReminderBody) -> None:
    for k, val in body.model_dump(exclude={"vehicle_id"}).items():
        setattr(r, k, val)
    if r.due_date is None and r.due_odometer_km is None and r.is_active:
        raise HTTPException(400, "请至少填写到期日期或到期里程")


@router.post("/reminders")
def create_reminder(body: ReminderBody, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.vehicle_id:
        require_vehicle(db, user, body.vehicle_id, "editor")
    r = Reminder(vehicle_id=body.vehicle_id, owner_user_id=None if body.vehicle_id else user.id, created_by=user.id)
    _apply_reminder(r, body)
    db.add(r)
    db.flush()
    if r.vehicle_id:
        log_activity(db, r.vehicle_id, user, "create", "reminder", r.id, f"添加了提醒「{r.title}」")
    db.commit()
    return {"id": r.id}


@router.put("/reminders/{rid}")
def update_reminder(rid: int, body: ReminderBody, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    r = db.get(Reminder, rid)
    if not r:
        raise HTTPException(404, "提醒不存在")
    _reminder_access(db, user, r, "editor")
    _apply_reminder(r, body)
    if r.vehicle_id:
        log_activity(db, r.vehicle_id, user, "update", "reminder", r.id, f"修改了提醒「{r.title}」")
    db.commit()
    return {"ok": True}


@router.delete("/reminders/{rid}")
def delete_reminder(rid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    r = db.get(Reminder, rid)
    if not r:
        raise HTTPException(404, "提醒不存在")
    _reminder_access(db, user, r, "editor")
    if r.vehicle_id:
        log_activity(db, r.vehicle_id, user, "delete", "reminder", r.id, f"删除了提醒「{r.title}」")
    db.delete(r)
    db.commit()
    return {"ok": True}


@router.post("/reminders/{rid}/complete")
def complete_reminder(rid: int, body: CompleteIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """以实际完成的日期和里程顺延；一次性提醒完成后停用。可顺手记一笔费用。"""
    r = db.get(Reminder, rid)
    if not r:
        raise HTTPException(404, "提醒不存在")
    _reminder_access(db, user, r, "editor")
    done_odo = body.odometer_km
    if done_odo is None and r.interval_km and r.vehicle_id:
        # 没填完成时的里程：用车辆最新里程顺延，否则按里程的周期会丢失
        done_odo = _vehicle_context(db, db.get(Vehicle, r.vehicle_id))[0]
    r.last_done_at, r.last_done_odometer_km = body.done_at, done_odo
    nd, no = next_due(ReminderIn(interval_months=r.interval_months, interval_km=r.interval_km), body.done_at, done_odo)
    if nd is None and no is None:
        r.is_active = False
    else:
        r.due_date, r.due_odometer_km = nd, no
    if body.expense_cents and r.vehicle_id:
        cat = body.expense_category if body.expense_category in EXPENSE_CATEGORIES else "maintenance"
        db.add(Expense(vehicle_id=r.vehicle_id, category=cat, title=r.title,
                       occurred_at=datetime.combine(body.done_at, datetime.min.time()).replace(hour=12),
                       odometer_km=body.odometer_km, amount_cents=body.expense_cents, vendor=body.vendor,
                       note=body.note, payer_user_id=user.id, created_by=user.id, updated_by=user.id))
    if r.vehicle_id:
        log_activity(db, r.vehicle_id, user, "update", "reminder", r.id, f"完成了「{r.title}」")
    db.commit()
    return {"ok": True, "due_date": iso(r.due_date), "due_odometer_km": r.due_odometer_km, "is_active": r.is_active}


@router.post("/reminders/{rid}/mute")
def mute_reminder(rid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    r = db.get(Reminder, rid)
    if not r:
        raise HTTPException(404, "提醒不存在")
    _reminder_access(db, user, r)
    m = db.get(ReminderMute, (rid, user.id))
    if m:
        db.delete(m)
    else:
        db.add(ReminderMute(reminder_id=rid, user_id=user.id))
    db.commit()
    return {"muted": not bool(m)}


# ---------- 常用地点 ----------


class TariffItem(BaseModel):
    start: str = Field(..., alias="from", pattern=r"^\d{2}:\d{2}$")
    end: str = Field(..., alias="to", pattern=r"^\d{2}:\d{2}$")
    price: float = Field(..., ge=0, le=20)

    model_config = {"populate_by_name": True}


class PlaceIn(BaseModel):
    vehicle_id: Optional[int] = None
    shared: bool = True
    name: str = Field(..., min_length=1, max_length=64)
    kind: str = "other"
    lat: Optional[float] = None
    lng: Optional[float] = None
    tou_tariff: list[TariffItem] = Field(default_factory=list)
    default_charge_type: Optional[str] = None
    default_fuel_grade: Optional[str] = None
    last_unit_price: Optional[float] = Field(None, ge=0, le=100)
    sort_order: int = 0
    is_hidden: bool = False

    @field_validator("kind")
    @classmethod
    def _kind(cls, v):
        if v not in PLACE_KINDS:
            raise ValueError("地点类型不正确")
        return v


def place_dict(p: Place) -> dict:
    try:
        tariff = json.loads(p.tou_tariff or "[]")
    except ValueError:
        tariff = []
    return {"id": p.id, "vehicle_id": p.vehicle_id, "shared": p.vehicle_id is not None, "name": p.name,
            "kind": p.kind, "lat": p.lat, "lng": p.lng, "tou_tariff": tariff,
            "default_charge_type": p.default_charge_type, "default_fuel_grade": p.default_fuel_grade,
            "last_unit_price": p.last_unit_price, "sort_order": p.sort_order, "is_hidden": p.is_hidden,
            "use_count": p.use_count, "last_used_at": iso(p.last_used_at)}


def _place_access(db: Session, user: User, p: Place, min_role="editor") -> None:
    if p.vehicle_id is None:
        if p.owner_user_id != user.id:
            raise HTTPException(404, "地点不存在")
    else:
        require_vehicle(db, user, p.vehicle_id, min_role)


def _apply_place(p: Place, body: PlaceIn) -> None:
    for k in ("name", "kind", "lat", "lng", "default_charge_type", "default_fuel_grade", "last_unit_price",
              "sort_order", "is_hidden"):
        setattr(p, k, getattr(body, k))
    p.name = p.name.strip()
    p.tou_tariff = json.dumps([t.model_dump(by_alias=True) for t in body.tou_tariff])


@router.get("/places")
def list_places(vehicle_id: Optional[int] = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    vids = my_vehicle_ids(db, user)
    if vehicle_id:
        require_vehicle(db, user, vehicle_id)
        vids = [vehicle_id]
    rows = db.scalars(select(Place).where(or_(Place.vehicle_id.in_(vids),
                                              (Place.vehicle_id.is_(None)) & (Place.owner_user_id == user.id)))
                      .order_by(Place.sort_order, Place.use_count.desc(), Place.id))
    return [place_dict(p) for p in rows]


@router.post("/places")
def create_place(body: PlaceIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    vid = body.vehicle_id if body.shared else None
    if body.shared:
        if not body.vehicle_id:
            raise HTTPException(400, "共享地点需要指定车辆")
        require_vehicle(db, user, body.vehicle_id, "editor")
    p = Place(vehicle_id=vid, owner_user_id=user.id)
    _apply_place(p, body)
    db.add(p)
    db.commit()
    return place_dict(p)


@router.put("/places/{pid}")
def update_place(pid: int, body: PlaceIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = db.get(Place, pid)
    if not p:
        raise HTTPException(404, "地点不存在")
    _place_access(db, user, p)
    _apply_place(p, body)
    db.commit()
    return place_dict(p)


@router.delete("/places/{pid}")
def delete_place(pid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = db.get(Place, pid)
    if not p:
        raise HTTPException(404, "地点不存在")
    _place_access(db, user, p)
    # 历史记录保留地点名称，只解除关联
    db.execute(update(Record).where(Record.place_id == pid).values(place_id=None))
    db.delete(p)
    db.commit()
    return {"ok": True}


class MergeIn(BaseModel):
    from_ids: list[int]
    to_id: int


@router.post("/places/merge")
def merge_places(body: MergeIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = db.get(Place, body.to_id)
    if not target:
        raise HTTPException(404, "地点不存在")
    _place_access(db, user, target)
    for fid in body.from_ids:
        if fid == body.to_id:
            continue
        src = db.get(Place, fid)
        if not src:
            continue
        _place_access(db, user, src)
        db.execute(update(Record).where(Record.place_id == fid).values(place_id=target.id, place_name=target.name))
        target.use_count = (target.use_count or 0) + (src.use_count or 0)
        db.delete(src)
    db.commit()
    return place_dict(target)
