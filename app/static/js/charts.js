/* charts.js — 轻量 SVG 图表：迷你折线、折线、柱状（可堆叠）、环图。颜色取 CSS 变量，自动适配深浅色。 */
import { esc } from "./util.js";

const css = (name) => `var(--${name})`;

function niceMax(v) {
  if (!(v > 0)) return 1;
  const e = Math.pow(10, Math.floor(Math.log10(v)));
  const f = v / e;
  const n = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
  return n * e;
}
function niceRange(lo, hi) {
  if (lo === hi) {
    lo = lo * 0.8;
    hi = hi * 1.2 || 1;
  }
  const pad = (hi - lo) * 0.15;
  let a = lo - pad;
  const b = niceMax(hi + pad);
  if (a < 0 || lo >= 0) a = Math.max(0, a);
  const step = niceMax((b - a) / 4) / (niceMax((b - a) / 4) / ((b - a) / 4) > 1.6 ? 2 : 1);
  a = Math.floor(a / step) * step;
  return [a, b];
}
const fmtTick = (v) => (Math.abs(v) >= 1000 ? (v / 1000).toFixed(v % 1000 ? 1 : 0) + "k" : +v.toFixed(2));

/** 迷你折线（首页卡片里），返回 SVG 字符串。 */
export function sparkline(values, { color = "brand", w = 120, h = 36, hollow = [] } = {}) {
  const vals = values.filter((v) => v != null);
  if (vals.length < 2) return "";
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const span = hi - lo || 1;
  const pts = values.map((v, i) => [
    (i / (values.length - 1)) * (w - 6) + 3,
    h - 4 - ((v - lo) / span) * (h - 8),
  ]);
  const d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
  const area = d + ` L${pts[pts.length - 1][0].toFixed(1)} ${h} L${pts[0][0].toFixed(1)} ${h} Z`;
  const last = pts[pts.length - 1];
  const id = "sg" + Math.random().toString(36).slice(2, 8);
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" preserveAspectRatio="none">
    <defs><linearGradient id="${id}" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0" stop-color="${css(color)}" stop-opacity=".28"/><stop offset="1" stop-color="${css(color)}" stop-opacity="0"/></linearGradient></defs>
    <path d="${area}" fill="url(#${id})"/>
    <path d="${d}" fill="none" stroke="${css(color)}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
    ${pts.map((p, i) => (hollow[i] ? `<circle cx="${p[0]}" cy="${p[1]}" r="2.2" fill="var(--surface-1)" stroke="${css(color)}" stroke-width="1.5"/>` : "")).join("")}
    <circle cx="${last[0]}" cy="${last[1]}" r="3" fill="${css(color)}"/></svg>`;
}

/* ---------- 通用坐标系 ---------- */

function setup(el, height) {
  el.classList.add("chart");
  el.style.position = "relative";
  const w = Math.max(el.clientWidth || 320, 240);
  return { w, h: height };
}

function attachTooltip(el, svg, { count, xAt, content, guide = true, h, top }) {
  const tip = document.createElement("div");
  tip.className = "chart-tip hidden";
  el.appendChild(tip);
  const line = svg.querySelector(".guide");
  const pick = (clientX) => {
    const r = svg.getBoundingClientRect();
    const x = clientX - r.left;
    let best = 0;
    let bd = Infinity;
    for (let i = 0; i < count; i++) {
      const d = Math.abs(xAt(i) - x);
      if (d < bd) {
        bd = d;
        best = i;
      }
    }
    return best;
  };
  const show = (clientX) => {
    const i = pick(clientX);
    const c = content(i);
    if (!c) return hide();
    tip.innerHTML = c;
    tip.classList.remove("hidden");
    const x = xAt(i);
    const tw = tip.offsetWidth;
    tip.style.left = Math.min(Math.max(x - tw / 2, 0), el.clientWidth - tw) + "px";
    tip.style.top = (top ?? 0) + "px";
    if (guide && line) {
      line.setAttribute("x1", x);
      line.setAttribute("x2", x);
      line.style.opacity = 1;
    }
    svg.querySelectorAll("[data-i]").forEach((n) => n.classList.toggle("hl", +n.dataset.i === i));
  };
  const hide = () => {
    tip.classList.add("hidden");
    if (line) line.style.opacity = 0;
    svg.querySelectorAll(".hl").forEach((n) => n.classList.remove("hl"));
  };
  svg.addEventListener("pointermove", (e) => show(e.clientX));
  svg.addEventListener("pointerdown", (e) => show(e.clientX));
  svg.addEventListener("pointerleave", (e) => e.pointerType === "mouse" && hide());
}

function yAxis(ticks, y, w, padL, fmt) {
  return ticks
    .map((t) => `<g><line x1="${padL}" x2="${w}" y1="${y(t)}" y2="${y(t)}" class="grid"/>
      <text x="${padL - 6}" y="${y(t) + 4}" class="axis" text-anchor="end">${esc(fmt(t))}</text></g>`)
    .join("");
}

function ticksOf(a, b, n = 4) {
  const out = [];
  for (let i = 0; i <= n; i++) out.push(a + ((b - a) * i) / n);
  return out;
}

/**
 * 折线图。series: [{name, color, values:[number|null], hollow:[bool]}]；labels: x 轴文字。
 * 用于能耗趋势、单价走势、累计里程等。
 */
export function lineChart(el, { labels, series, height = 200, unit = "", fmt = fmtTick, area = true, tipLabel } = {}) {
  const render = () => {
    el.innerHTML = "";
    const { w, h } = setup(el, height);
    const all = series.flatMap((s) => s.values).filter((v) => v != null);
    if (!all.length) {
      el.innerHTML = `<div class="chart-empty">暂无数据</div>`;
      return;
    }
    const [lo, hi] = niceRange(Math.min(...all), Math.max(...all));
    const padL = 40;
    const padR = 10;
    const padT = 12;
    const padB = 24;
    const n = labels.length;
    const xAt = (i) => (n === 1 ? (padL + w - padR) / 2 : padL + (i / (n - 1)) * (w - padL - padR));
    const y = (v) => padT + (1 - (v - lo) / (hi - lo)) * (h - padT - padB);
    const ticks = ticksOf(lo, hi);
    const step = Math.max(1, Math.ceil(n / Math.floor((w - padL) / 64)));
    let s = `<svg width="${w}" height="${h}" class="chart-svg">${yAxis(ticks, y, w - padR, padL, fmt)}`;
    labels.forEach((l, i) => {
      if (i % step === 0 || i === n - 1) {
        if (i !== n - 1 && n - 1 - i < step * 0.6) return;
        s += `<text x="${xAt(i)}" y="${h - 6}" class="axis" text-anchor="middle">${esc(l)}</text>`;
      }
    });
    s += `<line class="guide" y1="${padT}" y2="${h - padB}" x1="0" x2="0"/>`;
    series.forEach((ser, si) => {
      let d = "";
      let started = false;
      ser.values.forEach((v, i) => {
        if (v == null) {
          started = false;
          return;
        }
        d += (started ? "L" : "M") + xAt(i).toFixed(1) + " " + y(v).toFixed(1) + " ";
        started = true;
      });
      const col = css(ser.color);
      if (area && series.length === 1) {
        const idx = ser.values.map((v, i) => (v == null ? null : i)).filter((i) => i != null);
        if (idx.length > 1) {
          const gid = "lg" + si + Math.random().toString(36).slice(2, 6);
          s += `<defs><linearGradient id="${gid}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${col}" stop-opacity=".22"/><stop offset="1" stop-color="${col}" stop-opacity="0"/></linearGradient></defs>`;
          s += `<path d="${d} L${xAt(idx[idx.length - 1])} ${h - padB} L${xAt(idx[0])} ${h - padB} Z" fill="url(#${gid})"/>`;
        }
      }
      s += `<path d="${d}" fill="none" stroke="${col}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round" ${ser.dashed ? 'stroke-dasharray="5 4"' : ""}/>`;
      ser.values.forEach((v, i) => {
        if (v == null) return;
        const hollow = ser.hollow?.[i];
        const r = n > 40 ? 2 : 3.2;
        s += `<circle data-i="${i}" class="pt" cx="${xAt(i)}" cy="${y(v)}" r="${r}" fill="${hollow ? "var(--surface-1)" : col}" stroke="${col}" stroke-width="${hollow ? 1.6 : 0}"/>`;
      });
    });
    s += "</svg>";
    el.insertAdjacentHTML("beforeend", s);
    const svg = el.querySelector("svg");
    attachTooltip(el, svg, {
      count: n,
      xAt,
      top: 0,
      content: (i) => {
        const rows = series
          .filter((ser) => ser.values[i] != null)
          .map((ser) => `<div><i style="background:${css(ser.color)}"></i>${esc(ser.name)} <b>${esc(fmt(ser.values[i]))}${esc(unit)}</b>${ser.hollow?.[i] ? " <em>估</em>" : ""}</div>`);
        if (!rows.length) return "";
        return `<div class="tip-h">${esc(tipLabel ? tipLabel(i) : labels[i])}</div>${rows.join("")}`;
      },
    });
  };
  render();
  observe(el, render);
}

/**
 * 柱状图（可堆叠）。series: [{name, color, values, pattern?}]。
 * 用于月度花费、月度里程、月度能耗。
 */
export function barChart(el, { labels, series, height = 200, stacked = false, unit = "", fmt = fmtTick, tipFmt, line } = {}) {
  const render = () => {
    el.innerHTML = "";
    const { w, h } = setup(el, height);
    const n = labels.length;
    const totals = labels.map((_, i) =>
      stacked ? series.reduce((a, s) => a + (s.values[i] || 0), 0) : Math.max(...series.map((s) => s.values[i] || 0))
    );
    const max = niceMax(Math.max(...totals, line ? Math.max(...line.values.filter((v) => v != null), 0) : 0));
    if (!(Math.max(...totals) > 0)) {
      el.innerHTML = `<div class="chart-empty">暂无数据</div>`;
      return;
    }
    const padL = 40;
    const padR = 8;
    const padT = 12;
    const padB = 24;
    const bw = (w - padL - padR) / n;
    const xAt = (i) => padL + bw * i + bw / 2;
    const y = (v) => padT + (1 - v / max) * (h - padT - padB);
    const barW = Math.max(Math.min(bw * (stacked || series.length === 1 ? 0.56 : 0.8), 34), 3);
    let s = `<svg width="${w}" height="${h}" class="chart-svg">${yAxis(ticksOf(0, max), y, w - padR, padL, fmt)}`;
    const step = Math.max(1, Math.ceil(n / Math.floor((w - padL) / 44)));
    labels.forEach((l, i) => {
      if (i % step === 0) s += `<text x="${xAt(i)}" y="${h - 6}" class="axis" text-anchor="middle">${esc(l)}</text>`;
    });
    labels.forEach((_, i) => {
      s += `<g data-i="${i}" class="bar-g">`;
      if (stacked) {
        let acc = 0;
        series.forEach((ser, si) => {
          const v = ser.values[i] || 0;
          if (v <= 0) return;
          const y0 = y(acc + v);
          const bh = y(acc) - y0;
          const top = series.slice(si + 1).every((x) => !(x.values[i] > 0));
          s += `<rect x="${xAt(i) - barW / 2}" y="${y0}" width="${barW}" height="${Math.max(bh - (top ? 0 : 1), 0.5)}" rx="${top ? 5 : 0}" fill="${css(ser.color)}" ${ser.pattern ? 'fill-opacity=".45"' : ""}/>`;
          if (top && bh > 5) s += `<rect x="${xAt(i) - barW / 2}" y="${y0 + 4}" width="${barW}" height="${Math.max(bh - 4, 0)}" fill="${css(ser.color)}" ${ser.pattern ? 'fill-opacity=".45"' : ""}/>`;
          acc += v;
        });
      } else {
        const k = series.length;
        const sw = barW / k;
        series.forEach((ser, si) => {
          const v = ser.values[i];
          if (!(v > 0)) return;
          const x = xAt(i) - barW / 2 + sw * si;
          s += `<rect x="${x + 1}" y="${y(v)}" width="${Math.max(sw - 2, 2)}" height="${y(0) - y(v)}" rx="4" fill="${css(ser.color)}"/>`;
        });
      }
      s += `</g>`;
    });
    if (line) {
      let d = "";
      let st = false;
      line.values.forEach((v, i) => {
        if (v == null) return (st = false);
        d += (st ? "L" : "M") + xAt(i) + " " + y(v) + " ";
        st = true;
      });
      s += `<path d="${d}" fill="none" stroke="${css(line.color)}" stroke-width="2" stroke-dasharray="4 3"/>`;
    }
    s += `<line class="guide" y1="${padT}" y2="${h - padB}"/></svg>`;
    el.insertAdjacentHTML("beforeend", s);
    const svg = el.querySelector("svg");
    attachTooltip(el, svg, {
      count: n,
      xAt,
      guide: false,
      top: 0,
      content: (i) => {
        const rows = series
          .filter((ser) => ser.values[i] > 0)
          .map((ser) => `<div><i style="background:${css(ser.color)}"></i>${esc(ser.name)} <b>${esc((tipFmt || fmt)(ser.values[i]))}${esc(unit)}</b></div>`);
        if (line && line.values[i] != null) rows.push(`<div><i style="background:${css(line.color)}"></i>${esc(line.name)} <b>${esc((tipFmt || fmt)(line.values[i]))}</b></div>`);
        if (stacked && rows.length > 1) rows.push(`<div class="tip-total">合计 <b>${esc((tipFmt || fmt)(totals[i]))}${esc(unit)}</b></div>`);
        return rows.length ? `<div class="tip-h">${esc(labels[i])}</div>${rows.join("")}` : "";
      },
    });
  };
  render();
  observe(el, render);
}

/** 环图：items [{label, value, color}]，center: 中间文字 HTML。 */
export function donut(el, { items, size = 168, thickness = 22, center = "" }) {
  const total = items.reduce((a, b) => a + b.value, 0);
  if (!(total > 0)) {
    el.innerHTML = `<div class="chart-empty">暂无数据</div>`;
    return;
  }
  const r = (size - thickness) / 2;
  const c = 2 * Math.PI * r;
  let off = 0;
  const gap = items.length > 1 ? 2 : 0;
  const arcs = items
    .map((it) => {
      const len = (it.value / total) * c;
      const seg = `<circle r="${r}" cx="${size / 2}" cy="${size / 2}" fill="none" stroke="${css(it.color)}" stroke-width="${thickness}"
        stroke-dasharray="${Math.max(len - gap, 0.5)} ${c}" stroke-dashoffset="${-off}" transform="rotate(-90 ${size / 2} ${size / 2})"/>`;
      off += len;
      return seg;
    })
    .join("");
  el.innerHTML = `<div class="donut" style="width:${size}px;height:${size}px">
    <svg width="${size}" height="${size}"><circle r="${r}" cx="${size / 2}" cy="${size / 2}" fill="none" stroke="var(--surface-3)" stroke-width="${thickness}"/>${arcs}</svg>
    <div class="donut-center">${center}</div></div>`;
}

const observers = new WeakMap();
function observe(el, fn) {
  if (!("ResizeObserver" in window)) return;
  observers.get(el)?.disconnect();
  let lastW = el.clientWidth;
  let t;
  const ro = new ResizeObserver(() => {
    const wNow = el.clientWidth;
    if (Math.abs(wNow - lastW) < 8) return;
    lastW = wNow;
    clearTimeout(t);
    t = setTimeout(fn, 120);
  });
  ro.observe(el);
  observers.set(el, ro);
}
