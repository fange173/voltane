/* me.js — 我的：个人设置、常用地点、加入车辆、数据（导入导出、回收站）、管理后台 */
import { icon } from "./icons.js";
import { navigate } from "./router.js";
import { onLeave, refreshChrome, resetView, setNav, setTitle, view } from "./shell.js";
import { CHARGE_LABEL, EXPENSE_LABEL, PLACE_KIND, currentVehicle, emit, gradeLabel, loadVehicles, pref, savePref, setVehicle, state } from "./state.js";
import { itemRow } from "./records.js";
import { avatar, bindSegmented, confirmDialog, emptyState, promptDialog, setLoading, sheet, skeleton, switchRow, toast, toastError } from "./ui.js";
import { $, $$, api, esc, fmtDate, html, money, num, raw, toNum } from "./util.js";

const COLORS = ["#4F46E5", "#0EA5E9", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6", "#EC4899", "#14B8A6"];

export function applyTheme(t = pref("theme", "auto")) {
  if (t === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.dataset.theme = t;
  const dark = t === "dark" || (t === "auto" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", dark ? "#0b0d14" : "#f3f4f8");
}

export function mePage() {
  resetView();
  setNav("me");
  setTitle("我的");
  const u = state.user;
  const el = view();
  el.innerHTML = String(html`
    <div class="card me-hero">${avatar(u, "lg")}<div class="grow"><h2>${u.name}</h2><div class="muted small">@${u.username} · ${u.is_admin ? "管理员" : "成员"}</div></div>
      <button class="btn btn-sm" id="me-edit">${raw(icon("edit", 16))}编辑</button></div>

    <div class="section-title"><span>显示</span></div>
    <div class="card">
      <div class="field"><label>主题</label><div class="segmented" id="theme-seg">${[["auto", "跟随系统"], ["light", "浅色"], ["dark", "深色"]].map(([k, l]) => html`<button data-v="${k}" class="${pref("theme", "auto") === k ? "on" : ""}">${l}</button>`)}</div></div>
      ${switchRow("place_top", "地点默认选中最常用的", pref("place_default", "none") === "top", "关闭时默认「不记录」")}
      ${switchRow("show_history", "首页显示「提车至今」卡片", !pref("hide_history", false))}
    </div>

    <div class="section-title"><span>车辆与数据</span></div>
    <div class="card flush list">
      ${state.vehicles.map((v) => html`<button class="list-item" data-vid="${v.id}"><span class="ico sm" style="background:${v.color};color:#fff">${raw(icon("car", 16))}</span><span class="grow">${v.name}<div class="tiny muted">${v.member_count} 人共享${v.is_archived ? " · 已归档" : ""}</div></span>${raw(icon("right", 16))}</button>`)}
      <a class="list-item" data-link href="/vehicles/new"><span class="ico sm brand">${raw(icon("plus", 16))}</span><span class="grow">添加车辆</span>${raw(icon("right", 16))}</a>
      <a class="list-item" data-link href="/me/join"><span class="ico sm brand">${raw(icon("key", 16))}</span><span class="grow">用邀请码加入车辆</span>${raw(icon("right", 16))}</a>
      <a class="list-item" data-link href="/me/places"><span class="ico sm">${raw(icon("pin", 16))}</span><span class="grow">常用地点与电价</span>${raw(icon("right", 16))}</a>
      <a class="list-item" data-link href="/me/data"><span class="ico sm">${raw(icon("download", 16))}</span><span class="grow">导入导出 · 回收站</span>${raw(icon("right", 16))}</a>
    </div>

    <div class="section-title"><span>账号</span></div>
    <div class="card flush list">
      <button class="list-item" id="me-pwd"><span class="ico sm">${raw(icon("key", 16))}</span><span class="grow">修改密码</span>${raw(icon("right", 16))}</button>
      ${u.is_admin ? html`<a class="list-item" data-link href="/admin"><span class="ico sm brand">${raw(icon("shield", 16))}</span><span class="grow">管理后台</span>${raw(icon("right", 16))}</a>` : ""}
      <button class="list-item" id="me-logout"><span class="ico sm danger">${raw(icon("logout", 16))}</span><span class="grow danger-c">退出登录</span></button>
      <button class="list-item" id="me-logout-all"><span class="ico sm danger">${raw(icon("logout", 16))}</span><span class="grow danger-c">退出所有设备</span></button>
    </div>
    <p class="end-mark">油电簿 Voltane · 一车两能，一本算清</p>`);
  bindSegmented($("#theme-seg", el), (t) => {
    savePref("theme", t);
    applyTheme(t);
  });
  el.querySelector("[name=place_top]").onchange = (e) => savePref("place_default", e.target.checked ? "top" : "none");
  el.querySelector("[name=show_history]").onchange = (e) => savePref("hide_history", !e.target.checked);
  $$("[data-vid]", el).forEach((b) => b.addEventListener("click", () => {
    setVehicle(+b.dataset.vid);
    navigate("/vehicle");
  }));
  $("#me-edit", el).onclick = () => editProfile();
  $("#me-pwd", el).onclick = () => changePassword();
  $("#me-logout", el).onclick = async () => {
    if (!(await confirmDialog({ title: "退出登录？", ok: "退出" }))) return;
    await api("/api/auth/logout", { method: "POST" });
    location.href = "/";
  };
  $("#me-logout-all", el).onclick = async () => {
    if (!(await confirmDialog({ title: "退出所有设备？", message: "所有设备、浏览器和 App 上的登录都会失效，需要重新登录。", ok: "全部退出", danger: true }))) return;
    try {
      await api("/api/auth/logout-all", { method: "POST" });
      location.href = "/";
    } catch (ex) {
      toastError(ex);
    }
  };
}

function editProfile() {
  const u = state.user;
  sheet({
    title: "编辑资料",
    render(body, ctx) {
      let color = u.color;
      body.innerHTML = String(html`<div class="stack"><div class="field"><label>昵称</label><input class="input" name="n" value="${u.name}" maxlength="32"></div>
        <div class="field"><label>头像颜色</label><div class="swatches">${COLORS.map((c) => html`<button type="button" class="swatch ${c === color ? "on" : ""}" data-c="${c}" style="background:${c}"></button>`)}</div></div></div>`);
      body.addEventListener("click", (e) => {
        const s = e.target.closest("[data-c]");
        if (!s) return;
        color = s.dataset.c;
        $$(".swatch", body).forEach((x) => x.classList.toggle("on", x === s));
      });
      ctx.setFoot(`<button class="btn btn-primary grow" data-ok>保存</button>`);
      $("[data-ok]", ctx.foot).onclick = async () => {
        try {
          const r = await api("/api/auth/profile", { method: "PUT", body: { display_name: $("[name=n]", body).value, avatar_color: color } });
          Object.assign(state.user, r);
          ctx.close();
          refreshChrome();
          mePage();
        } catch (e) {
          toastError(e);
        }
      };
    },
  });
}

export function changePassword(forced = false) {
  sheet({
    title: forced ? "请先修改初始密码" : "修改密码",
    render(body, ctx) {
      body.innerHTML = String(html`<div class="stack"><div class="field"><label>原密码</label><input class="input" type="password" name="o" autocomplete="current-password"></div>
        <div class="field"><label>新密码（至少 6 位）</label><input class="input" type="password" name="n" autocomplete="new-password"></div>
        <div class="field"><label>再输一次</label><input class="input" type="password" name="n2" autocomplete="new-password"></div><div class="form-error"></div></div>`);
      ctx.setFoot(`<button class="btn btn-primary grow" data-ok>确定</button>`);
      $("[data-ok]", ctx.foot).onclick = async (e) => {
        const o = $("[name=o]", body).value;
        const n = $("[name=n]", body).value;
        if (n !== $("[name=n2]", body).value) return ($(".form-error", body).textContent = "两次输入的新密码不一致");
        setLoading(e.currentTarget, true);
        try {
          await api("/api/auth/change-password", { method: "POST", body: { old_password: o, new_password: n } });
          ctx.close();
          toast("密码已修改");
        } catch (ex) {
          $(".form-error", body).textContent = ex.message;
        } finally {
          setLoading(e.currentTarget, false);
        }
      };
    },
  });
}

/* ---------- 加入车辆 ---------- */

export async function joinPage({ params }) {
  resetView();
  setNav("me");
  setTitle("加入车辆");
  const el = view();
  el.innerHTML = String(html`<div class="card join-card stack">
    <div class="ico brand" style="width:56px;height:56px">${raw(icon("key", 26))}</div>
    <h2>用邀请码加入家人的车</h2><p class="muted small">让车主在「车辆 → 成员 → 邀请」里生成邀请码，加入后可以一起记录和查看。</p>
    <input class="input code-input num" id="code" maxlength="8" placeholder="8 位邀请码" value="${params.code || ""}" autocapitalize="characters">
    <div id="preview"></div><div class="form-error"></div>
    <button class="btn btn-primary btn-block" id="join">加入</button></div>`);
  const code = $("#code");
  const preview = async () => {
    const c = code.value.trim().toUpperCase();
    if (c.length !== 8) return ($("#preview").innerHTML = "");
    try {
      const p = await api(`/api/invites/${c}`);
      $("#preview").innerHTML = String(html`<div class="callout">${raw(icon("car", 18))}<span><b>${p.inviter}</b> 邀请你以「${p.role_label}」加入「${p.vehicle_name}」</span></div>`);
    } catch (e) {
      $("#preview").innerHTML = String(html`<div class="callout warn">${e.message}</div>`);
    }
  };
  code.addEventListener("input", preview);
  if (params.code) preview();
  $("#join").onclick = async (e) => {
    setLoading(e.currentTarget, true);
    try {
      const r = await api(`/api/invites/${code.value.trim().toUpperCase()}/accept`, { method: "POST" });
      await loadVehicles();
      setVehicle(r.vehicle_id);
      refreshChrome();
      toast(r.already ? "你已经是这辆车的成员了" : "已加入");
      navigate("/");
    } catch (ex) {
      $(".form-error", el).textContent = ex.message;
    } finally {
      setLoading(e.currentTarget, false);
    }
  };
}

/* ---------- 常用地点 ---------- */

export async function placesPage() {
  resetView();
  setNav("me");
  setTitle("常用地点");
  const el = view();
  // 点击处理挂在共用的 #view 上，离开页面时要摘掉，否则会带到其他页面
  onLeave(() => (el.onclick = null));
  const v = currentVehicle();
  el.innerHTML = String(skeleton(3, 64));
  const load = async () => {
    let list;
    try {
      list = await api("/api/places" + (v ? `?vehicle_id=${v.id}` : ""));
    } catch (e) {
      el.innerHTML = String(emptyState({ ico: "warning", title: "加载失败", text: e.message }));
      return;
    }
    el.innerHTML = String(html`<div class="row between toolbar"><p class="muted small grow">记录时可以一键选择；家充桩设置峰谷电价后会自动算金额。</p>
      <button class="btn btn-primary btn-sm" id="pl-add">${raw(icon("plus", 16))}添加</button></div>
      ${list.length ? html`<div class="card flush list">${list.map((p) => html`<button class="list-item ${p.is_hidden ? "dim" : ""}" data-id="${p.id}">
        <span class="ico sm ${p.kind === "gas_station" ? "fuel" : p.kind === "other" ? "" : "elec"}">${raw(icon(p.kind === "home_charger" ? "home" : p.kind === "gas_station" ? "fuel" : p.kind === "charge_station" ? "bolt" : "pin", 16))}</span>
        <span class="grow">${p.name} ${p.is_hidden ? html`<span class="badge">已隐藏</span>` : ""}${p.shared ? "" : html` <span class="badge">仅自己</span>`}
          <div class="tiny muted">${PLACE_KIND[p.kind]}${p.tou_tariff.length ? ` · 峰谷电价 ${p.tou_tariff.length} 段` : p.last_unit_price ? ` · 上次 ¥${p.last_unit_price}` : ""} · 用过 ${p.use_count} 次</div></span>${raw(icon("right", 16))}</button>`)}</div>
        ${list.length > 1 ? html`<button class="btn btn-ghost btn-block" id="pl-merge" style="margin-top:10px">${raw(icon("copy", 16))}合并重复的地点</button>` : ""}`
        : emptyState({ ico: "pin", title: "还没有常用地点", text: "记录时输入过两次的地点会提示保存；也可以在这里先添加家里的充电桩" })}`);
    $("#pl-add", el).onclick = () => placeSheet(null, v, load);
    $("#pl-merge", el)?.addEventListener("click", () => mergeSheet(list, load));
    el.onclick = (e) => {
      const b = e.target.closest("[data-id]");
      if (b) placeSheet(list.find((p) => p.id === +b.dataset.id), v, load);
    };
  };
  await load();
}

function placeSheet(p, v, reload) {
  sheet({
    title: p ? "编辑地点" : "添加地点",
    render(body, ctx) {
      const tariff = p?.tou_tariff?.length ? p.tou_tariff : [];
      body.innerHTML = String(html`<form class="stack" novalidate>
        <div class="field"><label>名称</label><input class="input" name="name" value="${p?.name || ""}" maxlength="64" placeholder="如：家里车位"></div>
        <div class="field"><label>类型</label><div class="segmented" id="pk">${Object.entries(PLACE_KIND).map(([k, l]) => html`<button type="button" data-v="${k}" class="${(p?.kind || "home_charger") === k ? "on" : ""}">${l}</button>`)}</div></div>
        <div data-tariff class="stack"><div class="field"><label>峰谷电价 <span class="tiny muted">按开始时间和时长自动算金额</span></label>
          <div id="tariff-rows" class="stack">${(tariff.length ? tariff : [{ from: "22:00", to: "08:00", price: "" }, { from: "08:00", to: "22:00", price: "" }]).map((t) => tariffRow(t))}</div>
          <button type="button" class="link-btn tiny" id="t-add">${raw(icon("plus", 14))} 加一段</button></div></div>
        <div class="field" data-price><label>默认单价</label><div class="input-wrap"><input class="input num" name="price" inputmode="decimal" value="${p?.last_unit_price ?? ""}"><span class="unit">元</span></div><span class="hint">会随着最近一次记录自动更新</span></div>
        ${v ? switchRow("shared", `这辆车（${v.name}）的成员共用`, p ? p.shared : true) : ""}
        ${p ? switchRow("hidden", "隐藏（不出现在记录表单里）", p.is_hidden) : ""}
        <div class="form-error"></div></form>`);
      const getKind = bindSegmented($("#pk", body), (k) => sync(k));
      const sync = (k) => {
        $("[data-tariff]", body).classList.toggle("hidden", k !== "home_charger");
      };
      sync(p?.kind || "home_charger");
      $("#t-add", body).onclick = () => $("#tariff-rows", body).insertAdjacentHTML("beforeend", String(tariffRow({ from: "", to: "", price: "" })));
      body.addEventListener("click", (e) => e.target.closest("[data-t-del]")?.closest(".tariff-row").remove());
      const btns = [`<button class="btn btn-primary grow" data-ok>保存</button>`];
      if (p) btns.unshift(`<button class="btn btn-danger" data-del>${icon("trash", 18)}</button>`);
      ctx.setFoot(btns.join(""));
      $("[data-del]", ctx.foot)?.addEventListener("click", async () => {
        if (!(await confirmDialog({ title: "删除地点？", message: "历史记录里的地点名称会保留。", ok: "删除", danger: true }))) return;
        await api(`/api/places/${p.id}`, { method: "DELETE" });
        ctx.close();
        reload();
      });
      $("[data-ok]", ctx.foot).onclick = async () => {
        const f = $("form", body);
        const kind = getKind();
        const rows = kind === "home_charger" ? $$(".tariff-row", body).map((r) => ({ from: $("[data-f]", r).value, to: $("[data-to]", r).value, price: toNum($("[data-p]", r).value) })).filter((t) => t.from && t.to && t.price != null) : [];
        const payload = { vehicle_id: v?.id ?? p?.vehicle_id ?? null, shared: f.shared ? f.shared.checked : !!p?.shared, name: f.name.value.trim(), kind,
          tou_tariff: rows, last_unit_price: toNum(f.price.value), is_hidden: f.hidden ? f.hidden.checked : false, sort_order: p?.sort_order ?? 0,
          default_charge_type: kind === "home_charger" ? "home" : p?.default_charge_type ?? null, default_fuel_grade: p?.default_fuel_grade ?? null, lat: p?.lat ?? null, lng: p?.lng ?? null };
        if (!payload.name) return ($(".form-error", body).textContent = "请填写名称");
        try {
          await api(p ? `/api/places/${p.id}` : "/api/places", { method: p ? "PUT" : "POST", body: payload });
          ctx.close();
          reload();
          toast("已保存");
        } catch (e) {
          $(".form-error", body).textContent = e.message;
        }
      };
    },
  });
}

function tariffRow(t) {
  return html`<div class="tariff-row"><input class="input" type="time" data-f value="${t.from}"><span class="muted">至</span><input class="input" type="time" data-to value="${t.to}">
    <div class="input-wrap"><input class="input num" data-p inputmode="decimal" value="${t.price}" placeholder="0.30"><span class="unit">元/度</span></div>
    <button type="button" class="icon-btn" data-t-del aria-label="删除">${raw(icon("x", 16))}</button></div>`;
}

function mergeSheet(list, reload) {
  sheet({
    title: "合并地点",
    render(body, ctx) {
      body.innerHTML = String(html`<p class="small muted">勾选要合并的地点，再选保留哪一个；历史记录会改到保留的地点下。</p>
        <div class="card flush list" style="margin-top:10px">${list.map((p) => html`<label class="list-item"><input type="checkbox" data-m="${p.id}"><span class="grow">${p.name}<div class="tiny muted">用过 ${p.use_count} 次</div></span>
          <input type="radio" name="keep" value="${p.id}" aria-label="保留"><span class="tiny muted">保留</span></label>`)}</div>`);
      ctx.setFoot(`<button class="btn btn-primary grow" data-ok>合并</button>`);
      $("[data-ok]", ctx.foot).onclick = async () => {
        const keep = $("[name=keep]:checked", body)?.value;
        const from = $$("[data-m]:checked", body).map((c) => +c.dataset.m);
        if (!keep || from.length < 1) return toast("请勾选要合并的地点并选择保留哪个", { error: true });
        try {
          await api("/api/places/merge", { method: "POST", body: { from_ids: from, to_id: +keep } });
          ctx.close();
          reload();
          toast("已合并");
        } catch (e) {
          toastError(e);
        }
      };
    },
  });
}

/* ---------- 数据 ---------- */

export async function dataPage() {
  resetView();
  setNav("me");
  setTitle("导入导出");
  const v = currentVehicle();
  const el = view();
  if (!v) return navigate("/");
  el.innerHTML = String(html`
    <div class="section-title"><span>导出「${v.name}」</span></div>
    <div class="card flush list">
      <a class="list-item" href="/api/export?vehicle_id=${v.id}&format=csv" download><span class="ico sm">${raw(icon("download", 16))}</span><span class="grow">记录 CSV<div class="tiny muted">加油、充电、快照，Excel 可直接打开</div></span></a>
      <a class="list-item" href="/api/export?vehicle_id=${v.id}&format=expenses_csv" download><span class="ico sm">${raw(icon("download", 16))}</span><span class="grow">费用 CSV</span></a>
      <a class="list-item" href="/api/export?vehicle_id=${v.id}&format=json" download><span class="ico sm brand">${raw(icon("download", 16))}</span><span class="grow">完整 JSON<div class="tiny muted">包含记录、费用、汇总、提醒，可以导回</div></span></a>
    </div>
    ${v.role === "owner" ? html`<div class="section-title"><span>导入到「${v.name}」</span></div>
      <div class="card stack"><p class="small muted">支持本应用导出的 JSON。导入的记录会标记「补录」，整批可以撤销。</p>
        <input type="file" accept=".json,application/json" id="imp-file" class="input">
        <button class="btn btn-primary" id="imp-go">${raw(icon("upload", 18))}导入</button><div class="form-error"></div></div>` : ""}
    <div class="section-title"><span>回收站</span><span class="tiny">保留 30 天</span></div>
    <div id="trash">${skeleton(2, 56)}</div>`);
  $("#imp-go", el)?.addEventListener("click", async (e) => {
    const f = $("#imp-file", el).files[0];
    if (!f) return toast("请先选择文件", { error: true });
    const fd = new FormData();
    fd.append("file", f);
    setLoading(e.currentTarget, true);
    try {
      const r = await api(`/api/import?vehicle_id=${v.id}`, { method: "POST", form: fd });
      emit("data-changed");
      toast(`已导入 ${r.records} 条记录、${r.expenses} 笔费用`, { action: { label: "撤销", onClick: async () => {
        await api(`/api/import/${r.batch}?vehicle_id=${v.id}`, { method: "DELETE" });
        emit("data-changed");
        toast("已撤销导入");
      } } });
    } catch (ex) {
      $(".form-error", el).textContent = ex.message;
    } finally {
      setLoading(e.currentTarget, false);
    }
  });
  const loadTrash = async () => {
    const items = await api(`/api/trash?vehicle_id=${v.id}`);
    const box = $("#trash", el);
    box.innerHTML = String(items.length ? html`<div class="card flush list">${items.map((it) => html`<div class="trash-row">${itemRow(it)}<button class="btn btn-sm btn-soft" data-restore="${it.type}:${it.id}">恢复</button></div>`)}</div>`
      : html`<div class="card"><p class="small muted">回收站是空的</p></div>`);
    box.onclick = async (e) => {
      const b = e.target.closest("[data-restore]");
      if (!b) return;
      const [type, id] = b.dataset.restore.split(":");
      try {
        await api(type === "expense" ? `/api/expenses/${id}/restore` : `/api/records/${id}/restore`, { method: "POST" });
        emit("data-changed");
        toast("已恢复");
        loadTrash();
      } catch (ex) {
        toastError(ex);
      }
    };
  };
  loadTrash();
}

/* ---------- 管理后台 ---------- */

export async function adminPage() {
  resetView();
  setNav("me");
  setTitle("管理后台");
  const el = view();
  // 点击处理挂在共用的 #view 上，离开页面时要摘掉，否则会带到其他页面
  onLeave(() => (el.onclick = null));
  if (!state.user.is_admin) return navigate("/me");
  el.innerHTML = String(skeleton(3, 100));
  const load = async () => {
    const [sys, users] = await Promise.all([api("/api/admin/system"), api("/api/admin/users")]);
    el.innerHTML = String(html`
      <div class="kpi-grid">
        <div class="kpi card"><div class="kpi-label">用户</div><div class="kpi-value num">${sys.users}</div></div>
        <div class="kpi card"><div class="kpi-label">车辆</div><div class="kpi-value num">${sys.vehicles}</div></div>
        <div class="kpi card"><div class="kpi-label">记录</div><div class="kpi-value num">${sys.records}</div></div>
        <div class="kpi card"><div class="kpi-label">数据库</div><div class="kpi-value num">${sys.db_size != null ? num(sys.db_size / 1024 / 1024, 1) : "–"}<small>MB</small></div></div>
      </div>
      <div class="section-title"><span>用户</span><button class="link-btn" id="u-add">${raw(icon("plus", 14))} 新建</button></div>
      <div class="card flush list">${users.map((u) => html`<button class="list-item" data-uid="${u.id}">${avatar(u)}<span class="grow">${u.name} ${u.role === "admin" ? html`<span class="badge brand">管理员</span>` : ""}${!u.is_active ? html`<span class="badge danger">已停用</span>` : ""}
        <div class="tiny muted">@${u.username} · ${u.vehicle_count} 辆车</div></span>${raw(icon("right", 16))}</button>`)}</div>
      <div class="section-title"><span>设置</span></div>
      <div class="card">${switchRow("allow_signup", "允许通过邀请链接注册", sys.allow_signup, "开启后，车主生成的「可注册」邀请链接能让对方自己建账号")}</div>
      ${sys.orphan_vehicles.length ? html`<div class="section-title"><span>没有车主的车辆</span></div><div class="card flush list">${sys.orphan_vehicles.map((v) => html`<button class="list-item" data-orphan="${v.id}"><span class="grow">${v.name}</span><span class="link-btn">指定车主</span></button>`)}</div>` : ""}
      <div class="section-title"><span>备份</span><span class="tiny">每天凌晨自动备份，保留 ${sys.backup_keep} 份</span></div>
      <div class="card stack">
        <div class="row wrap"><button class="btn" id="bk-down">${raw(icon("download", 18))}下载当前备份</button>
          <label class="btn btn-danger">${raw(icon("upload", 18))}从备份恢复<input type="file" id="bk-file" accept=".db" hidden></label></div>
        ${sys.backups.length ? html`<div class="tiny muted">${sys.backups.slice(0, 5).map((b) => html`<div>${b.name} · ${num(b.size / 1024, 0)} KB · ${b.mtime.replace("T", " ")}</div>`)}</div>` : html`<p class="tiny muted">还没有自动备份</p>`}
      </div>`);
    $("[name=allow_signup]", el).onchange = (e) => api("/api/admin/settings", { method: "PUT", body: { allow_signup: e.target.checked } }).then(() => toast("已保存"));
    $("#u-add", el).onclick = () => userSheet(null, load);
    el.onclick = (e) => {
      const b = e.target.closest("[data-uid]");
      if (b) userSheet(users.find((u) => u.id === +b.dataset.uid), load);
      const o = e.target.closest("[data-orphan]");
      if (o) assignOwner(+o.dataset.orphan, users, load);
    };
    $("#bk-down", el).onclick = async (e) => {
      setLoading(e.currentTarget, true);
      try {
        const res = await api("/api/admin/backup", { method: "POST", raw: true });
        if (!res.ok) throw new Error("备份失败");
        const blob = await res.blob();
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `voltane-${new Date().toISOString().slice(0, 10)}.db`;
        a.click();
        URL.revokeObjectURL(a.href);
      } catch (ex) {
        toastError(ex);
      } finally {
        setLoading(e.currentTarget, false);
      }
    };
    $("#bk-file", el).onchange = async (e) => {
      const f = e.target.files[0];
      if (!f) return;
      if (!(await confirmDialog({ title: "从备份恢复？", message: "当前所有数据会被备份文件覆盖（覆盖前会自动再备份一次当前库）。", ok: "恢复", danger: true }))) return;
      const fd = new FormData();
      fd.append("file", f);
      try {
        await api("/api/admin/restore", { method: "POST", form: fd });
        toast("已恢复，正在刷新");
        setTimeout(() => location.reload(), 800);
      } catch (ex) {
        toastError(ex);
      }
    };
  };
  try {
    await load();
  } catch (e) {
    el.innerHTML = String(emptyState({ ico: "warning", title: "加载失败", text: e.message }));
  }
}

function userSheet(u, reload) {
  sheet({
    title: u ? u.name : "新建用户",
    render(body, ctx) {
      body.innerHTML = String(html`<div class="stack">
        ${!u ? html`<div class="field"><label>用户名</label><input class="input" name="username" autocomplete="off" placeholder="字母、数字、下划线"></div>` : ""}
        <div class="field"><label>昵称</label><input class="input" name="display_name" value="${u?.name || ""}"></div>
        <div class="field"><label>${u ? "重置密码（不改留空）" : "初始密码"}</label><input class="input" type="text" name="password" autocomplete="off" placeholder="至少 6 位"></div>
        <div class="field"><label>角色</label><select class="input" name="role"><option value="member" ${u?.role !== "admin" ? "selected" : ""}>成员</option><option value="admin" ${u?.role === "admin" ? "selected" : ""}>管理员</option></select></div>
        ${u ? switchRow("active", "启用", u.is_active) : ""}<div class="form-error"></div></div>`);
      ctx.setFoot(`<button class="btn btn-primary grow" data-ok>保存</button>`);
      $("[data-ok]", ctx.foot).onclick = async () => {
        const g = (n) => $(`[name=${n}]`, body);
        try {
          if (u) {
            await api(`/api/admin/users/${u.id}`, { method: "PUT", body: { display_name: g("display_name").value, role: g("role").value, is_active: g("active").checked, password: g("password").value || null } });
          } else {
            await api("/api/admin/users", { method: "POST", body: { username: g("username").value.trim(), password: g("password").value, display_name: g("display_name").value, role: g("role").value } });
          }
          ctx.close();
          reload();
          toast("已保存");
        } catch (e) {
          $(".form-error", body).textContent = e.message;
        }
      };
    },
  });
}

function assignOwner(vid, users, reload) {
  sheet({
    title: "指定车主",
    render(body, ctx) {
      body.innerHTML = String(html`<div class="card flush list">${users.map((u) => html`<button class="list-item" data-u="${u.id}">${avatar(u)}<span class="grow">${u.name}</span></button>`)}</div>`);
      body.onclick = async (e) => {
        const b = e.target.closest("[data-u]");
        if (!b) return;
        await api(`/api/admin/vehicles/${vid}/owner`, { method: "POST", body: { user_id: +b.dataset.u } });
        ctx.close();
        reload();
      };
    },
  });
}

export { CHARGE_LABEL, EXPENSE_LABEL, esc, fmtDate, gradeLabel, money, promptDialog };
