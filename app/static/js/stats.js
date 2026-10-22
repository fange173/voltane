/* stats.js — 统计：能耗、花费、里程、日历（含月报）、年度报告 */
import { barChart, donut, lineChart } from "./charts.js";
import { CATEGORY_ICON, icon } from "./icons.js";
import { navigate } from "./router.js";
import { listen, resetView, setNav, setTitle, view } from "./shell.js";
import { CHARGE_LABEL, EXPENSE_LABEL, SEGMENT_TAGS, currentVehicle, gradeLabel, pref, savePref, state } from "./state.js";
import { bindRows, itemRow, openDetail } from "./records.js";
import { bindSegmented, changeTag, emptyState, qTag, skeleton } from "./ui.js";
import { $, $$, api, esc, fixed, fmtDate, html, km, money, num, raw, todayISO } from "./util.js";

const TABS = [["energy", "能耗"], ["cost", "花费"], ["mileage", "里程"], ["calendar", "日历"]];
const CAT_COLOR = { fuel: "fuel", elec: "elec", maintenance: "expense", insurance: "brand", other: "snapshot" };

function monthsAgo(n) {
  const d = new Date();
  d.setMonth(d.getMonth() - n + 1, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
}
const RANGE_FROM = { "6m": () => monthsAgo(6), "12m": () => monthsAgo(12), year: () => `${new Date().getFullYear()}-01-01`, all: () => null };

function rangeSeg(id, cur, opts = [["6m", "近半年"], ["12m", "近一年"], ["all", "全部"]]) {
  return html`<div class="segmented range-seg" id="${id}">${opts.map(([k, l]) => html`<button data-v="${k}" class="${k === cur ? "on" : ""}">${l}</button>`)}</div>`;
}

export async function statsPage({ params }) {
  const tab = params.tab || pref("stats_tab", "energy");
  resetView();
  setNav(tab === "calendar" ? "calendar" : "stats");
  setTitle(tab === "calendar" ? "用车日历" : "统计");
  const v = currentVehicle();
  const el = view();
  if (!v) return navigate("/");
  el.innerHTML = String(html`<div class="segmented tabs-seg" id="stats-tabs">${TABS.map(([k, l]) => html`<button data-v="${k}" class="${k === tab ? "on" : ""}">${l}</button>`)}</div>
    <div id="stats-body" style="margin-top:14px">${skeleton(3, 180)}</div>`);
  bindSegmented($("#stats-tabs"), (k) => {
    savePref("stats_tab", k);
    navigate("/stats/" + k, { replace: true });
  });
  listen("vehicle-changed", () => statsPage({ params }));
  const body = $("#stats-body");
  const run = () => ({ energy: energyTab, cost: costTab, mileage: mileageTab, calendar: calendarTab }[tab] || energyTab)(body, v);
  listen("data-changed", run);
  await run();
}

/* ---------- 能耗 ---------- */

async function energyTab(body, v) {
  const rk = pref("energy_range", "12m");
  const from = RANGE_FROM[rk]();
  let d;
  try {
    d = await api(`/api/stats/energy?vehicle_id=${v.id}${from ? "&date_from=" + from : ""}`);
  } catch (e) {
    body.innerHTML = String(emptyState({ ico: "warning", title: "加载失败", text: e.message }));
    return;
  }
  const trend = (segs) => segs.filter((s) => s.status === "ok" && s.kind === "drive" && !s.excluded && s.per100 != null);
  const fuelT = trend(d.segments.fuel || []);
  const elecT = trend(d.segments.elec || []);
  const comb = (d.segments.combined || []).filter((s) => s.status === "ok");
  const tot = d.totals;
  body.innerHTML = String(html`
    <div class="row between" style="margin-bottom:12px">${rangeSeg("e-range", rk)}</div>
    <div class="kpi-grid">
      ${v.has_fuel ? energyKpi("行驶油耗", tot.fuel?.drive, "L", "fuel") : ""}
      ${v.has_elec ? energyKpi("行驶电耗", tot.elec?.drive, "kWh", "elec") : ""}
      ${v.has_elec ? energyKpi("综合电耗", tot.elec?.overall, "kWh", "elec", "含静置") : ""}
      ${v.has_elec ? idleKpi(tot.elec?.idle) : ""}
    </div>
    ${v.has_fuel ? html`<div class="card chart-card"><div class="card-head"><span class="card-title"><i class="lg-dot fuel"></i>油耗趋势</span><span class="card-sub">L/100km · 空心点为估算</span></div>
      <div id="c-fuel" class="chart-box"></div></div>` : ""}
    ${v.has_elec ? html`<div class="card chart-card"><div class="card-head"><span class="card-title"><i class="lg-dot elec"></i>电耗趋势</span><span class="card-sub">kWh/100km · 不含静置段</span></div>
      <div id="c-elec" class="chart-box"></div></div>` : ""}
    <div class="card chart-card"><div class="card-head"><span class="card-title">每公里能源成本</span><span class="card-sub">元/km · 按月</span></div><div id="c-cpk" class="chart-box"></div></div>
    ${comb.length ? html`<div class="section-title"><span>满到满综合段</span><span>${comb.length} 段</span></div>
      <div class="card flush list">${comb.slice().reverse().slice(0, 12).map((s) => html`<button class="list-item seg-row" data-end="${s.end_id}">
        <span class="grow"><span class="rec-title">${fmtDate(s.start_ts)} → ${fmtDate(s.end_ts)} <span class="muted tiny">${km(s.distance)} km</span></span>
          <span class="rec-sub">${s.cost_per_km != null ? `¥${fixed(s.cost_per_km)}/km · ` : ""}${s.equiv_per100 != null ? `等效 ${num(s.equiv_per100)}L · ` : ""}${s.elec_share != null ? `电能 ${num(s.elec_share * 100, 0)}%` : ""}</span></span>
        <span class="rec-right"><b class="num fuel-c">${num(s.per100)} L</b><b class="num elec-c tiny">${s.elec_per100 != null ? num(s.elec_per100) + " kWh" : "–"}</b></span></button>`)}</div>` : ""}
    ${d.charging ? chargingBlock(d.charging) : ""}
    <div class="section-title"><span>能耗分段</span><span class="tiny">点开可以打标签或排除</span></div>
    <div class="card flush list" id="seg-list">${segList(v, d)}</div>`);
  bindSegmented($("#e-range", body), (k) => {
    savePref("energy_range", k);
    energyTab(body, v);
  });
  const lbl = (s) => fmtDate(s.end_ts);
  if (v.has_fuel) lineChart($("#c-fuel", body), { labels: fuelT.map(lbl), series: [{ name: "油耗", color: "fuel", values: fuelT.map((s) => s.per100), hollow: fuelT.map((s) => s.quality !== "A") }], unit: " L", tipLabel: (i) => `${fmtDate(fuelT[i].start_ts)} → ${fmtDate(fuelT[i].end_ts)} · ${km(fuelT[i].distance)}km` });
  if (v.has_elec) lineChart($("#c-elec", body), { labels: elecT.map(lbl), series: [{ name: "电耗", color: "elec", values: elecT.map((s) => s.per100), hollow: elecT.map((s) => s.quality !== "A") }], unit: " kWh", tipLabel: (i) => `${fmtDate(elecT[i].start_ts)} → ${fmtDate(elecT[i].end_ts)} · ${km(elecT[i].distance)}km` });
  const months = d.months.filter((m) => m.mileage > 0);
  lineChart($("#c-cpk", body), { labels: months.map((m) => m.month.slice(2).replace("-", "/")), series: [{ name: "每公里", color: "brand", values: months.map((m) => m.energy_cost_per_km) }], unit: " 元", fmt: (x) => (+x).toFixed(2) });
  if (d.charging) {
    const types = d.charging.types.filter((t) => t.kwh > 0);
    const colors = ["elec", "brand", "fuel", "expense", "snapshot"];
    donut($("#c-ctype", body), { items: types.map((t, i) => ({ label: CHARGE_LABEL[t.type], value: t.kwh, color: colors[i % colors.length] })), size: 140, thickness: 18,
      center: `<b class="num">${num(types.reduce((a, t) => a + t.kwh, 0), 0)}</b><small>kWh</small>` });
    barChart($("#c-soc", body), { labels: d.charging.soc_start.map((_, i) => `${i * 10}`), series: [{ name: "次数", color: "elec", values: d.charging.soc_start }], height: 140, fmt: (x) => Math.round(x) });
  }
  body.querySelectorAll(".seg-row").forEach((b) => b.addEventListener("click", () => openDetail("record", +b.dataset.end)));
}

function energyKpi(label, t, unit, tone, sub = "") {
  return html`<div class="kpi card ${tone}"><div class="kpi-label">${raw(icon(tone === "fuel" ? "fuel" : "bolt", 16))}${label}</div>
    <div class="kpi-value num">${t?.per100 != null ? num(t.per100) : "–"}<small>${unit}/100km</small></div>
    <div class="kpi-foot">${t ? html`<span class="tiny muted">${km(t.distance)} km${sub ? " · " + sub : ""}</span>${qTag(t.quality)}` : html`<span class="tiny muted">还没有可用的段</span>`}</div></div>`;
}

function idleKpi(idle) {
  return html`<div class="kpi card"><div class="kpi-label">${raw(icon("gauge", 16))}静置损耗</div>
    <div class="kpi-value num">${idle ? num(idle.amount, 1) : "0"}<small>kWh</small></div>
    <div class="kpi-foot"><span class="tiny muted">${idle ? `${num(idle.per_day)} kWh/天 · 占 ${num((idle.share || 0) * 100, 1)}%` : "记「状态快照」后单独统计"}</span></div></div>`;
}

function chargingBlock(c) {
  return html`<div class="grid-2 resp-2">
    <div class="card"><div class="card-head"><span class="card-title">充电方式</span><span class="card-sub">按度数</span></div>
      <div class="donut-row"><div id="c-ctype"></div><div class="legend">${c.types.map((t, i) => html`<div class="lg-row"><i class="lg-dot ${["elec", "brand", "fuel", "expense", "snapshot"][i % 5]}"></i><span class="grow">${CHARGE_LABEL[t.type]}</span><span class="num">${num(t.kwh, 0)}</span><span class="muted tiny">${t.avg_price != null ? "¥" + fixed(t.avg_price, 2) + "/度" : ""}</span></div>`)}</div></div></div>
    <div class="card"><div class="card-head"><span class="card-title">开始充电时的电量</span><span class="card-sub">次数分布 %</span></div><div id="c-soc" class="chart-box"></div></div>
  </div>
  <div class="card k-card"><div class="row between"><span class="card-title">${raw(icon("battery", 18))}电量换算</span>${c.k_source === "default" ? html`<span class="badge">按电池容量估算</span>` : c.k_source === "none" ? html`<span class="badge warn">未校准</span>` : html`<span class="badge ok">已按 ${c.samples.length} 次充电校准</span>`}</div>
    <div class="row wrap" style="gap:20px;margin-top:10px">
      <div><span class="tiny muted">每 1% 电量</span><div class="num big-mid">${c.k != null ? fixed(c.k, 3) : "–"} <small>kWh</small></div></div>
      ${c.k_ac != null ? html`<div><span class="tiny muted">慢充</span><div class="num big-mid">${fixed(c.k_ac, 3)}</div></div>` : ""}
      ${c.k_dc != null ? html`<div><span class="tiny muted">快充</span><div class="num big-mid">${fixed(c.k_dc, 3)}</div></div>` : ""}
      ${c.capacity_est != null ? html`<div><span class="tiny muted">可用容量估算</span><div class="num big-mid">${num(c.capacity_est, 1)} <small>kWh</small></div></div>` : ""}
    </div>
    <p class="tiny muted" style="margin-top:8px">按「充电量 ÷ SOC 涨幅」学习，包含充电损耗；容量估算只供参考。</p></div>`;
}

function segList(v, d) {
  const all = [...(d.segments.fuel || []), ...(d.segments.elec || [])].sort((a, b) => (a.end_ts < b.end_ts ? 1 : -1)).slice(0, 40);
  if (!all.length) return String(emptyState({ ico: "chart", title: "还没有能耗段", text: "两次「加满」之间、或两条带电量的记录之间就是一段" }));
  return all.map((s) => {
    const unit = s.energy === "fuel" ? "L" : "kWh";
    const val = s.status !== "ok" ? html`<span class="badge warn">${{ missed: "漏记", negative: "异常", incomplete: "含汇总", uncertain: "误差大" }[s.status] || "–"}</span>`
      : s.kind === "idle" ? html`<b class="num">${num(s.amount)} kWh</b>` : html`<b class="num ${s.energy === "fuel" ? "fuel-c" : "elec-c"}">${num(s.per100)} ${unit}</b>`;
    const tags = [];
    if (s.kind === "idle") tags.push(html`<span class="badge">静置 ${s.per_day != null ? num(s.per_day) + "/天" : ""}</span>`);
    if (s.kind === "short") tags.push(html`<span class="badge">短段</span>`);
    if (s.tag !== "normal") tags.push(html`<span class="badge brand">${SEGMENT_TAGS[s.tag]}</span>`);
    if (s.excluded) tags.push(html`<span class="badge">已排除</span>`);
    if (s.anomaly) tags.push(html`<span class="badge warn">${{ high: "偏高", low: "偏低", parked: "停放" }[s.anomaly]}</span>`);
    return String(html`<button class="list-item seg-row" data-end="${s.end_id}">
      <span class="ico sm ${s.energy === "fuel" ? "fuel" : "elec"}">${raw(icon(s.energy === "fuel" ? "fuel" : "bolt", 16))}</span>
      <span class="grow"><span class="rec-title">${fmtDate(s.start_ts)} → ${fmtDate(s.end_ts)} ${tags}</span><span class="rec-sub">${km(s.distance)} km · ${num(s.days, 1)} 天${s.quality !== "A" ? " · 估算" : ""}</span></span>
      <span class="rec-right">${val}</span></button>`);
  }).join("");
}

/* ---------- 花费 ---------- */

async function costTab(body, v) {
  const rk = pref("cost_range", "12m");
  const scope = pref("cost_scope", "vehicle");
  const from = RANGE_FROM[rk]();
  let d;
  try {
    d = await api(`/api/stats/cost?${scope === "vehicle" ? "vehicle_id=" + v.id + "&" : ""}${from ? "date_from=" + from : ""}`);
  } catch (e) {
    body.innerHTML = String(emptyState({ ico: "warning", title: "加载失败", text: e.message }));
    return;
  }
  const months = d.months || [];
  const hasSummary = months.some((m) => m.summary > 0);
  body.innerHTML = String(html`
    <div class="row wrap between" style="margin-bottom:12px;gap:10px">${rangeSeg("c-range", rk, [["year", "今年"], ["12m", "近一年"], ["all", "全部"]])}
      ${state.vehicles.length > 1 ? html`<div class="segmented" id="c-scope" style="width:200px"><button data-v="vehicle" class="${scope === "vehicle" ? "on" : ""}">当前车辆</button><button data-v="all" class="${scope === "all" ? "on" : ""}">全部车辆</button></div>` : ""}</div>
    <div class="kpi-grid">
      <div class="kpi card"><div class="kpi-label">${raw(icon("wallet", 16))}总花费</div><div class="kpi-value num">${money(d.total_cents, { digits: 0 })}</div><div class="kpi-foot"><span class="tiny muted">${d.from || ""} 起</span></div></div>
      <div class="kpi card"><div class="kpi-label">${raw(icon("coin", 16))}每公里总成本</div><div class="kpi-value num">${d.cost_per_km != null ? "¥" + fixed(d.cost_per_km) : "–"}</div><div class="kpi-foot"><span class="tiny muted">${km(d.mileage)} km</span></div></div>
    </div>
    <div class="card chart-card"><div class="card-head"><span class="card-title">月度花费</span><span class="card-sub">${hasSummary ? "浅色部分含历史汇总分摊" : "元"}</span></div>
      <div id="c-month" class="chart-box"></div>
      <div class="legend inline">${[["fuel", "加油"], ["elec", "充电"], ["maintenance", "保养维修"], ["insurance", "保险"], ["other", "其他"]].map(([k, l]) => html`<span><i class="lg-dot ${CAT_COLOR[k]}"></i>${l}</span>`)}</div></div>
    <div class="grid-2 resp-2">
      <div class="card"><div class="card-head"><span class="card-title">分类</span></div>
        <div class="donut-row"><div id="c-donut"></div>
        <div class="legend">${d.categories.slice(0, 7).map((c) => html`<div class="lg-row"><i class="lg-dot ${catTone(c.key)}"></i><span class="grow">${c.label}</span><span class="num">${money(c.cents, { digits: 0 })}</span><span class="muted tiny">${c.share != null ? num(c.share * 100, 0) + "%" : ""}</span></div>`)}</div></div></div>
      <div class="card chart-card"><div class="card-head"><span class="card-title">累计花费</span></div><div id="c-cum" class="chart-box"></div></div>
    </div>
    ${d.prices?.fuel?.length > 1 ? html`<div class="card chart-card"><div class="card-head"><span class="card-title"><i class="lg-dot fuel"></i>油价走势</span><span class="card-sub">元/L（实付）</span></div><div id="c-pf" class="chart-box"></div></div>` : ""}
    ${d.prices?.elec?.length > 1 ? html`<div class="card chart-card"><div class="card-head"><span class="card-title"><i class="lg-dot elec"></i>电价走势</span><span class="card-sub">元/度（实付）</span></div><div id="c-pe" class="chart-box"></div></div>` : ""}
    ${d.places?.length ? html`<div class="section-title"><span>常去的地点</span></div><div class="card flush list">${d.places.slice(0, 8).map((p) => html`<div class="list-item">
      <span class="ico sm ${p.kind === "fuel" ? "fuel" : "elec"}">${raw(icon(p.kind === "fuel" ? "fuel" : "bolt", 16))}</span><span class="grow">${p.name}<div class="tiny muted">${p.count} 次 · ${money(p.cents, { digits: 0 })}</div></span>
      <span class="num">${p.avg_price != null ? "¥" + fixed(p.avg_price, p.kind === "fuel" ? 2 : 3) : "–"}<small class="muted">/${p.kind === "fuel" ? "L" : "度"}</small></span></div>`)}</div>` : ""}
    ${d.payers?.length > 1 ? html`<div class="section-title"><span>谁付的</span></div><div class="card flush list">${d.payers.map((p) => html`<div class="list-item"><span class="grow">${p.user?.name || "未知"}</span><b class="num">${money(p.cents)}</b></div>`)}</div>` : ""}`);
  bindSegmented($("#c-range", body), (k) => {
    savePref("cost_range", k);
    costTab(body, v);
  });
  const sc = $("#c-scope", body);
  if (sc) bindSegmented(sc, (k) => {
    savePref("cost_scope", k);
    costTab(body, v);
  });
  const labels = months.map((m) => m.month.slice(2).replace("-", "/"));
  const yuan = (x) => (x / 100);
  barChart($("#c-month", body), {
    labels, stacked: true, fmt: (x) => (x >= 1000 ? (x / 1000).toFixed(1) + "k" : Math.round(x)), tipFmt: (x) => "¥" + num(x),
    series: [
      { name: "加油", color: "fuel", values: months.map((m) => yuan(m.fuel)) },
      { name: "充电", color: "elec", values: months.map((m) => yuan(m.elec)) },
      { name: "保养维修", color: "expense", values: months.map((m) => yuan(m.maintenance)) },
      { name: "保险", color: "brand", values: months.map((m) => yuan(m.insurance)) },
      { name: "其他", color: "snapshot", values: months.map((m) => yuan(m.other)) },
    ],
  });
  donut($("#c-donut", body), { items: d.categories.map((c) => ({ label: c.label, value: c.cents, color: catTone(c.key) })), size: 140, thickness: 18,
    center: `<b class="num">${esc(money(d.total_cents, { digits: 0 }))}</b><small>合计</small>` });
  lineChart($("#c-cum", body), { labels, series: [{ name: "累计", color: "brand", values: months.map((m) => yuan(m.cumulative)) }], fmt: (x) => (x >= 1000 ? (x / 1000).toFixed(1) + "k" : Math.round(x)) });
  const priceChart = (id, rows, groups, labelOf) => {
    const box = $(id, body);
    if (!box) return;
    const g = Object.entries(groups).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([k]) => k);
    const ts = [...new Set(rows.map((r) => r.ts.slice(0, 10)))].sort();
    const colors = ["fuel", "brand", "elec"];
    lineChart(box, {
      labels: ts.map((t) => fmtDate(t)), area: g.length === 1, fmt: (x) => (+x).toFixed(2),
      series: g.map((k, i) => ({ name: labelOf(k), color: colors[i], values: ts.map((t) => rows.find((r) => r.group === k && r.ts.startsWith(t))?.price ?? null) })),
    });
  };
  const count = (rows) => rows.reduce((a, r) => ((a[r.group] = (a[r.group] || 0) + 1), a), {});
  if (d.prices?.fuel?.length > 1) priceChart("#c-pf", d.prices.fuel, count(d.prices.fuel), gradeLabel);
  if (d.prices?.elec?.length > 1) priceChart("#c-pe", d.prices.elec, count(d.prices.elec), (k) => CHARGE_LABEL[k] || k);
}

function catTone(key) {
  if (key === "fuel") return "fuel";
  if (key === "elec") return "elec";
  if (["maintenance", "repair", "tires"].includes(key)) return "expense";
  if (key === "insurance") return "brand";
  return "snapshot";
}

/* ---------- 里程 ---------- */

async function mileageTab(body, v) {
  let d;
  try {
    d = await api(`/api/stats/mileage?vehicle_id=${v.id}`);
  } catch (e) {
    body.innerHTML = String(emptyState({ ico: "warning", title: "加载失败", text: e.message }));
    return;
  }
  const months = (d.months || []).slice(-24);
  const thisMonth = months[months.length - 1];
  body.innerHTML = String(html`
    <div class="kpi-grid">
      <div class="kpi card"><div class="kpi-label">${raw(icon("gauge", 16))}当前里程</div><div class="kpi-value num">${km(d.latest_odo)}<small>km</small></div></div>
      <div class="kpi card"><div class="kpi-label">${raw(icon("road", 16))}本月</div><div class="kpi-value num">${km(thisMonth?.km)}<small>km</small></div>
        <div class="kpi-foot">${changeTag(thisMonth?.km, months[months.length - 2]?.km, { neutral: true })}</div></div>
      <div class="kpi card"><div class="kpi-label">${raw(icon("route", 16))}最近日均</div><div class="kpi-value num">${num(d.recent_daily, 1)}<small>km</small></div><div class="kpi-foot"><span class="tiny muted">近 60 天</span></div></div>
      <div class="kpi card"><div class="kpi-label">${raw(icon("history", 16))}长期日均</div><div class="kpi-value num">${num(d.daily_avg, 1)}<small>km</small></div></div>
    </div>
    <div class="card chart-card"><div class="card-head"><span class="card-title">月度里程</span><span class="card-sub">虚线为去年同月</span></div><div id="c-mk" class="chart-box"></div></div>
    <div class="card chart-card"><div class="card-head"><span class="card-title">累计里程</span></div><div id="c-odo" class="chart-box"></div></div>`);
  const hasLy = months.some((m) => m.last_year);
  barChart($("#c-mk", body), {
    labels: months.map((m) => m.month.slice(2).replace("-", "/")),
    series: [{ name: "里程", color: "brand", values: months.map((m) => m.km) }],
    line: hasLy ? { name: "去年同月", color: "snapshot", values: months.map((m) => m.last_year ?? null) } : null,
    unit: " km", fmt: (x) => Math.round(x),
  });
  const pts = d.points || [];
  lineChart($("#c-odo", body), { labels: pts.map((p) => fmtDate(p.ts)), series: [{ name: "里程", color: "brand", values: pts.map((p) => p.odo) }], unit: " km", fmt: (x) => (x >= 10000 ? (x / 10000).toFixed(1) + "万" : Math.round(x)) });
}

/* ---------- 日历 ---------- */

let calMonth = null;

async function calendarTab(body, v) {
  if (!calMonth) calMonth = todayISO().slice(0, 7);
  let d;
  try {
    d = await api(`/api/stats/calendar?vehicle_id=${v.id}&month=${calMonth}`);
  } catch (e) {
    body.innerHTML = String(emptyState({ ico: "warning", title: "加载失败", text: e.message }));
    return;
  }
  const [y, m] = calMonth.split("-").map(Number);
  const first = new Date(y, m - 1, 1);
  const daysIn = new Date(y, m, 0).getDate();
  const lead = (first.getDay() + 6) % 7;
  const maxCents = Math.max(1, ...Object.values(d.days).map((x) => x.cents));
  const today = todayISO();
  const sel = body.dataset.sel && body.dataset.sel.startsWith(calMonth) ? body.dataset.sel : null;
  const cells = [];
  for (let i = 0; i < lead; i++) cells.push(html`<div class="cal-cell empty"></div>`);
  for (let day = 1; day <= daysIn; day++) {
    const key = `${calMonth}-${String(day).padStart(2, "0")}`;
    const info = d.days[key];
    const heat = info ? Math.min(info.cents / maxCents, 1) : 0;
    cells.push(html`<button class="cal-cell ${info ? "has" : ""} ${key === today ? "today" : ""} ${key === sel ? "sel" : ""}" data-day="${key}" style="--heat:${heat.toFixed(2)}">
      <span class="cal-num">${day}</span>
      <span class="cal-dots">${(info?.kinds || []).map((k) => raw(`<i class="lg-dot ${k === "charge" ? "elec" : k === "expense" ? "expense" : k}"></i>`))}</span>
      ${info?.cents ? html`<span class="cal-amt num">${info.cents >= 100000 ? Math.round(info.cents / 100) : num(info.cents / 100, 0)}</span>` : ""}</button>`);
  }
  const rep = d.report.report;
  const prev = d.report.prev;
  body.innerHTML = String(html`
    <div class="cal-layout">
      <div class="card cal-card">
        <div class="cal-head"><button class="icon-btn" data-mv="-1" aria-label="上个月">${raw(icon("left"))}</button>
          <b>${y} 年 ${m} 月</b><button class="icon-btn" data-mv="1" aria-label="下个月">${raw(icon("right"))}</button></div>
        <div class="cal-week">${["一", "二", "三", "四", "五", "六", "日"].map((w) => html`<span>${w}</span>`)}</div>
        <div class="cal-grid">${cells}</div>
        <div class="legend inline tiny"><span><i class="lg-dot fuel"></i>加油</span><span><i class="lg-dot elec"></i>充电</span><span><i class="lg-dot expense"></i>费用</span><span><i class="lg-dot snapshot"></i>快照</span><span class="muted">底色越深花费越多</span></div>
      </div>
      <div class="stack">
        <div id="cal-day"></div>
        <div class="card month-report">
          <div class="card-head"><span class="card-title">${raw(icon("sparkle", 18))}${m} 月月报</span><a class="link-btn" data-link href="/stats/year/${y}">${y} 年度报告 ›</a></div>
          <p class="report-sentence">${d.report.sentence}</p>
          <div class="report-grid">
            ${repItem("里程", km(rep.mileage) + " km", changeTag(rep.mileage, prev.mileage, { neutral: true }))}
            ${repItem("能源花费", money(rep.energy_cents), changeTag(rep.energy_cents, prev.energy_cents))}
            ${repItem("其他花费", money(rep.other_cents), changeTag(rep.other_cents, prev.other_cents))}
            ${repItem("每公里能源", rep.energy_cost_per_km != null ? "¥" + fixed(rep.energy_cost_per_km) : "–", changeTag(rep.energy_cost_per_km, prev.energy_cost_per_km))}
            ${v.has_fuel ? repItem("加油", `${rep.fuel_count} 次 · ${num(rep.fuel_l, 1)} L`) : ""}
            ${v.has_elec ? repItem("充电", `${rep.elec_count} 次 · ${num(rep.elec_kwh, 0)} kWh`) : ""}
            ${v.has_fuel && rep.fuel?.drive ? repItem("平均油耗", num(rep.fuel.drive.per100) + " L") : ""}
            ${v.has_elec && rep.elec?.drive ? repItem("平均电耗", num(rep.elec.drive.per100) + " kWh") : ""}
            ${v.has_elec && rep.home_share != null ? repItem("家充占比", num(rep.home_share * 100, 0) + "%") : ""}
            ${rep.elec?.idle ? repItem("静置损耗", num(rep.elec.idle.amount, 1) + " kWh") : ""}
          </div></div>
      </div></div>`);
  body.querySelectorAll("[data-mv]").forEach((b) => b.addEventListener("click", () => {
    const dd = new Date(y, m - 1 + +b.dataset.mv, 1);
    calMonth = `${dd.getFullYear()}-${String(dd.getMonth() + 1).padStart(2, "0")}`;
    calendarTab(body, v);
  }));
  const showDay = (key) => {
    body.dataset.sel = key;
    $$(".cal-cell", body).forEach((c) => c.classList.toggle("sel", c.dataset.day === key));
    const info = d.days[key];
    const box = $("#cal-day", body);
    box.innerHTML = String(info
      ? html`<div class="section-title" style="margin-top:0"><span>${fmtDate(key, { week: true })}</span><span>${money(info.cents)}</span></div><div class="card flush list">${info.items.map((it) => itemRow(it, { showDate: false }))}</div>`
      : html`<div class="card"><p class="muted small">${fmtDate(key, { week: true })} 没有记录</p></div>`);
    bindRows(box);
  };
  body.querySelector(".cal-grid").addEventListener("click", (e) => {
    const c = e.target.closest("[data-day]");
    if (c) showDay(c.dataset.day);
  });
  const lastDay = Object.keys(d.days).sort().pop();
  showDay(sel || (calMonth === today.slice(0, 7) && d.days[today] ? today : lastDay || (calMonth === today.slice(0, 7) ? today : `${calMonth}-01`)));
}

function repItem(label, value, delta = "") {
  return html`<div class="rep-item"><span class="tiny muted">${label}</span><b class="num">${value}</b>${delta}</div>`;
}

/* ---------- 年度报告 ---------- */

export async function yearPage({ params }) {
  resetView();
  setNav("stats");
  const year = +params.year || new Date().getFullYear();
  setTitle(`${year} 年度报告`);
  const v = currentVehicle();
  const el = view();
  if (!v) return navigate("/");
  el.innerHTML = String(skeleton(3, 160));
  listen("vehicle-changed", () => yearPage({ params }));
  let d;
  try {
    d = await api(`/api/stats/yearly-report?vehicle_id=${v.id}&year=${year}`);
  } catch (e) {
    el.innerHTML = String(emptyState({ ico: "warning", title: "加载失败", text: e.message }));
    return;
  }
  const r = d.report;
  const top = d.top_item;
  el.innerHTML = String(html`
    <div class="chips" style="margin-bottom:14px">${d.years.map((yy) => html`<a class="chip sm ${yy === year ? "on" : ""}" data-link href="/stats/year/${yy}">${yy}</a>`)}</div>
    <div class="year-hero">
      <div class="year-badge">${year}</div>
      <h2>${v.name} 的一年</h2>
      <div class="year-big">
        <div><b class="num">${km(r.mileage)}</b><span>公里</span></div>
        <div><b class="num">${money(r.total_cents, { digits: 0 })}</b><span>总花费</span></div>
        <div><b class="num">${r.total_cost_per_km != null ? "¥" + fixed(r.total_cost_per_km) : "–"}</b><span>每公里</span></div>
      </div></div>
    <div class="kpi-grid" style="margin-top:14px">
      ${v.has_fuel ? html`<div class="kpi card fuel"><div class="kpi-label">${raw(icon("fuel", 16))}加油</div><div class="kpi-value num">${r.fuel_count}<small>次</small></div><div class="kpi-foot"><span class="tiny muted">${num(r.fuel_l, 0)} L · ${money(r.fuel_cents, { digits: 0 })}</span></div></div>` : ""}
      ${v.has_elec ? html`<div class="kpi card elec"><div class="kpi-label">${raw(icon("bolt", 16))}充电</div><div class="kpi-value num">${r.elec_count}<small>次</small></div><div class="kpi-foot"><span class="tiny muted">${num(r.elec_kwh, 0)} kWh · ${money(r.elec_cents, { digits: 0 })}</span></div></div>` : ""}
      ${v.has_fuel ? html`<div class="kpi card"><div class="kpi-label">平均油耗</div><div class="kpi-value num">${r.fuel?.drive ? num(r.fuel.drive.per100) : "–"}<small>L</small></div></div>` : ""}
      ${v.has_elec ? html`<div class="kpi card"><div class="kpi-label">平均电耗</div><div class="kpi-value num">${r.elec?.drive ? num(r.elec.drive.per100) : "–"}<small>kWh</small></div></div>` : ""}
    </div>
    <div class="card chart-card"><div class="card-head"><span class="card-title">每月里程与花费</span></div><div id="y-chart" class="chart-box"></div></div>
    <div class="grid-2 resp-2">
      ${d.best_month ? html`<div class="card highlight"><span class="tiny muted">跑得最多的月份</span><b class="num">${+d.best_month.month.slice(5)} 月 · ${km(d.best_month.mileage)} km</b></div>` : ""}
      ${top ? html`<div class="card highlight"><span class="tiny muted">最贵的一笔</span><b>${top.type === "expense" ? EXPENSE_LABEL[top.category] + (top.title ? " · " + top.title : "") : top.kind === "fuel" ? "加油" : "充电"} · <span class="num">${money(top.type === "expense" ? top.amount_cents : top.paid_cents)}</span></b><span class="tiny muted">${fmtDate(top.occurred_at, { year: true })}</span></div>` : ""}
      ${r.elec?.idle ? html`<div class="card highlight"><span class="tiny muted">全年静置损耗</span><b class="num">${num(r.elec.idle.amount, 0)} kWh</b><span class="tiny muted">约占用电 ${num((r.elec.idle.share || 0) * 100, 0)}%</span></div>` : ""}
      ${r.home_share != null ? html`<div class="card highlight"><span class="tiny muted">家充占比</span><b class="num">${num(r.home_share * 100, 0)}%</b></div>` : ""}
    </div>`);
  barChart($("#y-chart", el), {
    labels: d.months.map((m) => +m.month.slice(5) + "月"),
    series: [{ name: "里程 km", color: "brand", values: d.months.map((m) => m.mileage) }],
    line: { name: "花费 ¥", color: "fuel", values: d.months.map((m) => (m.total_cents ? m.total_cents / 100 : null)) },
    fmt: (x) => Math.round(x),
  });
}
