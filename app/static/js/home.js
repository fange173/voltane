/* home.js — 概览：最近能耗、本期 KPI、快捷记录、待确认、提醒、提车至今、最近记录 */
import { sparkline } from "./charts.js";
import { icon } from "./icons.js";
import { navigate } from "./router.js";
import { listen, openQuickAdd, resetView, setAside, setNav, setTitle, view } from "./shell.js";
import { canEdit, currentVehicle, emit, pref, savePref, state } from "./state.js";
import { bindRows, itemRow, openDetail } from "./records.js";
import { reminderRow } from "./reminders.js";
import { HELP_CPK, MIN_CENTS, MIN_KM, avatar, bindSegmented, changeTag, emptyState, helpTip, qTag, skeleton, toast, toastError } from "./ui.js";
import { $, $$, ago, api, durationText, esc, fixed, fmtDate, html, km, money, num, parseLocal, raw } from "./util.js";

const RANGES = [["month", "本月"], ["30d", "近30天"], ["year", "今年"], ["all", "全部"]];
let costScopeSaving = false;
let costData = null;
const HINT = {
  need_start: "还没有记录：先记一条「状态快照」或一次「加满」作为起点",
  need_full: "下次加油建议加满，之后油耗就准了",
  below_gauge_resolution: "油量变化低于油表分辨率，暂不显示油耗",
  need_second_full: "再加满一次，就能算出第一段准确油耗",
  need_soc: "充电时填一下开始和结束电量，电耗会更准",
  need_more: "再多记几次就有结果了",
};

export async function homePage() {
  resetView();
  setNav("home");
  setTitle("概览");
  const el = view();
  const v = currentVehicle();
  if (!v) return welcome(el);
  el.innerHTML = String(skeleton(4, 120));
  listen("data-changed", () => load());
  listen("vehicle-changed", () => homePage());
  const load = async () => {
    const vv = currentVehicle();
    if (!vv) return;
    try {
      const d = await api(`/api/stats/overview?vehicle_id=${vv.id}&range=${pref("home_range", "month")}`);
      if (location.pathname === "/" && currentVehicle()?.id === vv.id) render(el, vv, d, load);
    } catch (e) {
      el.innerHTML = String(emptyState({ ico: "warning", title: "加载失败", text: e.message }));
    }
  };
  await load();
}

function welcome(el) {
  el.innerHTML = String(html`<div class="welcome">
    <img src="/static/icon.svg" alt="" class="welcome-logo">
    <h1>欢迎使用油电簿</h1>
    <p class="muted">一车两能，一本算清。记录加油、充电和用车花费，算准混动车的油耗和电耗。</p>
    <div class="stack" style="width:min(360px,100%)">
      <a class="btn btn-primary btn-block" data-link href="/vehicles/new">${raw(icon("plus", 18))}添加我的车</a>
      <a class="btn btn-block" data-link href="/me/join">${raw(icon("key", 18))}用邀请码加入家人的车</a>
    </div></div>`);
}

function render(el, v, d, reload) {
  const p = d.period;
  const prev = d.prev;
  costData = { p, prev };
  const mileage = p.mileage_known === false ? null : p.mileage;
  const prevMileage = prev?.mileage_known === false ? null : prev?.mileage;
  const editable = canEdit(v);
  const pending = pendingCard(d.pending, editable);
  const reminders = remindersCard(d.reminders);
  const history = historyCard(v, d.history);
  el.innerHTML = String(html`
    ${d.in_progress.map((r) => chargingCard(r))}
    <div class="home-grid">
      <div class="home-main stack">
        ${heroCard(v, d)}
        <div class="row between range-row">
          <div class="segmented range-seg" id="range-seg">${RANGES.map(([k, l]) => html`<button data-v="${k}" class="${k === d.range.key ? "on" : ""}">${l}</button>`)}</div>
        </div>
        <div class="kpi-grid">
          ${kpi("里程", mileage == null ? "—" : km(mileage), mileage == null ? "" : "km", changeTag(mileage, prevMileage, { neutral: true, min: MIN_KM }), "road", mileage == null ? "里程数据不足" : "")}
          <div id="home-cost-card">${costCard(p, prev)}</div>
          ${v.has_fuel ? quantityCard(p, "fuel") : ""}
          ${v.has_elec ? quantityCard(p, "elec") : ""}
          ${perKmCard(p, prev, true)}
          ${perKmCard(p, prev, false)}
        </div>
        ${editable ? quickRow(v) : ""}
      </div>
      ${pending || reminders || history ? html`<div class="home-side stack">${pending}${reminders}${history}</div>` : ""}
    </div>
    <div class="section-title"><span>最近记录</span><a data-link href="/records" class="link-btn">全部</a></div>
    ${d.recent.length ? html`<div class="card flush list" id="recent">${d.recent.map((it) => itemRow(it))}</div>`
      : html`<div class="card">${emptyState({ ico: "list", title: "还没有记录", text: HINT.need_start })}</div>`}
  `);
  bindSegmented($("#range-seg", el), (k) => {
    savePref("home_range", k);
    reload();
  });
  bindCostCard(el);
  $("#recent") && bindRows($("#recent"));
  $$("[data-quick]", el).forEach((b) => b.addEventListener("click", async () => {
    const { openRecordForm } = await import("./record-form.js");
    openRecordForm({ kind: b.dataset.quick });
  }));
  $$("[data-finish]", el).forEach((b) => b.addEventListener("click", async () => {
    const r = d.in_progress.find((x) => x.id === +b.dataset.finish);
    const { openRecordForm } = await import("./record-form.js");
    openRecordForm({ record: { ...r, type: "record" }, vehicle: v, finish: true });
  }));
  $("[data-hero-action]", el)?.addEventListener("click", async (e) => {
    const k = e.currentTarget.dataset.heroAction;
    const { openRecordForm } = await import("./record-form.js");
    openRecordForm({ kind: k });
  });
  bindPending($(".home-grid", el), v, d.pending);
  $("#history-hide", el)?.addEventListener("click", () => {
    savePref("hide_history", true);
    $("#history-card")?.remove();
    toast("已隐藏，可在「我的」里重新打开");
  });
  $$("[data-rem]", el).forEach((b) => b.addEventListener("click", () => navigate("/reminders")));
  renderAside(v);
}

function chargingCard(r) {
  const mins = Math.max(0, Math.round((Date.now() - parseLocal(r.occurred_at).getTime()) / 60000));
  return html`<div class="charging-card">
    <span class="pulse-dot"></span>
    <div class="grow"><b>充电中 · 已 ${durationText(mins)}</b>
      <div class="tiny">${fmtDate(r.occurred_at, { time: true })} 开始${r.soc_before_pct != null ? ` · 开始电量 ${num(r.soc_before_pct, 0)}%` : ""}${r.place_name ? " · " + r.place_name : ""}</div></div>
    <button class="btn btn-sm" data-finish="${r.id}">结束充电</button></div>`;
}

function quantityCard(p, energy) {
  const fuel = energy === "fuel";
  const count = p[energy + "_count"] ?? 0;
  const missing = p[energy + "_qty_missing"] ?? 0;
  const amount = fuel ? p.fuel_l : p.elec_kwh;
  const known = amount != null && (count > missing || amount > 0);
  const foot = [`${count} 次`];
  if (missing) foot.push(`${known ? "已知小计，" : ""}${missing} 笔未填${fuel ? "油量" : "电量"}`);
  if (fuel && count && !p.energy_cost_missing) foot.push(money(p.fuel_cents));
  if (!fuel && p.home_share != null && !missing) foot.push(`家充 ${num(p.home_share * 100, 0)}%`);
  return kpi(fuel ? "加油" : "充电", known ? num(amount, 2) : missing ? "未填" : "—", known ? (fuel ? "L" : "kWh") : "", "",
    fuel ? "fuel" : "bolt", foot.join(" · "), energy);
}

function perKmCard(p, prev, energyOnly) {
  const key = energyOnly ? "energy_cost_per_km" : "total_cost_per_km";
  const costMissing = p.energy_cost_missing ?? 0;
  const value = p.mileage_known === false || costMissing ? null : p[key];
  const comparable = prev && prev.mileage_known !== false && prev.mileage >= MIN_KM && !prev.energy_cost_missing;
  return kpi(energyOnly ? "每公里补能支出" : "每公里成本（含摊销）", value == null ? "—" : "¥" + fixed(value), "",
    changeTag(value, comparable ? prev[key] : null), energyOnly ? "coin" : "receipt",
    costMissing ? `${costMissing} 笔补能金额未填` : "", "", energyOnly ? "" : HELP_CPK);
}

function costCard(p, prev) {
  const energyOnly = pref("home_cost_scope", "all") === "energy";
  const key = energyOnly ? "energy_cents" : "total_cents";
  const missing = p.energy_cost_missing ?? 0;
  const count = (p.fuel_count ?? 0) + (p.elec_count ?? 0);
  const amount = p[key];
  const parts = [];
  let value = money(amount, { digits: amount >= 1000000 ? 0 : 2 });
  if (missing) {
    if (amount) parts.push("已知小计");
    else if (count > missing) {
      value = "待补全";
      parts.push("已填补能金额均为零");
    } else value = "未填";
    parts.push(`${missing} 笔补能金额未填`);
  } else if (!count && !amount) value = "—";
  if (p.summary_cents) parts.push("含汇总分摊");
  return kpi(energyOnly ? "本期油电实付" : "本期实付", value, "",
    !missing && !prev?.energy_cost_missing && value !== "—" ? changeTag(amount, prev?.[key], { min: MIN_CENTS }) : "", "wallet", parts.join(" · "), "", "",
    html`<button type="button" class="cost-scope-toggle" aria-label="${energyOnly ? "切换为本期实付" : "切换为本期油电实付"}" ${costScopeSaving ? "disabled" : ""}>切换</button>`);
}

function bindCostCard(el) {
  const card = $("#home-cost-card", el);
  card.addEventListener("click", async (e) => {
    if (!e.target.closest(".cost-scope-toggle") || costScopeSaving || !state.user) return;
    const user = state.user;
    const previous = user.settings?.home_cost_scope;
    const next = previous === "energy" ? "all" : "energy";
    costScopeSaving = true;
    user.settings = { ...(user.settings || {}), home_cost_scope: next };
    updateCostCard(el);
    try {
      await api("/api/auth/settings", { method: "PUT", body: { home_cost_scope: next } });
    } catch (ex) {
      if (previous === undefined) {
        delete user.settings.home_cost_scope;
      } else {
        user.settings.home_cost_scope = previous;
      }
      toastError(ex);
    } finally {
      costScopeSaving = false;
      updateCostCard(el);
    }
  });
}

function updateCostCard(el) {
  const card = location.pathname === "/" && $("#home-cost-card", el);
  if (card && costData) card.innerHTML = String(costCard(costData.p, costData.prev));
}

function kpi(label, value, unit, delta, ico, sub = "", tone = "", help = "", action = "") {
  return html`<div class="kpi card ${tone}">
    <div class="kpi-label">${raw(icon(ico, 16))}${label}${help ? helpTip(help, label + "说明") : ""}${action}</div>
    <div class="kpi-value num">${value}<small>${unit}</small></div>
    <div class="kpi-foot">${delta || ""}${sub ? html`<span class="tiny muted">${sub}</span>` : ""}</div></div>`;
}

function quickRow(v) {
  const b = [];
  if (v.has_fuel) b.push(html`<button class="quick fuel" data-quick="fuel">${raw(icon("fuel", 20))}<span>加油</span></button>`);
  if (v.has_elec) b.push(html`<button class="quick elec" data-quick="charge">${raw(icon("bolt", 20))}<span>充电</span></button>`);
  b.push(html`<button class="quick snapshot" data-quick="snapshot">${raw(icon("gauge", 20))}<span>快照</span></button>`);
  b.push(html`<button class="quick expense" data-quick="expense">${raw(icon("receipt", 20))}<span>记费用</span></button>`);
  return html`<div class="quick-row">${b}</div>`;
}

/* ---------- 最近能耗 ---------- */

function bigNum(value, label, unit, tone, quality) {
  const ico = { fuel: "fuel", elec: "bolt", combined: "gauge" }[tone];
  return html`<div class="big ${tone}"><div class="big-label">${raw(icon(ico, 16))}${label} ${qTag(quality, null, true)}</div>
    <div class="big-reading"><b class="big-val num">${num(value)}</b><small class="big-unit">${unit}/100km</small></div></div>`;
}

function scopeText(x) {
  if (!x || x.quality === "D") return "";
  const start = x.start_ts || x.from_ts;
  const end = x.end_ts || x.to_ts;
  const scope = x.scope === "delivery" ? "自提车记录起" : x.scope === "ledger" ? "自记账起点起"
    : x.segments > 1 ? `最近 ${x.segments} 个 A 级段（按里程加权）` : x.anchor === "full" ? "满到满" : x.anchor === "gauge" ? "油表区间" : "最近可用段";
  return [scope, start && !(x.segments > 1) && fmtDate(start) + " 起",
    end && "截至 " + fmtDate(end), x.distance != null && "覆盖 " + km(x.distance) + " km"].filter(Boolean).join(" · ");
}

function rangeText(x, unit) {
  if (x.low100 != null && x.high100 != null) return `估算范围 ${num(x.low100)}–${num(x.high100)} ${unit}/100km`;
  return x.err100 != null ? `误差约 ±${num(x.err100)} ${unit}/100km` : "";
}

function componentMetric(label, value, unit, tone = "") {
  return html`<div><span class="tiny muted">${label}</span><b class="num ${tone}">${num(value)}<small>${unit}</small></b></div>`;
}

function heroCard(v, d) {
  const L = d.latest;
  const editable = canEdit(v);
  let body;
  let foot = [];
  let hint = null;
  let hintKind = null;
  const hybrid = v.has_fuel && v.has_elec;
  const c = hybrid && L.combined;
  const paired = c && c.equiv_per100 != null;
  const sources = [["fuel", "油耗", "L"], ["elec", "电耗", "kWh"]].filter(([key]) => v["has_" + key]);
  if (paired) {
    const factor = v.calc_settings?.kwh_per_l ?? 8.9;
    body = html`<div class="big-pair">${bigNum(c.equiv_per100, "折合油耗", "L", "combined", c.quality)}
      <div class="big-side">${componentMetric("同区间油耗", c.per100, "L/100km", "fuel-c")}
        ${componentMetric("同区间电耗", c.elec_per100, "kWh/100km", "elec-c")}</div></div>
      <p class="hero-scope">${scopeText(c)} ${helpTip(`同一区间内，折合油耗 = 油耗 + 电耗 ÷ ${factor}。当前车辆按 1 L 汽油折合 ${factor} kWh 计算。`, "折合油耗说明")}</p>`;
    const oilRange = rangeText(c, "L");
    foot = [oilRange && `油耗${oilRange}`, c.elec_share != null && `电能占比 ${num(c.elec_share * 100, 0)}%`,
      c.cost_per_km != null && `区间补能成本 ¥${fixed(c.cost_per_km)}/km`];
  } else {
    body = html`<div class="big-pair">${sources.map(([key, label, unit]) => {
      const x = L[key];
      const scope = scopeText(x);
      const range = rangeText(x, unit);
      return html`<div>${bigNum(x.per100, label, unit, key, x.quality)}
        ${scope ? html`<p class="hero-scope">${scope}</p>` : ""}
        ${range ? html`<p class="hero-scope">${range}</p>` : ""}</div>`;
    })}</div>`;
    if (hybrid) foot.push("油电各取各自的可用区间，不作折合");
    else {
      const x = L[sources[0][0]];
      if (x.cost_per_km != null) foot.push(`区间补能成本 ¥${fixed(x.cost_per_km)}/km`);
    }
  }
  for (const [key] of sources) {
    const x = L[key];
    if ((!paired || c.quality === "C") && ["C", "D"].includes(x.quality) && HINT[x.hint]) {
      hint = HINT[x.hint];
      hintKind = key === "fuel" ? "fuel" : "charge";
      break;
    }
  }
  const t = !hybrid && v.has_elec && L.elec_totals;
  const sparks = sources.flatMap(([key, label]) => {
    const pts = (d.spark[key] || []).filter((x) => x.per100 != null);
    if (pts.length < 2) return [];
    return html`<div class="hero-spark"><span class="tiny"><i class="lg-dot ${key}"></i>${label}趋势 · 最近 ${pts.length} 段</span>
      ${raw(sparkline(pts.map((x) => x.per100), { color: key, w: 240, hollow: pts.map((x) => x.quality !== "A") }))}</div>`;
  });
  const available = sources.filter(([key]) => L[key].per100 != null);
  let title;
  if (paired) title = c.scope === "segment" ? "最近可算的油电综合段" : "油电综合粗估";
  else if (!available.length) title = "能耗数据不足";
  else if (available.length > 1) title = "油耗与电耗（分开计算）";
  else {
    const [key, label] = available[0];
    title = L[key].quality === "C" ? `${label}粗估` : `最近可算的${label}`;
  }
  const slf = d.since_last_full;
  return html`<section class="card hero">
    <div class="row between"><span class="card-title">${title}</span><a class="hero-link" data-link href="/stats/energy">能耗详情 ›</a></div>
    ${body}
    ${foot.some(Boolean) ? html`<div class="hero-foot">${foot.filter(Boolean).map((s) => html`<span>${s}</span>`)}</div>` : ""}
    ${t && (t.overall || t.drive || t.idle) ? html`<div class="hero-period"><p class="tiny muted">所选时段 · ${fmtDate(d.range.from)} 至 ${fmtDate(d.range.to)}</p><div class="big-side">
      ${componentMetric("行驶电耗", t.drive?.per100, "kWh/100km")}${componentMetric("综合电耗（含静置）", t.overall?.per100, "kWh/100km")}
      ${t.idle ? componentMetric("静置耗电", t.idle.amount, "kWh") : ""}</div></div>` : ""}
    ${sparks.length ? html`<div class="hero-sparks">${sparks}</div>` : ""}
    ${slf && slf.km > 0 ? html`<div class="tiny muted">距上次加满已行驶 ${km(slf.km)} km</div>` : ""}
  </section>
  ${hint ? html`<div class="callout ${hintKind === "fuel" ? "fuel" : "elec"}">${raw(icon("sparkle", 18))}<span class="grow">${hint}</span>
    ${editable && hintKind ? html`<button class="btn btn-sm ${hintKind === "fuel" ? "btn-fuel" : "btn-elec"}" data-hero-action="${hintKind}">${hintKind === "fuel" ? "记加油" : "记充电"}</button>` : ""}</div>` : ""}`;
}

/* ---------- 待确认 / 提醒 / 提车至今 ---------- */

function pendingCard(items, editable) {
  if (!items?.length) return "";
  return html`<div class="card pending-card"><div class="card-head"><span class="card-title">${raw(icon("warning", 18))}待确认</span><span class="badge warn" data-count>${items.length}</span></div>
    ${items.map((it) => html`<div class="pending-item" data-key="${it.key}">
      <span class="grow small">${it.text}</span>
      <div class="row">${it.type === "anomaly" ? html`<button class="btn btn-sm btn-soft" data-open="${it.end_id}">查看</button>` : editable ? html`<button class="btn btn-sm btn-soft" data-fix="${it.key}">补一条</button>` : ""}
      <button class="btn btn-sm btn-ghost" data-dismiss="${it.key}">忽略</button></div></div>`)}</div>`;
}

function bindPending(el, v, items) {
  el.addEventListener("click", async (e) => {
    const t = e.target.closest("[data-open],[data-fix],[data-dismiss]");
    if (!t) return;
    e.preventDefault();
    try {
      if (t.dataset.open) return openDetail("record", +t.dataset.open);
      if (t.dataset.dismiss) {
        await api(`/api/vehicles/${v.id}/hints/dismiss`, { method: "POST", body: { key: t.dataset.dismiss } });
        const card = t.closest(".pending-card");
        t.closest(".pending-item").remove();
        const left = card.querySelectorAll(".pending-item").length;
        if (left) card.querySelector("[data-count]").textContent = left;
        else card.remove();
        return;
      }
      const it = items.find((x) => x.key === t.dataset.fix);
      const r = await api(`/api/vehicles/${v.id}/fix/missed`, { method: "POST", body: { after_id: it.after_id, before_id: it.before_id, kind: it.type === "missed_fuel" ? "fuel" : "charge" } });
      toast(`已自动补一条${it.type === "missed_fuel" ? "加油" : "充电"}（约 ${num(r.qty)}${it.type === "missed_fuel" ? "L" : "kWh"}）`, { sub: "标记为估算，可在记录里修改" });
      emit("data-changed");
    } catch (ex) {
      toastError(ex);
    }
  });
}

function remindersCard(list) {
  if (!list?.length) return "";
  return html`<div class="card flush"><div class="card-head" style="padding:14px 16px 0"><span class="card-title">${raw(icon("bell", 18))}用车提醒</span><a class="link-btn" data-link href="/reminders">全部</a></div>
    <div class="list">${list.map((r) => reminderRow(r, { compact: true }))}</div></div>`;
}

function historyCard(v, h) {
  if (!h || pref("hide_history", false) || h.days < 30) return "";
  const rows = [];
  if (h.fuel) rows.push(html`<div><span class="muted tiny">平均油耗</span><b class="num fuel-c">${num(h.fuel.per100)}</b><small>${h.fuel.below_resolution ? "低于油表分辨率" : `L/100km${h.fuel.low100 != null ? ` (${num(h.fuel.low100)}–${num(h.fuel.high100)})` : ""}`}</small></div>`);
  if (h.elec) rows.push(html`<div><span class="muted tiny">平均电耗</span><b class="num elec-c">${num(h.elec.per100)}</b><small>kWh/100km</small></div>`);
  return html`<div class="card history" id="history-card">
    <div class="card-head"><span class="card-title">${raw(icon("history", 18))}${h.scope === "delivery" ? "提车至今" : "记账以来"}</span>
      <span class="row">${h.has_summary ? html`<span class="badge">含历史汇总</span>` : ""}<button class="icon-btn" id="history-hide" aria-label="隐藏" style="width:28px;height:28px">${raw(icon("x", 14))}</button></span></div>
    <div class="history-grid">
      <div class="span-2"><span class="muted tiny">累计里程（${h.days} 天）</span><b class="num">${h.mileage_known === false ? "—" : html`${km(h.mileage)} <small>km</small>`}</b></div>
      <div><span class="muted tiny">累计实付</span><b class="num">${h.energy_cost_missing && !h.total_cents ? "—" : money(h.total_cents)}</b>
        ${h.energy_cost_missing ? html`<small>${h.total_cents ? "已知小计，" : ""}${h.energy_cost_missing} 笔补能金额未填</small>` : ""}</div>
      <div><span class="muted tiny">每公里成本（含摊销）</span><b class="num">${h.mileage_known !== false && !h.energy_cost_missing && h.total_cost_per_km != null ? "¥" + fixed(h.total_cost_per_km) : "—"}</b></div>
      ${rows}
    </div>
    ${h.summary_before_cents ? html`<p class="tiny muted">另有记账前的历史汇总 ${money(h.summary_before_cents, { digits: 0 })}，那段里程未知，没有计入每公里成本</p>` : ""}
    ${h.fuel || h.elec ? html`<p class="tiny muted">按${h.scope === "delivery" ? "提车" : "记账"}以来的${v.has_fuel && v.has_elec ? "加油、充电" : v.has_fuel ? "加油" : "充电"}总量粗略折算，仅供参考</p>` : ""}
  </div>`;
}

async function renderAside(v) {
  if (window.innerWidth < 1280) return;
  try {
    const acts = await api(`/api/vehicles/${v.id}/activity?limit=12`);
    if (location.pathname !== "/" || currentVehicle()?.id !== v.id) return;
    setAside(acts.length ? html`<div class="aside-title">最近动态</div>
      <div class="activity">${acts.map((a) => html`<div class="act-item">${avatar(a.user, "sm")}<div class="grow"><div class="small">${a.summary}</div><div class="tiny muted">${a.user?.name || ""} · ${ago(a.created_at)}</div></div></div>`)}</div>` : null);
  } catch {
    /* 右侧栏失败不影响首页 */
  }
}
