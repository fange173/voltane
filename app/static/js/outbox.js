/* outbox.js — 离线发件箱：网络失败时新建请求先存本地，联网后按 client_id 幂等重发 */
import { emit } from "./state.js";
import { toast } from "./ui.js";
import { api, store } from "./util.js";

const KEY = "outbox";

export const pending = () => store.get(KEY, []);

export function enqueue(item) {
  const list = pending();
  list.push({ ...item, queued_at: new Date().toISOString() });
  store.set(KEY, list);
  emit("outbox-changed");
}

let flushing = false;
export async function flush() {
  if (flushing) return;
  const list = pending();
  if (!list.length) return;
  flushing = true;
  let sent = 0;
  const left = [];
  for (const it of list) {
    try {
      await api(it.url, { method: "POST", body: it.body });
      sent++;
    } catch (e) {
      // 网络问题保留重试；业务错误（4xx）丢弃并提示，避免永远卡住
      if (e.status === 0 || e.status >= 500) left.push(it);
      else if (e.status === 409 && e.detail?.code === "duplicate") sent++;
      else toast(`一条离线记录同步失败：${e.message}`, { error: true });
    }
  }
  store.set(KEY, left);
  flushing = false;
  if (sent) {
    toast(`已同步 ${sent} 条离线记录`);
    emit("data-changed");
  }
  emit("outbox-changed");
}

export function startOutbox() {
  window.addEventListener("online", flush);
  document.addEventListener("visibilitychange", () => document.visibilityState === "visible" && flush());
  setInterval(flush, 60000);
  flush();
}
