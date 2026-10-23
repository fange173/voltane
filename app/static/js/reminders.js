/* reminders.js — 用车提醒：列表、完成并顺延、新建/编辑 */
import { icon } from "./icons.js";
import { listen, resetView, setNav, setTitle, updateBell, view } from "./shell.js";
import { EXPENSE_LABEL, canEdit, currentVehicle, emit, state } from "./state.js";
import { bindSegmented, confirmDialog, emptyState, setLoading, sheet, skeleton, switchRow, toast, toastError } from "./ui.js";
import { $, api, esc, fmtDate, html, km, money, num, raw, toNum, todayISO, yuanToCents } from "./util.js";

const TPL_ICON = {
  insurance: "shield", inspection: "clipboard", maintenance: "wrench", tires: "tire", brake_fluid: "drop",
  cabin_filter: "wash", battery_12v: "battery", loan: "wallet", license: "key", custom: "bell",
};
const STATUS = { overdue: ["已过期", "danger"], soon: ["临近", "warn"], ok: ["正常", "ok"], inactive: ["已停用", ""] };

function dueText(r) {
  const parts = [];
  if (r.days_left != null) parts.push(r.days_left < 0 ? `已过 ${-r.days_left} 天` : r.days_left === 0 ? "今天到期" : `还剩 ${r.days_left} 天`);
  if (r.km_left != null) parts.push(r.km_left < 0 ? `超出 ${km(-r.km_left)} km` : `还剩 ${km(r.km_left)} km`);
  if (r.km_left != null && r.km_left > 0 && r.eta_date && r.days_left == null) parts.push(`约 ${fmtDate(r.eta_date)}`);
  return parts.join(" · ") || "未设置到期";
}

export function reminderRow(r, { compact = false } = {}) {
  const [label, tone] = STATUS[r.status];
  const prog = Math.max(r.date_progress ?? 0, r.km_progress ?? 0);
  return html`<button class="list-item rem-row" data-rem="${r.id}">
    <span class="ico ${tone === "danger" ? "danger" : tone === "warn" ? "warn" : "brand"}">${raw(icon(TPL_ICON[r.template_key] || "bell", 20))}</span>
    <span class="grow"><span class="rec-title">${r.title}${r.personal ? html` <span class="badge">个人</span>` : ""}${!compact && r.vehicle_name && state.vehicles.length > 1 ? html` <span class="badge">${r.vehicle_name}</span>` : ""}${r.muted ? html` <span class="badge">已静音</span>` : ""}</span>
      <span class="rec-sub">${dueText(r)}${r.due_date && r.days_left != null && !compact ? ` · ${r.due_date}` : ""}</span>
      ${!compact && (r.date_progress != null || r.km_progress != null) ? html`<span class="progress ${r.status}" style="margin-top:6px"><i style="width:${Math.min(prog, 1) * 100}%"></i></span>` : ""}</span>
    ${tone ? html`<span class="badge ${tone}">${label}</span>` : html`<span class="badge">${label}</span>`}</button>`;
}

let scope = "vehicle";

export async function remindersPage() {
  resetView();
  setNav("reminders");
  setTitle("用车提醒");
  const el = view();
  const v = currentVehicle();
  el.innerHTML = String(html`<div class="row between toolbar">
      ${state.vehicles.length > 1 ? html`<div class="segmented" id="rem-scope" style="width:220px"><button data-v="vehicle" class="${scope === "vehicle" ? "on" : ""}">当前车辆</button><button data-v="all" class="${scope === "all" ? "on" : ""}">全部</button></div>` : html`<span></span>`}
      <button class="btn btn-primary btn-sm" id="rem-add">${raw(icon("plus", 16))}添加提醒</button></div>
    <div id="rem-list">${skeleton(4, 64)}</div>`);
  const load = async () => {
    try {
      const q = scope === "vehicle" && v ? `?vehicle_id=${v.id}` : "";
      let list = await api("/api/reminders" + q);
      if (scope === "vehicle" && v) {
        const personal = (await api("/api/reminders")).filter((r) => r.personal);
        list = list.concat(personal.filter((p) => !list.some((x) => x.id === p.id)));
      }
      draw(list);
    } catch (e) {
      $("#rem-list").innerHTML = String(emptyState({ ico: "warning", title: "加载失败", text: e.message }));
    }
  };
  const draw = (list) => {
    const box = $("#rem-list");
    if (!box) return;
    if (!list.length) {
      box.innerHTML = String(emptyState({ ico: "bell", title: "还没有提醒", text: "保险、年检、保养、轮胎……按日期或里程提醒，哪个先到算哪个" }));
      return;
    }
    const groups = [["overdue", "已过期"], ["soon", "即将到期"], ["ok", "正常"], ["inactive", "已停用"]];
    box.innerHTML = String(html`${groups.map(([k, l]) => {
      const arr = list.filter((r) => r.status === k);
      return arr.length ? html`<div class="section-title"><span>${l}</span><span>${arr.length}</span></div><div class="card flush list">${arr.map((r) => reminderRow(r))}</div>` : "";
    })}`);
    box.onclick = (e) => {
      const b = e.target.closest("[data-rem]");
      if (b) openReminder(list.find((r) => r.id === +b.dataset.rem));
    };
  };
  const sc = $("#rem-scope");
  if (sc) bindSegmented(sc, (k) => {
    scope = k;
    load();
  });
  $("#rem-add").onclick = () => pickTemplate();
  listen("data-changed", load);
  listen("vehicle-changed", () => remindersPage());
  await load();
}

async function openReminder(r) {
  const v = state.vehicles.find((x) => x.id === r.vehicle_id);
  const editable = r.personal || canEdit(v);
  sheet({
    title: r.title,
    render(body, ctx) {
      const [label, tone] = STATUS[r.status];
      body.innerHTML = String(html`
        <div class="rem-hero"><span class="ico ${tone === "danger" ? "danger" : tone === "warn" ? "warn" : "brand"}">${raw(icon(TPL_ICON[r.template_key] || "bell", 24))}</span>
          <div class="grow"><b>${dueText(r)}</b><div class="tiny muted">${r.vehicle_name || "个人提醒"}</div></div><span class="badge ${tone}">${label}</span></div>
        ${r.date_progress != null ? html`<div class="tiny muted" style="margin-top:12px">时间进度</div><div class="progress ${r.status}"><i style="width:${Math.min(r.date_progress, 1) * 100}%"></i></div>` : ""}
        ${r.km_progress != null ? html`<div class="tiny muted" style="margin-top:10px">里程进度</div><div class="progress ${r.status}"><i style="width:${Math.min(r.km_progress, 1) * 100}%"></i></div>` : ""}
        <div class="card" style="margin-top:14px">
          ${r.due_date ? html`<div class="kv"><span>到期日期</span><span>${r.due_date}</span></div>` : ""}
          ${r.due_odometer_km != null ? html`<div class="kv"><span>到期里程</span><span>${km(r.due_odometer_km)} km${r.current_odo != null ? `（当前 ${km(r.current_odo)}）` : ""}</span></div>` : ""}
          ${r.interval_months || r.interval_km ? html`<div class="kv"><span>周期</span><span>${[r.interval_months && `每 ${r.interval_months} 个月`, r.interval_km && `每 ${km(r.interval_km)} km`].filter(Boolean).join(" 或 ")}</span></div>` : ""}
          <div class="kv"><span>提前提醒</span><span>${r.lead_days} 天${r.due_odometer_km != null ? ` / ${km(r.lead_km)} km` : ""}</span></div>
          ${r.last_done_at ? html`<div class="kv"><span>上次完成</span><span>${r.last_done_at}${r.last_done_odometer_km != null ? ` · ${km(r.last_done_odometer_km)} km` : ""}</span></div>` : ""}
          ${r.note ? html`<div class="kv"><span>备注</span><span>${r.note}</span></div>` : ""}
        </div>`);
      const btns = [];
      if (editable && r.is_active) btns.push(`<button class="btn btn-primary grow" data-done>${icon("check", 18)}完成</button>`);
      if (editable) btns.push(`<button class="btn" data-edit>${icon("edit", 18)}</button>`);
      btns.push(`<button class="btn" data-mute title="${r.muted ? "恢复通知" : "不再通知我"}">${icon(r.muted ? "bell" : "x", 18)}${r.muted ? "恢复" : "静音"}</button>`);
      if (editable) btns.push(`<button class="btn btn-danger" data-del>${icon("trash", 18)}</button>`);
      ctx.setFoot(btns.join(""));
      $("[data-done]", ctx.foot)?.addEventListener("click", () => {
        ctx.close();
        setTimeout(() => completeReminder(r), 200);
      });
      $("[data-edit]", ctx.foot)?.addEventListener("click", () => {
        ctx.close();
        setTimeout(() => editReminder(r), 200);
      });
      $("[data-mute]", ctx.foot).addEventListener("click", async () => {
        await api(`/api/reminders/${r.id}/mute`, { method: "POST" });
        ctx.close();
        emit("data-changed");
        updateBell();
      });
      $("[data-del]", ctx.foot)?.addEventListener("click", async () => {
        if (!(await confirmDialog({ title: "删除提醒？", message: `删除「${esc(r.title)}」后不再提醒。`, ok: "删除", danger: true }))) return;
        await api(`/api/reminders/${r.id}`, { method: "DELETE" });
        ctx.close();
        emit("data-changed");
        updateBell();
      });
    },
  });
}

function completeReminder(r) {
  const cat = { insurance: "insurance", inspection: "inspection", tires: "tires", battery_12v: "repair", loan: "loan" }[r.template_key] || "maintenance";
  sheet({
    title: `完成「${r.title}」`,
    render(body, ctx) {
      body.innerHTML = String(html`<div class="stack">
        <div class="grid-2"><div class="field"><label>完成日期</label><input class="input" type="date" name="d" value="${todayISO()}"></div>
          <div class="field"><label>当时里程</label><div class="input-wrap"><input class="input num" name="o" inputmode="numeric" value="${r.current_odo != null ? Math.round(r.current_odo) : ""}"><span class="unit">km</span></div></div></div>
        ${r.vehicle_id ? html`<div class="field"><label>顺手记一笔${EXPENSE_LABEL[cat]}费用（可选）</label><div class="input-wrap"><input class="input num" name="c" inputmode="text" placeholder="0.00"><span class="unit">元</span></div></div>
          <div class="field"><label>商家（可选）</label><input class="input" name="vendor"></div>` : ""}
        <p class="tiny muted">${r.interval_months || r.interval_km ? "会以这次完成的日期和里程为基准顺延到下一次" : "这是一次性提醒，完成后会停用"}</p>
        <div class="form-error"></div></div>`);
      ctx.setFoot(`<button class="btn btn-primary grow" data-ok>${icon("check", 18)}完成</button>`);
      $("[data-ok]", ctx.foot).onclick = async (e) => {
        setLoading(e.currentTarget, true);
        try {
          const res = await api(`/api/reminders/${r.id}/complete`, { method: "POST", body: {
            done_at: $("[name=d]", body).value, odometer_km: toNum($("[name=o]", body).value),
            expense_cents: $("[name=c]", body) ? yuanToCents($("[name=c]", body).value) : null,
            expense_category: cat, vendor: $("[name=vendor]", body)?.value || "",
          } });
          ctx.close();
          emit("data-changed");
          updateBell();
          toast(res.is_active ? `已顺延到 ${[res.due_date, res.due_odometer_km != null && km(res.due_odometer_km) + " km"].filter(Boolean).join(" / ")}` : "已完成");
        } catch (ex) {
          $(".form-error", body).textContent = ex.message;
        } finally {
          setLoading(e.currentTarget, false);
        }
      };
    },
  });
}

async function pickTemplate() {
  let tpls = [];
  try {
    tpls = await api("/api/reminders/templates");
  } catch (e) {
    return toastError(e);
  }
  const t = await sheet({
    title: "添加提醒",
    render(body, ctx) {
      body.innerHTML = String(html`<div class="tpl-grid">${tpls.map((t) => html`<button class="action-tile" data-k="${t.key}">
        <span class="ico brand">${raw(icon(TPL_ICON[t.key] || "bell", 20))}</span><span><b>${t.title}</b><br><small>${[t.interval_months && `${t.interval_months} 个月`, t.interval_km && `${km(t.interval_km)} km`].filter(Boolean).join(" / ") || "按日期"}</small></span></button>`)}
        <button class="action-tile" data-k="custom"><span class="ico">${raw(icon("plus", 20))}</span><span><b>自定义</b><br><small>任意事项</small></span></button></div>`);
      body.onclick = (e) => {
        const b = e.target.closest("[data-k]");
        if (b) ctx.close(tpls.find((x) => x.key === b.dataset.k) || { key: "custom", title: "" });
      };
    },
  });
  if (t) setTimeout(() => editReminder(null, t), 200);
}

function editReminder(r, tpl = null) {
  const v = currentVehicle();
  const isNew = !r;
  const base = r || {
    title: tpl.title, template_key: tpl.key, interval_months: tpl.interval_months ?? null,
    interval_km: v && ["ice", "hev"].includes(v.energy_type) && tpl.ice_km ? tpl.ice_km : tpl.interval_km ?? null,
    lead_days: tpl.lead_days ?? 30, lead_km: tpl.lead_km ?? 500, personal: !!tpl.personal, is_active: true,
  };
  sheet({
    title: isNew ? "新建提醒" : "编辑提醒",
    render(body, ctx) {
      body.innerHTML = String(html`<form class="stack" novalidate>
        ${tpl?.hint ? html`<p class="callout small">${raw(icon("info", 16))}${tpl.hint}</p>` : ""}
        <div class="field"><label>名称</label><input class="input" name="title" value="${base.title}" maxlength="64" placeholder="如：更换机油"></div>
        <div class="grid-2"><div class="field"><label>到期日期</label><input class="input" type="date" name="due_date" value="${base.due_date || ""}"></div>
          <div class="field"><label>到期里程</label><div class="input-wrap"><input class="input num" name="due_odometer_km" inputmode="numeric" value="${base.due_odometer_km ?? ""}" placeholder="可不填"><span class="unit">km</span></div></div></div>
        <div class="grid-2"><div class="field"><label>每隔几个月</label><div class="input-wrap"><input class="input num" name="interval_months" inputmode="numeric" value="${base.interval_months ?? ""}" placeholder="一次性"><span class="unit">月</span></div></div>
          <div class="field"><label>每隔多少公里</label><div class="input-wrap"><input class="input num" name="interval_km" inputmode="numeric" value="${base.interval_km ?? ""}" placeholder="可不填"><span class="unit">km</span></div></div></div>
        <div class="grid-2"><div class="field"><label>提前几天提醒</label><div class="input-wrap"><input class="input num" name="lead_days" inputmode="numeric" value="${base.lead_days}"><span class="unit">天</span></div></div>
          <div class="field"><label>提前多少公里</label><div class="input-wrap"><input class="input num" name="lead_km" inputmode="numeric" value="${base.lead_km}"><span class="unit">km</span></div></div></div>
        <p class="tiny muted">日期和里程都填时，哪个先到算哪个；按里程的提醒会用最近的日均里程估算到期日。周期是常见值，请按保养手册修改。</p>
        ${isNew && v ? switchRow("personal", "只提醒我自己（不属于这辆车）", base.personal, "如驾驶证换证") : ""}
        ${!isNew ? switchRow("is_active", "启用", base.is_active) : ""}
        <div class="field"><label>备注</label><input class="input" name="note" value="${base.note || ""}" maxlength="200"></div>
        <div class="form-error"></div></form>`);
      // 到期里程不替用户猜，只提示当前里程
      const curOdo = state.vehicles.find((x) => x.id === (r ? r.vehicle_id : v?.id))?.latest_odo;
      if (curOdo) $("[name=due_odometer_km]", body).placeholder = `当前 ${km(curOdo)}`;
      ctx.setFoot(`<button class="btn btn-primary grow" data-ok>${icon("check", 18)}保存</button>`);
      $("[data-ok]", ctx.foot).onclick = async (e) => {
        const f = $("form", body);
        const personal = f.personal?.checked ?? r?.personal;
        const payload = {
          vehicle_id: personal ? null : r ? r.vehicle_id : v?.id,
          title: f.title.value.trim(), template_key: base.template_key || "custom",
          due_date: f.due_date.value || null, due_odometer_km: toNum(f.due_odometer_km.value),
          interval_months: toNum(f.interval_months.value), interval_km: toNum(f.interval_km.value),
          lead_days: toNum(f.lead_days.value) ?? 30, lead_km: toNum(f.lead_km.value) ?? 500,
          last_done_at: r?.last_done_at || null, last_done_odometer_km: r?.last_done_odometer_km ?? null,
          is_active: f.is_active ? f.is_active.checked : true, note: f.note.value.trim(),
        };
        if (!payload.title) return ($(".form-error", body).textContent = "请填写名称");
        setLoading(e.currentTarget, true);
        try {
          await api(isNew ? "/api/reminders" : `/api/reminders/${r.id}`, { method: isNew ? "POST" : "PUT", body: payload });
          ctx.close();
          emit("data-changed");
          updateBell();
          toast("已保存");
        } catch (ex) {
          $(".form-error", body).textContent = ex.message;
        } finally {
          setLoading(e.currentTarget, false);
        }
      };
    },
  });
}

export { money, num };
