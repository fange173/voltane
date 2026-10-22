/* ui.js — 抽屉、对话框、提示条与常用小组件 */
import { icon } from "./icons.js";
import { QUALITY_LABEL } from "./state.js";
import { $, esc, html, initial, raw } from "./util.js";

/* ---------- 抽屉 / 弹窗 ---------- */

const openSheets = [];

/**
 * 打开底部抽屉（≥768 为居中弹窗）。
 * render(body, ctx) 负责填充内容；ctx.close() 关闭；返回 Promise，关闭时 resolve(result)。
 */
export function sheet({ title = "", render, foot = null, wide = false, onClose } = {}) {
  return new Promise((resolve) => {
    const root = document.createElement("div");
    root.className = "sheet-root";
    root.innerHTML = `
      <div class="sheet-scrim"></div>
      <div class="sheet" role="dialog" aria-modal="true" style="${wide ? "max-width:760px" : ""}">
        <div class="sheet-grip"></div>
        <div class="sheet-head"><h2>${esc(title)}</h2>
          <button class="icon-btn sheet-x" aria-label="关闭">${icon("x")}</button></div>
        <div class="sheet-body"></div>
        <div class="sheet-foot hidden"></div>
      </div>`;
    document.body.appendChild(root);
    document.body.style.overflow = "hidden";
    const body = $(".sheet-body", root);
    const footEl = $(".sheet-foot", root);
    let result;
    let closed = false;
    const ctx = {
      root,
      body,
      foot: footEl,
      setTitle: (t) => ($(".sheet-head h2", root).textContent = t),
      setFoot(content) {
        footEl.innerHTML = String(content || "");
        footEl.classList.toggle("hidden", !content);
      },
      close(r) {
        if (closed) return;
        closed = true;
        result = r;
        $(".sheet", root).classList.add("closing");
        $(".sheet-scrim", root).classList.add("closing");
        setTimeout(() => {
          root.remove();
          openSheets.splice(openSheets.indexOf(ctx), 1);
          if (!openSheets.length) document.body.style.overflow = "";
          onClose?.(result);
          resolve(result);
        }, 200);
      },
    };
    openSheets.push(ctx);
    $(".sheet-scrim", root).addEventListener("click", () => ctx.close());
    $(".sheet-x", root).addEventListener("click", () => ctx.close());
    bindSwipeDown(root, ctx);
    if (foot) ctx.setFoot(foot);
    render?.(body, ctx);
  });
}

export function closeTopSheet() {
  const top = openSheets[openSheets.length - 1];
  if (top) {
    top.close();
    return true;
  }
  return false;
}
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeTopSheet();
});

/** 手机上从抓手或标题栏下拉关闭。 */
function bindSwipeDown(root, ctx) {
  const sh = $(".sheet", root);
  const handles = [$(".sheet-grip", root), $(".sheet-head", root)];
  let y0 = null;
  let dy = 0;
  handles.forEach((h) => {
    h.addEventListener("touchstart", (e) => {
      y0 = e.touches[0].clientY;
      dy = 0;
      sh.style.transition = "none";
    }, { passive: true });
    h.addEventListener("touchmove", (e) => {
      if (y0 == null) return;
      dy = Math.max(0, e.touches[0].clientY - y0);
      sh.style.transform = `translateY(${dy}px)`;
    }, { passive: true });
    h.addEventListener("touchend", () => {
      sh.style.transition = "";
      if (dy > 90) ctx.close();
      else sh.style.transform = "";
      y0 = null;
    });
  });
}

export function confirmDialog({ title = "确认", message = "", ok = "确定", cancel = "取消", danger = false } = {}) {
  return sheet({
    title,
    render(body, ctx) {
      body.innerHTML = `<div class="dialog"><p>${message}</p></div>`;
      ctx.setFoot(`<button class="btn grow" data-x>${esc(cancel)}</button>
        <button class="btn grow ${danger ? "btn-danger" : "btn-primary"}" data-ok>${esc(ok)}</button>`);
      $("[data-x]", ctx.foot).onclick = () => ctx.close(false);
      $("[data-ok]", ctx.foot).onclick = () => ctx.close(true);
    },
  }).then((r) => !!r);
}

export function promptDialog({ title, label = "", value = "", placeholder = "", ok = "确定", type = "text" }) {
  return sheet({
    title,
    render(body, ctx) {
      body.innerHTML = `<div class="field"><label>${esc(label)}</label>
        <input class="input" type="${type}" value="${esc(value)}" placeholder="${esc(placeholder)}"></div>`;
      const inp = $("input", body);
      ctx.setFoot(`<button class="btn grow" data-x>取消</button><button class="btn btn-primary grow" data-ok>${esc(ok)}</button>`);
      $("[data-x]", ctx.foot).onclick = () => ctx.close(null);
      $("[data-ok]", ctx.foot).onclick = () => ctx.close(inp.value.trim());
      inp.addEventListener("keydown", (e) => e.key === "Enter" && ctx.close(inp.value.trim()));
      setTimeout(() => inp.focus(), 250);
    },
  });
}

/* ---------- 提示条 ---------- */

let toastRoot;
export function toast(text, { sub = "", action = null, actions = null, error = false, ms = 3200 } = {}) {
  if (!toastRoot) {
    toastRoot = document.createElement("div");
    toastRoot.className = "toast-root";
    document.body.appendChild(toastRoot);
  }
  const acts = actions || (action ? [action] : []);
  const el = document.createElement("div");
  el.className = "toast" + (error ? " error" : "");
  el.innerHTML = `<div class="t-body">${esc(text)}${sub ? `<small>${esc(sub)}</small>` : ""}</div>
    ${acts.map((a, i) => `<button class="t-act" data-i="${i}">${esc(a.label)}</button>`).join("")}`;
  toastRoot.appendChild(el);
  const remove = () => {
    el.classList.add("out");
    setTimeout(() => el.remove(), 200);
  };
  const t = setTimeout(remove, acts.length ? Math.max(ms, 6000) : ms);
  el.querySelectorAll(".t-act").forEach((b) => {
    b.onclick = () => {
      clearTimeout(t);
      remove();
      acts[+b.dataset.i].onClick();
    };
  });
  return remove;
}
export const toastError = (e) => toast(e?.message || String(e), { error: true });

/* ---------- 小组件 ---------- */

export function avatar(u, cls = "") {
  if (!u) return "";
  return raw(`<span class="avatar ${cls}" style="background:${esc(u.color)}" title="${esc(u.name)}">${esc(initial(u.name))}</span>`);
}

export function qTag(q, err = null) {
  if (!q || q === "A") return "";
  const text = q === "B" && err != null ? `估 ±${err}` : QUALITY_LABEL[q];
  return raw(`<span class="q-tag ${q}">${esc(text)}</span>`);
}

export function emptyState({ ico = "info", title = "", text = "", action = "" }) {
  return html`<div class="empty"><div class="ico brand">${raw(icon(ico, 26))}</div>
    ${title ? html`<h3>${title}</h3>` : ""}<p>${text}</p>${raw(action)}</div>`;
}

export function skeleton(n = 3, h = 72) {
  return raw(Array.from({ length: n }, () => `<div class="skeleton" style="height:${h}px;margin-bottom:12px"></div>`).join(""));
}

export function switchRow(name, label, checked, sub = "") {
  return html`<label class="switch"><span class="switch-text"><span>${label}</span>${sub ? html`<small>${sub}</small>` : ""}</span>
    <input type="checkbox" name="${name}" ${checked ? "checked" : ""}><span class="track"></span></label>`;
}

/** 分段控件：点击切换 .on，返回当前值的 getter。 */
export function bindSegmented(el, onChange) {
  el.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-v]");
    if (!b || b.classList.contains("on")) return;
    el.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    onChange?.(b.dataset.v);
  });
  return () => el.querySelector("button.on")?.dataset.v;
}

export function bindChips(el, { multi = false, onChange } = {}) {
  el.addEventListener("click", (e) => {
    const c = e.target.closest(".chip[data-v]");
    if (!c) return;
    if (multi) c.classList.toggle("on");
    else {
      const was = c.classList.contains("on");
      el.querySelectorAll(".chip[data-v]").forEach((x) => x.classList.remove("on"));
      if (!was || !c.dataset.toggle) c.classList.add("on");
    }
    onChange?.(c.dataset.v, c);
  });
}

export function setLoading(btn, on) {
  if (!btn) return;
  btn.classList.toggle("loading", on);
  btn.disabled = on;
}

/** 和上一周期比的变化标签；neutral=true 时不分好坏（如里程）。 */
export function changeTag(cur, prev, { lowerIsBetter = true, neutral = false, digits = 0 } = {}) {
  if (cur == null || prev == null || prev === 0) return "";
  const ch = (cur - prev) / Math.abs(prev);
  if (Math.abs(ch) < 0.005) return raw(`<span class="delta flat">持平</span>`);
  const good = lowerIsBetter ? ch < 0 : ch > 0;
  return raw(`<span class="delta ${neutral ? "flat" : good ? "good" : "bad"}">${ch > 0 ? "↑" : "↓"}${Math.abs(ch * 100).toFixed(digits)}%</span>`);
}
