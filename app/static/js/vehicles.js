/* vehicles.js — 新车向导、车辆详情（数据体检、成员与邀请、历史汇总、参数、动态） */
import { icon } from "./icons.js";
import { navigate } from "./router.js";
import { listen, refreshChrome, resetView, setNav, setTitle, view, vehicleDot } from "./shell.js";
import {
  CHARGE_LABEL,
  ENERGY_LABEL,
  FUEL_GRADES,
  ROLE_LABEL,
  currentVehicle,
  emit,
  gradeLabel,
  loadVehicles,
  setVehicle,
  state,
} from "./state.js";
import { avatar, bindChips, bindSegmented, confirmDialog, emptyState, promptDialog, setLoading, sheet, skeleton, switchRow, toast, toastError } from "./ui.js";
import { $, $$, ago, api, esc, fixed, fmtDate, html, km, money, num, raw, toNum, todayISO, yuanToCents } from "./util.js";

const COLORS = ["#4F46E5", "#0EA5E9", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6", "#EC4899", "#111827", "#64748B"];
const TYPE_DESC = {
  phev: "既加油又充电，如比亚迪 DM-i",
  erev: "增程，如理想、问界",
  bev: "只充电",
  hev: "不插电的油混，如丰田双擎",
  ice: "只加油",
};
const hasFuel = (t) => ["ice", "hev", "phev", "erev"].includes(t);
const hasElec = (t) => ["phev", "erev", "bev"].includes(t);

/* ---------- 新车向导 ---------- */

export function wizardPage() {
  resetView();
  setNav("vehicle");
  setTitle("添加车辆");
  const el = view();
  const st = {
    step: 1,
    v: { name: "", plate: "", brand: "", model: "", energy_type: "phev", color: COLORS[0], tank_capacity_l: null, battery_usable_kwh: null,
      charge_limit_pct: 100, default_fuel_grade: "92", default_charge_type: "home", fuel_gauge_mode: "pct", fuel_gauge_bars: null,
      factory_fuel_l: 5, record_style: "detailed", delivery_date: null },
    start: { mode: "existing" },
    summary: {},
    reminders: { insurance: true, inspection: true, maintenance: true },
    remDates: {},
  };
  const steps = ["基本信息", "能源参数", "怎么开始记", "提醒"];
  const draw = () => {
    const s = st.step;
    el.innerHTML = String(html`<div class="wizard">
      <div class="wiz-steps">${steps.map((t, i) => html`<div class="wiz-step ${i + 1 === s ? "on" : i + 1 < s ? "done" : ""}"><span>${i + 1 < s ? raw(icon("check", 14)) : i + 1}</span>${t}</div>`)}</div>
      <div class="card wiz-card"><form class="stack" id="wiz" novalidate>${[step1, step2, step3, step4][s - 1](st)}</form>
        <div class="form-error" id="wiz-err"></div></div>
      <div class="wiz-foot">${s > 1 ? html`<button class="btn" id="wiz-back">${raw(icon("left", 18))}上一步</button>` : html`<a class="btn btn-ghost" data-link href="/">取消</a>`}
        <span class="grow"></span>
        ${s > 1 && s < 4 ? html`<button class="btn btn-ghost" id="wiz-skip">跳过</button>` : ""}
        <button class="btn btn-primary" id="wiz-next">${s < 4 ? "下一步" : "完成"}${raw(icon(s < 4 ? "right" : "check", 18))}</button></div></div>`);
    const f = $("#wiz");
    wireStep(f, st, draw);
    $("#wiz-back")?.addEventListener("click", () => {
      read(f, st);
      st.step--;
      draw();
    });
    $("#wiz-skip")?.addEventListener("click", () => {
      st.step++;
      draw();
    });
    $("#wiz-next").addEventListener("click", async (e) => {
      read(f, st);
      const err = validateStep(st);
      $("#wiz-err").textContent = err;
      if (err) return;
      if (st.step < 4) {
        st.step++;
        draw();
        return;
      }
      setLoading(e.currentTarget, true);
      try {
        const res = await api("/api/vehicles", { method: "POST", body: buildPayload(st) });
        await loadVehicles();
        setVehicle(res.id);
        refreshChrome();
        toast(`已添加「${res.name}」`, { sub: "先记一次加满或快照，能耗就开始计算了" });
        navigate("/");
      } catch (ex) {
        $("#wiz-err").textContent = ex.message;
      } finally {
        setLoading(e.currentTarget, false);
      }
    });
  };
  draw();
}

function step1(st) {
  const v = st.v;
  return html`
    <div class="field"><label>车名</label><input class="input" name="name" value="${v.name}" maxlength="32" placeholder="如：秦PLUS、家里的车"></div>
    <div class="grid-2"><div class="field"><label>车牌（可选）</label><input class="input" name="plate" value="${v.plate}" maxlength="16" placeholder="京A·12345"></div>
      <div class="field"><label>品牌型号（可选）</label><input class="input" name="model" value="${v.model}" maxlength="32" placeholder="比亚迪 秦PLUS DM-i"></div></div>
    <div class="field"><label>车型</label><div class="type-grid">${["phev", "erev", "bev", "hev", "ice"].map((t) => html`<button type="button" class="type-tile ${v.energy_type === t ? "on" : ""}" data-type="${t}">
      <span class="type-ico">${raw(icon(t === "bev" ? "bolt" : t === "ice" || t === "hev" ? "fuel" : "car", 22))}</span><b>${ENERGY_LABEL[t]}</b><small>${TYPE_DESC[t]}</small></button>`)}</div></div>
    <div class="field"><label>颜色</label><div class="swatches">${COLORS.map((c) => html`<button type="button" class="swatch ${v.color === c ? "on" : ""}" data-color="${c}" style="background:${c}" aria-label="${c}"></button>`)}</div></div>`;
}

function step2(st) {
  const v = st.v;
  const t = v.energy_type;
  return html`
    ${hasFuel(t) ? html`<div class="field"><label>油箱容积</label><div class="input-wrap"><input class="input num" name="tank_capacity_l" inputmode="decimal" value="${v.tank_capacity_l ?? ""}" placeholder="如 48"><span class="unit">L</span></div>
      <span class="hint">用于「加满自动算油量」和油表读数换算，可查官方参数</span></div>` : ""}
    ${hasElec(t) ? html`<div class="field"><label>电池可用容量</label><div class="input-wrap"><input class="input num" name="battery_usable_kwh" inputmode="decimal" value="${v.battery_usable_kwh ?? ""}" placeholder="如 18.3"><span class="unit">kWh</span></div>
      <span class="hint">常见：18.3 / 26.6 / 46 / 60 / 75 kWh。不确定可以不填，充几次电后会自动校准</span></div>
      <div class="field"><label>平时充电上限</label><div class="input-wrap"><input class="input num" name="charge_limit_pct" inputmode="numeric" value="${v.charge_limit_pct}"><span class="unit">%</span></div></div>
      <div class="field"><label>常用充电方式</label><div class="chips" data-ctype>${Object.entries(CHARGE_LABEL).map(([k, l]) => html`<button type="button" class="chip sm ${v.default_charge_type === k ? "on" : ""}" data-v="${k}">${l}</button>`)}</div></div>` : ""}
    ${hasFuel(t) ? html`<div class="field"><label>默认标号</label><div class="chips" data-grade>${FUEL_GRADES.map((g) => html`<button type="button" class="chip sm ${v.default_fuel_grade === g ? "on" : ""}" data-v="${g}">${gradeLabel(g)}</button>`)}</div></div>
      <div class="field"><label>油表显示方式</label><div class="segmented" data-gauge>${[["pct", "百分比"], ["bars", "格数"], ["liters", "升"]].map(([k, l]) => html`<button type="button" data-v="${k}" class="${v.fuel_gauge_mode === k ? "on" : ""}">${l}</button>`)}</div></div>
      <div class="field ${v.fuel_gauge_mode === "bars" ? "" : "hidden"}" data-bars-f><label>油表总格数</label><div class="input-wrap"><input class="input num" name="fuel_gauge_bars" inputmode="numeric" value="${v.fuel_gauge_bars ?? 8}"><span class="unit">格</span></div></div>` : ""}
    <div class="field"><label>记录习惯</label><div class="segmented" data-style>${[["detailed", "精细"], ["simple", "简单"]].map(([k, l]) => html`<button type="button" data-v="${k}" class="${v.record_style === k ? "on" : ""}">${l}</button>`)}</div>
      <span class="hint">精细：电量、油量放在表单显眼位置，能耗更准；简单：收进「更多」里</span></div>`;
}

function step3(st) {
  const s = st.start;
  const t = st.v.energy_type;
  const modes = [["existing", "已经开了一段时间", "填提车和现在的状态，历史可以粗略估算"], ["new", "新车，从现在开始记", "只填当前里程和状态"], ["skip", "以后再说", "第一条记录会自动作为起点"]];
  return html`<div class="mode-list">${modes.map(([k, l, d]) => html`<button type="button" class="mode-tile ${s.mode === k ? "on" : ""}" data-mode="${k}"><span class="radio"></span><span><b>${l}</b><small>${d}</small></span></button>`)}</div>
    ${s.mode === "existing" ? html`
      <div class="sub-title">提车时</div>
      <div class="grid-2"><div class="field"><label>提车日期</label><input class="input" type="date" name="delivery_date" value="${st.v.delivery_date || ""}" max="${todayISO()}"></div>
        <div class="field"><label>提车里程</label><div class="input-wrap"><input class="input num" name="delivery_odo" inputmode="numeric" value="${s.delivery_odo ?? ""}" placeholder="如 12"><span class="unit">km</span></div></div></div>
      ${hasFuel(t) ? html`<div class="grid-3"><div class="field"><label>出厂自带油</label><div class="input-wrap"><input class="input num" name="factory_fuel_l" inputmode="decimal" value="${st.v.factory_fuel_l}"><span class="unit">L</span></div></div>
        <div class="field"><label>提车时加了</label><div class="input-wrap"><input class="input num" name="delivery_fill_l" inputmode="decimal" value="${s.delivery_fill_l ?? ""}" placeholder="没加不填"><span class="unit">L</span></div></div>
        <div class="field"><label>花了</label><div class="input-wrap"><input class="input num" name="delivery_fill_yuan" inputmode="decimal" value="${s.delivery_fill_yuan ?? ""}"><span class="unit">元</span></div></div></div>
        <p class="tiny muted">出厂自带油量记得就改成实际值（比如当时油表读数 × 油箱容积），不记得就用默认 5L。</p>` : ""}
      <div class="sub-title">现在</div>` : ""}
    ${s.mode !== "skip" ? html`<div class="grid-3">
      <div class="field"><label>当前里程</label><div class="input-wrap"><input class="input num" name="current_odo" inputmode="numeric" value="${s.current_odo ?? ""}"><span class="unit">km</span></div></div>
      ${hasFuel(t) ? html`<div class="field"><label>当前油量</label><div class="input-wrap"><input class="input num" name="current_fuel_pct" inputmode="decimal" value="${s.current_fuel_pct ?? ""}" placeholder="可不填"><span class="unit">%</span></div></div>` : ""}
      ${hasElec(t) ? html`<div class="field"><label>当前电量</label><div class="input-wrap"><input class="input num" name="current_soc" inputmode="decimal" value="${s.current_soc ?? ""}" placeholder="可不填"><span class="unit">%</span></div></div>` : ""}
    </div>` : ""}
    ${s.mode === "existing" ? html`<details class="more" ${Object.keys(st.summary).length ? "open" : ""}><summary>${raw(icon("down", 16))}补一笔历史汇总（可选）</summary>
      <p class="tiny muted">提车到现在没逐条记的加油、充电，知道大概总数就填，记不清可以跳过，不影响使用。<br>数据来源：家充桩 App 的累计度数、电费账单、支付宝/微信账单里搜「加油」「充电」。</p>
      ${hasFuel(t) ? html`<div class="grid-3"><div class="field"><label>加油合计</label><div class="input-wrap"><input class="input num" name="s_fuel_yuan" inputmode="decimal" value="${st.summary.s_fuel_yuan ?? ""}"><span class="unit">元</span></div></div>
        <div class="field"><label>或升数</label><div class="input-wrap"><input class="input num" name="s_fuel_l" inputmode="decimal" value="${st.summary.s_fuel_l ?? ""}"><span class="unit">L</span></div></div>
        <div class="field"><label>平均油价</label><div class="input-wrap"><input class="input num" name="s_fuel_price" inputmode="decimal" value="${st.summary.s_fuel_price ?? ""}" placeholder="7.5"><span class="unit">元/L</span></div></div></div>` : ""}
      ${hasElec(t) ? html`<div class="grid-3"><div class="field"><label>充电合计</label><div class="input-wrap"><input class="input num" name="s_elec_kwh" inputmode="decimal" value="${st.summary.s_elec_kwh ?? ""}"><span class="unit">kWh</span></div></div>
        <div class="field"><label>花了</label><div class="input-wrap"><input class="input num" name="s_elec_yuan" inputmode="decimal" value="${st.summary.s_elec_yuan ?? ""}"><span class="unit">元</span></div></div>
        <div class="field"><label>平均电价</label><div class="input-wrap"><input class="input num" name="s_elec_price" inputmode="decimal" value="${st.summary.s_elec_price ?? ""}" placeholder="0.6"><span class="unit">元/度</span></div></div></div>` : ""}
    </details>` : ""}`;
}

function step4(st) {
  const items = [["insurance", "保险到期", "date"], ["inspection", "年检", "date"], ["maintenance", "保养", "km"], ["tires", "轮胎检查/更换", "km"], ["brake_fluid", "更换刹车油", ""], ["cabin_filter", "更换空调滤芯", ""], ["battery_12v", "12V 小电瓶检查", ""], ["license", "驾驶证换证（个人）", "date"]];
  return html`<p class="muted small">勾选需要的提醒，到期前会在首页和铃铛里提示；规则以后都能改。</p>
    <div class="rem-pick">${items.map(([k, l, need]) => html`<label class="rem-pick-row">
      <input type="checkbox" data-rk="${k}" ${st.reminders[k] ? "checked" : ""}><span class="grow"><b>${l}</b></span>
      ${need === "date" ? html`<input class="input" type="date" data-rd="${k}" value="${st.remDates[k] || ""}" placeholder="到期日">` : need === "km" ? html`<span class="tiny muted">按里程自动算</span>` : html`<span class="tiny muted">按周期</span>`}
    </label>`)}</div>`;
}

function wireStep(f, st, draw) {
  f.addEventListener("click", (e) => {
    const t = e.target.closest("[data-type],[data-color],[data-mode]");
    if (!t) return;
    read(f, st);
    if (t.dataset.type) st.v.energy_type = t.dataset.type;
    if (t.dataset.color) st.v.color = t.dataset.color;
    if (t.dataset.mode) st.start.mode = t.dataset.mode;
    draw();
  });
  const ct = $("[data-ctype]", f);
  if (ct) bindChips(ct, { onChange: (k) => (st.v.default_charge_type = k) });
  const gr = $("[data-grade]", f);
  if (gr) bindChips(gr, { onChange: (k) => (st.v.default_fuel_grade = k) });
  const g = $("[data-gauge]", f);
  if (g) bindSegmented(g, (k) => {
    st.v.fuel_gauge_mode = k;
    $("[data-bars-f]", f).classList.toggle("hidden", k !== "bars");
  });
  const sty = $("[data-style]", f);
  if (sty) bindSegmented(sty, (k) => (st.v.record_style = k));
}

function read(f, st) {
  const g = (n) => f.querySelector(`[name="${n}"]`);
  const val = (n) => g(n)?.value?.trim();
  if (st.step === 1) {
    st.v.name = val("name") ?? st.v.name;
    st.v.plate = val("plate") ?? st.v.plate;
    st.v.model = val("model") ?? st.v.model;
  }
  if (st.step === 2) {
    if (g("tank_capacity_l")) st.v.tank_capacity_l = toNum(val("tank_capacity_l"));
    if (g("battery_usable_kwh")) st.v.battery_usable_kwh = toNum(val("battery_usable_kwh"));
    if (g("charge_limit_pct")) st.v.charge_limit_pct = toNum(val("charge_limit_pct")) || 100;
    if (g("fuel_gauge_bars")) st.v.fuel_gauge_bars = toNum(val("fuel_gauge_bars"));
  }
  if (st.step === 3) {
    if (g("delivery_date")) st.v.delivery_date = val("delivery_date") || null;
    if (g("factory_fuel_l")) st.v.factory_fuel_l = toNum(val("factory_fuel_l")) ?? 5;
    for (const n of ["delivery_odo", "delivery_fill_l", "delivery_fill_yuan", "current_odo", "current_fuel_pct", "current_soc"]) {
      if (g(n)) st.start[n] = toNum(val(n));
    }
    for (const n of ["s_fuel_yuan", "s_fuel_l", "s_fuel_price", "s_elec_kwh", "s_elec_yuan", "s_elec_price"]) {
      if (g(n)) {
        const x = toNum(val(n));
        if (x == null) delete st.summary[n];
        else st.summary[n] = x;
      }
    }
  }
  if (st.step === 4) {
    $$("[data-rk]", f).forEach((c) => (st.reminders[c.dataset.rk] = c.checked));
    $$("[data-rd]", f).forEach((d) => (st.remDates[d.dataset.rd] = d.value || null));
  }
}

function validateStep(st) {
  if (st.step === 1 && !st.v.name) return "请填写车名";
  if (st.step === 2) {
    if (st.v.tank_capacity_l != null && (st.v.tank_capacity_l <= 0 || st.v.tank_capacity_l > 300)) return "油箱容积不太对";
    if (st.v.battery_usable_kwh != null && (st.v.battery_usable_kwh <= 0 || st.v.battery_usable_kwh > 300)) return "电池容量不太对";
  }
  if (st.step === 3 && st.start.mode !== "skip") {
    if (st.start.current_odo == null) return "请填写当前里程";
    if (st.start.mode === "existing" && st.start.delivery_odo != null && st.start.delivery_odo > st.start.current_odo) return "提车里程不能大于当前里程";
    for (const n of ["current_fuel_pct", "current_soc"]) if (st.start[n] != null && (st.start[n] < 0 || st.start[n] > 100)) return "油量和电量要在 0–100% 之间";
  }
  return "";
}

function buildPayload(st) {
  const v = { ...st.v };
  if (!hasFuel(v.energy_type)) v.tank_capacity_l = null;
  if (!hasElec(v.energy_type)) v.battery_usable_kwh = null;
  if (v.fuel_gauge_mode !== "bars") v.fuel_gauge_bars = null;
  const s = st.start;
  const body = { ...v, start: null, summary: null, reminders: [] };
  if (s.mode !== "skip") {
    body.start = {
      mode: s.mode, delivery_odo: s.delivery_odo ?? null, delivery_fill_l: s.delivery_fill_l ?? null,
      delivery_fill_cents: s.delivery_fill_yuan != null ? Math.round(s.delivery_fill_yuan * 100) : null,
      current_odo: s.current_odo, current_fuel_pct: s.current_fuel_pct ?? null, current_soc: s.current_soc ?? null,
    };
    if (s.mode === "existing") body.initial_odometer_km = s.delivery_odo ?? null;
  }
  const sm = st.summary;
  if (s.mode === "existing" && Object.keys(sm).length) {
    body.summary = {
      fuel_liters: sm.s_fuel_l ?? null, fuel_cents: sm.s_fuel_yuan != null ? Math.round(sm.s_fuel_yuan * 100) : null, fuel_price: sm.s_fuel_price ?? null,
      elec_kwh: sm.s_elec_kwh ?? null, elec_cents: sm.s_elec_yuan != null ? Math.round(sm.s_elec_yuan * 100) : null, elec_price: sm.s_elec_price ?? null,
    };
  }
  for (const [k, on] of Object.entries(st.reminders)) {
    if (on) body.reminders.push({ template_key: k, due_date: st.remDates[k] || null });
  }
  return body;
}

/* ---------- 车辆详情 ---------- */

export async function vehiclePage() {
  resetView();
  setNav("vehicle");
  const v = currentVehicle();
  if (!v) return navigate("/vehicles/new");
  setTitle(v.name);
  const el = view();
  el.innerHTML = String(skeleton(4, 120));
  listen("vehicle-changed", () => vehiclePage());
  listen("data-changed", () => vehiclePage());
  let health;
  let members;
  let summaries;
  let acts;
  let invites = [];
  try {
    [health, members, summaries, acts] = await Promise.all([
      api(`/api/vehicles/${v.id}/health`), api(`/api/vehicles/${v.id}/members`),
      api(`/api/summaries?vehicle_id=${v.id}`), api(`/api/vehicles/${v.id}/activity?limit=15`),
    ]);
    if (v.role === "owner") invites = await api(`/api/vehicles/${v.id}/invites`);
  } catch (e) {
    el.innerHTML = String(emptyState({ ico: "warning", title: "加载失败", text: e.message }));
    return;
  }
  const owner = v.role === "owner";
  const editable = (v.role === "owner" || v.role === "editor") && !v.is_archived;
  const cov = health.coverage;
  el.innerHTML = String(html`<div id="vpage">
    <div class="card vehicle-hero" style="--vc:${v.color}">
      <div class="row">${vehicleDot(v, 26)}<div class="grow"><h2>${v.name}</h2><div class="muted small">${ENERGY_LABEL[v.energy_type]}${v.plate ? " · " + v.plate : ""}${v.model ? " · " + v.model : ""}</div></div>
        ${editable ? html`<button class="btn btn-sm" id="v-edit">${raw(icon("edit", 16))}参数</button>` : ""}</div>
      <div class="v-params">
        ${health.latest_odo != null ? html`<div><span>当前里程</span><b class="num">${km(health.latest_odo)} km</b></div>` : ""}
        ${v.has_fuel ? html`<div><span>油箱</span><b class="num">${v.tank_capacity_l ? num(v.tank_capacity_l) + " L" : "未填"}</b></div>` : ""}
        ${v.has_elec ? html`<div><span>电池</span><b class="num">${v.battery_usable_kwh ? num(v.battery_usable_kwh) + " kWh" : "未填"}</b></div>` : ""}
        ${v.has_elec ? html`<div><span>每 1% 电量</span><b class="num">${health.k != null ? fixed(health.k, 3) + " kWh" : "–"}</b></div>` : ""}
        <div><span>我的角色</span><b>${ROLE_LABEL[v.role]}</b></div>
      </div>
      ${v.is_archived ? html`<div class="callout">${raw(icon("info", 16))}这辆车已归档，只能查看</div>` : ""}
    </div>

    <div class="section-title"><span>数据体检</span><span class="tiny">缺数据只会降低精度，不影响使用</span></div>
    <div class="card">
      <div class="coverage">${Object.entries(cov).map(([k, c]) => html`<div class="cov-item"><div class="row between"><span class="small">${k === "fuel" ? "油耗" : "电耗"}精确覆盖</span><b class="num">${c != null ? num(c * 100, 0) + "%" : "–"}</b></div>
        <div class="progress ${c != null && c > 0.6 ? "ok" : "soon"}"><i style="width:${(c || 0) * 100}%"></i></div></div>`)}
        <p class="tiny muted">记账以来 ${km(health.ledger_km)} km 里，能算出精确能耗的里程占比</p></div>
      ${health.issues.length ? html`<div class="issues">${health.issues.map((i) => html`<div class="issue ${i.level}" data-key="${i.key}">
        <span class="${i.level === "warn" ? "warn-c" : "muted"}">${raw(icon(i.level === "warn" ? "warning" : "info", 18))}</span><span class="grow small">${i.text}</span>
        <span class="row">${issueAction(i, editable)}<button class="icon-btn" data-dismiss="${i.key}" aria-label="不再提示" style="width:30px;height:30px">${raw(icon("x", 14))}</button></span></div>`)}</div>`
        : html`<div class="row ok-c small" style="margin-top:10px">${raw(icon("check", 18))}数据很完整</div>`}
    </div>

    <div class="section-title"><span>成员（${members.length}）</span>${owner ? html`<button class="link-btn" id="m-add">${raw(icon("plus", 14))} 邀请</button>` : ""}</div>
    <div class="card flush list" id="members">${members.map((m) => html`<div class="list-item">${avatar(m, "")}<span class="grow">${m.name}${m.is_me ? html` <span class="badge">我</span>` : ""}<div class="tiny muted">@${m.username} · ${fmtDate(m.joined_at)} 加入</div></span>
      ${owner && !m.is_me ? html`<button class="chip sm" data-role="${m.id}">${m.role_label} ${raw(icon("down", 12))}</button>` : html`<span class="badge">${m.role_label}</span>`}</div>`)}</div>
    ${owner && invites.length ? html`<div class="card flush list" style="margin-top:10px">${invites.map((i) => html`<div class="list-item"><span class="ico sm brand">${raw(icon("key", 16))}</span>
      <span class="grow"><b class="num code">${i.code}</b><div class="tiny muted">${i.role_label} · ${fmtDate(i.expires_at)} 过期 · 已用 ${i.used_count}/${i.max_uses}${i.allow_signup ? " · 可注册" : ""}</div></span>
      <button class="icon-btn" data-copy="${i.code}" aria-label="复制链接">${raw(icon("copy", 18))}</button><button class="icon-btn" data-revoke="${i.id}" aria-label="作废">${raw(icon("trash", 18))}</button></div>`)}</div>` : ""}

    <div class="section-title"><span>历史 / 周期汇总</span>${editable ? html`<button class="link-btn" id="s-add">${raw(icon("plus", 14))} 添加</button>` : ""}</div>
    ${summaries.length ? html`<div class="card flush list">${summaries.map((s) => html`<button class="list-item" data-sum="${s.id}"><span class="ico sm">${raw(icon("history", 16))}</span>
      <span class="grow">${s.start_date} 至 ${s.end_date} <span class="badge">${s.mode === "total" ? "按总数" : "补充没记的"}</span>
        <div class="tiny muted">${[s.fuel_liters != null && num(s.fuel_liters) + "L", s.fuel_cents != null && "加油 " + money(s.fuel_cents), s.elec_kwh != null && num(s.elec_kwh) + "kWh", s.elec_cents != null && "充电 " + money(s.elec_cents)].filter(Boolean).join(" · ")}
        ${s.diff?.warnings?.length ? html` · <span class="warn-c">总量小于已记录量</span>` : ""}</div></span>${raw(icon("right", 16))}</button>`)}</div>`
      : html`<div class="card"><p class="small muted">一条条记不清时，可以按一段时间填合计（如家充桩 App 的月度度数），系统会自动扣掉已经逐条记过的部分。</p></div>`}

    <div class="section-title"><span>最近动态</span></div>
    <div class="card"><div class="activity">${acts.length ? acts.map((a) => html`<div class="act-item">${avatar(a.user, "sm")}<div class="grow"><div class="small">${a.summary}</div><div class="tiny muted">${a.user?.name || ""} · ${ago(a.created_at)}</div></div></div>`) : html`<p class="muted small">暂无</p>`}</div></div>

    <div class="section-title"><span>更多</span></div>
    <div class="card flush list">
      <a class="list-item" data-link href="/vehicles/new">${raw(`<span class="ico sm">${icon("plus", 16)}</span>`)}<span class="grow">添加另一辆车</span>${raw(icon("right", 16))}</a>
      ${owner ? html`<button class="list-item" id="v-archive">${raw(`<span class="ico sm">${icon("box", 16)}</span>`)}<span class="grow">${v.is_archived ? "取消归档" : "归档（卖掉的车）"}</span></button>` : ""}
      ${!owner || members.filter((m) => m.role === "owner").length > 1 ? html`<button class="list-item" id="v-leave">${raw(`<span class="ico sm warn">${icon("logout", 16)}</span>`)}<span class="grow">退出这辆车</span></button>` : ""}
      ${owner ? html`<button class="list-item" id="v-delete">${raw(`<span class="ico sm danger">${icon("trash", 16)}</span>`)}<span class="grow danger-c">删除车辆和全部数据</span></button>` : ""}
    </div></div>`);
  bindVehiclePage($("#vpage", el), v, members, summaries);
}

function issueAction(i, editable) {
  if (!editable) return "";
  const map = { fix_missed: "补一条", record_fuel: "记加油", edit_vehicle: "去填写", finish_charge: "结束", open_summaries: "查看", filter_estimated: "查看" };
  return map[i.action] ? html`<button class="btn btn-sm btn-soft" data-act="${i.action}" data-key="${i.key}">${map[i.action]}</button>` : "";
}

function bindVehiclePage(el, v, members, summaries) {
  $("#v-edit", el)?.addEventListener("click", () => editVehicle(v));
  $("#m-add", el)?.addEventListener("click", () => inviteSheet(v));
  $("#s-add", el)?.addEventListener("click", () => summarySheet(v));
  el.addEventListener("click", async (e) => {
    const t = e.target.closest("[data-dismiss],[data-act],[data-role],[data-copy],[data-revoke],[data-sum]");
    if (!t) return;
    try {
      if (t.dataset.dismiss) {
        await api(`/api/vehicles/${v.id}/hints/dismiss`, { method: "POST", body: { key: t.dataset.dismiss } });
        t.closest(".issue").remove();
      } else if (t.dataset.act) {
        await issueGo(v, t.dataset.act, t.dataset.key);
      } else if (t.dataset.role) {
        roleSheet(v, members.find((m) => m.id === +t.dataset.role));
      } else if (t.dataset.copy) {
        copyInvite(t.dataset.copy);
      } else if (t.dataset.revoke) {
        if (await confirmDialog({ title: "作废邀请码？", message: "作废后这个邀请码不能再使用。", ok: "作废", danger: true })) {
          await api(`/api/invites/${t.dataset.revoke}`, { method: "DELETE" });
          emit("data-changed");
        }
      } else if (t.dataset.sum) {
        summarySheet(v, summaries.find((s) => s.id === +t.dataset.sum));
      }
    } catch (ex) {
      toastError(ex);
    }
  });
  $("#v-archive", el)?.addEventListener("click", async () => {
    await api(`/api/vehicles/${v.id}/archive`, { method: "POST", body: { archived: !v.is_archived } });
    await loadVehicles();
    refreshChrome();
    emit("data-changed");
  });
  $("#v-leave", el)?.addEventListener("click", async () => {
    if (!(await confirmDialog({ title: "退出这辆车？", message: "退出后你将看不到这辆车的数据，你记过的记录会保留。", ok: "退出", danger: true }))) return;
    try {
      await api(`/api/vehicles/${v.id}/leave`, { method: "POST" });
      await loadVehicles();
      refreshChrome();
      navigate("/");
    } catch (ex) {
      toastError(ex);
    }
  });
  $("#v-delete", el)?.addEventListener("click", async () => {
    const name = await promptDialog({ title: "删除车辆", label: `删除后所有成员都看不到这辆车，记录、费用、提醒全部删除且不能恢复。请输入车名「${v.name}」确认：`, ok: "删除" });
    if (name == null) return;
    try {
      await api(`/api/vehicles/${v.id}/delete`, { method: "POST", body: { confirm_name: name } });
      await loadVehicles();
      refreshChrome();
      toast("已删除");
      navigate("/");
    } catch (ex) {
      toastError(ex);
    }
  });
}

async function issueGo(v, act, key) {
  const { openRecordForm } = await import("./record-form.js");
  if (act === "fix_missed") {
    const h = await api(`/api/vehicles/${v.id}/health`);
    const i = h.issues.find((x) => x.key === key);
    if (!i) return;
    const r = await api(`/api/vehicles/${v.id}/fix/missed`, { method: "POST", body: { after_id: i.after_id, before_id: i.before_id, kind: i.kind } });
    toast(`已自动补一条（约 ${num(r.qty)}${i.kind === "fuel" ? "L" : "kWh"}）`, { sub: "标记为估算，可在记录里修改" });
    emit("data-changed");
  } else if (act === "record_fuel") openRecordForm({ kind: "fuel" });
  else if (act === "edit_vehicle") editVehicle(v);
  else if (act === "filter_estimated") navigate("/records");
  else if (act === "finish_charge") {
    const id = +key.split(":")[1];
    const r = await api(`/api/records/${id}`);
    openRecordForm({ record: { ...r, type: "record" }, vehicle: v, finish: true });
  }
}

function roleSheet(v, m) {
  sheet({
    title: m.name,
    render(body, ctx) {
      body.innerHTML = String(html`<div class="list card flush">${Object.entries(ROLE_LABEL).map(([k, l]) => html`<button class="list-item" data-r="${k}"><span class="grow"><b>${l}</b>
        <div class="tiny muted">${{ owner: "可以管理成员、删除车辆", editor: "可以记录和编辑，只能删除自己记的", viewer: "只能查看" }[k]}</div></span>${m.role === k ? raw(`<span class="ok-c">${icon("check")}</span>`) : ""}</button>`)}</div>
        <button class="btn btn-danger btn-block" style="margin-top:14px" data-remove>${raw(icon("trash", 18))}移出这辆车</button>`);
      body.addEventListener("click", async (e) => {
        try {
          const r = e.target.closest("[data-r]");
          if (r) {
            await api(`/api/vehicles/${v.id}/members/${m.id}`, { method: "PUT", body: { role: r.dataset.r } });
            ctx.close();
            emit("data-changed");
          }
          if (e.target.closest("[data-remove]")) {
            if (!(await confirmDialog({ title: "移出成员？", message: `移出后 ${esc(m.name)} 将看不到这辆车，已记的记录会保留。`, ok: "移出", danger: true }))) return;
            await api(`/api/vehicles/${v.id}/members/${m.id}`, { method: "DELETE" });
            ctx.close();
            emit("data-changed");
          }
        } catch (ex) {
          toastError(ex);
        }
      });
    },
  });
}

function copyInvite(code) {
  const link = `${location.origin}/join/${code}`;
  const text = `邀请你一起记录用车数据，打开 ${link} 或在「我的 → 加入车辆」输入邀请码 ${code}`;
  (navigator.clipboard?.writeText(text) || Promise.reject()).then(
    () => toast("邀请链接已复制", { sub: code }),
    () => promptDialog({ title: "复制邀请", label: "长按复制下面的内容", value: text })
  );
}

function inviteSheet(v) {
  sheet({
    title: "邀请成员",
    render(body, ctx) {
      body.innerHTML = String(html`<div class="stack">
        <div class="segmented" id="inv-mode"><button data-v="code" class="on">邀请码 / 链接</button><button data-v="user">按用户名添加</button></div>
        <div class="field"><label>角色</label><div class="chips" id="inv-role">${Object.entries(ROLE_LABEL).map(([k, l]) => html`<button class="chip sm ${k === "editor" ? "on" : ""}" data-v="${k}">${l}</button>`)}</div></div>
        <div data-pane="code" class="stack">
          <div class="grid-2"><div class="field"><label>有效期</label><div class="input-wrap"><input class="input num" name="days" value="7" inputmode="numeric"><span class="unit">天</span></div></div>
            <div class="field"><label>可用次数</label><div class="input-wrap"><input class="input num" name="uses" value="1" inputmode="numeric"><span class="unit">次</span></div></div></div>
          ${switchRow("signup", "允许对方用链接自己注册账号", false, "需要管理员在后台打开「允许邀请注册」")}
        </div>
        <div data-pane="user" class="hidden"><div class="field"><label>对方的用户名</label><input class="input" name="username" placeholder="账号由管理员创建"></div></div>
        <div class="form-error"></div></div>`);
      let mode = "code";
      bindSegmented($("#inv-mode", body), (k) => {
        mode = k;
        $$("[data-pane]", body).forEach((p) => p.classList.toggle("hidden", p.dataset.pane !== k));
      });
      bindChips($("#inv-role", body));
      ctx.setFoot(`<button class="btn btn-primary grow" data-ok>${icon("check", 18)}确定</button>`);
      $("[data-ok]", ctx.foot).onclick = async (e) => {
        const role = $("#inv-role .chip.on", body).dataset.v;
        setLoading(e.currentTarget, true);
        try {
          if (mode === "user") {
            await api(`/api/vehicles/${v.id}/members`, { method: "POST", body: { username: $("[name=username]", body).value.trim(), role } });
            toast("已添加成员");
          } else {
            const inv = await api(`/api/vehicles/${v.id}/invites`, { method: "POST", body: {
              role, days: toNum($("[name=days]", body).value) || 7, max_uses: toNum($("[name=uses]", body).value) || 1,
              allow_signup: $("[name=signup]", body).checked } });
            copyInvite(inv.code);
          }
          ctx.close();
          emit("data-changed");
        } catch (ex) {
          $(".form-error", body).textContent = ex.message;
        } finally {
          setLoading(e.currentTarget, false);
        }
      };
    },
  });
}

function summarySheet(v, s = null) {
  sheet({
    title: s ? "编辑汇总" : "添加汇总",
    render(body, ctx) {
      body.innerHTML = String(html`<div class="stack">
        <p class="tiny muted">一段时间内没有逐条记录的补能，按合计填写。只用于花费统计和长期粗略能耗，不影响精确能耗段。</p>
        <div class="grid-2"><div class="field"><label>开始日期</label><input class="input" type="date" name="start_date" value="${s?.start_date || ""}"></div>
          <div class="field"><label>结束日期</label><input class="input" type="date" name="end_date" value="${s?.end_date || todayISO()}"></div></div>
        <div class="field"><label>填写方式</label><div class="segmented" id="s-mode"><button data-v="extra" class="${!s || s.mode === "extra" ? "on" : ""}">补充没记的部分</button><button data-v="total" class="${s?.mode === "total" ? "on" : ""}">按总数（自动扣除已记录）</button></div></div>
        ${v.has_fuel ? html`<div class="grid-3"><div class="field"><label>加油升数</label><input class="input num" name="fuel_liters" inputmode="decimal" value="${s?.fuel_liters ?? ""}"></div>
          <div class="field"><label>加油金额</label><input class="input num" name="fuel_yuan" inputmode="decimal" value="${s?.fuel_cents != null ? s.fuel_cents / 100 : ""}"></div>
          <div class="field"><label>平均油价</label><input class="input num" name="fuel_price" inputmode="decimal" value="${s?.fuel_price ?? ""}" placeholder="只填金额时用"></div></div>` : ""}
        ${v.has_elec ? html`<div class="grid-3"><div class="field"><label>充电度数</label><input class="input num" name="elec_kwh" inputmode="decimal" value="${s?.elec_kwh ?? ""}"></div>
          <div class="field"><label>充电金额</label><input class="input num" name="elec_yuan" inputmode="decimal" value="${s?.elec_cents != null ? s.elec_cents / 100 : ""}"></div>
          <div class="field"><label>平均电价</label><input class="input num" name="elec_price" inputmode="decimal" value="${s?.elec_price ?? ""}" placeholder="只填金额时用"></div></div>` : ""}
        ${s?.diff ? html`<div class="formula">同期已逐条记录：${v.has_fuel ? `加油 ${num(s.diff.recorded_fuel_l)}L ` : ""}${v.has_elec ? `充电 ${num(s.diff.recorded_elec_kwh)}kWh` : ""}<br>计入的补差：${v.has_fuel ? `${num(s.diff.fuel_l ?? 0)}L ` : ""}${v.has_elec ? `${num(s.diff.elec_kwh ?? 0)}kWh` : ""}</div>` : ""}
        <div class="field"><label>备注</label><input class="input" name="note" value="${s?.note || ""}" maxlength="200"></div>
        <div class="form-error"></div></div>`);
      const getMode = bindSegmented($("#s-mode", body));
      const btns = [`<button class="btn btn-primary grow" data-ok>${icon("check", 18)}保存</button>`];
      if (s) btns.unshift(`<button class="btn btn-danger" data-del>${icon("trash", 18)}</button>`);
      ctx.setFoot(btns.join(""));
      $("[data-del]", ctx.foot)?.addEventListener("click", async () => {
        await api(`/api/summaries/${s.id}`, { method: "DELETE" });
        ctx.close();
        emit("data-changed");
      });
      $("[data-ok]", ctx.foot).onclick = async (e) => {
        const g = (n) => $(`[name=${n}]`, body);
        const payload = {
          vehicle_id: v.id, start_date: g("start_date").value, end_date: g("end_date").value, mode: getMode(),
          fuel_liters: g("fuel_liters") ? toNum(g("fuel_liters").value) : null, fuel_cents: g("fuel_yuan") ? yuanToCents(g("fuel_yuan").value) : null,
          fuel_price: g("fuel_price") ? toNum(g("fuel_price").value) : null,
          elec_kwh: g("elec_kwh") ? toNum(g("elec_kwh").value) : null, elec_cents: g("elec_yuan") ? yuanToCents(g("elec_yuan").value) : null,
          elec_price: g("elec_price") ? toNum(g("elec_price").value) : null, note: g("note").value.trim(), version: s?.version,
        };
        if (!payload.start_date || !payload.end_date) return ($(".form-error", body).textContent = "请填写起止日期");
        setLoading(e.currentTarget, true);
        try {
          await api(s ? `/api/summaries/${s.id}` : "/api/summaries", { method: s ? "PUT" : "POST", body: payload });
          ctx.close();
          emit("data-changed");
          toast("已保存汇总");
        } catch (ex) {
          $(".form-error", body).textContent = ex.message;
        } finally {
          setLoading(e.currentTarget, false);
        }
      };
    },
  });
}

function editVehicle(v) {
  sheet({
    title: "车辆参数",
    render(body, ctx) {
      const c = v.calc_settings || {};
      body.innerHTML = String(html`<form class="stack" novalidate>
        <div class="grid-2"><div class="field"><label>车名</label><input class="input" name="name" value="${v.name}" maxlength="32"></div>
          <div class="field"><label>车牌</label><input class="input" name="plate" value="${v.plate}" maxlength="16"></div></div>
        <div class="field"><label>品牌型号</label><input class="input" name="model" value="${v.model}" maxlength="32"></div>
        <div class="field"><label>颜色</label><div class="swatches">${COLORS.map((col) => html`<button type="button" class="swatch ${v.color === col ? "on" : ""}" data-color="${col}" style="background:${col}"></button>`)}</div></div>
        <div class="grid-2">
          ${v.has_fuel ? html`<div class="field"><label>油箱容积</label><div class="input-wrap"><input class="input num" name="tank_capacity_l" inputmode="decimal" value="${v.tank_capacity_l ?? ""}"><span class="unit">L</span></div></div>` : ""}
          ${v.has_elec ? html`<div class="field"><label>电池可用容量</label><div class="input-wrap"><input class="input num" name="battery_usable_kwh" inputmode="decimal" value="${v.battery_usable_kwh ?? ""}"><span class="unit">kWh</span></div></div>` : ""}
          ${v.has_elec ? html`<div class="field"><label>充电上限</label><div class="input-wrap"><input class="input num" name="charge_limit_pct" inputmode="numeric" value="${v.charge_limit_pct}"><span class="unit">%</span></div></div>` : ""}
          ${v.has_fuel ? html`<div class="field"><label>默认标号</label><select class="input" name="default_fuel_grade">${FUEL_GRADES.map((g) => html`<option value="${g}" ${v.default_fuel_grade === g ? "selected" : ""}>${gradeLabel(g)}</option>`)}</select></div>` : ""}
          ${v.has_elec ? html`<div class="field"><label>常用充电方式</label><select class="input" name="default_charge_type">${Object.entries(CHARGE_LABEL).map(([k, l]) => html`<option value="${k}" ${v.default_charge_type === k ? "selected" : ""}>${l}</option>`)}</select></div>` : ""}
          ${v.has_fuel ? html`<div class="field"><label>油表显示方式</label><select class="input" name="fuel_gauge_mode">${[["pct", "百分比"], ["bars", "格数"], ["liters", "升"]].map(([k, l]) => html`<option value="${k}" ${v.fuel_gauge_mode === k ? "selected" : ""}>${l}</option>`)}</select></div>
            <div class="field"><label>油表总格数</label><input class="input num" name="fuel_gauge_bars" inputmode="numeric" value="${v.fuel_gauge_bars ?? ""}" placeholder="格数显示时填"></div>` : ""}
          <div class="field"><label>记录习惯</label><select class="input" name="record_style"><option value="detailed" ${v.record_style === "detailed" ? "selected" : ""}>精细</option><option value="simple" ${v.record_style === "simple" ? "selected" : ""}>简单</option></select></div>
          <div class="field"><label>初始里程</label><div class="input-wrap"><input class="input num" name="initial_odometer_km" inputmode="numeric" value="${v.initial_odometer_km ?? ""}"><span class="unit">km</span></div></div>
        </div>
        <details class="more"><summary>${raw(icon("down", 16))}计算设置</summary><div class="stack">
          <div class="grid-2"><div class="field"><label>最小段里程</label><div class="input-wrap"><input class="input num" name="min_segment_km" value="${c.min_segment_km ?? 30}"><span class="unit">km</span></div><span class="hint">短于这个里程的段合并到下一段</span></div>
            <div class="field"><label>电能折算系数</label><div class="input-wrap"><input class="input num" name="kwh_per_l" value="${c.kwh_per_l ?? 8.9}"><span class="unit">kWh/L</span></div><span class="hint">算等效油耗用</span></div></div>
          ${v.has_fuel ? html`<div class="field"><label>油表读数误差</label><div class="input-wrap"><input class="input num" name="gauge_err_pct" value="${c.gauge_err_pct ?? 5}"><span class="unit">%</span></div><span class="hint">车机能精确到 1% 的可以填 2</span></div>
            ${switchRow("allow_gauge_anchor", "没有两次加满时用油表读数估算", c.allow_gauge_anchor ?? true)}` : ""}
        </div></details>
        <div class="form-error"></div></form>`);
      let color = v.color;
      body.addEventListener("click", (e) => {
        const s = e.target.closest("[data-color]");
        if (!s) return;
        color = s.dataset.color;
        $$(".swatch", body).forEach((x) => x.classList.toggle("on", x === s));
      });
      ctx.setFoot(`<button class="btn btn-primary grow" data-ok>${icon("check", 18)}保存</button>`);
      $("[data-ok]", ctx.foot).onclick = async (e) => {
        const f = $("form", body);
        const n = (k, d = null) => (f[k] ? toNum(f[k].value) ?? d : d);
        const payload = {
          name: f.name.value.trim(), plate: f.plate.value.trim(), brand: v.brand, model: f.model.value.trim(), year: v.year,
          energy_type: v.energy_type, tank_capacity_l: n("tank_capacity_l"), battery_usable_kwh: n("battery_usable_kwh"),
          charge_limit_pct: n("charge_limit_pct", v.charge_limit_pct), default_fuel_grade: f.default_fuel_grade?.value || v.default_fuel_grade,
          default_charge_type: f.default_charge_type?.value || v.default_charge_type, fuel_gauge_mode: f.fuel_gauge_mode?.value || v.fuel_gauge_mode,
          fuel_gauge_bars: n("fuel_gauge_bars"), factory_fuel_l: v.factory_fuel_l, record_style: f.record_style.value,
          initial_odometer_km: n("initial_odometer_km"), delivery_date: v.delivery_date, purchase_price_cents: v.purchase_price_cents, color,
          calc_settings: { min_segment_km: n("min_segment_km", 30), kwh_per_l: n("kwh_per_l", 8.9), gauge_err_pct: n("gauge_err_pct", 5),
            allow_gauge_anchor: f.allow_gauge_anchor ? f.allow_gauge_anchor.checked : true },
        };
        if (!payload.name) return ($(".form-error", body).textContent = "请填写车名");
        setLoading(e.currentTarget, true);
        try {
          await api(`/api/vehicles/${v.id}`, { method: "PUT", body: payload });
          await loadVehicles();
          refreshChrome();
          ctx.close();
          emit("data-changed");
          toast("已保存，能耗已按新参数重新计算");
        } catch (ex) {
          $(".form-error", body).textContent = ex.message;
        } finally {
          setLoading(e.currentTarget, false);
        }
      };
    },
  });
}

export { state };
