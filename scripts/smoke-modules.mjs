/**
 * smoke-modules.mjs — 无浏览器环境下 import 全部前端模块。
 * 能抓住：语法错误、import 未解析、模块顶层执行时的崩溃。
 * 用法：node scripts/smoke-modules.mjs
 */
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
console.log(`SMOKE OK: ${files.length} 个前端模块链接成功，顶层执行无崩溃`);
