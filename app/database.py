"""
database.py — SQLite 连接、ORM 模型、迁移与初始化

时间约定：业务时间（occurred_at、日期类字段）按 TZ 本地时间存 naive；created_at 等系统时间存 UTC naive。
金额统一存整数分（*_cents）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import date, datetime, timezone
from typing import Generator, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    event,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import NullPool

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:////app/data/voltane.db")
_IS_SQLITE = DATABASE_URL.startswith("sqlite")

# SQLite 配合 FastAPI 线程池：关闭同线程检查，NullPool 避免跨线程复用连接
_engine_kwargs: dict = {"echo": False}
if _IS_SQLITE:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
    _engine_kwargs["poolclass"] = NullPool
engine = create_engine(DATABASE_URL, **_engine_kwargs)


@event.listens_for(engine, "connect")
def _sqlite_on_connect(dbapi_conn, _record):
    if not _IS_SQLITE:
        return
    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA foreign_keys=ON")
    finally:
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

_PBKDF2_ITERATIONS = 260000


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def localnow() -> datetime:
    """容器 TZ 下的本地时间（naive），用于业务时间默认值。"""
    return datetime.now().replace(second=0, microsecond=0)


def hash_password(plain: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt.encode(), _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt}${dk.hex()}"


def verify_password(plain: str, stored: str) -> bool:
    try:
        algo, iters, salt, hexhash = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt.encode(), int(iters))
        return hmac.compare_digest(dk.hex(), hexhash)
    except (ValueError, TypeError):
        return False


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="member")  # admin | member
    display_name: Mapped[str] = mapped_column(String(64), default="")
    avatar_color: Mapped[str] = mapped_column(String(16), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    credentials_version: Mapped[int] = mapped_column(Integer, default=0)
    # 个人偏好（默认车辆、主题、首页时间范围等），JSON
    settings: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    @property
    def name(self) -> str:
        return self.display_name or self.username

    def get_settings(self) -> dict:
        try:
            return json.loads(self.settings or "{}")
        except ValueError:
            return {}


class Vehicle(Base):
    __tablename__ = "vehicles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    plate: Mapped[str] = mapped_column(String(32), default="")
    brand: Mapped[str] = mapped_column(String(64), default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    year: Mapped[Optional[int]] = mapped_column(Integer)
    energy_type: Mapped[str] = mapped_column(String(8), default="phev")  # ice|hev|phev|erev|bev
    tank_capacity_l: Mapped[Optional[float]] = mapped_column(Float)
    battery_usable_kwh: Mapped[Optional[float]] = mapped_column(Float)
    charge_limit_pct: Mapped[float] = mapped_column(Float, default=100)
    default_fuel_grade: Mapped[str] = mapped_column(String(16), default="92")
    default_charge_type: Mapped[str] = mapped_column(String(16), default="home")
    fuel_gauge_mode: Mapped[str] = mapped_column(String(8), default="pct")  # pct | bars | liters
    fuel_gauge_bars: Mapped[Optional[int]] = mapped_column(Integer)
    factory_fuel_l: Mapped[float] = mapped_column(Float, default=5)
    record_style: Mapped[str] = mapped_column(String(8), default="detailed")  # detailed | simple
    initial_odometer_km: Mapped[Optional[float]] = mapped_column(Float)
    delivery_date: Mapped[Optional[date]] = mapped_column(Date)
    purchase_price_cents: Mapped[Optional[int]] = mapped_column(Integer)
    color: Mapped[str] = mapped_column(String(16), default="#4F46E5")
    # 计算设置：min_segment_km、kwh_per_l、gauge_err_pct、allow_gauge_anchor
    calc_settings: Mapped[str] = mapped_column(Text, default="{}")
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    # 记录、汇总、段标签、参数变化时 +1，用作计算缓存的失效键
    data_version: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    def get_calc(self) -> dict:
        try:
            return json.loads(self.calc_settings or "{}")
        except ValueError:
            return {}


class VehicleMember(Base):
    __tablename__ = "vehicle_members"

    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(8), default="editor")  # owner | editor | viewer
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Invite(Base):
    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(8), default="editor")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    allow_signup: Mapped[bool] = mapped_column(Boolean, default=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Record(Base):
    """加油 / 充电 / 状态快照。"""

    __tablename__ = "records"
    __table_args__ = (
        Index("ix_records_vehicle_odo", "vehicle_id", "deleted_at", "odometer_km"),
        Index("ix_records_vehicle_time", "vehicle_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # fuel | charge | snapshot
    status: Mapped[str] = mapped_column(String(12), default="done")  # done | in_progress
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    odometer_km: Mapped[Optional[float]] = mapped_column(Float)
    odometer_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(10), default="manual")  # manual|backfill|import|auto_fix
    estimated_fields: Mapped[str] = mapped_column(Text, default="[]")
    client_id: Mapped[Optional[str]] = mapped_column(String(40), unique=True)
    # 加油
    fuel_grade: Mapped[Optional[str]] = mapped_column(String(16))
    fuel_liters: Mapped[Optional[float]] = mapped_column(Float)
    fuel_unit_price: Mapped[Optional[float]] = mapped_column(Float)
    fuel_is_full: Mapped[bool] = mapped_column(Boolean, default=False)
    fuel_level_before_pct: Mapped[Optional[float]] = mapped_column(Float)
    fuel_level_after_pct: Mapped[Optional[float]] = mapped_column(Float)
    # 充电
    charge_type: Mapped[Optional[str]] = mapped_column(String(10))  # home|public_ac|dc|super|swap
    charge_kwh: Mapped[Optional[float]] = mapped_column(Float)
    charge_unit_price: Mapped[Optional[float]] = mapped_column(Float)
    charge_minutes: Mapped[Optional[int]] = mapped_column(Integer)
    charge_to_limit: Mapped[bool] = mapped_column(Boolean, default=False)
    energy_fee_cents: Mapped[Optional[int]] = mapped_column(Integer)
    service_fee_cents: Mapped[Optional[int]] = mapped_column(Integer)
    # 通用
    soc_before_pct: Mapped[Optional[float]] = mapped_column(Float)
    soc_after_pct: Mapped[Optional[float]] = mapped_column(Float)
    amount_cents: Mapped[Optional[int]] = mapped_column(Integer)
    extra_fee_cents: Mapped[Optional[int]] = mapped_column(Integer)
    place_id: Mapped[Optional[int]] = mapped_column(ForeignKey("places.id", ondelete="SET NULL"))
    place_name: Mapped[Optional[str]] = mapped_column(String(64))
    lat: Mapped[Optional[float]] = mapped_column(Float)
    lng: Mapped[Optional[float]] = mapped_column(Float)
    missed_before: Mapped[bool] = mapped_column(Boolean, default=False)
    snapshot_tag: Mapped[Optional[str]] = mapped_column(String(16))
    note: Mapped[str] = mapped_column(Text, default="")
    import_batch_id: Mapped[Optional[str]] = mapped_column(String(32))
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    def est_fields(self) -> list:
        try:
            return list(json.loads(self.estimated_fields or "[]"))
        except ValueError:
            return []


class PeriodSummary(Base):
    __tablename__ = "period_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    mode: Mapped[str] = mapped_column(String(8), default="extra")  # extra | total
    start_odometer_km: Mapped[Optional[float]] = mapped_column(Float)
    end_odometer_km: Mapped[Optional[float]] = mapped_column(Float)
    fuel_liters: Mapped[Optional[float]] = mapped_column(Float)
    fuel_cents: Mapped[Optional[int]] = mapped_column(Integer)
    fuel_price: Mapped[Optional[float]] = mapped_column(Float)
    elec_kwh: Mapped[Optional[float]] = mapped_column(Float)
    elec_cents: Mapped[Optional[int]] = mapped_column(Integer)
    elec_price: Mapped[Optional[float]] = mapped_column(Float)
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SegmentMark(Base):
    __tablename__ = "segment_marks"

    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id", ondelete="CASCADE"), primary_key=True)
    end_record_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    energy: Mapped[str] = mapped_column(String(8), primary_key=True)  # fuel | elec | combined
    tag: Mapped[str] = mapped_column(String(16), default="normal")
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Expense(Base):
    __tablename__ = "expenses"
    __table_args__ = (Index("ix_expenses_vehicle_time", "vehicle_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(64), default="")
    sub_items: Mapped[str] = mapped_column(Text, default="[]")  # 保险险种明细 [{name, cents}]
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    odometer_km: Mapped[Optional[float]] = mapped_column(Float)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    vendor: Mapped[str] = mapped_column(String(64), default="")
    period_start: Mapped[Optional[date]] = mapped_column(Date)
    period_end: Mapped[Optional[date]] = mapped_column(Date)
    note: Mapped[str] = mapped_column(Text, default="")
    client_id: Mapped[Optional[str]] = mapped_column(String(40), unique=True)
    import_batch_id: Mapped[Optional[str]] = mapped_column(String(32))
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 为空表示个人提醒（如驾驶证换证），只有 owner_user_id 能看到
    vehicle_id: Mapped[Optional[int]] = mapped_column(ForeignKey("vehicles.id", ondelete="CASCADE"))
    owner_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(64), nullable=False)
    template_key: Mapped[str] = mapped_column(String(24), default="custom")
    due_date: Mapped[Optional[date]] = mapped_column(Date)
    due_odometer_km: Mapped[Optional[float]] = mapped_column(Float)
    interval_months: Mapped[Optional[int]] = mapped_column(Integer)
    interval_km: Mapped[Optional[float]] = mapped_column(Float)
    lead_days: Mapped[int] = mapped_column(Integer, default=30)
    lead_km: Mapped[float] = mapped_column(Float, default=500)
    last_done_at: Mapped[Optional[date]] = mapped_column(Date)
    last_done_odometer_km: Mapped[Optional[float]] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ReminderMute(Base):
    __tablename__ = "reminder_mutes"

    reminder_id: Mapped[int] = mapped_column(ForeignKey("reminders.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)


class Place(Base):
    __tablename__ = "places"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 为空表示个人地点；否则这辆车的成员共用
    vehicle_id: Mapped[Optional[int]] = mapped_column(ForeignKey("vehicles.id", ondelete="CASCADE"))
    owner_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="other")  # home_charger|charge_station|gas_station|other
    lat: Mapped[Optional[float]] = mapped_column(Float)
    lng: Mapped[Optional[float]] = mapped_column(Float)
    # 峰谷电价 [{"from":"22:00","to":"08:00","price":0.3}, ...]
    tou_tariff: Mapped[str] = mapped_column(Text, default="[]")
    default_charge_type: Mapped[Optional[str]] = mapped_column(String(10))
    default_fuel_grade: Mapped[Optional[str]] = mapped_column(String(16))
    last_unit_price: Mapped[Optional[float]] = mapped_column(Float)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ActivityLog(Base):
    __tablename__ = "activity_logs"
    __table_args__ = (Index("ix_activity_vehicle_time", "vehicle_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle_id: Mapped[Optional[int]] = mapped_column(ForeignKey("vehicles.id", ondelete="CASCADE"))
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(16))  # create | update | delete | restore | join ...
    entity: Mapped[str] = mapped_column(String(16))
    entity_id: Mapped[Optional[int]] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DismissedHint(Base):
    __tablename__ = "dismissed_hints"

    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    hint_key: Mapped[str] = mapped_column(String(64), primary_key=True)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


# ---------- 迁移 ----------
# PRAGMA user_version 驱动；每个版本一个函数，必须幂等。


def _migrate_v1(conn) -> None:
    Base.metadata.create_all(conn)


def _migrate_v2(conn) -> None:
    """不再单独记实付和付款人：旧库里的 paid_cents 并入 amount_cents（实付才是真实花费），旧列保留不动。"""
    cols = {r[1] for r in conn.execute(text("PRAGMA table_info(records)"))}
    if "paid_cents" in cols:
        conn.execute(text("UPDATE records SET amount_cents = paid_cents "
                          "WHERE paid_cents IS NOT NULL AND (amount_cents IS NULL OR amount_cents != paid_cents)"))


MIGRATIONS = [(1, _migrate_v1), (2, _migrate_v2)]


def run_migrations() -> list[int]:
    applied = []
    with engine.begin() as conn:
        current = conn.execute(text("PRAGMA user_version")).scalar() or 0
        for version, fn in MIGRATIONS:
            if version > current:
                fn(conn)
                conn.execute(text(f"PRAGMA user_version = {version}"))
                applied.append(version)
    return applied


DEFAULT_ADMIN = ("admin", "admin123")


def init_db() -> None:
    db_path = DATABASE_URL.replace("sqlite:///", "", 1) if _IS_SQLITE else ""
    if db_path and db_path != ":memory:":
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    run_migrations()
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.username == DEFAULT_ADMIN[0]))
        force_reset = os.getenv("RESET_ADMIN_PASSWORD", "").lower() in ("1", "true", "yes")
        if admin is None:
            db.add(User(username=DEFAULT_ADMIN[0], password_hash=hash_password(DEFAULT_ADMIN[1]),
                        role="admin", display_name="管理员"))
            db.commit()
        elif force_reset:
            admin.password_hash = hash_password(DEFAULT_ADMIN[1])
            admin.is_active = True
            admin.role = "admin"
            admin.credentials_version = (admin.credentials_version or 0) + 1
            db.commit()
            print("[Voltane] 已按 RESET_ADMIN_PASSWORD 重置 admin 密码为 admin123，请改回 false")


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.get(AppSetting, key)
    return row.value if row else default


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(AppSetting, key)
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))
