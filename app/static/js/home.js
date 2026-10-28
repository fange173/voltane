/* home.js — 概览：最近能耗、本期 KPI、快捷记录、待确认、提醒、提车至今、最近记录 */
import { sparkline } from "./charts.js";
import { icon } from "./icons.js";
import { navigate } from "./router.js";
import { listen, openQuickAdd, resetView, setAside, setNav, setTitle, view } from "./shell.js";
import { canEdit, currentVehicle, emit, pref, savePref, state } from "./state.js";
import { bindRows, itemRow, openDetail } from "./records.js";
import { reminderRow } from "./reminders.js";
import { MIN_CENTS, MIN_KM, avatar, bindSegmented, changeTag, emptyState, helpTip, qTag, skeleton, toast, toastError } from "./ui.js";
import { $, $$, ago, api, durationText, esc, fixed, fmtDate, html, km, money, num, parseLocal, raw } from "./util.js";

const RANGES = [["month", "本月"], ["30d", "近30天"], ["year", "今年"], ["all", "全部"]];
const HINT = {
  need_start: "还没有记录：先记一条「状态快照」或一次「加满」作为起点",
  need_full: "下次加油建议加满，之后油耗就准了",
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
  // 上期里程太少时每公里成本的环比没有参考意义
  const prevCpk = (k) => (prev && prev.mileage >= MIN_KM ? prev[k] : null);
  const editable = canEdit(v);
  el.innerHTML = String(html`
    ${d.in_progress.map((r) => chargingCard(r))}
    <div class="home-grid">
      <div class="home-main stack">
        ${heroCard(v, d)}
        <div class="row between range-row">
          <div class="segmented range-seg" id="range-seg">${RANGES.map(([k, l]) => html`<button data-v="${k}" class="${k === d.range.key ? "on" : ""}">${l}</button>`)}</div>
        </div>
        <div class="kpi-grid">
          ${kpi("里程", km(p.mileage), "km", changeTag(p.mileage, prev?.mileage, { neutral: true, min: MIN_KM }), "road")}
          ${kpi("总花费", money(p.total_cents, { digits: p.total_cents >= 1000000 ? 0 : 2 }), "", changeTag(p.total_cents, prev?.total_cents, { min: MIN_CENTS }), "wallet", p.summary_cents ? "含汇总分摊" : "")}
          ${v.has_fuel ? kpi("加油", num(p.fuel_l, 1), "L", raw(`<span class="tiny muted">${p.fuel_count} 次 · ${money(p.fuel_cents)}</span>`), "fuel", "", "fuel") : ""}
          ${v.has_elec ? kpi("充电", num(p.elec_kwh, 0), "kWh", raw(`<span class="tiny muted">${p.elec_count} 次${p.home_share != null ? ` · 家充 ${num(p.home_share * 100, 0)}%` : ""}</span>`), "bolt", "", "elec") : ""}
          ${kpi("每公里能源", p.energy_cost_per_km != null ? "¥" + fixed(p.energy_cost_per_km) : "–", "", changeTag(p.energy_cost_per_km, prevCpk("energy_cost_per_km")), "coin")}
          ${kpi("每公里总成本", p.total_cost_per_km != null ? "¥" + fixed(p.total_cost_per_km) : "–", "", changeTag(p.total_cost_per_km, prevCpk("total_cost_per_km")), "receipt", "", "", HELP_CPK)}
        </div>
        ${editable ? quickRow(v) : ""}
      </div>
      <div class="home-side stack">
        ${pendingCard(d.pending, editable)}
        ${remindersCard(d.reminders)}
        ${historyCard(v, d.history)}
      </div>
    </div>
    <div class="section-title"><span>最近记录</span><a data-link href="/records" class="link-btn">全部</a></div>
    ${d.recent.length ? html`<div class="card flush list" id="recent">${d.recent.map((it) => itemRow(it))}</div>`
      : html`<div class="card">${emptyState({ ico: "list", title: "还没有记录", text: HINT.need_start })}</div>`}
  `);
  bindSegmented($("#range-seg", el), (k) => {
    savePref("home_range", k);
    reload();
  });
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

const HELP_CPK = "花费 ÷ 里程。保险按保险期间（没填就按一年）按天摊销，只算落在这段时间里的部分；「总花费」仍按实际付款计。";
const HELP_EQUIV = "油和电取同一区间：油耗 + 电耗 ÷ 8.9（1 L 汽油约等于 8.9 kWh 电）就是折合油耗，能直接和燃油车比较。";

function kpi(label, value, unit, delta, ico, sub = "", tone = "", help = "") {
  return html`<div class="kpi card ${tone}">
    <div class="kpi-label">${raw(icon(ico, 16))}${label}${help ? helpTip(help) : ""}</div>
    <div class="kpi-value num">${value}<small>${unit}</small></div>
    <div class="kpi-foot">${delta || ""}${sub ? html`<span class="tiny muted">${sub}</span>` : ""}</div></div>`;
}

function quickRow(v) {
  const b = [];
  if (v.has_fuel) b.push(html`<button class="quick fuel" data-quick="fuel">${raw(icon("fuel", 20))}<span>加油</span></button>`);
  if (v.has_elec) b.push(html`<button class="quick elec" data-quick="charge">${raw(icon("bolt", 20))}<span>充电</span></button>`);
  b.push(html`<button class="quick expense" data-quick="expense">${raw(icon("receipt", 20))}<span>记一笔</span></button>`);
  b.push(html`<button class="quick snapshot" data-quick="snapshot">${raw(icon("gauge", 20))}<span>快照</span></button>`);
  return html`<div class="quick-row">${b}</div>`;
}

/* ---------- 最近能耗 ---------- */

function bigNum(value, unit, tone, q, err) {
  const ico = { fuel: "fuel", elec: "bolt", combined: "gauge" }[tone];
  return html`<div class="big ${tone}"><span class="big-ico">${raw(icon(ico, 18))}</span>
    <span class="big-val num">${value != null ? num(value) : "–"}</span><span class="big-unit">${unit}<br>/100km</span>${qTag(q, err)}</div>`;
}

function scopeText(x) {
  if (!x) return "";
  if (x.quality === "A") return x.segments > 1 ? `最近 ${x.segments} 段 · ${km(x.distance)}km` : `最近一段 · ${km(x.distance)}km`;
  if (x.quality === "B") return `估算 · ${km(x.distance)}km`;
  if (x.quality === "C") return `粗略 · ${x.scope === "delivery" ? "提车至今" : "记账以来"} ${km(x.distance)}km`;
  return "";
}

function rangeText(x) {
  if (x?.quality === "C" && x.low100 != null) return `约 ${num(x.low100)}–${num(x.high100)}`;
  return "";
}

function heroCard(v, d) {
  const L = d.latest;
  const editable = canEdit(v);
  let body;
  let foot = [];
  let hint = null;
  let hintKind = null;
  if (v.has_fuel && v.has_elec) {
    const c = L.combined;
    if (c && c.per100 != null) {
      // 油电同一区间，主数字是折合油耗
      const range = c.low100 != null ? ` (${num(c.low100)}–${num(c.high100)})` : "";
      body = html`<div class="big-pair">${bigNum(c.equiv_per100 ?? c.per100, "L 折合", "combined", c.quality)}
        <div class="big-side"><div><span class="muted tiny">油</span><b class="num fuel-c">${num(c.per100)}<small>L${range}</small></b></div>
          <div><span class="muted tiny">电</span><b class="num elec-c">${c.elec_per100 != null ? num(c.elec_per100) : "–"}<small>kWh</small></b></div></div></div>`;
      foot = [
        c.cost_per_km != null && html`<span><b class="num">¥${fixed(c.cost_per_km)}</b>/km</span>`,
        c.elec_share != null && html`<span>电能 <b class="num">${num(c.elec_share * 100, 0)}%</b></span>`,
        html`<span class="tiny muted">同一区间 ${km(c.distance)} km${helpTip(HELP_EQUIV)}</span>`,
      ];
      d._scope = c.scope === "segment"
        ? `${c.quality === "A" ? "满到满" : "油表读数之间"} · ${fmtDate(c.end_ts)}`
        : `粗略 · ${c.scope === "delivery" ? "提车至今" : "记账以来"}`;
      const f = L.fuel;
      if (c.quality === "C" && f?.hint) {
        hint = HINT[f.hint];
        hintKind = "fuel";
      }
    } else {
      const f = L.fuel;
      const e = L.elec;
      body = html`<div class="big-pair">${bigNum(f.per100, "L", "fuel", f.quality, f.err100)}${bigNum(e.per100, "kWh", "elec", e.quality, e.err100)}</div>`;
      foot = [
        f.per100 != null && html`<span class="tiny">油：${scopeText(f)} ${rangeText(f)}</span>`,
        e.per100 != null && html`<span class="tiny">电：${scopeText(e)}</span>`,
      ];
      if (f.hint && (f.quality === "C" || f.quality === "D")) {
        hint = HINT[f.hint];
        hintKind = "fuel";
      } else if (e.hint && e.quality === "D") {
        hint = HINT[e.hint];
        hintKind = "charge";
      }
    }
  } else if (v.has_elec) {
    const e = L.elec;
    const t = L.elec_totals;
    body = html`<div class="big-pair">${bigNum(e.per100, "kWh", "elec", e.quality, e.err100)}
      ${t?.overall ? html`<div class="big-side"><div><span class="muted tiny">本期行驶</span><b class="num">${t.drive ? num(t.drive.per100) : "–"}</b></div>
        <div><span class="muted tiny">含静置</span><b class="num">${num(t.overall.per100)}</b></div>
        ${t.idle ? html`<div><span class="muted tiny">静置</span><b class="num">${num(t.idle.amount, 1)}<small>kWh</small></b></div>` : ""}</div>` : ""}</div>`;
    foot = [scopeText(e) && html`<span class="tiny">${scopeText(e)}</span>`, e.cost_per_km != null && html`<span><b class="num">¥${fixed(e.cost_per_km)}</b>/km</span>`];
    if (e.quality === "D") {
      hint = HINT[e.hint];
      hintKind = "charge";
    }
  } else {
    const f = L.fuel;
    body = html`<div class="big-pair">${bigNum(f.per100, "L", "fuel", f.quality, f.err100)}</div>`;
    foot = [scopeText(f) && html`<span class="tiny">${scopeText(f)} ${rangeText(f)}</span>`, f.cost_per_km != null && html`<span><b class="num">¥${fixed(f.cost_per_km)}</b>/km</span>`];
    if (f.quality === "D" || f.quality === "C") {
      hint = HINT[f.hint];
      hintKind = "fuel";
    }
  }
  const sparks = [];
  if (v.has_fuel && d.spark.fuel.length > 1) sparks.push(raw(sparkline(d.spark.fuel.map((x) => x.per100), { color: "fuel", w: 140, hollow: d.spark.fuel.map((x) => x.quality !== "A") })));
  if (v.has_elec && d.spark.elec.length > 1) sparks.push(raw(sparkline(d.spark.elec.map((x) => x.per100), { color: "elec", w: 140, hollow: d.spark.elec.map((x) => x.quality !== "A") })));
  const slf = d.since_last_full;
  return html`<a class="card hero" data-link href="/stats/energy">
    <div class="row between"><span class="card-title">最近能耗</span><span class="tiny muted">${d._scope || ""}</span></div>
    ${body}
    <div class="hero-foot">${foot.filter(Boolean)}</div>
    ${sparks.length ? html`<div class="hero-sparks">${sparks}<span class="tiny muted">近 10 段趋势</span></div>` : ""}
    ${slf && slf.km > 0 ? html`<div class="tiny muted">距上次加满已行驶 ${km(slf.km)} km</div>` : ""}
  </a>
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
  if (h.fuel) rows.push(html`<div><span class="muted tiny">油耗</span><b class="num fuel-c">${num(h.fuel.per100)}</b><small>L${h.fuel.low100 != null ? ` (${num(h.fuel.low100)}–${num(h.fuel.high100)})` : ""}</small></div>`);
  if (h.elec) rows.push(html`<div><span class="muted tiny">电耗</span><b class="num elec-c">${num(h.elec.per100)}</b><small>kWh</small></div>`);
  return html`<div class="card history" id="history-card">
    <div class="card-head"><span class="card-title">${raw(icon("history", 18))}${h.scope === "delivery" ? "提车至今" : "记账以来"}</span>
      <span class="row">${h.has_summary ? html`<span class="badge">含历史汇总</span>` : ""}<button class="icon-btn" id="history-hide" aria-label="隐藏" style="width:28px;height:28px">${raw(icon("x", 14))}</button></span></div>
    <div class="history-grid">
      <div><span class="muted tiny">${h.days} 天</span><b class="num">${km(h.mileage)}</b><small>km</small></div>
      <div><span class="muted tiny">总花费</span><b class="num">${money(h.total_cents, { digits: 0 })}</b></div>
      ${rows}
      <div><span class="muted tiny">每公里总成本</span><b class="num">${h.total_cost_per_km != null ? "¥" + fixed(h.total_cost_per_km) : "–"}</b></div>
    </div>
    ${h.summary_before_cents ? html`<p class="tiny muted">另有记账前的历史汇总 ${money(h.summary_before_cents, { digits: 0 })}，那段里程未知，没有计入每公里成本</p>` : ""}
    ${h.fuel || h.elec ? html`<p class="tiny muted">粗略口径：按长期补能总量估算，仅供参考</p>` : ""}
  </div>`;
}

async function renderAside(v) {
  if (window.innerWidth < 1280) return;
  try {
    const acts = await api(`/api/vehicles/${v.id}/activity?limit=12`);
    if (location.pathname !== "/") return;
    setAside(html`<div class="aside-title">最近动态</div>
      ${acts.length ? html`<div class="activity">${acts.map((a) => html`<div class="act-item">${avatar(a.user, "sm")}<div class="grow"><div class="small">${a.summary}</div><div class="tiny muted">${a.user?.name || ""} · ${ago(a.created_at)}</div></div></div>`)}</div>`
        : html`<p class="muted small">还没有动态</p>`}`);
  } catch {
    /* 右侧栏失败不影响首页 */
  }
}
