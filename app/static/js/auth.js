/* auth.js — 登录页、未登录时的邀请注册页 */
import { icon } from "./icons.js";
import { setLoading } from "./ui.js";
import { $, api, html, raw } from "./util.js";

export function loginPage(onDone, { code = null } = {}) {
  const app = $("#app");
  app.innerHTML = String(html`<div class="auth-page"><div class="auth-card">
    <div class="auth-brand"><img src="/static/icon.svg" alt=""><h1>油电簿</h1><p>一车两能，一本算清</p></div>
    <div id="invite-box"></div>
    <form class="card stack" id="login-form" novalidate>
      <div class="field"><label for="lu">用户名</label><input class="input" id="lu" name="username" autocomplete="username" required></div>
      <div class="field"><label for="lp">密码</label><input class="input" id="lp" name="password" type="password" autocomplete="current-password" required></div>
      <label class="mini-check"><input type="checkbox" name="remember" checked> 记住登录（90 天）</label>
      <div class="form-error"></div>
      <button class="btn btn-primary btn-block" type="submit">登 录</button>
    </form>
    <p class="tiny muted" style="text-align:center">账号由管理员创建；首次部署默认账号 admin / admin123</p>
  </div></div>`);
  const form = $("#login-form");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("button[type=submit]", form);
    setLoading(btn, true);
    try {
      const user = await api("/api/auth/login", { method: "POST", body: {
        username: form.username.value.trim(), password: form.password.value, remember: form.remember.checked } });
      onDone(user);
    } catch (ex) {
      $(".form-error", form).textContent = ex.message;
    } finally {
      setLoading(btn, false);
    }
  });
  if (code) inviteBox(code, onDone);
  setTimeout(() => form.username?.focus(), 100);
}

async function inviteBox(code, onDone) {
  let p;
  try {
    p = await api(`/api/invites/${code}`);
  } catch (e) {
    $("#invite-box").innerHTML = String(html`<div class="callout warn">${e.message}</div>`);
    return;
  }
  $("#invite-box").innerHTML = String(html`<div class="callout">${raw(icon("car", 18))}<span><b>${p.inviter}</b> 邀请你以「${p.role_label}」加入「${p.vehicle_name}」。${p.allow_signup ? "没有账号可以直接注册：" : "请先登录，登录后自动加入。"}</span></div>
    ${p.allow_signup ? html`<form class="card stack" id="signup-form" novalidate style="margin-bottom:12px">
      <div class="field"><label>用户名</label><input class="input" name="username" autocomplete="username" placeholder="字母、数字、下划线"></div>
      <div class="field"><label>昵称</label><input class="input" name="display_name" placeholder="家人怎么称呼你"></div>
      <div class="field"><label>密码（至少 6 位）</label><input class="input" type="password" name="password" autocomplete="new-password"></div>
      <div class="form-error"></div><button class="btn btn-primary btn-block" type="submit">注册并加入</button>
      <p class="tiny muted" style="text-align:center">已有账号？在下面登录</p></form>` : ""}`);
  const f = $("#signup-form");
  if (!f) return;
  f.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("button[type=submit]", f);
    setLoading(btn, true);
    try {
      const user = await api("/api/auth/signup", { method: "POST", body: { code, username: f.username.value.trim(), password: f.password.value, display_name: f.display_name.value.trim() } });
      onDone(user, { joined: true });
    } catch (ex) {
      $(".form-error", f).textContent = ex.message;
    } finally {
      setLoading(btn, false);
    }
  });
}
