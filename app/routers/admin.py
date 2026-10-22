"""
admin.py — 用户管理、备份与恢复、系统设置、导入导出
"""
from __future__ import annotations

import csv
import io
import json
import os
import secrets
import sqlite3
import tempfile
import time
from datetime import date, datetime
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.access import require_vehicle
from app.auth import require_admin, get_current_user, user_brief
from app.constants import EXPENSE_CATEGORIES
from app.database import (
    DATABASE_URL,
    Expense,
    PeriodSummary,
    Record,
    Reminder,
    User,
    Vehicle,
    VehicleMember,
    engine,
    get_db,
    get_setting,
    hash_password,
    set_setting,
)
from app.routers.auth import USERNAME_RE
from app.services import CHARGE_LABEL, bump, iso, log_activity, record_dict, summary_dict, vehicle_dict

router = APIRouter(prefix="/api", tags=["admin"])

DB_PATH = DATABASE_URL.replace("sqlite:///", "", 1) if DATABASE_URL.startswith("sqlite") else ""
BACKUP_DIR = Path(os.getenv("BACKUP_DIR") or (Path(DB_PATH).parent / "backups" if DB_PATH else "backups"))
BACKUP_KEEP = int(os.getenv("BACKUP_KEEP", "14"))


# ---------- 用户 ----------


class UserCreateIn(BaseModel):
    username: str = Field(..., min_length=2, max_length=32)
    password: str = Field(..., min_length=6, max_length=128)
    display_name: str = Field("", max_length=32)
    role: Literal["admin", "member"] = "member"


class UserUpdateIn(BaseModel):
    display_name: Optional[str] = Field(None, max_length=32)
    role: Optional[Literal["admin", "member"]] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(None, min_length=6, max_length=128)


@router.get("/admin/users")
def list_users(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(User).order_by(User.id))
    out = []
    for u in rows:
        n = db.scalar(select(func.count()).select_from(VehicleMember).where(VehicleMember.user_id == u.id))
        out.append({**user_brief(u), "role": u.role, "is_active": u.is_active, "vehicle_count": n,
                    "created_at": iso(u.created_at)})
    return out


@router.post("/admin/users")
def create_user(body: UserCreateIn, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    name = body.username.strip()
    if not USERNAME_RE.match(name):
        raise HTTPException(400, "用户名只能包含字母、数字、下划线、点和短横线，2–32 位")
    if db.scalar(select(User).where(User.username == name)):
        raise HTTPException(409, "用户名已存在")
    u = User(username=name, password_hash=hash_password(body.password), display_name=body.display_name.strip(),
             role=body.role)
    db.add(u)
    db.commit()
    return user_brief(u)


@router.put("/admin/users/{uid}")
def update_user(uid: int, body: UserUpdateIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "用户不存在")
    if u.id == admin.id and (body.role == "member" or body.is_active is False):
        raise HTTPException(400, "不能停用或降级自己")
    if body.display_name is not None:
        u.display_name = body.display_name.strip()
    if body.role is not None:
        u.role = body.role
    if body.is_active is not None:
        u.is_active = body.is_active
        u.credentials_version += 1
    if body.password:
        u.password_hash = hash_password(body.password)
        u.credentials_version += 1
    db.commit()
    return {"ok": True}


@router.get("/admin/system")
def system_info(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    size = os.path.getsize(DB_PATH) if DB_PATH and os.path.exists(DB_PATH) else None
    backups = sorted(BACKUP_DIR.glob("voltane-*.db"), reverse=True) if BACKUP_DIR.exists() else []
    orphan = db.scalars(select(Vehicle).where(~Vehicle.id.in_(
        select(VehicleMember.vehicle_id).where(VehicleMember.role == "owner"))))
    return {
        "db_size": size,
        "users": db.scalar(select(func.count()).select_from(User)),
        "vehicles": db.scalar(select(func.count()).select_from(Vehicle)),
        "records": db.scalar(select(func.count()).select_from(Record).where(Record.deleted_at.is_(None))),
        "backups": [{"name": b.name, "size": b.stat().st_size,
                     "mtime": datetime.fromtimestamp(b.stat().st_mtime).isoformat(timespec="minutes")}
                    for b in backups],
        "backup_keep": BACKUP_KEEP,
        "allow_signup": get_setting(db, "allow_signup", "false") == "true",
        "orphan_vehicles": [{"id": v.id, "name": v.name} for v in orphan],
    }


class SettingsIn(BaseModel):
    allow_signup: Optional[bool] = None


@router.put("/admin/settings")
def admin_settings(body: SettingsIn, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    if body.allow_signup is not None:
        set_setting(db, "allow_signup", "true" if body.allow_signup else "false")
    db.commit()
    return {"ok": True}


class AssignOwnerIn(BaseModel):
    user_id: int


@router.post("/admin/vehicles/{vid}/owner")
def assign_owner(vid: int, body: AssignOwnerIn, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    """车主账号丢失时，管理员把车转给别人，避免数据成为孤儿。"""
    if not db.get(Vehicle, vid) or not db.get(User, body.user_id):
        raise HTTPException(404, "车辆或用户不存在")
    m = db.get(VehicleMember, (vid, body.user_id))
    if m:
        m.role = "owner"
    else:
        db.add(VehicleMember(vehicle_id=vid, user_id=body.user_id, role="owner"))
    db.commit()
    return {"ok": True}


# ---------- 备份与恢复 ----------


def backup_to(path: str) -> None:
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def auto_backup() -> Optional[str]:
    """每日自动备份到 data/backups，保留 BACKUP_KEEP 份。当天已有备份时跳过。"""
    if not DB_PATH or not os.path.exists(DB_PATH):
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    name = BACKUP_DIR / f"voltane-{date.today():%Y%m%d}.db"
    if name.exists():
        return None
    tmp = str(name) + ".tmp"
    backup_to(tmp)
    os.replace(tmp, name)
    for old in sorted(BACKUP_DIR.glob("voltane-*.db"), reverse=True)[BACKUP_KEEP:]:
        old.unlink(missing_ok=True)
    return str(name)


@router.post("/admin/backup")
def download_backup(tasks: BackgroundTasks, _: User = Depends(require_admin)):
    if not DB_PATH:
        raise HTTPException(400, "当前数据库不支持在线备份")
    fd, path = tempfile.mkstemp(prefix="voltane-backup-", suffix=".db")
    os.close(fd)
    backup_to(path)
    tasks.add_task(os.unlink, path)
    return FileResponse(path, filename=f"voltane-{datetime.now():%Y%m%d-%H%M}.db",
                        media_type="application/octet-stream")


@router.post("/admin/restore")
async def restore_backup(file: UploadFile = File(...), _: User = Depends(require_admin)):
    """用上传的备份覆盖当前数据库：先校验是本应用的 SQLite 文件，覆盖前自动再备份一次当前库。"""
    if not DB_PATH:
        raise HTTPException(400, "当前数据库不支持恢复")
    data = await file.read()
    if not data.startswith(b"SQLite format 3\x00"):
        raise HTTPException(400, "不是有效的 SQLite 备份文件")
    fd, tmp = tempfile.mkstemp(prefix="voltane-restore-", suffix=".db")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    try:
        con = sqlite3.connect(tmp)
        tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        con.close()
        if not {"users", "vehicles", "records"} <= tables:
            raise HTTPException(400, "备份文件里没有本应用的数据表")
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup_to(str(BACKUP_DIR / f"before-restore-{int(time.time())}.db"))
        src = sqlite3.connect(tmp)
        dst = sqlite3.connect(DB_PATH)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
    finally:
        os.unlink(tmp)
    engine.dispose()
    from app.services import _cache

    _cache.clear()
    return {"ok": True}


# ---------- 导出 ----------

RECORD_CSV = [
    ("occurred_at", "时间"), ("kind", "类型"), ("odometer_km", "里程"), ("fuel_grade", "标号"),
    ("fuel_liters", "加油量L"), ("fuel_unit_price", "油价"), ("fuel_is_full", "加满"),
    ("fuel_level_before_pct", "加油前油量%"), ("charge_type", "充电方式"), ("charge_kwh", "充电量kWh"),
    ("charge_unit_price", "电价"), ("charge_to_limit", "充到上限"), ("soc_before_pct", "开始SOC"),
    ("soc_after_pct", "结束SOC"), ("amount_cents", "金额"), ("paid_cents", "实付"), ("extra_fee_cents", "附加费"),
    ("place_name", "地点"), ("missed_before", "漏记"), ("snapshot_tag", "快照标签"), ("note", "备注"),
]


def _cents(x):
    return f"{x / 100:.2f}" if x is not None else ""


@router.get("/export")
def export(vehicle_id: int, format: Literal["json", "csv", "expenses_csv"] = "json",
           user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    v, _ = require_vehicle(db, user, vehicle_id)
    recs = list(db.scalars(select(Record).where(Record.vehicle_id == v.id, Record.deleted_at.is_(None))
                           .order_by(Record.occurred_at)))
    exps = list(db.scalars(select(Expense).where(Expense.vehicle_id == v.id, Expense.deleted_at.is_(None))
                           .order_by(Expense.occurred_at)))
    stamp = datetime.now().strftime("%Y%m%d")
    if format == "json":
        from app.services import expense_dict

        data = {
            "app": "voltane", "version": 1, "exported_at": datetime.now().isoformat(timespec="seconds"),
            "vehicle": vehicle_dict(v),
            "records": [record_dict(r) for r in recs],
            "expenses": [expense_dict(e) for e in exps],
            "summaries": [summary_dict(s) for s in db.scalars(select(PeriodSummary).where(
                PeriodSummary.vehicle_id == v.id, PeriodSummary.deleted_at.is_(None)))],
            "reminders": [{c.name: _jsonable(getattr(r, c.name)) for c in Reminder.__table__.columns}
                          for r in db.scalars(select(Reminder).where(Reminder.vehicle_id == v.id))],
        }
        body = json.dumps(data, ensure_ascii=False, indent=1)
        return Response(body, media_type="application/json", headers={
            "Content-Disposition": f"attachment; filename=voltane-{v.id}-{stamp}.json"})
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf)
    if format == "csv":
        w.writerow([label for _, label in RECORD_CSV])
        for r in recs:
            row = []
            for f, _ in RECORD_CSV:
                val = getattr(r, f)
                if f.endswith("_cents"):
                    val = _cents(val)
                elif f == "occurred_at":
                    val = r.occurred_at.strftime("%Y-%m-%d %H:%M")
                elif f == "kind":
                    val = {"fuel": "加油", "charge": "充电", "snapshot": "快照"}[val]
                elif f == "charge_type":
                    val = CHARGE_LABEL.get(val or "", "")
                elif isinstance(val, bool):
                    val = "是" if val else ""
                row.append("" if val is None else val)
            w.writerow(row)
        name = f"voltane-{v.id}-records-{stamp}.csv"
    else:
        w.writerow(["时间", "分类", "名称", "金额", "里程", "商家", "开始日期", "结束日期", "备注"])
        for e in exps:
            w.writerow([e.occurred_at.strftime("%Y-%m-%d %H:%M"), EXPENSE_CATEGORIES.get(e.category, e.category),
                        e.title, _cents(e.amount_cents), e.odometer_km or "", e.vendor, iso(e.period_start) or "",
                        iso(e.period_end) or "", e.note])
        name = f"voltane-{v.id}-expenses-{stamp}.csv"
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename={name}"})


def _jsonable(x):
    if isinstance(x, (datetime, date)):
        return x.isoformat()
    return x


# ---------- 导入（本应用导出的 JSON） ----------


def _dt(s):
    return datetime.fromisoformat(s) if s else None


def _d(s):
    return date.fromisoformat(s[:10]) if s else None


@router.post("/import")
async def import_json(vehicle_id: int, file: UploadFile = File(...), user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    """把导出的 JSON 导入到一辆已有的车（需要车主权限），整批共用一个批次号，可以整批撤销。"""
    v, _ = require_vehicle(db, user, vehicle_id, "owner")
    try:
        data = json.loads((await file.read()).decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "文件不是有效的 JSON") from exc
    if data.get("app") != "voltane":
        raise HTTPException(400, "只支持导入本应用导出的 JSON")
    batch = secrets.token_hex(6)
    cols = {c.name for c in Record.__table__.columns}
    n_rec = n_exp = 0
    for r in data.get("records", []):
        if r.get("status") == "in_progress":
            continue
        row = Record(vehicle_id=v.id, created_by=user.id, updated_by=user.id, payer_user_id=user.id,
                     source="import", import_batch_id=batch)
        for k, val in r.items():
            if k in cols and k not in ("id", "vehicle_id", "created_by", "updated_by", "payer_user_id", "client_id",
                                        "version", "deleted_at", "created_at", "updated_at", "place_id", "source",
                                        "occurred_at", "estimated_fields"):
                setattr(row, k, val)
        row.occurred_at = _dt(r["occurred_at"])
        row.estimated_fields = json.dumps(r.get("estimated_fields") or [])
        db.add(row)
        n_rec += 1
    for e in data.get("expenses", []):
        if e.get("category") not in EXPENSE_CATEGORIES:
            continue
        db.add(Expense(vehicle_id=v.id, category=e["category"], title=e.get("title") or "",
                       sub_items=json.dumps(e.get("sub_items") or [], ensure_ascii=False),
                       occurred_at=_dt(e["occurred_at"]), odometer_km=e.get("odometer_km"),
                       amount_cents=int(e.get("amount_cents") or 0), vendor=e.get("vendor") or "",
                       period_start=_d(e.get("period_start")), period_end=_d(e.get("period_end")),
                       note=e.get("note") or "", payer_user_id=user.id, created_by=user.id, updated_by=user.id,
                       import_batch_id=batch))
        n_exp += 1
    bump(v)
    log_activity(db, v.id, user, "create", "import", None, f"导入了 {n_rec} 条记录、{n_exp} 笔费用")
    db.commit()
    return {"batch": batch, "records": n_rec, "expenses": n_exp}


@router.delete("/import/{batch}")
def undo_import(batch: str, vehicle_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    v, _ = require_vehicle(db, user, vehicle_id, "owner")
    a = db.execute(delete(Record).where(Record.vehicle_id == v.id, Record.import_batch_id == batch)).rowcount
    b = db.execute(delete(Expense).where(Expense.vehicle_id == v.id, Expense.import_batch_id == batch)).rowcount
    bump(v)
    log_activity(db, v.id, user, "delete", "import", None, f"撤销了一次导入（{a} 条记录、{b} 笔费用）")
    db.commit()
    return {"records": a, "expenses": b}
