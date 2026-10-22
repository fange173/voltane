/* util.js — 请求、格式化、DOM 小工具 */

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === "string" ? detail : detail?.message || "请求失败");
    this.status = status;
    this.detail = detail;
  }
}

let onUnauthorized = null;
export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn;
}

/** JSON 请求；401 走全局回登录页（登录/注册接口除外）。网络错误抛 ApiError(0)。 */
export async function api(path, { method = "GET", body, headers = {}, raw = false, form } = {}) {
  const opts = { method, headers: { ...headers }, credentials: "same-origin" };
  if (form) {
    opts.body = form;
  } else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch {
    throw new ApiError(0, "网络连接失败");
  }
  if (raw) return res;
  let data = null;
  const text = await res.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/api/auth/login") && !path.startsWith("/api/auth/signup")) {
      onUnauthorized?.();
    }
    let detail = data?.detail ?? data ?? res.statusText;
    if (Array.isArray(detail)) detail = detail.map((d) => d.msg?.replace(/^Value error, /, "")).join("；");
    throw new ApiError(res.status, detail);
  }
  return data;
}

/* ---------- HTML ---------- */

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

/** 模板标签：插值自动转义；用 raw() 包裹的片段原样输出。 */
export function html(strings, ...vals) {
  let out = "";
  strings.forEach((s, i) => {
    out += s;
    if (i < vals.length) out += toHtml(vals[i]);
  });
  return new Raw(out);
}
class Raw {
  constructor(s) {
    this.s = s;
  }
  toString() {
    return this.s;
  }
}
export const raw = (s) => new Raw(String(s ?? ""));
function toHtml(v) {
  if (v == null || v === false) return "";
  if (v instanceof Raw) return v.s;
  if (Array.isArray(v)) return v.map(toHtml).join("");
  return esc(v);
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

export function mount(el, content) {
  el.innerHTML = String(content);
  return el;
}

/* ---------- 数字与金额 ---------- */

export function num(x, digits = 2) {
  if (x == null || Number.isNaN(x)) return "–";
  const n = Number(x);
  return n.toLocaleString("zh-CN", { minimumFractionDigits: 0, maximumFractionDigits: digits });
}
export function fixed(x, digits = 2) {
  if (x == null || Number.isNaN(x)) return "–";
  return Number(x).toLocaleString("zh-CN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
export const km = (x) => (x == null ? "–" : Math.round(x).toLocaleString("zh-CN"));
export function money(cents, { sign = false, digits } = {}) {
  if (cents == null) return "–";
  const v = cents / 100;
  const d = digits ?? (Math.abs(v) >= 10000 ? 0 : 2);
  const s = Math.abs(v).toLocaleString("zh-CN", { minimumFractionDigits: d, maximumFractionDigits: d });
  return (v < 0 ? "-" : sign ? "+" : "") + "¥" + s;
}
export function yuanToCents(s) {
  if (s === "" || s == null) return null;
  const v = evalAmount(s);
  return v == null ? null : Math.round(v * 100);
}
/** 金额输入支持简单算式，例如「200+15」。 */
export function evalAmount(s) {
  const t = String(s).replace(/[，,\s¥]/g, "");
  if (!t) return null;
  if (!/^[\d.+\-*/()]+$/.test(t)) return null;
  // CSP 不允许 eval，用小型递归下降解析四则运算
  let i = 0;
  const peek = () => t[i];
  const number = () => {
    if (peek() === "(") {
      i++;
      const v = expr();
      if (t[i++] !== ")") throw 0;
      return v;
    }
    if (peek() === "-") {
      i++;
      return -number();
    }
    const m = /^\d*\.?\d+|^\d+\.?/.exec(t.slice(i));
    if (!m) throw 0;
    i += m[0].length;
    return parseFloat(m[0]);
  };
  const term = () => {
    let v = number();
    while (peek() === "*" || peek() === "/") v = t[i++] === "*" ? v * number() : v / number();
    return v;
  };
  const expr = () => {
    let v = term();
    while (peek() === "+" || peek() === "-") v = t[i++] === "+" ? v + term() : v - term();
    return v;
  };
  try {
    const v = expr();
    return i === t.length && Number.isFinite(v) ? v : null;
  } catch {
    return null;
  }
}
export const toNum = (s) => {
  if (s === "" || s == null) return null;
  const v = Number(String(s).replace(/[，,\s]/g, ""));
  return Number.isFinite(v) ? v : null;
};
export function pctChange(cur, prev) {
  if (cur == null || prev == null || prev === 0) return null;
  return (cur - prev) / Math.abs(prev);
}

/* ---------- 日期 ---------- */

const pad = (n) => String(n).padStart(2, "0");
export function localISO(d = new Date()) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
export const todayISO = () => localISO().slice(0, 10);
export function parseLocal(s) {
  if (!s) return null;
  const [d, t = "00:00"] = s.split("T");
  const [y, m, day] = d.split("-").map(Number);
  const [hh, mm] = t.split(":").map(Number);
  return new Date(y, m - 1, day, hh || 0, mm || 0);
}
const WEEK = "日一二三四五六";
export function fmtDate(s, { year = "auto", time = false, week = false } = {}) {
  const d = typeof s === "string" ? parseLocal(s) : s;
  if (!d) return "–";
  const now = new Date();
  const showYear = year === true || (year === "auto" && d.getFullYear() !== now.getFullYear());
  let out = `${showYear ? d.getFullYear() + "-" : ""}${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  if (week) out += ` 周${WEEK[d.getDay()]}`;
  if (time) out += ` ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  return out;
}
export function relDay(s) {
  const d = parseLocal(s);
  if (!d) return "";
  const a = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const n = new Date();
  const b = new Date(n.getFullYear(), n.getMonth(), n.getDate());
  const diff = Math.round((b - a) / 86400000);
  if (diff === 0) return "今天";
  if (diff === 1) return "昨天";
  if (diff === 2) return "前天";
  return fmtDate(s, { week: true });
}
export function ago(iso) {
  const t = new Date(iso).getTime();
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return "刚刚";
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  if (s < 86400 * 7) return `${Math.floor(s / 86400)} 天前`;
  return fmtDate(new Date(t));
}
export function durationText(minutes) {
  if (minutes == null) return "–";
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  return h ? `${h}小时${m ? m + "分" : ""}` : `${m}分钟`;
}

/* ---------- 杂项 ---------- */

export function uuid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}
export function debounce(fn, ms = 200) {
  let t;
  return (...a) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...a), ms);
  };
}
export const store = {
  get(k, d = null) {
    try {
      const v = localStorage.getItem("vt:" + k);
      return v == null ? d : JSON.parse(v);
    } catch {
      return d;
    }
  },
  set(k, v) {
    try {
      localStorage.setItem("vt:" + k, JSON.stringify(v));
    } catch {
      /* 隐私模式下写不进去也不影响使用 */
    }
  },
  del(k) {
    try {
      localStorage.removeItem("vt:" + k);
    } catch {
      /* 忽略 */
    }
  },
};
export const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);
export const initial = (name) => (name || "?").trim().slice(0, 1).toUpperCase();
