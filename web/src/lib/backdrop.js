// Chat backdrop: a warped mesh gradient painted once into a tiny canvas (the browser's smooth upscale
// does the rest), with film grain on top. Static, so it costs nothing after the first paint and has no
// motion to reduce. Colours come from --mesh-1..4 in app.css, so light and dark each get their own.

const W = 96;
const H = 64;
const KEY = "lark.backdrop"; // "mesh" (default) or "plain", per device

export const getBackdrop = () => {
  try {
    return localStorage.getItem(KEY) === "plain" ? "plain" : "mesh";
  } catch {
    return "mesh";
  }
};

export const setBackdrop = (v) => {
  try {
    localStorage.setItem(KEY, v);
  } catch {}
  document.documentElement.dataset.backdrop = v;
};

const rgb = (hex) => {
  const h = hex.trim().replace("#", "");
  const n = parseInt(h.length === 3 ? h.replace(/./g, "$&$&") : h, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
};

const smooth = (t) => t * t * (3 - 2 * t);

// A 4x3 grid of control colours (the "mesh"), sampled through a two-pass domain warp so the
// patches bend into each other as flowing shapes instead of sitting as round blobs.
const GRID = [
  [1, 0, 2, 3],
  [0, 3, 1, 0],
  [2, 1, 0, 2],
];

export function paintMesh(canvas) {
  const css = getComputedStyle(document.documentElement);
  const pal = [1, 2, 3, 4].map((i) => rgb(css.getPropertyValue(`--mesh-${i}`) || "#888"));
  const ctx = canvas.getContext("2d");
  canvas.width = W;
  canvas.height = H;
  const img = ctx.createImageData(W, H);
  const cols = GRID[0].length - 1;
  const rows = GRID.length - 1;
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      let u = x / (W - 1);
      let v = y / (H - 1);
      // two layers of warp: broad sweeps, then a gentler fold on top of them
      const u1 = u + 0.22 * Math.sin(v * 3.1 + 0.6) + 0.08 * Math.sin(v * 7.3 + u * 2.0);
      const v1 = v + 0.2 * Math.sin(u * 2.7 + 1.9) + 0.07 * Math.cos(u * 6.1 - v * 1.7);
      u = u1 + 0.1 * Math.sin(v1 * 4.4 + 2.3);
      v = v1 + 0.1 * Math.cos(u1 * 3.6 + 0.4);
      const gx = Math.min(Math.max(u, 0), 1) * cols;
      const gy = Math.min(Math.max(v, 0), 1) * rows;
      const cx = Math.min(Math.floor(gx), cols - 1);
      const cy = Math.min(Math.floor(gy), rows - 1);
      const tx = smooth(gx - cx);
      const ty = smooth(gy - cy);
      const a = pal[GRID[cy][cx]], b = pal[GRID[cy][cx + 1]];
      const c = pal[GRID[cy + 1][cx]], d = pal[GRID[cy + 1][cx + 1]];
      const i = (y * W + x) * 4;
      for (let k = 0; k < 3; k++) {
        const top = a[k] + (b[k] - a[k]) * tx;
        const bot = c[k] + (d[k] - c[k]) * tx;
        img.data[i + k] = top + (bot - top) * ty;
      }
      img.data[i + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
}

// Repaint when the colour scheme changes (system setting or the data-theme attribute).
export function watchMesh(canvas) {
  const paint = () => paintMesh(canvas);
  paint();
  const mq = matchMedia("(prefers-color-scheme: dark)");
  mq.addEventListener("change", paint);
  const mo = new MutationObserver(paint);
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  return () => {
    mq.removeEventListener("change", paint);
    mo.disconnect();
  };
}
