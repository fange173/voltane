/* record-form.js — 加油 / 充电 / 状态快照 / 费用 表单
   自动推算规则（方案 4.10.3）：字段为空时才自动填，填了以用户为准；自动填的数量记入 estimated_fields。 */
import { CATEGORY_ICON, icon } from "./icons.js";
import { enqueue } from "./outbox.js";
import {
  CHARGE_LABEL,
  EXPENSE_LABEL,
  FUEL_GRADES,
  SNAPSHOT_TAGS,
  currentVehicle,
  emit,
  gradeLabel,
  pref,
  savePref,
  state,
} from "./state.js";
import { bindChips, confirmDialog, setLoading, sheet, toast, toastError } from "./ui.js";
import {
  $,
  $$,
  api,
  durationText,
  esc,
  evalAmount,
  fixed,
  fmtDate,
  html,
  km,
  localISO,
  money,
  num,
  parseLocal,
  raw,
  store,
  toNum,
  todayISO,
  uuid,
} from "./util.js";

const KIND_TITLE = { fuel: "加油", charge: "充电", snapshot: "状态快照", expense: "记一笔" };
// 自动推算的字段 → 引擎里的估算字段名
const EST_NAME = {
  fuel_liters: "fuel_l",
  fuel_level_before_pct: "fuel_before_pct",
  fuel_level_after_pct: "fuel_after_pct",
  charge_kwh: "charge_kwh",
  soc_after_pct: "soc_after",
  soc_before_pct: "soc_before",
};

/* ---------- 字段渲染 ---------- */

function numField(name, label, unit, { ph = "", hint = "", mode = "decimal", cls = "" } = {}) {
  return html`<div class="field ${cls}" data-f="${name}"><label>${label}</label>
    <div class="input-wrap"><input class="input num" name="${name}" inputmode="${mode}" autocomplete="off" placeholder="${ph}">
    <span class="auto-tag hidden">自动</span><span class="unit">${unit}</span></div>
    ${hint ? html`<span class="hint">${hint}</span>` : ""}</div>`;
}

function levelField(name, label, { tone = "elec", hint = "" } = {}) {
  return html`<div class="field level-field" data-f="${name}" data-tone="${tone}"><label>${label}<span class="lv-val num"></span></label>
    <div class="level-row"><input type="range" min="0" max="100" step="5" value="0" class="lv-range unset" aria-label="${label}">
      <div class="input-wrap lv-num"><input class="input num" name="${name}" inputmode="decimal" placeholder="–"><span class="auto-tag hidden">自动</span><span class="unit">%</span></div></div>
    ${hint ? html`<span class="hint">${hint}</span>` : ""}</div>`;
}

function gaugeField(name, label, v) {
  if (v.fuel_gauge_mode === "bars" && v.fuel_gauge_bars) {
    const n = v.fuel_gauge_bars;
    return html`<div class="field gauge-field" data-f="${name}" data-bars="${n}"><label>${label}<span class="lv-val num"></span></label>
      <div class="bars">${Array.from({ length: n }, (_, i) => raw(`<button type="button" data-b="${i + 1}"></button>`))}</div>
      <input type="hidden" name="${name}"><span class="hint">点格子：亮了几格就点第几格，再点一次清除</span></div>`;
  }
  if (!v.tank_capacity_l) return levelField(name, label, { tone: "fuel" });
  // 知道油箱容积时可以在百分比和升之间切换，存的始终是百分比
  const unit = fuelUnit(v);
  return html`<div class="field level-field ${unit === "liters" ? "unit-liters" : ""}" data-f="${name}" data-tone="fuel" data-liters="1" data-tank="${v.tank_capacity_l}">
    <label>${label}<span class="lv-val num"></span><span class="unit-toggle"><button type="button" data-unit="pct">%</button><button type="button" data-unit="liters">L</button></span></label>
    <div class="level-row"><input type="range" min="0" max="100" step="5" value="0" class="lv-range unset" aria-label="${label}">
      <div class="input-wrap lv-num"><input class="input num" name="${name}" inputmode="decimal" placeholder="–"><span class="auto-tag hidden">自动</span><span class="unit">%</span></div></div>
    <div class="input-wrap lit-wrap"><input class="input num" data-lit inputmode="decimal" placeholder="仪表显示的剩余升数"><span class="unit">L</span></div></div>`;
}

/** 油量输入单位：上次在表单里选的，没选过按车辆的油表显示方式。 */
function fuelUnit(v) {
  return pref("fuel_unit", v.fuel_gauge_mode === "liters" ? "liters" : "pct");
}

function placeChips(places, kind) {
  const top = places.slice(0, 6);
  const geo = window.isSecureContext && "geolocation" in navigator;
  return html`<div class="field" data-f="place"><label>地点
      ${geo ? html`<button type="button" class="link-btn tiny" data-geo style="margin-left:auto">${raw(icon("pin", 14))} 定位</button>` : ""}</label>
    <div class="chips" data-place-chips>
      <button type="button" class="chip sm on" data-v="">不记录</button>
      ${top.map((p) => html`<button type="button" class="chip sm" data-v="${p.id}" data-name="${p.name}">${raw(icon(p.kind === "home_charger" ? "home" : p.kind === "gas_station" ? "fuel" : p.kind === "charge_station" ? "bolt" : "pin", 14))}${p.name}</button>`)}
      <button type="button" class="chip sm" data-v="__other">${raw(icon("plus", 14))}其他…</button>
    </div>
    <div class="place-save hidden"><button type="button" class="link-btn tiny" data-save-place></button></div></div>`;
}

function timeField(value) {
  return html`<div class="field" data-f="time"><label>时间</label>
    <div class="chips" data-time-chips>
      <button type="button" class="chip sm" data-t="now">现在</button>
      <button type="button" class="chip sm" data-t="h1">1 小时前</button>
      <button type="button" class="chip sm" data-t="morning">今天早上</button>
      <button type="button" class="chip sm" data-t="yesterday">昨天</button>
    </div>
    <input class="input" type="datetime-local" name="occurred_at" value="${value}" max="${localISO(new Date(Date.now() + 86400000))}"></div>`;
}

function odoField(prefill) {
  const last = prefill?.last_odo;
  return html`<div class="field" data-f="odo"><label>里程<span class="odo-delta muted tiny" style="margin-left:auto"></span></label>
    <div class="input-wrap"><input class="input num big-input" name="odometer_km" inputmode="numeric" placeholder="${last != null ? "上次 " + km(last) : "仪表盘总里程"}"><span class="unit">km</span></div>
    <div class="row wrap tiny">
      <label class="mini-check"><input type="checkbox" name="trip_mode"> 按本次行驶输入</label>
      <label class="mini-check"><input type="checkbox" name="odo_unknown"> 不确定里程</label>
    </div></div>`;
}

function moneyTriple(kind) {
  const q = kind === "fuel" ? "fuel_liters" : "charge_kwh";
  const p = kind === "fuel" ? "fuel_unit_price" : "charge_unit_price";
  return html`<div class="grid-3 triple">
    ${numField(q, kind === "fuel" ? "加油量" : "充电量", kind === "fuel" ? "L" : "kWh")}
    ${numField("amount", "花费金额", "元", { mode: "text" })}
    ${kind === "fuel" ? numField(p, "单价", "元/L") : html`<div class="field" data-f="${p}"><label>单价</label>
      <div class="input-wrap"><input class="input num derived" name="${p}" readonly tabindex="-1" placeholder="自动计算"><span class="unit">元/度</span></div></div>`}
  </div>`;
}

/* ---------- 主入口 ---------- */

/**
 * 打开记录表单。record 为编辑对象（含 type=record/expense）；finish=true 表示结束一条「充电中」。
 */
export async function openRecordForm({ kind = "fuel", record = null, finish = false, vehicle = null, preset = {} } = {}) {
  const v = vehicle || (record ? state.vehicles.find((x) => x.id === record.vehicle_id) : currentVehicle());
  if (!v) return;
  if (record) kind = record.type === "expense" ? "expense" : record.kind;
  const isEdit = !!record;
  const kinds = [v.has_fuel && "fuel", v.has_elec && "charge", "snapshot", "expense"].filter(Boolean);
  if (!kinds.includes(kind)) kind = kinds[0];

  let ctxRef;
  const st = { kind, prefill: null, place: null, est: new Set(), estSticky: new Set(), lastEdited: [], time: null, record };

  await sheet({
    title: isEdit ? (finish ? "结束充电" : "编辑" + KIND_TITLE[kind]) : KIND_TITLE[kind],
    render(body, ctx) {
      ctxRef = ctx;
      body.innerHTML = String(html`
        ${!isEdit ? html`<div class="segmented kind-seg" style="margin-bottom:14px">${kinds.map((k) =>
          html`<button type="button" data-v="${k}" data-tone="${k === "charge" ? "elec" : k === "expense" ? "expense" : k}" class="${k === kind ? "on" : ""}">${raw(icon(k === "fuel" ? "fuel" : k === "charge" ? "bolt" : k === "snapshot" ? "gauge" : "receipt", 16))}${KIND_TITLE[k] === "记一笔" ? "费用" : KIND_TITLE[k] === "状态快照" ? "快照" : KIND_TITLE[k]}</button>`)}</div>` : ""}
        <form class="rec-form stack" autocomplete="off" novalidate></form>`);
      const seg = $(".kind-seg", body);
      if (seg) {
        seg.addEventListener("click", (e) => {
          const b = e.target.closest("button[data-v]");
          if (!b || b.dataset.v === st.kind) return;
          $$("button", seg).forEach((x) => x.classList.toggle("on", x === b));
          const keep = snapshotCommon(body);
          st.kind = b.dataset.v;
          ctx.setTitle(KIND_TITLE[st.kind]);
          renderForm(body, ctx, v, st, { record: null, keep, preset: {} });
        });
      }
      renderForm(body, ctx, v, st, { record, finish, preset });
    },
  });
}

function snapshotCommon(body) {
  const f = $("form", body);
  return { occurred_at: f.occurred_at?.value, odometer_km: f.odometer_km?.value, note: f.note?.value };
}

async function renderForm(body, ctx, v, st, { record, finish = false, keep = null, preset = {} }) {
  const form = $("form", body);
  const kind = st.kind;
  // 已保存记录里的估算字段原样保留（仍算估算），用户改了对应字段才去掉
  st.estSticky = new Set(record?.estimated_fields || []);
  st.est = new Set(st.estSticky);
  st.lastEdited = [];
  st.place = null;
  form.innerHTML = `<div class="skeleton" style="height:220px"></div>`;
  ctx.setFoot("");
  if (kind !== "expense") {
    try {
      st.prefill = await api(`/api/records/prefill?vehicle_id=${v.id}&kind=${kind}`);
    } catch (e) {
      st.prefill = { places: [], recent_notes: [] };
      if (e.status !== 0) toastError(e);
    }
  }
  const time = record?.occurred_at || keep?.occurred_at || localISO();
  const detailed = v.record_style !== "simple";

  if (kind === "expense") {
    renderExpense(form, ctx, v, st, { record, time, keep, preset });
    return;
  }

  const p = st.prefill;
  const socLabel = kind === "fuel" ? "当前电量" : "电量 SOC";
  const levelBlock = (inMore) => {
    const out = [];
    if (kind === "fuel" && v.has_elec && detailed !== inMore) out.push(levelField("soc_before_pct", "当前电量 SOC", { hint: "填写后混动能耗更准" }));
    if (kind === "charge" && v.has_fuel && inMore) out.push(gaugeField("fuel_level_before_pct", "当前油量", v));
    if (kind === "snapshot" && !inMore) {
      if (v.has_elec) out.push(levelField("soc_before_pct", socLabel));
      if (v.has_fuel) out.push(gaugeField("fuel_level_before_pct", "油量", v));
    }
    return out;
  };

  form.innerHTML = String(html`
    ${kind === "charge" && !record ? html`<div class="callout" data-start-charge>${raw(icon("plug", 18))}<span class="grow">正在充电？先记开始，拔枪时再补结束</span><button type="button" class="btn btn-sm btn-soft">开始充电</button></div>` : ""}
    ${kind === "snapshot" ? html`<p class="hint-text">${raw(icon("info", 16))}出发前、长时间停车前后记一下，可以把哨兵等静置耗电单独统计出来。电量和油量记得哪个填哪个，不确定的留空。</p>` : ""}
    ${!record && p?.last ? html`<div class="row between"><span class="tiny muted">上次：${fmtDate(p.last.occurred_at)} ${p.last.place_name || ""}</span><button type="button" class="link-btn tiny" data-same>${raw(icon("copy", 14))} 同上次</button></div>` : ""}
    ${timeField(time)}
    ${odoField(p)}
    ${kind === "fuel" ? html`
      <div class="field"><label>标号</label><div class="chips" data-grade>${FUEL_GRADES.map((g) => html`<button type="button" class="chip sm" data-v="${g}">${gradeLabel(g)}</button>`)}</div></div>
      ${moneyTriple("fuel")}
      <label class="switch big-switch"><span class="switch-text"><span>${raw(icon("fuel", 18))} 加满</span><small>加满之间的油耗最准</small></span><input type="checkbox" name="fuel_is_full" checked><span class="track"></span></label>
      ${levelBlock(false)}` : ""}
    ${kind === "charge" ? html`
      <div class="field"><label>方式</label><div class="chips" data-ctype>${Object.entries(CHARGE_LABEL).map(([k, l]) => html`<button type="button" class="chip sm" data-v="${k}">${l}</button>`)}</div></div>
      <div class="grid-2 soc-pair">${levelField("soc_before_pct", "开始 SOC")}${levelField("soc_after_pct", "结束 SOC")}</div>
      <label class="switch"><span class="switch-text"><span>充到上限（${num(v.charge_limit_pct)}%）</span><small>勾选后结束 SOC 按上限算</small></span><input type="checkbox" name="charge_to_limit"><span class="track"></span></label>
      ${moneyTriple("charge")}
      <div class="tariff-note tiny muted hidden"></div>` : ""}
    ${kind === "snapshot" ? html`${levelBlock(false)}
      <div class="field"><label>标签</label><div class="chips" data-snap>${["before_park", "after_park", "month_start", "other"].map((t) => html`<button type="button" class="chip sm" data-v="${t}">${SNAPSHOT_TAGS[t]}</button>`)}</div></div>` : ""}
    ${kind !== "snapshot" ? placeChips(p?.places || [], kind) : ""}
    <details class="more" ${record && hasMore(record, kind) ? "open" : ""}><summary>${raw(icon("down", 16))}更多</summary><div class="stack">
      ${kind === "fuel" ? html`<div class="grid-2">${gaugeField("fuel_level_before_pct", "加油前油量", v)}<div class="after-wrap">${gaugeField("fuel_level_after_pct", "加油后油量", v)}</div></div>` : ""}
      ${levelBlock(true)}
      ${kind === "charge" ? html`<div class="grid-3">${numField("charge_minutes", "时长", "分钟", { mode: "numeric" })}${numField("energy_fee", "电费", "元", { mode: "text" })}${numField("service_fee", "服务费", "元", { mode: "text" })}</div>
        ${numField("extra_fee", "占位/停车费", "元", { mode: "text", hint: "计入花费，不计入电价" })}` : ""}
      ${kind === "fuel" ? numField("extra_fee", "其他费用（洗车等）", "元", { mode: "text", hint: "计入花费，不计入油价" }) : ""}
      ${kind !== "snapshot" ? html`<label class="switch"><span class="switch-text"><span>上次${kind === "fuel" ? "加油" : "充电"}没记</span><small>勾选后跨过这里的能耗不计算，避免算错</small></span><input type="checkbox" name="missed_before"><span class="track"></span></label>` : ""}
    </div></details>
    <div class="field"><label>备注</label>
      ${p?.recent_notes?.length ? html`<div class="chips" data-notes>${p.recent_notes.map((n) => html`<button type="button" class="chip sm" data-v="${n}">${n}</button>`)}</div>` : ""}
      <textarea class="input" name="note" rows="2" maxlength="500" placeholder="可不填"></textarea></div>
    <div class="form-error"></div>`);

  wireCommon(form, v, st);
  if (record) {
    form.dataset.edit = "1";
    fillRecord(form, v, st, record, finish);
  } else {
    applyDefaults(form, v, st, kind, keep, preset);
    restoreDraft(form, v, st, kind);
  }
  recompute(form, v, st);

  const saveLabel = finish ? "结束充电" : "保存";
  ctx.setFoot(String(html`${!record ? html`<button type="button" class="btn btn-ghost" data-again>保存并再记</button>` : ""}
    <button type="button" class="btn btn-primary grow" data-save>${raw(icon("check", 18))}${saveLabel}</button>`));
  $("[data-save]", ctx.foot).onclick = (e) => save(form, ctx, v, st, { record, again: false, btn: e.currentTarget });
  const again = $("[data-again]", ctx.foot);
  if (again) again.onclick = (e) => save(form, ctx, v, st, { record, again: true, btn: e.currentTarget });
  const sc = $("[data-start-charge] button", form);
  if (sc) sc.onclick = (e) => save(form, ctx, v, st, { record: null, startOnly: true, btn: e.currentTarget });
}

function hasMore(r, kind) {
  return r.fuel_level_before_pct != null || r.fuel_level_after_pct != null || r.missed_before || r.extra_fee_cents ||
    r.charge_minutes || r.energy_fee_cents || (kind === "charge" && r.fuel_level_before_pct != null);
}

/* ---------- 交互绑定 ---------- */

function wireCommon(form, v, st) {
  // 时间快捷
  $("[data-time-chips]", form)?.addEventListener("click", (e) => {
    const c = e.target.closest("[data-t]");
    if (!c) return;
    const d = new Date();
    if (c.dataset.t === "h1") d.setHours(d.getHours() - 1);
    if (c.dataset.t === "morning") d.setHours(8, 0);
    if (c.dataset.t === "yesterday") d.setDate(d.getDate() - 1);
    form.occurred_at.value = localISO(d);
    $$("[data-t]", form).forEach((x) => x.classList.toggle("on", x === c));
    recompute(form, v, st);
  });
  // 单选胶囊
  for (const [sel, name] of [["[data-grade]", "fuel_grade"], ["[data-ctype]", "charge_type"], ["[data-snap]", "snapshot_tag"]]) {
    const el = $(sel, form);
    if (el) {
      bindChips(el, { onChange: () => {
        form.dataset[name] = $(".chip.on", el)?.dataset.v || "";
        recompute(form, v, st);
      } });
    }
  }
  $("[data-notes]", form)?.addEventListener("click", (e) => {
    const c = e.target.closest("[data-v]");
    if (!c) return;
    const t = form.note;
    t.value = t.value ? (t.value.includes(c.dataset.v) ? t.value : t.value + " " + c.dataset.v) : c.dataset.v;
  });
  // 地点
  const pc = $("[data-place-chips]", form);
  if (pc) {
    pc.addEventListener("click", (e) => {
      const c = e.target.closest(".chip");
      if (!c) return;
      if (c.dataset.v === "__other") return pickPlace(form, v, st);
      setPlace(form, v, st, c.dataset.v ? st.prefill.places.find((p) => String(p.id) === c.dataset.v) : null);
    });
  }
  $("[data-geo]", form)?.addEventListener("click", () => locate(form, v, st));
  $("[data-save-place]", form)?.addEventListener("click", async (e) => {
    const cur = st.place;
    if (!cur || cur.id) return;
    const kind = st.kind === "fuel" ? "gas_station" : form.dataset.charge_type === "home" ? "home_charger" : "charge_station";
    setLoading(e.currentTarget, true);
    try {
      const pl = await api("/api/places", { method: "POST", body: { vehicle_id: v.id, shared: true, name: cur.name, kind,
        default_charge_type: st.kind === "charge" ? form.dataset.charge_type || null : null,
        default_fuel_grade: st.kind === "fuel" ? form.dataset.fuel_grade || null : null,
        last_unit_price: toNum(form[st.kind === "fuel" ? "fuel_unit_price" : "charge_unit_price"]?.value) } });
      st.prefill.places = [pl, ...(st.prefill.places || [])];
      $(".chip.tmp", form)?.remove();
      setPlace(form, v, st, pl);
      toast("已存为常用地点");
    } catch (ex) {
      toastError(ex);
    } finally {
      setLoading(e.currentTarget, false);
    }
  });
  // 同上次
  $("[data-same]", form)?.addEventListener("click", () => {
    const l = st.prefill.last;
    if (!l) return;
    if (l.fuel_grade) selectChip(form, "[data-grade]", "fuel_grade", l.fuel_grade);
    if (l.charge_type) selectChip(form, "[data-ctype]", "charge_type", l.charge_type);
    if (st.kind === "fuel" && l.fuel_unit_price != null) setUser(form.fuel_unit_price, l.fuel_unit_price);
    if (l.place_id) {
      const pl = st.prefill.places.find((p) => p.id === l.place_id);
      if (pl) setPlace(form, v, st, pl);
    } else if (l.place_name) setPlace(form, v, st, { id: null, name: l.place_name });
    if (st.kind === "fuel") form.fuel_is_full.checked = !!l.fuel_is_full;
    recompute(form, v, st);
    toast(st.kind === "fuel" ? "已复制上次的地点、标号和单价" : "已复制上次的地点和方式");
  });
  // 数字输入：记录用户手动改过的字段
  form.addEventListener("input", (e) => {
    const t = e.target;
    if (t.name && t.classList.contains("input")) {
      t.dataset.user = t.value.trim() === "" ? "" : "1";
      clearAuto(t);
      syncLevel(t);
      if (["fuel_liters", "charge_kwh", "fuel_unit_price", "charge_unit_price", "amount"].includes(t.name)) {
        st.lastEdited = st.lastEdited.filter((n) => n !== t.name);
        if (t.value.trim()) st.lastEdited.push(t.name);
      }
      unstick(st, t.name);
    }
    recompute(form, v, st);
    saveDraft(form, v, st);
  });
  form.addEventListener("change", (e) => {
    if (e.target.type === "checkbox") {
      if (e.target.name === "odo_unknown") {
        form.odometer_km.disabled = e.target.checked;
        if (e.target.checked) form.odometer_km.value = "";
      }
      recompute(form, v, st);
      saveDraft(form, v, st);
    }
  });
  wireLevels(form, v, st);
}

function wireLevels(form, v, st) {
  $$(".level-field", form).forEach((f) => {
    const range = $(".lv-range", f);
    const inp = $("input.input", f);
    range.addEventListener("input", () => {
      inp.value = range.value;
      inp.dataset.user = "1";
      range.style.setProperty("--p", range.value + "%");
      range.classList.remove("unset");
      clearAuto(inp);
      unstick(st, inp.name);
      recompute(form, v, st);
      saveDraft(form, v, st);
    });
  });
  $$(".gauge-field", form).forEach((f) => {
    const n = +f.dataset.bars;
    const hidden = $("input[type=hidden]", f);
    f.addEventListener("click", (e) => {
      const b = e.target.closest("[data-b]");
      if (!b) return;
      const k = +b.dataset.b;
      const cur = hidden.value ? Math.round((+hidden.value / 100) * n) : 0;
      hidden.value = cur === k ? "" : ((k / n) * 100).toFixed(1);
      hidden.dataset.user = hidden.value ? "1" : "";
      recompute(form, v, st);
    });
  });
  $$("[data-liters]", form).forEach((f) => {
    const lit = $("[data-lit]", f);
    const pct = $("input[name]", f);
    lit.addEventListener("input", () => {
      const l = toNum(lit.value);
      pct.value = l == null ? "" : Math.min((l / v.tank_capacity_l) * 100, 100).toFixed(1);
      pct.dataset.user = pct.value ? "1" : "";
      clearAuto(pct);
      lit.classList.remove("auto");
      syncLevel(pct);
      unstick(st, pct.name);
      recompute(form, v, st);
      saveDraft(form, v, st);
    });
    f.addEventListener("click", (e) => {
      const b = e.target.closest("[data-unit]");
      if (!b) return;
      $$("[data-liters]", form).forEach((x) => x.classList.toggle("unit-liters", b.dataset.unit === "liters"));
      savePref("fuel_unit", b.dataset.unit);
    });
  });
}

/** 用户改了某个字段：去掉它的估算标记（含已保存记录带来的）。 */
function unstick(st, fieldName) {
  const n = EST_NAME[fieldName] || (fieldName === "odometer_km" ? "odometer_km" : null);
  if (!n) return;
  st.est.delete(n);
  st.estSticky.delete(n);
}

function selectChip(form, sel, name, value) {
  const el = $(sel, form);
  if (!el) return;
  $$(".chip", el).forEach((c) => c.classList.toggle("on", c.dataset.v === String(value)));
  form.dataset[name] = value;
}

function setUser(inp, val) {
  inp.value = val ?? "";
  inp.dataset.user = val == null || val === "" ? "" : "1";
  clearAuto(inp);
  syncLevel(inp);
}

function setAuto(inp, val, digits = 2) {
  if (!inp) return;
  inp.value = typeof val === "number" ? String(+val.toFixed(digits)) : val;
  inp.dataset.auto = "1";
  inp.classList.add("auto");
  inp.parentElement.querySelector(".auto-tag")?.classList.remove("hidden");
  syncLevel(inp);
}

function clearAuto(inp) {
  if (!inp) return;
  delete inp.dataset.auto;
  inp.classList.remove("auto");
  inp.parentElement?.querySelector(".auto-tag")?.classList.add("hidden");
}

function syncLevel(inp) {
  const f = inp.closest(".level-field");
  if (f) {
    const r = $(".lv-range", f);
    if (inp.value !== "") {
      r.value = inp.value;
      r.classList.remove("unset");
    } else r.classList.add("unset");
    r.style.setProperty("--p", (inp.value !== "" ? Math.min(+inp.value, 100) : 0) + "%");
  }
  const lf = inp.closest("[data-liters]");
  const lit = lf && $("[data-lit]", lf);
  // 正在输入的升数不回写，避免「3.」被改成「3」
  if (lit && inp !== lit && document.activeElement !== lit) {
    const T = +lf.dataset.tank;
    lit.value = inp.value !== "" && T ? String(+((+inp.value / 100) * T).toFixed(1)) : "";
    lit.classList.toggle("auto", !!inp.dataset.auto);
  }
  const g = inp.closest(".gauge-field");
  if (g) {
    const n = +g.dataset.bars;
    const k = inp.value ? Math.round((+inp.value / 100) * n) : 0;
    $$("[data-b]", g).forEach((b) => b.classList.toggle("on", +b.dataset.b <= k));
    $(".lv-val", g).textContent = inp.value ? `${k}/${n} 格` : "";
  }
}

/** 选中地点；用户主动选地点时带出默认方式、标号和单价，载入已保存的记录或草稿时（defaults: false）保持原值。 */
function setPlace(form, v, st, place, { defaults = true } = {}) {
  st.place = place;
  const pc = $("[data-place-chips]", form);
  let chip = place?.id ? $(`.chip[data-v="${place.id}"]`, pc) : null;
  $$(".chip", pc).forEach((c) => c.classList.remove("on"));
  if (!place) {
    $('.chip[data-v=""]', pc).classList.add("on");
  } else if (chip) {
    chip.classList.add("on");
  } else {
    // 临时地点或不在前 6 的常用地点：插到「其他」前面
    $(".chip.tmp", pc)?.remove();
    chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip sm on tmp";
    chip.dataset.v = place.id || "";
    chip.textContent = place.name;
    chip.onclick = (e) => e.stopPropagation();
    pc.insertBefore(chip, $('.chip[data-v="__other"]', pc));
  }
  const ps = $(".place-save", form);
  if (ps) {
    ps.classList.toggle("hidden", !place || !!place.id);
    if (place && !place.id) $("[data-save-place]", ps).textContent = `存为常用地点「${place.name}」`;
  }
  if (place?.id && defaults) {
    if (st.kind === "charge" && place.default_charge_type) selectChip(form, "[data-ctype]", "charge_type", place.default_charge_type);
    if (st.kind === "fuel" && place.default_fuel_grade) selectChip(form, "[data-grade]", "fuel_grade", place.default_fuel_grade);
    if (st.kind === "fuel" && !form.fuel_unit_price.dataset.user && place.last_unit_price) setUser(form.fuel_unit_price, place.last_unit_price);
  }
  recompute(form, v, st);
}

async function pickPlace(form, v, st) {
  const places = st.prefill.places || [];
  const res = await sheet({
    title: "选择地点",
    render(body, ctx) {
      body.innerHTML = String(html`<div class="field"><input class="input" placeholder="搜索或输入新地点名称" data-q></div>
        <div class="list card flush" style="margin-top:12px" data-list></div>`);
      const q = $("[data-q]", body);
      const list = $("[data-list]", body);
      const draw = () => {
        const t = q.value.trim();
        const hits = places.filter((p) => !t || p.name.includes(t));
        list.innerHTML = String(html`${t && !places.some((p) => p.name === t) ? html`<button class="list-item" data-new>${raw(`<span class="ico sm brand">${icon("plus", 16)}</span>`)}<span class="grow">使用「${t}」<div class="tiny muted">只记在这条记录里</div></span></button>` : ""}
          ${hits.map((p) => html`<button class="list-item" data-id="${p.id}"><span class="ico sm">${raw(icon("pin", 16))}</span><span class="grow">${p.name}<div class="tiny muted">${p.use_count ? `用过 ${p.use_count} 次` : ""}</div></span></button>`)}
          ${!hits.length && !t ? html`<div class="empty small">还没有常用地点，直接输入名称即可</div>` : ""}`);
      };
      q.addEventListener("input", draw);
      q.addEventListener("keydown", (e) => e.key === "Enter" && q.value.trim() && ctx.close({ id: null, name: q.value.trim() }));
      list.addEventListener("click", (e) => {
        const b = e.target.closest(".list-item");
        if (!b) return;
        if (b.dataset.new != null) ctx.close({ id: null, name: q.value.trim() });
        else ctx.close(places.find((p) => String(p.id) === b.dataset.id));
      });
      draw();
      setTimeout(() => q.focus(), 250);
    },
  });
  if (res) setPlace(form, v, st, res);
}

function locate(form, v, st) {
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      const { latitude: lat, longitude: lng } = pos.coords;
      st.coords = { lat, lng };
      let best = null;
      let bd = Infinity;
      for (const p of st.prefill.places || []) {
        if (p.lat == null) continue;
        const d = haversine(lat, lng, p.lat, p.lng);
        if (d < bd) {
          bd = d;
          best = p;
        }
      }
      if (best && bd < 300) {
        setPlace(form, v, st, best);
        toast(`已定位到「${best.name}」`);
      } else toast("附近 300 米内没有常用地点，已记下坐标");
    },
    () => toast("定位失败，请检查浏览器权限", { error: true }),
    { enableHighAccuracy: true, timeout: 8000 }
  );
}

function haversine(a1, o1, a2, o2) {
  const R = 6371000;
  const r = (x) => (x * Math.PI) / 180;
  const d = Math.sin(r(a2 - a1) / 2) ** 2 + Math.cos(r(a1)) * Math.cos(r(a2)) * Math.sin(r(o2 - o1) / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(d));
}

/* ---------- 默认值、编辑回填、草稿 ---------- */

function applyDefaults(form, v, st, kind, keep, preset) {
  if (keep?.odometer_km) setUser(form.odometer_km, keep.odometer_km);
  if (keep?.note) form.note.value = keep.note;
  if (kind === "fuel") selectChip(form, "[data-grade]", "fuel_grade", v.default_fuel_grade || "92");
  if (kind === "charge") selectChip(form, "[data-ctype]", "charge_type", v.default_charge_type || "home");
  if (kind === "snapshot") selectChip(form, "[data-snap]", "snapshot_tag", preset.snapshot_tag || "other");
  if (pref("place_default", "none") === "top" && st.prefill.places?.length && kind !== "snapshot") {
    setPlace(form, v, st, st.prefill.places[0]);
  }
  // 当前电量 / 油量只做占位提示，不当成值
  const p = st.prefill;
  if (p?.last_soc != null) {
    $$('input[name="soc_before_pct"]', form).forEach((i) => {
      i.placeholder = String(p.last_soc);
      i.title = `上次记录后的电量 ${p.last_soc}%`;
    });
  }
    $$("[data-t]", form)[0]?.classList.add("on");
}

function fillRecord(form, v, st, r, finish) {
  form.occurred_at.value = r.occurred_at;
  if (r.odometer_km != null && !r.odometer_estimated) setUser(form.odometer_km, r.odometer_km);
  if (r.odometer_estimated) {
    form.odo_unknown.checked = true;
    form.odometer_km.disabled = true;
  }
  const est = new Set(r.estimated_fields || []);
  // 估算值按用户值载入（不会被重新推算清掉），只是显示成灰色斜体提示它是估算的
  const put = (name, val, estName) => {
    const inp = form[name];
    if (!inp || val == null) return;
    setUser(inp, val);
    if (estName && est.has(estName)) {
      inp.classList.add("auto");
      inp.closest("[data-liters]")?.querySelector("[data-lit]")?.classList.add("auto");
    }
  };
  if (r.kind === "fuel") {
    selectChip(form, "[data-grade]", "fuel_grade", r.fuel_grade || "92");
    put("fuel_liters", r.fuel_liters, "fuel_l");
    put("fuel_unit_price", r.fuel_unit_price);
    form.fuel_is_full.checked = !!r.fuel_is_full;
    put("fuel_level_before_pct", r.fuel_level_before_pct, "fuel_before_pct");
    put("fuel_level_after_pct", r.fuel_level_after_pct, "fuel_after_pct");
  }
  if (r.kind === "charge") {
    selectChip(form, "[data-ctype]", "charge_type", r.charge_type || "home");
    put("charge_kwh", r.charge_kwh, "charge_kwh");
    put("soc_after_pct", r.soc_after_pct, "soc_after");
    form.charge_to_limit.checked = !!r.charge_to_limit;
    put("charge_minutes", r.charge_minutes);
    if (r.energy_fee_cents != null) setUser(form.energy_fee, r.energy_fee_cents / 100);
    if (r.service_fee_cents != null) setUser(form.service_fee, r.service_fee_cents / 100);
    if (r.kind === "charge") put("fuel_level_before_pct", r.fuel_level_before_pct, "fuel_before_pct");
    if (finish) {
      const mins = Math.round((Date.now() - parseLocal(r.occurred_at).getTime()) / 60000);
      if (mins > 0 && mins < 3 * 24 * 60) setUser(form.charge_minutes, mins);
      $("details.more", form).open = true;
    }
  }
  put("soc_before_pct", r.soc_before_pct, "soc_before");
  if (r.kind === "snapshot") {
    selectChip(form, "[data-snap]", "snapshot_tag", r.snapshot_tag || "other");
    put("fuel_level_before_pct", r.fuel_level_before_pct, "fuel_before_pct");
  }
  if (r.amount_cents != null && form.amount) setUser(form.amount, r.amount_cents / 100);
  if (r.extra_fee_cents != null && form.extra_fee) setUser(form.extra_fee, r.extra_fee_cents / 100);
  if (form.missed_before) form.missed_before.checked = !!r.missed_before;
    form.note.value = r.note || "";
  if (r.place_id || r.place_name) {
    const pl = st.prefill.places.find((p) => p.id === r.place_id) || { id: r.place_id, name: r.place_name };
    st.place = pl;
    setPlace(form, v, st, pl, { defaults: false });
  }
  st.lastEdited = ["fuel_liters", "charge_kwh", "fuel_unit_price", "charge_unit_price", "amount"].filter((n) => form[n]?.dataset.user);
}

const draftKey = (v, st) => `draft:${v.id}:${st.kind}`;

function saveDraft(form, v, st) {
  if (form.dataset.edit) return;
  clearTimeout(st.draftT);
  st.draftT = setTimeout(() => {
    const vals = {};
    $$("input.input, textarea.input, select.input, input[type=hidden]", form).forEach((i) => {
      if (i.name && i.dataset.user && i.value !== "") vals[i.name] = i.value;
    });
    $$("input[type=checkbox]", form).forEach((i) => (vals["cb:" + i.name] = i.checked));
    vals.__ds = { ...form.dataset };
    vals.__place = st.place;
    vals.__ts = Date.now();
    store.set(draftKey(v, st), vals);
  }, 400);
}

function restoreDraft(form, v, st) {
  const d = store.get(draftKey(v, st));
  if (!d || Date.now() - d.__ts > 24 * 3600 * 1000) return;
  const keys = Object.keys(d).filter((k) => !k.startsWith("__") && !k.startsWith("cb:") && k !== "occurred_at");
  if (!keys.length) return;
  for (const k of keys) if (form[k] && !form[k].readOnly) setUser(form[k], d[k]);
  for (const k of Object.keys(d).filter((x) => x.startsWith("cb:"))) if (form[k.slice(3)]) form[k.slice(3)].checked = d[k];
  for (const [k, val] of Object.entries(d.__ds || {})) {
    const sel = { fuel_grade: "[data-grade]", charge_type: "[data-ctype]", snapshot_tag: "[data-snap]" }[k];
    if (sel) selectChip(form, sel, k, val);
  }
  if (d.__place) setPlace(form, v, st, d.__place, { defaults: false });
  toast("已恢复上次没保存的内容", {
    action: { label: "清空", onClick: () => {
      store.del(draftKey(v, st));
      $$("input.input, textarea.input", form).forEach((i) => {
        if (i.name !== "occurred_at") setUser(i, "");
      });
      recompute(form, v, st);
    } },
  });
}

/* ---------- 自动推算 ---------- */

function val(form, name) {
  const i = form[name];
  if (!i || i.disabled) return null;
  if (name === "amount" || name.endsWith("_fee")) return evalAmount(i.value);
  return toNum(i.value);
}
const isUser = (form, name) => !!form[name]?.dataset.user;

function tariffAmount(place, kwh, startISO, minutes) {
  const tariff = place?.tou_tariff || [];
  if (!tariff.length || !kwh) return null;
  const start = parseLocal(startISO);
  if (!start) return null;
  const priceAt = (d) => {
    const m = d.getHours() * 60 + d.getMinutes();
    for (const t of tariff) {
      const [fh, fm] = t.from.split(":").map(Number);
      const [th, tm] = t.to.split(":").map(Number);
      const a = fh * 60 + fm;
      const b = th * 60 + tm;
      const inside = a <= b ? m >= a && m < b : m >= a || m < b;
      if (inside) return t.price;
    }
    return tariff[0].price;
  };
  if (!minutes) return kwh * priceAt(start);
  // 按分钟把度数平均分摊到各时段
  const steps = Math.min(minutes, 3 * 24 * 60);
  let sum = 0;
  for (let i = 0; i < steps; i += 5) sum += priceAt(new Date(start.getTime() + i * 60000)) * Math.min(5, steps - i);
  return (kwh * sum) / steps;
}

function recompute(form, v, st) {
  const kind = st.kind;
  const k = st.prefill?.k;
  const T = v.tank_capacity_l;
  // 1. 清掉之前自动填的值，按依赖重新算
  $$("input[data-auto]", form).forEach((i) => {
    i.value = "";
    clearAuto(i);
    syncLevel(i);
  });
  for (const n of Object.values(EST_NAME)) st.est.delete(n);
  for (const n of st.estSticky) st.est.add(n);
  const markEst = (name) => EST_NAME[name] && st.est.add(EST_NAME[name]);

  const q = kind === "fuel" ? "fuel_liters" : "charge_kwh";
  const p = kind === "fuel" ? "fuel_unit_price" : "charge_unit_price";
  const ttt = (userOnly) => {
    if (kind !== "fuel" || !form[q]) return;
    const has = (n) => val(form, n) != null && (!userOnly || isUser(form, n));
    const vq = val(form, q);
    const vp = val(form, p);
    const va = val(form, "amount");
    if (has(q) && has(p) && !has("amount") && !isUser(form, "amount")) setAuto(form.amount, vq * vp, 2);
    else if (has(q) && has("amount") && !has(p) && !isUser(form, p) && vq > 0) setAuto(form[p], va / vq, kind === "fuel" ? 2 : 3);
    else if (has(p) && has("amount") && !has(q) && !isUser(form, q) && vp > 0) setAuto(form[q], va / vp, 2);
  };

  // 2. 优先用用户填的两项算第三项（加油机上的金额和单价最准）
  ttt(true);

  // 3. 依据车辆参数推算
  if (kind === "fuel" && T) {
    // 按升或格数输入时百分比多保留两位，换算回升数才不会有偏差
    const lvDigits = fuelUnit(v) === "pct" && v.fuel_gauge_mode !== "bars" ? 0 : 2;
    const full = form.fuel_is_full.checked;
    const before = val(form, "fuel_level_before_pct");
    const liters = val(form, "fuel_liters");
    if (full) {
      if (before != null && liters == null && !isUser(form, "fuel_liters")) {
        setAuto(form.fuel_liters, ((100 - before) / 100) * T, 1);
        markEst("fuel_liters");
      } else if (liters != null && before == null && !isUser(form, "fuel_level_before_pct") && form.fuel_level_before_pct) {
        setAuto(form.fuel_level_before_pct, Math.max(0, ((T - liters) / T) * 100), lvDigits);
        markEst("fuel_level_before_pct");
      }
    } else if (before != null && liters != null && form.fuel_level_after_pct && !isUser(form, "fuel_level_after_pct")) {
      setAuto(form.fuel_level_after_pct, Math.min(100, before + (liters / T) * 100), lvDigits);
      markEst("fuel_level_after_pct");
    }
  }
  if (kind === "fuel") $(".after-wrap", form)?.classList.toggle("hidden", form.fuel_is_full.checked);
  if (kind === "charge") {
    const swap = form.dataset.charge_type === "swap";
    const toLimit = form.charge_to_limit.checked;
    // 充到上限：结束 SOC 固定为上限值，不能手改
    const lockLimit = (on) => {
      form.soc_after_pct.readOnly = on;
      const rg = $(".lv-range", form.soc_after_pct.closest(".level-field"));
      if (rg) rg.disabled = on;
    };
    if (toLimit) {
      delete form.soc_after_pct.dataset.user;
      setAuto(form.soc_after_pct, v.charge_limit_pct ?? 100, 0);
    }
    lockLimit(toLimit);
    const s0 = val(form, "soc_before_pct");
    const s1 = val(form, "soc_after_pct");
    const per = swap ? (v.battery_usable_kwh ? v.battery_usable_kwh / 100 : null) : k;
    if (s0 != null && s1 == null && !toLimit && per && isUser(form, "charge_kwh")) {
      setAuto(form.soc_after_pct, Math.min(100, s0 + val(form, "charge_kwh") / per), 0);
      markEst("soc_after_pct");
    }
    // 峰谷电价只给参考金额，不替用户填
    const note = $(".tariff-note", form);
    const tariff = st.place?.tou_tariff?.length ? st.place : null;
    const kwh = val(form, "charge_kwh");
    const ref = tariff && kwh ? tariffAmount(tariff, kwh, form.occurred_at.value, val(form, "charge_minutes")) : null;
    note.classList.toggle("hidden", ref == null);
    if (ref != null) note.textContent = `按「${tariff.name}」的峰谷电价约 ${money(Math.round(ref * 100))}`;
    // 电费 + 服务费
    const ef = val(form, "energy_fee");
    const sf = val(form, "service_fee");
    if ((ef != null || sf != null) && !isUser(form, "amount") && val(form, "amount") == null) setAuto(form.amount, (ef || 0) + (sf || 0), 2);
    // 单价只由金额和电量算出；缺金额或电量的旧记录保留原来的单价
    const amt = val(form, "amount");
    const price = amt != null && kwh > 0 ? +(amt / kwh).toFixed(3) : st.record?.charge_unit_price ?? null;
    form.charge_unit_price.value = price ?? "";
  }

  // 4. 再用自动值补齐三填二
  ttt(false);

  // 5. 实付、优惠、里程增量、显示值
  updateOdoDelta(form, st);
  $$(".level-field", form).forEach((f) => {
    const i = $("input.input", f);
    let t = i.value !== "" ? `${num(+i.value, 0)}%` : "";
    if (t && f.dataset.tone === "fuel" && T) t += ` · ${num((+i.value / 100) * T, 1)}L`;
    $(".lv-val", f).textContent = t;
  });
}

/** 所选时间前后最近的两次里程读数；没有时间轴数据时退回最新里程。 */
function odoNeighbors(st, t) {
  const pts = st.prefill?.odo_points;
  if (!pts?.length || !t) return { prev: st.prefill?.last_odo != null ? [st.prefill.last_odo_ts, st.prefill.last_odo] : null, next: null };
  let prev = null;
  let next = null;
  for (const p of pts) {
    if (p[0] < t) prev = p;
    else if (p[0] > t && !next) next = p;
  }
  return { prev, next };
}

function updateOdoDelta(form, st) {
  const el = $(".odo-delta", form);
  if (!el) return;
  const { prev, next } = odoNeighbors(st, form.occurred_at.value);
  const last = prev?.[1];
  const trip = form.trip_mode?.checked;
  const raw = toNum(form.odometer_km.value);
  form.odometer_km.placeholder = trip ? "本次行驶公里数" : last != null ? "上次 " + km(last) : "仪表盘总里程";
  el.classList.remove("warn-c");
  if (form.odo_unknown?.checked) {
    el.textContent = "里程按前后记录推算";
    return;
  }
  if (raw == null || last == null) {
    el.textContent = last != null ? `上次 ${km(last)}` : "";
    return;
  }
  const delta = trip ? raw : raw - last;
  const total = trip ? last + raw : raw;
  // 补录历史：和所选时间之前的那次读数比，超过之后的读数才标红
  if (next) {
    el.textContent = total > next[1] ? `比之后 ${fmtDate(next[0])} 的 ${km(next[1])} km 还大` : `比 ${fmtDate(prev[0])} ${delta >= 0 ? "+" : ""}${km(delta)} km`;
    if (delta < 0 || total > next[1]) el.classList.add("warn-c");
    return;
  }
  el.textContent = trip ? `总里程 ${km(total)}` : `本次 ${delta >= 0 ? "+" : ""}${km(delta)} km`;
  if (delta < 0 || delta > 2000) el.classList.add("warn-c");
}

/* ---------- 保存 ---------- */

function collect(form, v, st, { startOnly = false } = {}) {
  const kind = st.kind;
  const trip = form.trip_mode?.checked;
  let odo = form.odo_unknown?.checked ? null : toNum(form.odometer_km.value);
  const prevOdo = odoNeighbors(st, form.occurred_at.value).prev?.[1];
  if (odo != null && trip && prevOdo != null) odo = prevOdo + odo;
  const cents = (n) => {
    const x = val(form, n);
    return x == null ? null : Math.round(x * 100);
  };
  const est = new Set(st.est);
  const amount = cents("amount");
  const b = {
    vehicle_id: v.id,
    kind,
    status: startOnly ? "in_progress" : "done",
    occurred_at: form.occurred_at.value || localISO(),
    odometer_km: odo,
    odometer_estimated: odo == null,
    source: st.record?.source || (parseLocal(form.occurred_at.value) < new Date(Date.now() - 7 * 86400000) ? "backfill" : "manual"),
    note: form.note.value.trim(),
    missed_before: !!form.missed_before?.checked,
        amount_cents: amount,
    extra_fee_cents: cents("extra_fee"),
    soc_before_pct: val(form, "soc_before_pct"),
    fuel_level_before_pct: val(form, "fuel_level_before_pct"),
    place_id: st.place?.id || null,
    place_name: st.place?.name || null,
    lat: st.coords?.lat ?? null,
    lng: st.coords?.lng ?? null,
  };
  if (kind === "fuel") {
    Object.assign(b, {
      fuel_grade: form.dataset.fuel_grade || null,
      fuel_liters: val(form, "fuel_liters"),
      fuel_unit_price: val(form, "fuel_unit_price"),
      fuel_is_full: form.fuel_is_full.checked,
      fuel_level_after_pct: form.fuel_is_full.checked ? null : val(form, "fuel_level_after_pct"),
    });
  }
  if (kind === "charge") {
    Object.assign(b, {
      charge_type: form.dataset.charge_type || "home",
      charge_kwh: startOnly ? null : val(form, "charge_kwh"),
      charge_unit_price: startOnly ? null : val(form, "charge_unit_price"),
      charge_to_limit: startOnly ? false : form.charge_to_limit.checked,
      soc_after_pct: startOnly ? null : val(form, "soc_after_pct"),
      charge_minutes: val(form, "charge_minutes"),
      energy_fee_cents: cents("energy_fee"),
      service_fee_cents: cents("service_fee"),
    });
    if (startOnly) b.amount_cents = null;
  }
  if (kind === "snapshot") b.snapshot_tag = form.dataset.snapshot_tag || "other";
  if (odo == null) est.add("odometer_km");
  else est.delete("odometer_km");
  b.estimated_fields = [...est];
  return b;
}

function validate(form, b, st) {
  if (b.odometer_km == null && !form.odo_unknown?.checked) return "请填写里程，或勾选「不确定里程」";
  if (b.kind === "snapshot" && b.odometer_km == null && b.soc_before_pct == null && b.fuel_level_before_pct == null) return "快照至少要填里程、电量或油量中的一项";
  if (b.kind === "fuel" && b.status === "done" && b.fuel_liters == null && b.amount_cents == null) return "请至少填写加油量或金额";
  for (const n of ["soc_before_pct", "soc_after_pct", "fuel_level_before_pct", "fuel_level_after_pct"]) {
    if (b[n] != null && (b[n] < 0 || b[n] > 100)) return "电量和油量要在 0–100% 之间";
  }
  return "";
}

async function save(form, ctx, v, st, { record, again = false, startOnly = false, btn }) {
  const errEl = $(".form-error", form);
  const body = collect(form, v, st, { startOnly });
  const err = validate(form, body, st);
  errEl.textContent = err;
  if (err) return;
  if (!record) body.client_id = uuid();
  else body.version = record.version;
  setLoading(btn, true);
  try {
    let res;
    try {
      res = await api(record ? `/api/records/${record.id}` : "/api/records", { method: record ? "PUT" : "POST", body });
    } catch (e) {
      if (e.status === 409 && e.detail?.code === "duplicate") {
        setLoading(btn, false);
        const go = await confirmDialog({ title: "可能重复", message: esc(e.detail.message) + "<br>仍然保存吗？", ok: "仍然保存" });
        if (!go) return;
        setLoading(btn, true);
        res = await api("/api/records", { method: "POST", body: { ...body, force: true } });
      } else if (e.status === 409 && e.detail?.code === "conflict") {
        toast(e.detail.message, { error: true });
        ctx.close();
        emit("data-changed");
        return;
      } else if (e.status === 0 && !record) {
        enqueue({ url: "/api/records", body, label: KIND_TITLE[body.kind], vehicle_id: v.id });
        store.del(draftKey(v, st));
        toast("网络不通，已离线保存", { sub: "联网后会自动同步" });
        ctx.close(true);
        return;
      } else throw e;
    }
    store.del(draftKey(v, st));
    maybeSuggestPlace(v, body);
    afterSave(res, record, startOnly);
    if (again) {
      ctx.close(true);
      setTimeout(() => openRecordForm({ kind: st.kind, vehicle: v }), 220);
    } else ctx.close(true);
  } catch (e) {
    errEl.textContent = e.message;
  } finally {
    setLoading(btn, false);
  }
}

function maybeSuggestPlace(v, body) {
  if (body.place_id || !body.place_name) return;
  const counts = store.get("place-names", {});
  const key = `${v.id}:${body.place_name}`;
  counts[key] = (counts[key] || 0) + 1;
  store.set("place-names", counts);
  const skip = store.get("place-skip", []);
  if (counts[key] === 2 && !skip.includes(key)) {
    setTimeout(() => toast(`要把「${body.place_name}」存成常用地点吗？`, {
      actions: [
        { label: "不再提示", onClick: () => store.set("place-skip", [...skip, key]) },
        { label: "保存", onClick: async () => {
          const kind = body.kind === "fuel" ? "gas_station" : body.kind === "charge" ? (body.charge_type === "home" ? "home_charger" : "charge_station") : "other";
          await api("/api/places", { method: "POST", body: { vehicle_id: v.id, shared: true, name: body.place_name, kind,
            default_charge_type: body.charge_type || null, default_fuel_grade: body.fuel_grade || null,
            last_unit_price: body.fuel_unit_price ?? body.charge_unit_price ?? null } });
          toast("已存为常用地点");
        } },
      ],
    }), 3500);
  }
}

function afterSave(res, record, startOnly) {
  emit("data-changed");
  const r = res.record;
  for (const w of res.warnings || []) toast(w, { error: true, ms: 4500 });
  if (startOnly) {
    toast("已记下开始充电", { sub: "拔枪后在首页「充电中」卡片里补结束" });
    return;
  }
  const segs = res.segments || [];
  const seg = segs.find((s) => s.energy === "combined") || segs.find((s) => s.energy === "fuel") || segs.find((s) => s.energy === "elec");
  const actions = [];
  if (!record) {
    actions.push({ label: "撤销", onClick: async () => {
      await api(`/api/records/${r.id}`, { method: "DELETE" });
      emit("data-changed");
      toast("已撤销");
    } });
  }
  const elec = segs.find((s) => s.energy === "elec");
  if (elec && (elec.distance < 5 || elec.anomaly === "parked")) {
    actions.push({ label: "标记静置", onClick: async () => {
      await api("/api/segments", { method: "PATCH", body: { vehicle_id: r.vehicle_id, end_record_id: r.id, energy: "elec", tag: "idle" } });
      emit("data-changed");
      toast("已把这段标记为静置，不计入行驶能耗");
    } });
  }
  if (!seg) {
    const finished = record?.status === "in_progress";
    toast(finished ? "充电已结束" : record ? "已保存" : "已记录", { sub: finished && r.charge_kwh ? `本次 ${num(r.charge_kwh)} kWh · ${money(r.amount_cents)}` : "", actions });
    return;
  }
  let text;
  if (seg.energy === "combined") text = `本段 ${num(seg.per100)} L + ${num(seg.elec_per100)} kWh / 100km`;
  else if (seg.energy === "fuel") text = `本段油耗 ${num(seg.per100)} L/100km`;
  else text = `本段电耗 ${num(seg.per100)} kWh/100km`;
  let sub = `${km(seg.distance)} km`;
  if (seg.prev_per100) {
    const ch = (seg.per100 - seg.prev_per100) / seg.prev_per100;
    sub += ` · 比上段${Math.abs(ch) < 0.005 ? "持平" : (ch > 0 ? " ↑" : " ↓") + Math.abs(ch * 100).toFixed(0) + "%"}`;
  }
  if (seg.quality !== "A") sub += " · 估算";
  toast(text, { sub, actions });
}

/* ---------- 费用表单 ---------- */

const INSURANCE_ITEMS = ["交强险", "商业险", "驾意险", "车船税"];

function renderExpense(form, ctx, v, st, { record, time, keep, preset }) {
  const cat = record?.category || preset.category || "maintenance";
  form.innerHTML = String(html`
    <div class="cat-grid">${Object.entries(EXPENSE_LABEL).map(([k, l]) => html`<button type="button" class="cat-tile ${k === cat ? "on" : ""}" data-cat="${k}">
      <span class="ico sm expense">${raw(icon(CATEGORY_ICON[k], 18))}</span><span>${l}</span></button>`)}</div>
    <div class="field"><label>金额</label><div class="input-wrap"><input class="input num big-input" name="amount" inputmode="text" placeholder="0.00，可输入 200+15"><span class="unit">元</span></div></div>
    <div class="ins-block stack hidden">
      <div class="field"><label>险种 <span class="tiny muted">填了各项金额会自动合计</span></label>
        <div class="ins-items">${INSURANCE_ITEMS.map((n) => html`<div class="input-wrap"><span class="ins-name">${n}</span><input class="input num" data-ins="${n}" inputmode="decimal" placeholder="–"><span class="unit">元</span></div>`)}</div></div>
      <div class="grid-2"><div class="field"><label>保险起期</label><input class="input" type="date" name="period_start"></div>
        <div class="field"><label>保险止期</label><input class="input" type="date" name="period_end"></div></div>
      <span class="hint tiny muted">保存后会自动更新「保险到期」提醒</span>
    </div>
    <div class="grid-2"><div class="field"><label>日期</label><input class="input" type="datetime-local" name="occurred_at" value="${time}"></div>
      ${numField("odometer_km", "里程（可选）", "km", { mode: "numeric" })}</div>
    <div class="grid-2"><div class="field"><label>名称（可选）</label><input class="input" name="title" maxlength="64" placeholder="如：小保养"></div>
      <div class="field"><label>商家（可选）</label><input class="input" name="vendor" maxlength="64" placeholder="如：4S 店"></div></div>
    <div class="field"><label>备注</label><textarea class="input" name="note" rows="2" maxlength="500" placeholder="可不填"></textarea></div>
    <div class="form-error"></div>`);
  let category = cat;
  const insBlock = $(".ins-block", form);
  const syncCat = () => {
    insBlock.classList.toggle("hidden", category !== "insurance");
    if (category === "insurance" && !form.period_start.value) {
      const d = parseLocal(form.occurred_at.value) || new Date();
      const s = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1);
      const e = new Date(s.getFullYear() + 1, s.getMonth(), s.getDate() - 1);
      form.period_start.value = localISO(s).slice(0, 10);
      form.period_end.value = localISO(e).slice(0, 10);
    }
  };
  $(".cat-grid", form).addEventListener("click", (e) => {
    const b = e.target.closest("[data-cat]");
    if (!b) return;
    category = b.dataset.cat;
    $$(".cat-tile", form).forEach((x) => x.classList.toggle("on", x === b));
    syncCat();
  });
  form.addEventListener("input", (e) => {
    if (e.target.dataset.ins != null) {
      const sum = $$("[data-ins]", form).reduce((a, i) => a + (toNum(i.value) || 0), 0);
      if (sum > 0 && !form.amount.dataset.user) {
        form.amount.value = fixed(sum).replace(/,/g, "");
        form.amount.classList.add("auto");
      }
    } else if (e.target.name === "amount") {
      form.amount.dataset.user = form.amount.value ? "1" : "";
      form.amount.classList.remove("auto");
    }
  });
  if (record) {
    form.amount.value = record.amount_cents / 100;
    form.amount.dataset.user = "1";
    form.occurred_at.value = record.occurred_at;
    if (record.odometer_km != null) form.odometer_km.value = record.odometer_km;
    form.title.value = record.title || "";
    form.vendor.value = record.vendor || "";
    form.note.value = record.note || "";
    if (record.period_start) form.period_start.value = record.period_start;
    if (record.period_end) form.period_end.value = record.period_end;
    for (const it of record.sub_items || []) {
      const i = $(`[data-ins="${it.name}"]`, form);
      if (i) i.value = it.cents / 100;
    }
      } else {
    if (keep?.odometer_km) form.odometer_km.value = keep.odometer_km;
    if (preset.title) form.title.value = preset.title;
      }
  syncCat();
  ctx.setFoot(`<button type="button" class="btn btn-primary grow" data-save>${icon("check", 18)}保存</button>`);
  $("[data-save]", ctx.foot).onclick = async (e) => {
    const err = $(".form-error", form);
    const amt = evalAmount(form.amount.value);
    if (amt == null || amt < 0) {
      err.textContent = "请填写金额";
      return;
    }
    const body = {
      vehicle_id: v.id,
      category,
      title: form.title.value.trim(),
      occurred_at: form.occurred_at.value || localISO(),
      odometer_km: toNum(form.odometer_km.value),
      amount_cents: Math.round(amt * 100),
      vendor: form.vendor.value.trim(),
      note: form.note.value.trim(),
            period_start: category === "insurance" ? form.period_start.value || null : null,
      period_end: category === "insurance" ? form.period_end.value || null : null,
      sub_items: category === "insurance" ? $$("[data-ins]", form).filter((i) => toNum(i.value)).map((i) => ({ name: i.dataset.ins, cents: Math.round(toNum(i.value) * 100) })) : [],
    };
    if (record) body.version = record.version;
    else body.client_id = uuid();
    setLoading(e.currentTarget, true);
    try {
      let res;
      try {
        res = await api(record ? `/api/expenses/${record.id}` : "/api/expenses", { method: record ? "PUT" : "POST", body });
      } catch (ex) {
        if (ex.status === 0 && !record) {
          enqueue({ url: "/api/expenses", body, label: "费用", vehicle_id: v.id });
          toast("网络不通，已离线保存", { sub: "联网后会自动同步" });
          ctx.close(true);
          return;
        }
        throw ex;
      }
      ctx.close(true);
      emit("data-changed");
      if (res.insurance_reminder) toast(`已更新「保险到期」提醒：${res.insurance_reminder.due_date}`);
      else toast(record ? "已保存" : `已记录 ${EXPENSE_LABEL[category]} ${money(body.amount_cents)}`);
      for (const s of res.suggest_complete || []) {
        const ok = await confirmDialog({ title: "顺手完成提醒？", message: `要把「${esc(s.title)}」标记为已完成，并按本次日期和里程顺延吗？`, ok: "完成并顺延" });
        if (ok) {
          await api(`/api/reminders/${s.id}/complete`, { method: "POST", body: { done_at: body.occurred_at.slice(0, 10), odometer_km: body.odometer_km } });
          toast(`「${s.title}」已顺延`);
          emit("data-changed");
        }
      }
    } catch (ex) {
      err.textContent = ex.message;
    } finally {
      setLoading(e.currentTarget, false);
    }
  };
}

export { durationText, todayISO };
