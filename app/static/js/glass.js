// glass.js — 底栏液态玻璃折射：思路借鉴 shuding/liquid-glass（MIT, Copyright (c) 2025 Shu Ding），
// 用 canvas 生成边缘位移图 + SVG feDisplacementMap，经 backdrop-filter: url() 应用。仅 Chromium 支持，其余浏览器走 CSS 降级。
const NS = "http://www.w3.org/2000/svg";
const BEZEL = 16;

function supportsSvgBackdrop() {
  const brands = navigator.userAgentData?.brands;
  return !!brands && brands.some((b) => /Chromium/i.test(b.brand));
}

// 胶囊形位移图：边缘处向内偏移，中心为中性灰（不位移）
function buildMap(w, h) {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const ctx = c.getContext("2d");
  const img = ctx.createImageData(w, h);
  const r = h / 2;
  const sdf = (x, y) => {
    const cx = Math.min(Math.max(x, r), w - r);
    return r - Math.hypot(x - cx, y - r);
  };
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const d = sdf(x + 0.5, y + 0.5);
      let dx = 0;
      let dy = 0;
      if (d > 0 && d < BEZEL) {
        const gx = sdf(x + 1.5, y + 0.5) - sdf(x - 0.5, y + 0.5);
        const gy = sdf(x + 0.5, y + 1.5) - sdf(x + 0.5, y - 0.5);
        const n = Math.hypot(gx, gy) || 1;
        const m = Math.pow(1 - d / BEZEL, 2);
        dx = (gx / n) * m;
        dy = (gy / n) * m;
      }
      const i = (y * w + x) * 4;
      img.data[i] = 128 + dx * 127;
      img.data[i + 1] = 128 + dy * 127;
      img.data[i + 2] = 128;
      img.data[i + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  return c.toDataURL();
}

export function initGlass(nav) {
  if (!nav || !supportsSvgBackdrop() || document.getElementById("glass-defs")) return;
  const svg = document.createElementNS(NS, "svg");
  svg.id = "glass-defs";
  svg.setAttribute("width", "0");
  svg.setAttribute("height", "0");
  svg.style.cssText = "position:absolute;pointer-events:none";
  svg.innerHTML = `<defs><filter id="glass-refract" x="0" y="0" width="100%" height="100%" color-interpolation-filters="sRGB">
    <feImage id="glass-map" result="map" preserveAspectRatio="none"/>
    <feDisplacementMap in="SourceGraphic" in2="map" scale="26" xChannelSelector="R" yChannelSelector="G"/>
  </filter></defs>`;
  document.body.appendChild(svg);
  const update = () => {
    const w = Math.round(nav.offsetWidth);
    const h = Math.round(nav.offsetHeight);
    if (!w || !h) {
      nav.classList.remove("glass-svg");
      return;
    }
    const map = svg.querySelector("#glass-map");
    map.setAttribute("href", buildMap(w, h));
    map.setAttribute("width", w);
    map.setAttribute("height", h);
    nav.classList.add("glass-svg");
  };
  update();
  new ResizeObserver(update).observe(nav);
}
