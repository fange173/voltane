/* outbox.js — 离线新建按账号和 client_id 分笔存放，服务端确认后才移除。 */
import { emit, state } from "./state.js";
import { toast } from "./ui.js";
import { api } from "./util.js";

const prefix = (uid) => `vt:outbox:${uid}:`;
const keyOf = (uid, id) => prefix(uid) + id;

export function pending(uid = state.user?.id) {
  if (!uid) return [];
  const items = [];
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (key?.startsWith(prefix(uid))) items.push(JSON.parse(localStorage.getItem(key)));
  }
  return items.sort((a, b) => a.queued_at.localeCompare(b.queued_at));
}

export function enqueue(item) {
  if (!state.user?.id || !item.body?.client_id) throw new Error("未能确认离线记录所属账号，请保留表单重试");
  try {
    localStorage.setItem(keyOf(state.user.id, item.body.client_id), JSON.stringify({ ...item, queued_at: new Date().toISOString() }));
  } catch {
    throw new Error("未能写入本地存储，记录尚未保存，请保留表单重试");
  }
  emit("outbox-changed");
}

export function confirmDuplicate(id) {
  const it = pending().find((item) => item.body.client_id === id && item.duplicate);
  if (!it) return;
  enqueue({ ...it, duplicate: false, error: null, body: { ...it.body, force: true } });
  return flush();
}

let flushing = false;
export async function flush() {
  const uid = state.user?.id;
  if (!uid || flushing) return;
  flushing = true;
  let sent = 0;
  try {
    for (const it of pending(uid)) {
      if (state.user?.id !== uid) break;
      if (it.duplicate) continue;
      const key = keyOf(uid, it.body.client_id);
      try {
        const result = await api(it.url, { method: "POST", body: it.body });
        const saved = result?.record ?? result?.expense;
        if (!saved?.id || saved.client_id !== it.body.client_id || saved.vehicle_id !== it.vehicle_id) continue;
        localStorage.removeItem(key);
        sent++;
      } catch (e) {
        if (!e.status || e.status === 401 || e.status >= 500) break;
        if (it.error !== e.message && localStorage.getItem(key)) {
          localStorage.setItem(key, JSON.stringify({ ...it, error: e.message, duplicate: e.status === 409 && e.detail?.code === "duplicate" }));
          toast(`离线记录未同步：${e.message}`, { error: true });
        }
      }
    }
  } finally {
    flushing = false;
    if (sent) { toast(`已同步 ${sent} 条离线记录`); emit("data-changed"); }
    emit("outbox-changed");
  }
}

export function startOutbox() {
  // 旧队列没有账号归属，保留原数据，不用当前登录账号自动代发。
  if (localStorage.getItem("vt:outbox") && localStorage.getItem("vt:outbox") !== "[]") {
    toast("本机有旧版待同步记录，因无法确认所属账号已保留，请核对原账本");
  }
  window.addEventListener("online", flush);
  document.addEventListener("visibilitychange", () => document.visibilityState === "visible" && flush());
  setInterval(flush, 60000);
  flush();
}
