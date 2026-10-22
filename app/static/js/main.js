/* main.js — 启动：登录态检查、路由注册、外壳装配、离线发件箱与 Service Worker */
import { loginPage } from "./auth.js";
import { homePage } from "./home.js";
import { adminPage, applyTheme, changePassword, dataPage, joinPage, mePage, placesPage } from "./me.js";
import { startOutbox } from "./outbox.js";
import { recordsPage, openDetail } from "./records.js";
import { remindersPage } from "./reminders.js";
import { fallback, navigate, onBeforeRender, render, route, startRouter } from "./router.js";
import { buildShell, refreshChrome } from "./shell.js";
import { loadVehicles, setVehicle, state } from "./state.js";
import { statsPage, yearPage } from "./stats.js";
import { toast } from "./ui.js";
import { api, setUnauthorizedHandler, store } from "./util.js";
import { vehiclePage, wizardPage } from "./vehicles.js";

route("/", homePage, { nav: "home" });
route("/records", recordsPage, { nav: "records" });
route("/records/:id", async ({ params }) => {
  await recordsPage();
  openDetail("record", +params.id);
}, { nav: "records" });
route("/stats", statsPage, { nav: "stats" });
route("/stats/year/:year", yearPage, { nav: "stats" });
route("/stats/:tab", statsPage, { nav: "stats" });
route("/calendar", () => navigate("/stats/calendar", { replace: true }));
route("/reminders", remindersPage, { nav: "reminders" });
route("/vehicle", vehiclePage, { nav: "vehicle" });
route("/vehicles/new", wizardPage, { nav: "vehicle" });
route("/me", mePage, { nav: "me" });
route("/me/join", joinPage, { nav: "me" });
route("/join/:code", joinPage, { nav: "me" });
route("/me/places", placesPage, { nav: "me" });
route("/me/data", dataPage, { nav: "me" });
route("/admin", adminPage, { nav: "me" });
route("/new/:kind", async ({ params }) => {
  navigate("/", { replace: true });
  const { openRecordForm } = await import("./record-form.js");
  setTimeout(() => openRecordForm({ kind: params.kind === "charge" ? "charge" : params.kind }), 300);
});
fallback(() => navigate("/", { replace: true }));
onBeforeRender(() => {});

async function enter(user, { joined = false } = {}) {
  state.user = user;
  applyTheme();
  await loadVehicles();
  buildShell();
  startOutbox();
  const m = location.pathname.match(/^\/join\/([A-Za-z0-9]+)$/);
  if (m && joined) {
    await loadVehicles();
    const last = state.vehicles[state.vehicles.length - 1];
    if (last) setVehicle(last.id);
    refreshChrome();
    toast("已加入");
    history.replaceState({}, "", "/");
  }
  await startRouter();
  if (user.must_change_password) changePassword(true);
}

async function bootstrap() {
  applyTheme(store.get("theme-cache", "auto"));
  setUnauthorizedHandler(() => {
    if (state.user) location.reload();
  });
  try {
    const user = await api("/api/auth/me");
    store.set("theme-cache", user.settings?.theme || "auto");
    await enter(user);
  } catch {
    const m = location.pathname.match(/^\/join\/([A-Za-z0-9]+)$/);
    loginPage((user, opts) => enter(user, opts), { code: m ? m[1].toUpperCase() : null });
  }
}

bootstrap();

// PWA：只在安全上下文（https / localhost）注册；http://局域网 IP 下不生效属预期
if ("serviceWorker" in navigator && window.isSecureContext) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}

export { render };
