/* router.js — History API 路由：path → 视图渲染函数 */

const routes = [];
let notFound = null;
let current = null;
let beforeRender = null;

/** 注册路由，pattern 形如 "/records/:id"。 */
export function route(pattern, handler, meta = {}) {
  const keys = [];
  const re = new RegExp(
    "^" + pattern.replace(/\/:(\w+)/g, (_, k) => (keys.push(k), "/([^/]+)")).replace(/\//g, "\\/") + "\\/?$"
  );
  routes.push({ re, keys, handler, meta, pattern });
}
export const fallback = (fn) => (notFound = fn);
export const onBeforeRender = (fn) => (beforeRender = fn);

export function match(path) {
  for (const r of routes) {
    const m = r.re.exec(path);
    if (m) {
      const params = {};
      r.keys.forEach((k, i) => (params[k] = decodeURIComponent(m[i + 1])));
      return { ...r, params };
    }
  }
  return null;
}

export function navigate(path, { replace = false } = {}) {
  if (path === location.pathname + location.search && !replace) {
    render();
    return;
  }
  history[replace ? "replaceState" : "pushState"]({}, "", path);
  render();
}

export async function render() {
  const path = location.pathname;
  const query = Object.fromEntries(new URLSearchParams(location.search));
  const m = match(path);
  current = { path, query, ...(m || {}) };
  beforeRender?.(current);
  if (!m) return notFound?.(current);
  return m.handler({ params: m.params, query, path });
}

export const currentRoute = () => current;

export function startRouter() {
  window.addEventListener("popstate", render);
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[data-link]");
    if (!a || e.metaKey || e.ctrlKey || e.shiftKey || a.target === "_blank") return;
    e.preventDefault();
    navigate(a.getAttribute("href"));
  });
  return render();
}
