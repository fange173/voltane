/**
 * smoke-modules.mjs — 无浏览器环境下 import 全部前端模块。
 * 能抓住：语法错误、import 未解析、模块顶层执行时的崩溃。
 * 用法：node scripts/smoke-modules.mjs
 */
import assert from "node:assert/strict";
import { readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const noop = () => {};
function fakeEl() {
  return {
    addEventListener: noop, removeEventListener: noop, classList: { add: noop, remove: noop, toggle: noop, contains: () => false },
    style: {}, dataset: {}, value: "", checked: false, textContent: "", innerHTML: "", insertAdjacentHTML: noop,
    appendChild: noop, append: noop, remove: noop, setAttribute: noop, removeAttribute: noop, getAttribute: () => null, closest: () => null,
    querySelector: () => fakeEl(), querySelectorAll: () => [], focus: noop, getBoundingClientRect: () => ({ left: 0, top: 0 }),
  };
}
const store = new Map();
globalThis.window = globalThis;
globalThis.addEventListener = noop;
globalThis.localStorage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v)), removeItem: (k) => store.delete(k) };
globalThis.location = { pathname: "/", search: "", href: "http://x/", origin: "http://x" };
globalThis.history = { pushState: noop, replaceState: noop };
// Node 20 没有全局 navigator（21 起才有），模块里的 `"x" in navigator` 会直接抛错
if (!("navigator" in globalThis)) globalThis.navigator = {};
globalThis.matchMedia = () => ({ matches: false, addEventListener: noop });
globalThis.document = {
  documentElement: fakeEl(), body: fakeEl(), title: "", visibilityState: "visible",
  querySelector: () => fakeEl(), querySelectorAll: () => [], getElementById: () => fakeEl(), createElement: () => fakeEl(),
  addEventListener: noop, removeEventListener: noop,
};
globalThis.fetch = () => Promise.reject(new TypeError("smoke: no network"));
globalThis.setInterval = () => 0;

const dir = join(dirname(fileURLToPath(import.meta.url)), "..", "app", "static", "js");
const files = readdirSync(dir).filter((f) => f.endsWith(".js")).sort();
for (const f of files) await import(join(dir, f));

const util = await import(join(dir, "util.js"));
if (util.evalAmount("200+15") !== 215 || util.evalAmount("(1+2)*3") !== 9 || util.evalAmount("1+") !== null) {
  throw new Error("evalAmount 计算不正确");
}
if (String(util.html`<b>${"<x>"}</b>`) !== "<b>&lt;x&gt;</b>") throw new Error("html 未转义插值");
await new Promise((r) => setTimeout(r, 50));
// 通过真实页面入口渲染接口样例，不依赖浏览器或后端。
const { state } = await import(join(dir, "state.js"));
const { homePage } = await import(join(dir, "home.js"));
const { statsPage } = await import(join(dir, "stats.js"));
const nodes = new Map();
document.querySelector = (sel) => {
  if (!nodes.has(sel)) nodes.set(sel, { ...fakeEl(), children: [] });
  return nodes.get(sel);
};
window.scrollTo = noop;
window.innerWidth = 390;
const v = { id: 1, has_fuel: true, has_elec: true, calc_settings: { kwh_per_l: 9.5 } };
state.vehicles = [v];
state.vid = v.id;
const mock = (data) => { globalThis.fetch = async () => ({ ok: true, text: async () => JSON.stringify(data) }); };
const missing = { quality: "D", per100: null };
const combined = { scope: "segment", anchor: "full", quality: "B", per100: 4, elec_per100: 9.5, equiv_per100: 5,
  start_ts: "2025-01-01", end_ts: "2025-01-08", distance: 123 };
const rough = { scope: "ledger", quality: "C", per100: 4, low100: 2, high100: 6, distance: 430, from_ts: "2025-01-01", to_ts: "2025-01-20" };
const points = (n) => Array.from({ length: n }, (_, i) => ({ per100: i + 1, quality: "B" }));
async function renderHome(latest, overview = {}) {
  mock({ period: {}, range: { key: "month", from: "2025-02-01", to: "2025-02-28" }, in_progress: [], recent: [], pending: [],
    latest, spark: { fuel: points(2), elec: points(3) }, ...overview });
  await homePage();
  return nodes.get("#view").innerHTML;
}
let page = await renderHome({ combined, fuel: missing, elec: missing });
for (const text of ["最近可算的油电综合段", "截至", "01-08", "覆盖 123 km", "B ·", "油耗趋势 · 最近 2 段", "电耗趋势 · 最近 3 段", "电耗 ÷ 9.5", "本期实付", "每公里成本（含摊销）"]) assert.ok(page.includes(text), text);
assert.doesNotMatch(page, /满到满精算|近 10 段|<a\b[^>]*class="card hero"/);
page = await renderHome({ combined: { ...combined, scope: "ledger", quality: "C" }, fuel: rough, elec: missing });
assert.match(page, /油电综合粗估/);
page = await renderHome({ combined: { ...combined, equiv_per100: null }, fuel: rough, elec: missing });
assert.match(page, /油耗粗估/);
assert.match(page, /截至 .*01-20/);
assert.match(page, /2–6 L\/100km/);
page = await renderHome({ fuel: missing, elec: missing });
assert.match(page, /能耗数据不足/);
assert.doesNotMatch(page, /截至|NaN/);
const oil = { quality: "B", per100: 5, segments: 1, err100: 0.5, distance: 90, end_ts: "2025-01-14" };
page = await renderHome({ fuel: oil, elec: { ...oil, quality: "A", end_ts: "2025-01-27" } });
assert.match(page, /油耗与电耗（分开计算）/);
assert.ok(page.includes("01-14") && page.includes("01-27"));
v.has_elec = false;
page = await renderHome({ fuel: oil });
assert.match(page, /最近可算的油耗/);
assert.match(page, /误差约 ±0.5 L\/100km/);
assert.doesNotMatch(page, /电耗趋势/);
v.has_fuel = false;
v.has_elec = true;
page = await renderHome({ elec: { quality: "A", per100: 11, segments: 3, distance: 430, end_ts: "2025-01-08" }, elec_totals: { overall: { per100: null }, idle: { amount: 2 } } });
assert.match(page, /最近 3 个 A 级段/);
assert.match(page, /综合电耗（含静置）<\/span><b[^>]*>–/);
assert.doesNotMatch(page, /油耗趋势/);
const sparse = { mileage: 0, mileage_known: false, elec_count: 2, fuel_count: 0, elec_kwh: 0, elec_qty_missing: 2,
  fuel_l: 0, fuel_qty_missing: 0, energy_cents: 0, total_cents: 0, energy_cost_missing: 2, energy_cost_per_km: 0, total_cost_per_km: 0 };
const previous = { mileage: 160, mileage_known: true, energy_cents: 12000, total_cents: 12000, energy_cost_per_km: 0.75, total_cost_per_km: 0.75 };
const withPeriod = (period, other = {}) => renderHome({ fuel: missing, elec: missing }, { period, prev: previous, ...other });
page = await withPeriod(sparse);
assert.match(page, /2 笔未填电量/);
assert.match(page, /2 笔补能金额未填/);
assert.doesNotMatch(page, /¥0\.00|>0<small>|class="delta |class="home-side/);
page = await withPeriod({ ...sparse, elec_qty_missing: 1, elec_kwh: 5.25, energy_cost_missing: 1, energy_cents: 300, total_cents: 700 });
assert.match(page, /5\.25<small>kWh/);
assert.match(page, /已知小计，1 笔未填电量/);
assert.match(page, /¥7\.00/);
assert.match(page, /已知小计 · 1 笔补能金额未填/);
assert.doesNotMatch(page, /class="delta /);
page = await withPeriod({ ...sparse, mileage_known: true, elec_qty_missing: 0, elec_kwh: 10, energy_cost_missing: 0,
  energy_cost_per_km: null, total_cost_per_km: null }, { prev: null, history: { days: 40 } });
assert.match(page, /0<small>km/);
assert.match(page, /¥0\.00/);
assert.match(page, /class="home-side stack"/);
page = await withPeriod(sparse, { history: { ...sparse, days: 40, total_cents: 580 } });
assert.match(page, /累计里程（40 天）<\/span><b class="num">—/);
assert.match(page, /累计实付<\/span><b class="num">¥5\.80/);
assert.match(page, /已知小计，2 笔补能金额未填/);
page = await withPeriod({ ...sparse, energy_cost_missing: 1 });
assert.match(page, /已填补能金额均为零/);
assert.doesNotMatch(page, /¥0\.00/);
v.has_fuel = true;
page = await withPeriod({ ...sparse, fuel_count: 1, fuel_qty_missing: 1, energy_cost_missing: 3, total_cents: 3000 });
assert.match(page, /1 笔未填油量/);
assert.match(page, /¥30\.00/);
state.user = { settings: { home_cost_scope: "energy" } };
page = await withPeriod({ ...sparse, total_cents: 3000 });
assert.match(page, /本期油电实付/);
assert.doesNotMatch(page, /¥30\.00|¥0\.00/);
state.user = null;
mock({ segments: {}, totals: {}, months: [], charging: { k: 0.201, k_source: "single", capacity_est: 18.1, samples: [{}], types: [], soc_start: [] } });
await statsPage({ params: { tab: "energy" } });
page = nodes.get("#stats-body").innerHTML;
assert.match(page, /从 0% 充到 100% 预计用电（桩侧）[^\n]*>20\.1 <small>kWh/);
assert.match(page, /电池侧可用容量估算[^\n]*>18\.1 <small>kWh/);
assert.match(page, /还没有可用的静置段/);
assert.doesNotMatch(page, />0<small>kWh/);
mock({ segments: {}, totals: { elec: { idle: { amount: 0, count: 1 } } }, months: [] });
await statsPage({ params: { tab: "energy" } });
assert.match(nodes.get("#stats-body").innerHTML, />0<small>kWh/);
console.log(`SMOKE OK: ${files.length} 个前端模块链接成功，首页缺项、能耗分支、静置与桩侧换算样例通过`);
