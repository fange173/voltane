/* state.js — 全局状态、枚举文案、事件 */
import { api, store } from "./util.js";

export const state = {
  user: null,
  vehicles: [],
  vid: null,
  dueCount: 0,
};

export const ENERGY_LABEL = { ice: "燃油", hev: "油混", phev: "插混", erev: "增程", bev: "纯电" };
export const CHARGE_LABEL = { home: "家充", public_ac: "公共慢充", dc: "快充", swap: "换电" };
/** 旧数据里的「超充」并入「快充」。 */
export const chargeLabel = (k) => CHARGE_LABEL[k === "super" ? "dc" : k];
export const FUEL_GRADES = ["92", "95", "98", "0", "E92", "E95"];
export const gradeLabel = (g) => (g === "0" ? "0# 柴油" : g ? `${g}#` : "");
export const SNAPSHOT_TAGS = {
  before_park: "停车前",
  after_park: "停车后",
  month_start: "月初",
  other: "其他",
  ledger_start: "记账起点",
  delivery: "提车",
};
export const SEGMENT_TAGS = {
  normal: "正常",
  idle: "哨兵/静置",
  winter: "冬季/空调",
  highway: "高速",
  load: "拉货/满载",
  abnormal: "异常",
};
export const EXPENSE_LABEL = {
  insurance: "保险",
  maintenance: "保养",
  repair: "维修",
  inspection: "年检",
  tires: "轮胎",
  wash: "洗车美容",
  parking: "停车",
  toll: "通行费",
  fine: "违章",
  accessory: "用品/改装",
  loan: "车贷",
  other: "其他",
};
export const ROLE_LABEL = { owner: "车主", editor: "共同记录", viewer: "只看" };
export const PLACE_KIND = {
  home_charger: "家充桩",
  charge_station: "充电站",
  gas_station: "加油站",
  other: "其他",
};
export const QUALITY_LABEL = { A: "精确", B: "估", C: "粗略", D: "不足" };

/** 按车辆的油表显示方式把油量百分比换成「40%」「19.2 L」「3/8 格」。 */
export function fuelLevelText(pct, v) {
  if (pct == null) return "";
  if (v?.fuel_gauge_mode === "liters" && v.tank_capacity_l) return `${+((pct / 100) * v.tank_capacity_l).toFixed(1)} L`;
  if (v?.fuel_gauge_mode === "bars" && v.fuel_gauge_bars) return `${Math.round((pct / 100) * v.fuel_gauge_bars)}/${v.fuel_gauge_bars} 格`;
  return `${Math.round(pct)}%`;
}

export function currentVehicle() {
  return state.vehicles.find((v) => v.id === state.vid) || null;
}

export function canEdit(v = currentVehicle()) {
  return !!v && (v.role === "owner" || v.role === "editor") && !v.is_archived;
}

export async function loadVehicles() {
  state.vehicles = await api("/api/vehicles");
  const saved = store.get("vid") ?? state.user?.settings?.default_vehicle_id;
  const ok = (id) => state.vehicles.some((v) => v.id === id);
  if (!ok(state.vid)) {
    const active = state.vehicles.filter((v) => !v.is_archived);
    state.vid = ok(saved) ? saved : active[0]?.id ?? state.vehicles[0]?.id ?? null;
  }
  return state.vehicles;
}

export function setVehicle(id) {
  if (state.vid === id) return;
  state.vid = id;
  store.set("vid", id);
  savePref("default_vehicle_id", id);
  emit("vehicle-changed");
}

export async function savePref(key, value) {
  if (!state.user) return;
  state.user.settings = { ...(state.user.settings || {}), [key]: value };
  try {
    await api("/api/auth/settings", { method: "PUT", body: { [key]: value } });
  } catch {
    /* 偏好保存失败不影响使用 */
  }
}
export const pref = (key, d) => state.user?.settings?.[key] ?? d;

/* 事件：data-changed（记录增删改后刷新视图）、vehicle-changed */
const bus = new EventTarget();
export const emit = (name, detail) => bus.dispatchEvent(new CustomEvent(name, { detail }));
export const on = (name, fn) => {
  bus.addEventListener(name, fn);
  return () => bus.removeEventListener(name, fn);
};
