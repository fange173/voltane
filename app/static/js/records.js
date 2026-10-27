/* records.js — 时间线（记录 + 费用）、条目渲染、详情面板（含能耗算式与段标签） */
import { CATEGORY_ICON, icon } from "./icons.js";
import { pending } from "./outbox.js";
import { navigate } from "./router.js";
import { listen, resetView, setNav, setTitle, view } from "./shell.js";
import {
  chargeLabel,
  EXPENSE_LABEL,
  SEGMENT_TAGS,
  SNAPSHOT_TAGS,
  canEdit,
  currentVehicle,
  emit,
  fuelLevelText,
  gradeLabel,
  state,
} from "./state.js";
import { avatar, confirmDialog, emptyState, qTag, sheet, skeleton, toast, toastError } from "./ui.js";
import { $, $$, api, debounce, esc, fixed, fmtDate, html, km, money, num, raw, relDay } from "./util.js";

/* ---------- 条目 ---------- */

export function itemTitle(it) {
  if (it.type === "expense") return (EXPENSE_LABEL[it.category] || "费用") + (it.title ? ` · ${it.title}` : "");
  if (it.kind === "fuel") return `${gradeLabel(it.fuel_grade) || "加油"} ${it.fuel_liters != null ? num(it.fuel_liters) + "L" : ""}`.trim();
  if (it.kind === "charge") {
    if (it.status === "in_progress") return `${chargeLabel(it.charge_type) || "充电"} · 充电中`;
    return `${chargeLabel(it.charge_type) || "充电"} ${it.charge_kwh != null ? num(it.charge_kwh) + "kWh" : ""}`.trim();
  }
  const parts = [];
  if (it.soc_before_pct != null) parts.push(`电 ${num(it.soc_before_pct, 0)}%`);
  if (it.fuel_level_before_pct != null) parts.push(`油 ${fuelLevelText(it.fuel_level_before_pct, state.vehicles.find((x) => x.id === it.vehicle_id))}`);
  return `${SNAPSHOT_TAGS[it.snapshot_tag] || "快照"}${parts.length ? " · " + parts.join(" ") : ""}`;
}

export function itemIcon(it) {
  if (it.type === "expense") return raw(`<span class="ico expense">${icon(CATEGORY_ICON[it.category] || "receipt", 20)}</span>`);
  const map = { fuel: ["fuel", "fuel"], charge: ["elec", "bolt"], snapshot: ["snapshot", "gauge"] };
  const [tone, ic] = map[it.kind];
  return raw(`<span class="ico ${tone}">${icon(ic, 20)}</span>`);
}

function badgeHtml(b) {
  const unit = b.energy === "elec" ? "kWh" : b.energy === "combined" ? "L 折合" : "L";
  if (b.kind === "idle" && b.per_day != null) return html`<span class="badge ${b.anomaly ? "warn" : ""}">${b.anomaly ? raw("●") : ""}静置 ${num(b.per_day)} kWh/天</span>`;
  if (b.per100 == null) return "";
  const cls = b.anomaly ? "warn" : b.energy === "fuel" ? "fuel" : "elec";
  return html`<span class="badge ${cls}">${b.anomaly ? raw("●") : ""}${num(b.per100)} ${unit}${b.quality !== "A" ? "·估" : ""}</span>`;
}

export function itemRow(it, { showDate = true } = {}) {
  const sub = [];
  if (showDate) sub.push(relDay(it.occurred_at) + " " + it.occurred_at.slice(11, 16));
  else sub.push(it.occurred_at.slice(11, 16));
  if (it.odometer_km != null && !it.odometer_estimated) sub.push(km(it.odometer_km) + " km");
  else if (it.type === "record" && it.odometer_estimated) sub.push("里程估算");
  if (it.place_name) sub.push(it.place_name);
  if (it.type === "expense" && it.vendor) sub.push(it.vendor);
  const cents = it.amount_cents;
  const tags = [];
  if (it.kind === "fuel" && it.fuel_is_full) tags.push(html`<span class="badge fuel">满</span>`);
  if (it.source === "backfill" || it.source === "import") tags.push(html`<span class="badge">补录</span>`);
  if (it.source === "auto_fix") tags.push(html`<span class="badge warn">自动补</span>`);
  if (it.pending) tags.push(html`<span class="badge warn">待同步</span>`);
  return html`<button class="list-item rec-row ${it.pending ? "pending" : ""}" data-type="${it.type}" data-id="${it.id ?? ""}">
    ${itemIcon(it)}
    <span class="grow"><span class="rec-title">${itemTitle(it)} ${tags}</span>
      <span class="rec-sub">${sub.join(" · ")}</span></span>
    <span class="rec-right">${cents != null ? html`<b class="num">${money(cents)}</b>` : html`<b class="faint">–</b>`}
      <span class="row" style="gap:4px;justify-content:flex-end">${(it.badges || []).map(badgeHtml)}${avatar(it.created_by, "sm")}</span></span>
  </button>`;
}

/** 绑定列表点击 → 打开详情。 */
export function bindRows(root) {
  root.addEventListener("click", (e) => {
    const r = e.target.closest(".rec-row");
    if (!r || !r.dataset.id) return;
    openDetail(r.dataset.type, +r.dataset.id);
  });
}

/* ---------- 时间线页 ---------- */

const FILTERS = [
  ["", "全部"],
  ["fuel", "加油"],
  ["charge", "充电"],
  ["snapshot", "快照"],
  ["expense", "费用"],
  ["mine", "我记的"],
  ["anomaly", "有异常"],
  ["estimated", "补录/估算"],
];

let filter = "";
let query = "";

export async function recordsPage() {
  resetView();
  setNav("records");
  setTitle("记录");
  const v = currentVehicle();
  const el = view();
  if (!v) return navigate("/");
  const filters = FILTERS.filter(([k]) => (k === "fuel" ? v.has_fuel : k === "charge" ? v.has_elec : true));
  el.innerHTML = String(html`
    <div class="toolbar">
      <div class="chips" id="rec-filters">${filters.map(([k, l]) => html`<button class="chip sm ${k === filter ? "on" : ""}" data-v="${k}">${l}</button>`)}</div>
      <div class="search-box"><span class="muted">${raw(icon("search", 18))}</span><input class="input" id="rec-q" placeholder="搜索地点、备注" value="${query}"></div>
    </div>
    <div id="rec-list">${skeleton(6, 64)}</div>`);
  $("#rec-filters").addEventListener("click", (e) => {
    const c = e.target.closest("[data-v]");
    if (!c) return;
    filter = c.dataset.v;
    $$("#rec-filters .chip").forEach((x) => x.classList.toggle("on", x === c));
    load(true);
  });
  $("#rec-q").addEventListener("input", debounce((e) => {
    query = e.target.value.trim();
    load(true);
  }, 300));
  bindRows($("#rec-list"));
  let items = [];
  let next = null;
  const load = async (reset = false) => {
    if (reset) {
      items = [];
      next = null;
    }
    const p = new URLSearchParams({ vehicle_id: v.id, limit: 60 });
    if (["fuel", "charge", "snapshot", "expense", "estimated"].includes(filter)) p.set("kind", filter);
    if (filter === "mine") p.set("mine", "true");
    if (filter === "anomaly") p.set("anomaly", "true");
    if (query) p.set("q", query);
    if (next) p.set("before", next);
    try {
      const r = await api("/api/records?" + p);
      items = items.concat(r.items);
      next = r.next;
      draw();
    } catch (e) {
      $("#rec-list").innerHTML = String(emptyState({ ico: "warning", title: "加载失败", text: e.message }));
    }
  };
  const draw = () => {
    const list = $("#rec-list");
    if (!list) return;
    const pend = filter ? [] : pending().filter((x) => x.vehicle_id === v.id).map((x) => ({
      ...x.body, type: x.url.includes("expenses") ? "expense" : "record", pending: true, id: null,
    }));
    const all = pend.concat(items);
    if (!all.length) {
      list.innerHTML = String(emptyState({
        ico: "list",
        title: filter || query ? "没有符合条件的记录" : "还没有记录",
        text: filter || query ? "换个筛选条件试试" : "点下方「＋」记第一笔，建议从一次「加满」或「状态快照」开始",
      }));
      return;
    }
    const groups = new Map();
    for (const it of all) {
      const m = it.occurred_at.slice(0, 7);
      if (!groups.has(m)) groups.set(m, []);
      groups.get(m).push(it);
    }
    list.innerHTML = String(html`${[...groups].map(([m, arr]) => html`
      <section class="month-group">
        <div class="month-head"><b>${m.replace("-", " 年 ")} 月</b><span>${monthSummary(arr)}</span></div>
        <div class="card flush list">${arr.map((it) => itemRow(it))}</div>
      </section>`)}
      ${next ? html`<button class="btn btn-block btn-ghost" id="more">加载更多</button>` : html`<p class="end-mark">— 共 ${all.length} 条 —</p>`}`);
    $("#more")?.addEventListener("click", () => load());
  };
  listen("data-changed", () => load(true));
  listen("outbox-changed", draw);
  listen("vehicle-changed", () => recordsPage());
  await load(true);
}

function monthSummary(arr) {
  let cents = 0;
  let fuel = 0;
  let kwh = 0;
  const odos = [];
  for (const it of arr) {
    cents += (it.amount_cents) || 0;
    if (it.kind === "fuel") fuel += it.fuel_liters || 0;
    if (it.kind === "charge") kwh += it.charge_kwh || 0;
    if (it.odometer_km != null && !it.odometer_estimated) odos.push(it.odometer_km);
  }
  const parts = [money(cents)];
  if (fuel) parts.push(num(fuel, 1) + "L");
  if (kwh) parts.push(num(kwh, 0) + "kWh");
  if (odos.length > 1) parts.push(km(Math.max(...odos) - Math.min(...odos)) + "km");
  return parts.join(" · ");
}

/* ---------- 详情 ---------- */

export async function openDetail(type, id) {
  let d;
  try {
    d = await api(type === "expense" ? `/api/expenses/${id}` : `/api/records/${id}`);
  } catch (e) {
    return toastError(e);
  }
  d.type = type;
  const v = state.vehicles.find((x) => x.id === d.vehicle_id) || currentVehicle();
  sheet({
    title: type === "expense" ? "费用详情" : d.kind === "fuel" ? "加油详情" : d.kind === "charge" ? "充电详情" : "快照详情",
    render(body, ctx) {
      body.innerHTML = String(detailHtml(d, v));
      const btns = [];
      if (d.status === "in_progress" && d.can_edit) btns.push(`<button class="btn btn-elec grow" data-finish>${icon("check", 18)}结束充电</button>`);
      if (d.can_edit) btns.push(`<button class="btn grow" data-edit>${icon("edit", 18)}编辑</button>`);
      if (d.can_delete) btns.push(`<button class="btn btn-danger" data-del aria-label="删除">${icon("trash", 18)}</button>`);
      ctx.setFoot(btns.join(""));
      $("[data-edit]", ctx.foot)?.addEventListener("click", async () => {
        ctx.close();
        const { openRecordForm } = await import("./record-form.js");
        setTimeout(() => openRecordForm({ record: d, vehicle: v }), 200);
      });
      $("[data-finish]", ctx.foot)?.addEventListener("click", async () => {
        ctx.close();
        const { openRecordForm } = await import("./record-form.js");
        setTimeout(() => openRecordForm({ record: d, vehicle: v, finish: true }), 200);
      });
      $("[data-del]", ctx.foot)?.addEventListener("click", async () => {
        const ok = await confirmDialog({ title: "删除这条记录？", message: "删除后会进入回收站，30 天内可以恢复。", ok: "删除", danger: true });
        if (!ok) return;
        try {
          await api(type === "expense" ? `/api/expenses/${id}` : `/api/records/${id}`, { method: "DELETE" });
          ctx.close();
          emit("data-changed");
          toast("已删除", { action: { label: "撤销", onClick: async () => {
            await api(type === "expense" ? `/api/expenses/${id}/restore` : `/api/records/${id}/restore`, { method: "POST" });
            emit("data-changed");
          } } });
        } catch (e) {
          toastError(e);
        }
      });
      bindSegmentMarks(body, d, v);
    },
  });
}

function detailHtml(d, v) {
  const cents = d.amount_cents;
  const kv = [];
  const add = (k, val) => val != null && val !== "" && kv.push([k, val]);
  add("时间", fmtDate(d.occurred_at, { time: true, year: true, week: true }));
  add("里程", d.odometer_km != null ? (d.odometer_estimated ? "估算 " : "") + km(d.odometer_km) + " km" : "未填（按前后记录推算）");
  if (d.type === "expense") {
    add("分类", EXPENSE_LABEL[d.category]);
    add("名称", d.title);
    add("商家", d.vendor);
    for (const s of d.sub_items || []) add(s.name, money(s.cents));
    if (d.period_start) add("保险期间", `${d.period_start} 至 ${d.period_end || "–"}`);
  } else {
    if (d.kind === "fuel") {
      add("标号", gradeLabel(d.fuel_grade));
      add("加油量", d.fuel_liters != null ? num(d.fuel_liters) + " L" + (d.estimated_fields?.includes("fuel_l") ? "（推算）" : "") : null);
      add("单价", d.fuel_unit_price != null ? fixed(d.fuel_unit_price) + " 元/L" : null);
      add("加满", d.fuel_is_full ? "是" : "否");
      add("加油前油量", fuelLevelText(d.fuel_level_before_pct, v) || null);
      add("加油后油量", fuelLevelText(d.fuel_level_after_pct, v) || null);
      add("当前电量", d.soc_before_pct != null ? num(d.soc_before_pct, 0) + "%" : null);
    }
    if (d.kind === "charge") {
      add("方式", chargeLabel(d.charge_type));
      add("电量", d.soc_before_pct != null || d.soc_after_pct != null ? `${d.soc_before_pct != null ? num(d.soc_before_pct, 0) + "%" : "?"} → ${d.soc_after_pct != null ? num(d.soc_after_pct, 0) + "%" : d.charge_to_limit ? "上限" : "?"}` : null);
      add("充电量", d.charge_kwh != null ? num(d.charge_kwh) + " kWh" + (d.estimated_fields?.includes("charge_kwh") ? "（推算）" : "") : null);
      add("单价", d.charge_unit_price != null ? fixed(d.charge_unit_price, 3) + " 元/度" : null);
      add("时长", d.charge_minutes != null ? Math.floor(d.charge_minutes / 60) + "小时" + (d.charge_minutes % 60) + "分" : null);
      if (d.energy_fee_cents != null) add("电费 / 服务费", `${money(d.energy_fee_cents)} / ${money(d.service_fee_cents)}`);
      add("当前油量", fuelLevelText(d.fuel_level_before_pct, v) || null);
    }
    if (d.kind === "snapshot") {
      add("标签", SNAPSHOT_TAGS[d.snapshot_tag]);
      add("电量", d.soc_before_pct != null ? num(d.soc_before_pct, 0) + "%" : null);
      add("油量", fuelLevelText(d.fuel_level_before_pct, v) || null);
    }
    if (d.extra_fee_cents) add("附加费", money(d.extra_fee_cents));
    add("地点", d.place_name);
    if (d.missed_before) add("漏记", "上次补能没记");
  }
  add("备注", d.note);
  const who = d.created_by ? html`${avatar(d.created_by, "sm")} ${d.created_by.name} 记录${d.updated_by && d.updated_by.id !== d.created_by.id ? html` · ${d.updated_by.name} 修改过` : ""}` : "";
  return html`
    <div class="detail-hero">${itemIcon(d)}<div class="grow"><div class="detail-title">${itemTitle(d)}</div>
      <div class="tiny muted row">${who}</div></div>
      ${cents != null ? html`<div class="detail-amount num">${money(cents)}</div>` : ""}</div>
    ${dedupeSegments(d.segments || []).map((s) => segmentCard(s, d, v))}
    <div class="card" style="margin-top:12px">${kv.map(([k, val]) => html`<div class="kv"><span>${k}</span><span>${val}</span></div>`)}</div>`;
}

/** 综合段和同区间的油段重复时只显示综合段。 */
function dedupeSegments(segs) {
  const comb = segs.filter((s) => s.energy === "combined");
  return segs.filter((s) => !(s.energy === "fuel" && comb.some((c) => c.start_id === s.start_id && c.end_id === s.end_id)));
}

function fmtVal(x, unit) {
  return `${num(x)}${unit === "L" ? "L" : "kWh"}`;
}

function segmentCard(s, d, v) {
  const isComb = s.energy === "combined";
  const unit = s.energy === "fuel" || isComb ? "L" : "kWh";
  const title = isComb ? "本段综合能耗" : s.energy === "fuel" ? "本段油耗" : s.kind === "idle" ? "本段静置耗电" : "本段电耗";
  const head = s.status !== "ok"
    ? html`<span class="badge warn">${{ missed: "有漏记，未计算", negative: "数据异常（消耗为负）", incomplete: "期间有汇总补能，不单独计算", uncertain: "误差太大，不显示" }[s.status] || "无法计算"}</span>`
    : s.kind === "idle"
      ? html`<b class="num seg-big">${num(s.amount)} <small>kWh</small></b><span class="muted small">${num(s.days, 1)} 天 · ${num(s.per_day)} kWh/天</span>`
      : isComb
        ? html`<b class="num seg-big fuel-c">${num(s.per100)} <small>L</small></b><b class="num seg-big elec-c">${s.elec_per100 != null ? num(s.elec_per100) : "–"} <small>kWh</small></b><span class="muted small">/100km</span>`
        : html`<b class="num seg-big ${s.energy === "fuel" ? "fuel-c" : "elec-c"}">${num(s.per100)} <small>${unit}/100km</small></b>${qTag(s.quality, s.err100)}`;
  const parts = s.explain?.parts || [];
  const formula = parts.map((p) => {
    if (p.text) return html`<div>${p.text}</div>`;
    const kText = p.k ? html`<span class="faint">（每 1% 电量 ≈ ${fixed(p.k, 3)} kWh）</span>` : "";
    return html`<div><b>${p.energy === "fuel" ? "油" : "电"}</b>：起点 ${p.start}（${fmtVal(p.start_value, p.unit)}）− 终点 ${p.end}（${fmtVal(p.end_value, p.unit)}）+ 期间补能 ${fmtVal(p.added, p.unit)} = <b>${fmtVal(p.amount, p.unit)}</b> ${kText}</div>`;
  });
  const canMark = !isComb && d.can_edit;
  return html`<div class="card seg-card ${s.energy}">
    <div class="row between"><span class="card-title">${title}</span><span class="tiny muted">${fmtDate(s.start_ts)} → ${fmtDate(s.end_ts)} · ${km(s.distance)} km</span></div>
    <div class="row wrap seg-head">${head}${s.anomaly ? html`<span class="badge warn">${{ high: "偏高", low: "偏低", parked: "疑似停放耗电", idle_high: "停车耗电偏高" }[s.anomaly]}</span>` : ""}</div>
    ${isComb && s.status === "ok" ? html`<div class="row wrap tiny muted" style="gap:12px">
      ${s.cost_per_km != null ? html`<span>每公里 ¥${fixed(s.cost_per_km)}</span>` : ""}
      ${s.equiv_per100 != null ? html`<span>等效油耗 ${num(s.equiv_per100)} L</span>` : ""}
      ${s.elec_share != null ? html`<span>电能占比 ${num(s.elec_share * 100, 0)}%</span>` : ""}</div>` : ""}
    ${parts.length ? html`<div class="formula">${formula}<div>÷ ${km(s.distance)} km × 100</div></div>` : ""}
    ${canMark ? html`<div class="seg-marks" data-energy="${s.energy}" data-end="${s.end_id}">
      <div class="chips wrap">${Object.entries(SEGMENT_TAGS).map(([k, l]) => html`<button class="chip sm ${s.tag === k ? "on" : ""}" data-tag="${k}">${l}</button>`)}</div>
      <label class="mini-check"><input type="checkbox" data-excl ${s.excluded ? "checked" : ""}> 不计入行驶能耗（仍计入综合能耗和花费）</label></div>` : ""}
  </div>`;
}

function bindSegmentMarks(body, d, v) {
  $$(".seg-marks", body).forEach((box) => {
    const send = async (tag, excluded) => {
      try {
        await api("/api/segments", { method: "PATCH", body: { vehicle_id: v.id, end_record_id: +box.dataset.end, energy: box.dataset.energy, tag, excluded } });
        emit("data-changed");
        toast("已更新这段的标签");
      } catch (e) {
        toastError(e);
      }
    };
    box.addEventListener("click", (e) => {
      const c = e.target.closest("[data-tag]");
      if (!c) return;
      $$("[data-tag]", box).forEach((x) => x.classList.toggle("on", x === c));
      send(c.dataset.tag, $("[data-excl]", box).checked);
    });
    $("[data-excl]", box).addEventListener("change", (e) => send($("[data-tag].on", box)?.dataset.tag || "normal", e.target.checked));
  });
}

export function canEditCurrent() {
  return canEdit(currentVehicle());
}
