"""
vehicles.py — 车辆、新车向导、成员与邀请、动态、k 值、数据体检与一键修复
"""
from __future__ import annotations

import json
import secrets
from datetime import date, datetime, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.access import ROLE_LABEL, owner_count, require_vehicle
from app.auth import get_current_user, user_brief
from app.constants import TEMPLATE_BY_KEY
from app.database import (
    ActivityLog,
    DismissedHint,
    Invite,
    PeriodSummary,
    Record,
    Reminder,
    User,
    Vehicle,
    VehicleMember,
    get_db,
    localnow,
    utcnow,
)
from app.engine.aggregate import odo_observations
from app.engine.reminders import add_months
from app.insights import health_report, latest_odo_of
from app.services import bump, iso, load_expenses, load_ledger, log_activity, users_map, vehicle_dict

router = APIRouter(prefix="/api", tags=["vehicles"])

EnergyType = Literal["ice", "hev", "phev", "erev", "bev"]


class VehicleIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=32)
    plate: str = Field("", max_length=16)
    brand: str = Field("", max_length=32)
    model: str = Field("", max_length=32)
    year: Optional[int] = Field(None, ge=1950, le=2100)
    energy_type: EnergyType = "phev"
    tank_capacity_l: Optional[float] = Field(None, gt=0, le=300)
    battery_usable_kwh: Optional[float] = Field(None, gt=0, le=300)
    charge_limit_pct: float = Field(100, ge=50, le=100)
    default_fuel_grade: str = Field("92", max_length=8)
    default_charge_type: str = Field("home", max_length=10)
    fuel_gauge_mode: Literal["pct", "bars", "liters"] = "pct"
    fuel_gauge_bars: Optional[int] = Field(None, ge=2, le=20)
    factory_fuel_l: float = Field(5, ge=0, le=100)
    record_style: Literal["detailed", "simple"] = "detailed"
    initial_odometer_km: Optional[float] = Field(None, ge=0, le=3_000_000)
    delivery_date: Optional[date] = None
    purchase_price_cents: Optional[int] = Field(None, ge=0)
    color: str = Field("#4F46E5", max_length=16)
    calc_settings: dict = Field(default_factory=dict)


class StartIn(BaseModel):
    """新车向导第 3 步：怎么开始记。"""

    mode: Literal["new", "existing", "skip"] = "existing"
    delivery_odo: Optional[float] = Field(None, ge=0)
    delivery_fill_l: Optional[float] = Field(None, ge=0, le=300)
    delivery_fill_cents: Optional[int] = Field(None, ge=0)
    delivery_soc: Optional[float] = Field(None, ge=0, le=100)
    current_odo: Optional[float] = Field(None, ge=0)
    current_fuel_pct: Optional[float] = Field(None, ge=0, le=100)
    current_soc: Optional[float] = Field(None, ge=0, le=100)


class WizardSummaryIn(BaseModel):
    start_date: Optional[date] = None
    fuel_liters: Optional[float] = Field(None, ge=0)
    fuel_cents: Optional[int] = Field(None, ge=0)
    fuel_price: Optional[float] = Field(None, gt=0)
    elec_kwh: Optional[float] = Field(None, ge=0)
    elec_cents: Optional[int] = Field(None, ge=0)
    elec_price: Optional[float] = Field(None, gt=0)
    note: str = Field("", max_length=200)


class WizardReminderIn(BaseModel):
    template_key: str
    due_date: Optional[date] = None
    due_odometer_km: Optional[float] = None


class VehicleCreateIn(VehicleIn):
    start: Optional[StartIn] = None
    summary: Optional[WizardSummaryIn] = None
    reminders: list[WizardReminderIn] = Field(default_factory=list)


def _summary_has_data(s: "WizardSummaryIn") -> bool:
    return any(x is not None for x in (s.fuel_liters, s.fuel_cents, s.elec_kwh, s.elec_cents))


def _clean_calc(c: dict) -> dict:
    out = {}
    for key, lo, hi in (("min_segment_km", 5, 500), ("kwh_per_l", 5, 15), ("gauge_err_pct", 0, 30)):
        if key in c and c[key] is not None:
            out[key] = min(max(float(c[key]), lo), hi)
    if "allow_gauge_anchor" in c:
        out["allow_gauge_anchor"] = bool(c["allow_gauge_anchor"])
    return out


def _apply(v: Vehicle, body: VehicleIn) -> None:
    data = body.model_dump(exclude={"calc_settings", "start", "summary", "reminders"})
    for k, val in data.items():
        setattr(v, k, val)
    v.calc_settings = json.dumps(_clean_calc(body.calc_settings))


def _vehicle_list_item(db: Session, v: Vehicle, role: str) -> dict:
    d = vehicle_dict(v, role)
    d["member_count"] = db.scalar(
        select(func.count()).select_from(VehicleMember).where(VehicleMember.vehicle_id == v.id)
    )
    d["latest_odo"] = db.scalar(
        select(func.max(Record.odometer_km)).where(Record.vehicle_id == v.id, Record.deleted_at.is_(None))
    )
    return d


@router.get("/vehicles")
def list_vehicles(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(Vehicle, VehicleMember.role).join(VehicleMember, VehicleMember.vehicle_id == Vehicle.id)
        .where(VehicleMember.user_id == user.id).order_by(Vehicle.is_archived, Vehicle.id)
    ).all()
    return [_vehicle_list_item(db, v, role) for v, role in rows]


@router.post("/vehicles")
def create_vehicle(body: VehicleCreateIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    v = Vehicle(created_by=user.id)
    _apply(v, body)
    st = body.start
    today = localnow().date()
    # 中途开始记的用户常常不记得提车信息：只有提车日期和里程都填了才建提车锚点，缺哪个都不猜
    has_delivery = bool(st and st.mode == "existing" and v.delivery_date and st.delivery_odo is not None)
    if st and st.mode == "existing":
        if v.delivery_date and v.delivery_date > today:
            raise HTTPException(400, "提车日期不能晚于今天")
        if st.delivery_odo is not None and st.current_odo is not None and st.delivery_odo > st.current_odo:
            raise HTTPException(400, "提车里程不能大于当前里程")
        if st.delivery_fill_l and not has_delivery:
            raise HTTPException(400, "填「提车时加油」需要同时填提车日期和提车里程")
        sm = body.summary
        if sm and _summary_has_data(sm) and not (sm.start_date or v.delivery_date):
            raise HTTPException(400, "历史汇总需要填开始日期")
    if has_delivery:
        v.initial_odometer_km = st.delivery_odo
    elif st and st.mode == "new":
        v.initial_odometer_km = st.current_odo
    elif st and st.mode == "existing":
        # 中途接入且不知道提车里程：没有起始里程，里程统计从记账起点开始
        v.initial_odometer_km = None
    db.add(v)
    db.flush()
    db.add(VehicleMember(vehicle_id=v.id, user_id=user.id, role="owner"))
    now = localnow()

    def snap(ts, odo, tag, fuel_pct=None, soc=None, est=()):
        r = Record(vehicle_id=v.id, kind="snapshot", occurred_at=ts, odometer_km=odo, snapshot_tag=tag,
                   fuel_level_before_pct=fuel_pct if v.energy_type != "bev" else None,
                   soc_before_pct=soc if v.energy_type not in ("ice", "hev") else None,
                   estimated_fields=json.dumps(list(est)), created_by=user.id, updated_by=user.id,
                   source="manual", note="新车向导")
        db.add(r)
        return r

    if has_delivery:
        ddate = v.delivery_date
        dts = datetime.combine(ddate, datetime.min.time()).replace(hour=9)
        dodo = st.delivery_odo
        has_fuel = v.energy_type != "bev"
        pct = None
        if has_fuel and v.tank_capacity_l:
            pct = round(min(v.factory_fuel_l / v.tank_capacity_l * 100, 100), 2)
        snap(dts, dodo, "delivery", fuel_pct=pct, soc=st.delivery_soc, est=("fuel_before_pct",))
        if has_fuel and st.delivery_fill_l:
            db.add(Record(vehicle_id=v.id, kind="fuel", occurred_at=dts + timedelta(minutes=1), odometer_km=dodo,
                          fuel_liters=st.delivery_fill_l, fuel_grade=v.default_fuel_grade,
                          paid_cents=st.delivery_fill_cents, amount_cents=st.delivery_fill_cents,
                          fuel_unit_price=round(st.delivery_fill_cents / 100 / st.delivery_fill_l, 2)
                          if st.delivery_fill_cents else None,
                          fuel_is_full=False, created_by=user.id, updated_by=user.id, payer_user_id=user.id,
                          note="提车时加油"))
    if st and st.mode == "existing" and body.summary and _summary_has_data(body.summary):
        s_ = body.summary
        s_start = s_.start_date or (v.delivery_date + timedelta(days=1))
        s_end = max(today - timedelta(days=1), s_start)
        db.add(PeriodSummary(vehicle_id=v.id, start_date=s_start, end_date=s_end, mode="extra",
                             start_odometer_km=st.delivery_odo if has_delivery else None, end_odometer_km=st.current_odo,
                             fuel_liters=s_.fuel_liters, fuel_cents=s_.fuel_cents, fuel_price=s_.fuel_price,
                             elec_kwh=s_.elec_kwh, elec_cents=s_.elec_cents, elec_price=s_.elec_price,
                             note=s_.note or "新车向导：历史汇总", created_by=user.id, updated_by=user.id))
    if st and st.mode in ("new", "existing") and st.current_odo is not None:
        snap(now, st.current_odo, "ledger_start", fuel_pct=st.current_fuel_pct, soc=st.current_soc)

    for item in body.reminders:
        t = TEMPLATE_BY_KEY.get(item.template_key)
        if not t:
            continue
        personal = t.get("personal")
        interval_km = t.get("ice_km") if (v.energy_type in ("ice", "hev") and t.get("ice_km")) else t.get("interval_km")
        due_odo = item.due_odometer_km
        if due_odo is None and interval_km and st and st.current_odo is not None:
            due_odo = st.current_odo + interval_km
        due_date = item.due_date
        if due_date is None and t.get("interval_months"):
            # 没填日期时按「提车日（没有就今天）+ 周期」推算，滚动到今天之后
            base = v.delivery_date or today
            due_date = add_months(base, t["interval_months"])
            while due_date <= now.date():
                due_date = add_months(due_date, t["interval_months"])
        if due_date is None and due_odo is None:
            continue
        db.add(Reminder(vehicle_id=None if personal else v.id, owner_user_id=user.id if personal else None,
                        title=t["title"], template_key=t["key"], due_date=due_date, due_odometer_km=due_odo,
                        interval_months=t.get("interval_months"), interval_km=interval_km,
                        lead_days=t.get("lead_days", 30), lead_km=t.get("lead_km", 500), created_by=user.id))
    settings = user.get_settings()
    if not settings.get("default_vehicle_id"):
        settings["default_vehicle_id"] = v.id
        user.settings = json.dumps(settings, ensure_ascii=False)
    log_activity(db, v.id, user, "create", "vehicle", v.id, f"添加了车辆「{v.name}」")
    db.commit()
    return vehicle_dict(v, "owner")


@router.get("/vehicles/{vid}")
def get_vehicle(vid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    v, role = require_vehicle(db, user, vid)
    return _vehicle_list_item(db, v, role)


@router.put("/vehicles/{vid}")
def update_vehicle(vid: int, body: VehicleIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    v, _ = require_vehicle(db, user, vid, "editor")
    _apply(v, body)
    bump(v)
    log_activity(db, v.id, user, "update", "vehicle", v.id, "修改了车辆参数")
    db.commit()
    return vehicle_dict(v)


class ArchiveIn(BaseModel):
    archived: bool


@router.post("/vehicles/{vid}/archive")
def archive_vehicle(vid: int, body: ArchiveIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    v, _ = require_vehicle(db, user, vid, "owner")
    v.is_archived = body.archived
    log_activity(db, v.id, user, "update", "vehicle", v.id, "归档了车辆" if body.archived else "取消归档")
    db.commit()
    return {"ok": True}


class DeleteVehicleIn(BaseModel):
    confirm_name: str


@router.post("/vehicles/{vid}/delete")
def delete_vehicle(vid: int, body: DeleteVehicleIn, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    v, _ = require_vehicle(db, user, vid, "owner")
    if body.confirm_name.strip() != v.name:
        raise HTTPException(400, "车名不一致，未删除")
    db.execute(delete(Reminder).where(Reminder.vehicle_id == vid))
    db.delete(v)
    db.commit()
    return {"ok": True}


# ---------- 成员 ----------


class MemberAddIn(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    role: Literal["owner", "editor", "viewer"] = "editor"


class MemberRoleIn(BaseModel):
    role: Literal["owner", "editor", "viewer"]


@router.get("/vehicles/{vid}/members")
def list_members(vid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_vehicle(db, user, vid)
    rows = db.execute(
        select(VehicleMember, User).join(User, User.id == VehicleMember.user_id)
        .where(VehicleMember.vehicle_id == vid).order_by(VehicleMember.joined_at)
    ).all()
    return [{**user_brief(u), "role": m.role, "role_label": ROLE_LABEL[m.role], "joined_at": iso(m.joined_at),
             "is_me": u.id == user.id} for m, u in rows]


@router.post("/vehicles/{vid}/members")
def add_member(vid: int, body: MemberAddIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    v, _ = require_vehicle(db, user, vid, "owner")
    target = db.scalar(select(User).where(User.username == body.username.strip(), User.is_active.is_(True)))
    if not target:
        raise HTTPException(404, "没有这个用户名")
    m = db.get(VehicleMember, (vid, target.id))
    if m:
        raise HTTPException(409, f"{target.name} 已经是成员了")
    db.add(VehicleMember(vehicle_id=vid, user_id=target.id, role=body.role))
    log_activity(db, vid, user, "join", "member", target.id, f"添加了成员 {target.name}（{ROLE_LABEL[body.role]}）")
    db.commit()
    return {"ok": True}


@router.put("/vehicles/{vid}/members/{uid}")
def update_member(vid: int, uid: int, body: MemberRoleIn, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    require_vehicle(db, user, vid, "owner")
    m = db.get(VehicleMember, (vid, uid))
    if not m:
        raise HTTPException(404, "成员不存在")
    if m.role == "owner" and body.role != "owner" and owner_count(db, vid) <= 1:
        raise HTTPException(400, "至少要保留一位车主")
    m.role = body.role
    target = db.get(User, uid)
    log_activity(db, vid, user, "update", "member", uid, f"把 {target.name} 设为{ROLE_LABEL[body.role]}")
    db.commit()
    return {"ok": True}


@router.delete("/vehicles/{vid}/members/{uid}")
def remove_member(vid: int, uid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if uid == user.id:
        return leave_vehicle(vid, user, db)
    require_vehicle(db, user, vid, "owner")
    m = db.get(VehicleMember, (vid, uid))
    if not m:
        raise HTTPException(404, "成员不存在")
    if m.role == "owner" and owner_count(db, vid) <= 1:
        raise HTTPException(400, "至少要保留一位车主")
    target = db.get(User, uid)
    db.delete(m)
    log_activity(db, vid, user, "delete", "member", uid, f"移除了成员 {target.name}")
    db.commit()
    return {"ok": True}


@router.post("/vehicles/{vid}/leave")
def leave_vehicle(vid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _, role = require_vehicle(db, user, vid)
    if role == "owner" and owner_count(db, vid) <= 1:
        raise HTTPException(400, "你是唯一的车主，请先把其他成员设为车主，或删除车辆")
    db.delete(db.get(VehicleMember, (vid, user.id)))
    log_activity(db, vid, user, "delete", "member", user.id, f"{user.name} 退出了车辆")
    db.commit()
    return {"ok": True}


# ---------- 邀请 ----------

_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class InviteIn(BaseModel):
    role: Literal["owner", "editor", "viewer"] = "editor"
    days: int = Field(7, ge=1, le=90)
    max_uses: int = Field(1, ge=1, le=20)
    allow_signup: bool = False


def _invite_dict(inv: Invite) -> dict:
    return {"id": inv.id, "code": inv.code, "role": inv.role, "role_label": ROLE_LABEL[inv.role],
            "expires_at": iso(inv.expires_at), "max_uses": inv.max_uses, "used_count": inv.used_count,
            "allow_signup": inv.allow_signup}


@router.get("/vehicles/{vid}/invites")
def list_invites(vid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_vehicle(db, user, vid, "owner")
    rows = db.scalars(select(Invite).where(
        Invite.vehicle_id == vid, Invite.revoked_at.is_(None), Invite.expires_at > utcnow(),
        Invite.used_count < Invite.max_uses,
    ).order_by(Invite.id.desc()))
    return [_invite_dict(i) for i in rows]


@router.post("/vehicles/{vid}/invites")
def create_invite(vid: int, body: InviteIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_vehicle(db, user, vid, "owner")
    for _ in range(10):
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
        if not db.scalar(select(Invite).where(Invite.code == code)):
            break
    inv = Invite(code=code, vehicle_id=vid, role=body.role, created_by=user.id,
                 expires_at=utcnow() + timedelta(days=body.days), max_uses=body.max_uses,
                 allow_signup=body.allow_signup)
    db.add(inv)
    db.commit()
    return _invite_dict(inv)


@router.delete("/invites/{iid}")
def revoke_invite(iid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    inv = db.get(Invite, iid)
    if not inv:
        raise HTTPException(404, "邀请不存在")
    require_vehicle(db, user, inv.vehicle_id, "owner")
    inv.revoked_at = utcnow()
    db.commit()
    return {"ok": True}


def _valid_invite(db: Session, code: str) -> Invite:
    inv = db.scalar(select(Invite).where(Invite.code == code.strip().upper()))
    if not inv or inv.revoked_at or inv.expires_at < utcnow() or inv.used_count >= inv.max_uses:
        raise HTTPException(404, "邀请码无效或已过期")
    return inv


@router.get("/invites/{code}")
def preview_invite(code: str, db: Session = Depends(get_db)):
    """未登录也能预览（用于邀请注册页），只返回车名、邀请人和角色。"""
    inv = _valid_invite(db, code)
    v = db.get(Vehicle, inv.vehicle_id)
    inviter = db.get(User, inv.created_by)
    from app.database import get_setting

    return {"vehicle_name": v.name, "inviter": inviter.name if inviter else "", "role": inv.role,
            "role_label": ROLE_LABEL[inv.role],
            "allow_signup": inv.allow_signup and get_setting(db, "allow_signup", "false") == "true"}


@router.post("/invites/{code}/accept")
def accept_invite(code: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    inv = _valid_invite(db, code)
    existing = db.get(VehicleMember, (inv.vehicle_id, user.id))
    if existing:
        return {"ok": True, "vehicle_id": inv.vehicle_id, "already": True}
    db.add(VehicleMember(vehicle_id=inv.vehicle_id, user_id=user.id, role=inv.role))
    inv.used_count += 1
    log_activity(db, inv.vehicle_id, user, "join", "member", user.id,
                 f"{user.name} 通过邀请码加入（{ROLE_LABEL[inv.role]}）")
    db.commit()
    return {"ok": True, "vehicle_id": inv.vehicle_id}


# ---------- 动态、k 值、体检 ----------


@router.get("/vehicles/{vid}/activity")
def activity(vid: int, limit: int = 30, before_id: Optional[int] = None,
             user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_vehicle(db, user, vid)
    q = select(ActivityLog).where(ActivityLog.vehicle_id == vid)
    if before_id:
        q = q.where(ActivityLog.id < before_id)
    rows = list(db.scalars(q.order_by(ActivityLog.id.desc()).limit(min(limit, 100))))
    umap = users_map(db, [r.user_id for r in rows])
    return [{"id": r.id, "user": umap.get(r.user_id), "action": r.action, "entity": r.entity,
             "entity_id": r.entity_id, "summary": r.summary, "created_at": iso(r.created_at) + "Z"} for r in rows]


@router.get("/vehicles/{vid}/calibration")
def calibration(vid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    v, _ = require_vehicle(db, user, vid)
    c = load_ledger(db, v).calib
    return {
        "k": c.k, "source": c.source, "k_ac": c.k_ac, "k_dc": c.k_dc, "capacity_est": c.capacity_est,
        "samples": [{"record_id": rid, "k": round(k, 4), "charge_type": ct, "ts": iso(ts),
                     "capacity_est": round(k * 100 * 0.9, 1)} for rid, k, ct, ts in reversed(c.samples)],
    }


@router.get("/vehicles/{vid}/health")
def health(vid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    v, _ = require_vehicle(db, user, vid)
    return health_report(db, v, user)


class DismissIn(BaseModel):
    key: str = Field(..., max_length=64)


@router.post("/vehicles/{vid}/hints/dismiss")
def dismiss_hint(vid: int, body: DismissIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_vehicle(db, user, vid)
    if not db.get(DismissedHint, (vid, user.id, body.key)):
        db.add(DismissedHint(vehicle_id=vid, user_id=user.id, hint_key=body.key))
        db.commit()
    return {"ok": True}


class FixMissedIn(BaseModel):
    after_id: int
    before_id: int
    kind: Literal["charge", "fuel"] = "charge"


@router.post("/vehicles/{vid}/fix/missed")
def fix_missed(vid: int, body: FixMissedIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """按两个读数之间的上升量，加上这段里程按常态能耗估算的消耗，自动补一条估算的补能记录。"""
    v, _ = require_vehicle(db, user, vid, "editor")
    led = load_ledger(db, v)
    idx = led.point_index()
    if body.after_id not in idx or body.before_id not in idx:
        raise HTTPException(404, "记录不存在")
    a, b = led.points[idx[body.after_id]], led.points[idx[body.before_id]]
    energy = "elec" if body.kind == "charge" else "fuel"
    la, lb = a.levels(energy)[1], b.levels(energy)[0]
    if la is None or lb is None:
        raise HTTPException(400, "这两条记录缺少读数，无法推算")
    segs = led.elec_segments if energy == "elec" else led.fuel_segments
    norms = sorted(s.per100 for s in segs if s.status == "ok" and s.kind == "drive" and s.per100)
    typical = norms[len(norms) // 2] if norms else 0
    qty = round(max(lb.value - la.value, 0) + (b.odo - a.odo) * typical / 100, 2)
    mid = a.ts + (b.ts - a.ts) / 2
    from app.engine.ledger import recent_price

    price = recent_price(led.points, idx[body.before_id], energy) or (0.6 if energy == "elec" else 7.5)
    cents = round(qty * price * 100)
    est = ["odometer_km", "paid_cents", "charge_kwh" if energy == "elec" else "fuel_l"]
    r = Record(vehicle_id=vid, kind=body.kind, occurred_at=mid.replace(second=0, microsecond=0), odometer_km=None,
               odometer_estimated=True, source="auto_fix", estimated_fields=json.dumps(est),
               paid_cents=cents, amount_cents=cents, created_by=user.id, updated_by=user.id,
               payer_user_id=user.id, note="自动补录（疑似漏记）")
    if energy == "elec":
        r.charge_kwh, r.charge_type, r.charge_unit_price = qty, v.default_charge_type, round(price, 3)
    else:
        r.fuel_liters, r.fuel_grade, r.fuel_unit_price = qty, v.default_fuel_grade, round(price, 2)
    db.add(r)
    bump(v)
    db.flush()
    log_activity(db, vid, user, "create", "record", r.id, "自动补录了一条疑似漏记的" + ("充电" if energy == "elec" else "加油"))
    db.commit()
    return {"ok": True, "record_id": r.id, "qty": qty, "cents": cents}


class DeliveryIn(BaseModel):
    odometer_km: float = Field(..., ge=0, le=3_000_000)
    delivery_date: Optional[date] = None


@router.post("/vehicles/{vid}/delivery")
def fix_delivery(vid: int, body: DeliveryIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """修正提车里程（和日期）：同时改提车快照、提车当天的加油记录和车辆初始里程。"""
    v, _ = require_vehicle(db, user, vid, "editor")
    snap = db.scalar(select(Record).where(Record.vehicle_id == vid, Record.snapshot_tag == "delivery",
                                          Record.deleted_at.is_(None)).order_by(Record.occurred_at).limit(1))
    if not snap:
        raise HTTPException(404, "没有提车记录")
    old_ts, old_odo = snap.occurred_at, snap.odometer_km
    new_ts = datetime.combine(body.delivery_date, old_ts.time()) if body.delivery_date else old_ts
    fills = list(db.scalars(select(Record).where(
        Record.vehicle_id == vid, Record.deleted_at.is_(None), Record.kind == "fuel",
        Record.occurred_at >= old_ts, Record.occurred_at <= old_ts + timedelta(hours=1),
        Record.odometer_km == old_odo)))
    for r in [snap, *fills]:
        r.occurred_at = new_ts + (r.occurred_at - old_ts)
        r.odometer_km = body.odometer_km
        r.updated_by = user.id
        r.version = (r.version or 1) + 1
    v.initial_odometer_km = body.odometer_km
    if body.delivery_date:
        v.delivery_date = body.delivery_date
    bump(v)
    log_activity(db, vid, user, "update", "vehicle", vid, f"把提车里程改为 {body.odometer_km:,.0f} km")
    db.commit()
    return {"ok": True, "updated": 1 + len(fills)}


@router.get("/vehicles/{vid}/odometer")
def odometer(vid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    v, _ = require_vehicle(db, user, vid)
    led = load_ledger(db, v)
    obs = odo_observations(led, load_expenses(db, vid))
    return {"latest": latest_odo_of(obs, v)}
