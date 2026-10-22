/* shell.js — 登录后的整体布局：PC 侧栏、顶栏、底部导航、右侧栏、车辆切换与「＋」面板 */
import { icon } from "./icons.js";
import { currentRoute, navigate } from "./router.js";
import { ENERGY_LABEL, canEdit, currentVehicle, on, setVehicle, state } from "./state.js";
import { avatar, sheet } from "./ui.js";
import { $, $$, api, esc, html, raw } from "./util.js";

const NAV = [
  { key: "home", href: "/", label: "概览", ico: "home" },
  { key: "records", href: "/records", label: "记录", ico: "list" },
  { key: "stats", href: "/stats", label: "统计", ico: "chart" },
  { key: "calendar", href: "/stats/calendar", label: "日历", ico: "calendar", side: true },
  { key: "reminders", href: "/reminders", label: "提醒", ico: "bell", side: true },
  { key: "vehicle", href: "/vehicle", label: "车辆", ico: "car", side: true },
  { key: "me", href: "/me", label: "我的", ico: "user" },
];

let built = false;

export function vehicleDot(v, size = 16) {
  if (!v) return "";
  return raw(`<span class="v-dot" style="background:${esc(v.color || "#4F46E5")}">${icon(v.energy_type === "bev" ? "bolt" : v.energy_type === "ice" || v.energy_type === "hev" ? "fuel" : "car", size)}</span>`);
}

const plateTail = (p) => (p ? p.replace(/\s/g, "").slice(-3) : "");

export function buildShell() {
  const app = $("#app");
  app.innerHTML = `
    <div class="layout" id="layout">
      <aside class="sidebar" id="sidebar"></aside>
      <div class="main">
        <header class="topbar" id="topbar">
          <button class="vehicle-pill" id="vpill"></button>
          <div class="page-title" id="page-title"></div>
          <div class="top-spacer"></div>
          <button class="icon-btn" id="bell" aria-label="提醒">${icon("bell")}<span class="dot hidden" id="bell-dot"></span></button>
        </header>
        <main class="view" id="view"></main>
      </div>
      <aside class="aside" id="aside"></aside>
    </div>
    <nav class="bottom-nav" id="bottom-nav">
      ${navItem(NAV[0])}${navItem(NAV[1])}
      <button class="nav-fab" id="fab" aria-label="记一笔">${icon("plus", 28)}</button>
      ${navItem(NAV[2])}${navItem(NAV[6])}
    </nav>`;
  $("#vpill").addEventListener("click", openVehicleSwitcher);
  $("#bell").addEventListener("click", () => navigate("/reminders"));
  $("#fab").addEventListener("click", () => openQuickAdd());
  window.addEventListener("scroll", () => $("#topbar")?.classList.toggle("scrolled", window.scrollY > 4), { passive: true });
  on("vehicle-changed", refreshChrome);
  built = true;
  refreshChrome();
}

function navItem(n) {
  return `<a class="nav-item" data-link data-nav="${n.key}" href="${n.href}">${icon(n.ico, 24)}<span>${n.label}</span></a>`;
}

export function refreshChrome() {
  if (!built) return;
  const v = currentVehicle();
  const pill = $("#vpill");
  if (v) {
    pill.innerHTML = String(html`${vehicleDot(v)}<span class="ellipsis">${v.name}</span>${v.plate ? html`<span class="v-plate">${plateTail(v.plate)}</span>` : ""}${state.vehicles.length > 1 ? raw(icon("down", 16)) : ""}`);
    pill.classList.toggle("static", false);
  } else {
    pill.innerHTML = `${icon("car", 18)}<span>添加车辆</span>`;
  }
  renderSidebar();
  setNav(currentRoute()?.meta?.nav);
  updateBell();
}

function renderSidebar() {
  const v = currentVehicle();
  const u = state.user;
  $("#sidebar").innerHTML = String(html`
    <div class="side-brand"><img class="side-brand-logo" src="/static/icon.svg" alt="">
      <div><div class="side-brand-name">油电簿</div><div class="side-brand-sub">VOLTANE</div></div></div>
    <button class="side-vehicle" id="side-vehicle">
      ${v ? html`${vehicleDot(v, 20)}<span class="grow"><b class="ellipsis">${v.name}</b><small>${ENERGY_LABEL[v.energy_type]}${v.plate ? " · " + v.plate : ""}</small></span>${raw(icon("down", 16))}`
        : html`<span class="ico sm brand">${raw(icon("plus", 18))}</span><b>添加车辆</b>`}
    </button>
    ${canEdit(v) ? html`<button class="btn btn-primary side-new" id="side-new">${raw(icon("plus", 18))}记一笔</button>` : ""}
    ${NAV.map((n) => html`<a class="side-link" data-link data-nav="${n.key}" href="${n.href}">${raw(icon(n.ico, 20))}<span>${n.label}</span></a>`)}
    <a class="side-foot" data-link href="/me">${avatar(u)}<span class="grow"><b class="small">${u?.name}</b><div class="tiny muted">${u?.is_admin ? "管理员" : "成员"}</div></span>${raw(icon("settings", 18))}</a>`);
  $("#side-vehicle").onclick = openVehicleSwitcher;
  const nb = $("#side-new");
  if (nb) nb.onclick = () => openQuickAdd();
}

export function setNav(key) {
  $$("[data-nav]").forEach((a) => a.classList.toggle("on", a.dataset.nav === key));
}

export function setTitle(t) {
  const el = $("#page-title");
  if (el) el.textContent = t || "";
  document.title = t ? `${t} · 油电簿` : "油电簿";
}

export function setAside(content) {
  const layout = $("#layout");
  const aside = $("#aside");
  if (!layout) return;
  layout.classList.toggle("with-aside", !!content);
  aside.innerHTML = content ? String(content) : "";
}

export function view() {
  return $("#view");
}

let cleanups = [];
/** 当前页面离开时要执行的清理（取消事件订阅等）。 */
export function onLeave(fn) {
  cleanups.push(fn);
}
/** 订阅事件，离开页面时自动取消。 */
export function listen(name, fn) {
  onLeave(on(name, fn));
}

/** 每次切页：执行上一页的清理，重置视图动画与滚动。 */
export function resetView() {
  cleanups.forEach((f) => f());
  cleanups = [];
  const el = $("#view");
  el.style.animation = "none";
  void el.offsetWidth;
  el.style.animation = "";
  window.scrollTo(0, 0);
  setAside(null);
}

export async function updateBell() {
  try {
    const r = await api("/api/reminders/due");
    state.dueCount = r.count;
  } catch {
    return;
  }
  const dot = $("#bell-dot");
  if (!dot) return;
  dot.textContent = state.dueCount > 9 ? "9+" : state.dueCount;
  dot.classList.toggle("hidden", !state.dueCount);
}

export function openVehicleSwitcher() {
  if (!state.vehicles.length) return navigate("/vehicles/new");
  sheet({
    title: "切换车辆",
    render(body, ctx) {
      body.innerHTML = String(html`<div class="list card flush">
        ${state.vehicles.map((v) => html`<button class="list-item" data-id="${v.id}">
          <span class="vehicle-pill static" style="padding:0;border:0;box-shadow:none;background:none">${vehicleDot(v, 20)}</span>
          <span class="grow"><b>${v.name}</b> ${v.is_archived ? html`<span class="badge">已归档</span>` : ""}
            <div class="tiny muted">${ENERGY_LABEL[v.energy_type]}${v.plate ? " · " + v.plate : ""} · ${v.member_count} 人共享</div></span>
          ${v.id === state.vid ? raw(`<span class="ok-c">${icon("check")}</span>`) : ""}</button>`)}
      </div>
      <div class="grid-2" style="margin-top:14px">
        <button class="btn" data-go="/vehicles/new">${raw(icon("plus", 18))}添加车辆</button>
        <button class="btn" data-go="/me/join">${raw(icon("key", 18))}加入车辆</button>
      </div>`);
      body.addEventListener("click", (e) => {
        const b = e.target.closest("[data-id]");
        if (b) {
          setVehicle(+b.dataset.id);
          ctx.close();
          return;
        }
        const g = e.target.closest("[data-go]");
        if (g) {
          ctx.close();
          navigate(g.dataset.go);
        }
      });
    },
  });
}

/** 「＋」面板：按车型只显示可用的补能类型。 */
export async function openQuickAdd() {
  const v = currentVehicle();
  if (!v) return navigate("/vehicles/new");
  if (!canEdit(v)) {
    const { toast } = await import("./ui.js");
    toast(v.is_archived ? "车辆已归档，只能查看" : "你在这辆车是「只看」角色");
    return;
  }
  const { openRecordForm } = await import("./record-form.js");
  const tiles = [];
  if (v.has_fuel) tiles.push(["fuel", "fuel", "加油", "满到满算油耗"]);
  if (v.has_elec) tiles.push(["charge", "elec", "充电", "支持分两步记"]);
  tiles.push(["expense", "expense", "记一笔", "保险、保养、停车…"]);
  tiles.push(["snapshot", "snapshot", "状态快照", "停车前后记一下电量"]);
  sheet({
    title: "记一笔",
    render(body, ctx) {
      body.innerHTML = `<div class="action-grid">${tiles
        .map(([k, tone, label, sub]) => `<button class="action-tile" data-k="${k}">
          <span class="ico ${tone}">${icon(k === "fuel" ? "fuel" : k === "charge" ? "bolt" : k === "expense" ? "receipt" : "gauge")}</span>
          <span><b>${label}</b><br><small>${sub}</small></span></button>`)
        .join("")}</div>`;
      body.addEventListener("click", (e) => {
        const b = e.target.closest("[data-k]");
        if (!b) return;
        ctx.close();
        setTimeout(() => openRecordForm({ kind: b.dataset.k }), 180);
      });
    },
  });
}
