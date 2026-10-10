// The live sky behind everything: a gradient that follows the local time of day
// (night, dawn, day, golden hour, dusk) with a few soft clouds drifting slowly.
// Drawn at half the device resolution (DPR capped at 2) with high-quality smoothing, so
// gradients stay clean without banding or mud; it pauses while the
// tab is hidden and is a still picture under prefers-reduced-motion.
// ?sky=dawn|day|golden|dusk|night pins the sky (kept for the session, handy for screenshots).

// [top, middle, bottom, cloud, glow]
const PAL = {
  night: ["#0a1430", "#17234b", "#2f3566", [150, 160, 215, 0.13], [120, 110, 190, 0.10]],
  dawn: ["#4f659e", "#a998c4", "#f2c4b6", [255, 236, 236, 0.30], [255, 196, 170, 0.30]],
  day: ["#4f88d0", "#94bdeb", "#dde9f5", [255, 255, 255, 0.58], [255, 250, 235, 0.22]],
  golden: ["#5d80b6", "#c9a3a3", "#f4a77c", [255, 226, 204, 0.40], [255, 178, 120, 0.42]],
  dusk: ["#253f6e", "#5b6898", "#b88f9a", [205, 170, 200, 0.20], [235, 160, 140, 0.24]],
};
// Hour-of-day keyframes; colours are blended between neighbours.
const TIMELINE = [[0, "night"], [4.6, "night"], [6.0, "dawn"], [7.6, "day"], [15.8, "day"], [17.3, "golden"], [18.3, "dusk"], [19.6, "night"], [24, "night"]];

const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const mix = (a, b, t) => a.map((v, i) => v + (b[i] - v) * t);
const rgb = (c) => `rgb(${c.slice(0, 3).map(Math.round).join(",")})`;
const rgba = (c, a = c[3]) => `rgba(${c.slice(0, 3).map(Math.round).join(",")},${a})`;
const lum = (c) => (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]) / 255;

function forced() {
  try {
    const q = new URLSearchParams(location.search).get("sky");
    if (q && PAL[q]) sessionStorage.setItem("odar.sky", q);
    else if (q === "live" || q === "auto") sessionStorage.removeItem("odar.sky");
    const s = sessionStorage.getItem("odar.sky");
    return PAL[s] ? s : "";
  } catch { return ""; }
}

// The sky for a given hour (0-24), as resolved colours plus its name and tone.
export function skyAt(hour, pin = forced()) {
  let a = "night", b = "night", t = 0;
  if (pin) { a = b = pin; } else {
    for (let i = 0; i < TIMELINE.length - 1; i++) {
      const [h0, n0] = TIMELINE[i], [h1, n1] = TIMELINE[i + 1];
      if (hour >= h0 && hour < h1) { a = n0; b = n1; t = h1 > h0 ? (hour - h0) / (h1 - h0) : 0; break; }
    }
  }
  const s = (k) => mix(hex(PAL[a][k]), hex(PAL[b][k]), t);
  const top = s(0), mid = s(1), bot = s(2);
  const cloud = mix(PAL[a][3], PAL[b][3], t), glow = mix(PAL[a][4], PAL[b][4], t);
  const brightness = 0.25 * lum(top) + 0.5 * lum(mid) + 0.25 * lum(bot);
  return { name: t < 0.5 ? a : b, top, mid, bot, cloud, glow, dark: brightness < 0.5 };
}

const now = () => { const d = new Date(); return d.getHours() + d.getMinutes() / 60 + d.getSeconds() / 3600; };

let canvas, ctx, W = 0, H = 0, sky = null, clouds = [], raf = 0, last = 0, onTone = null, lastTone = null;
// Backing-store scale: half the (capped) device pixel ratio, i.e. 1:1 CSS px on a retina
// phone. Soft clouds need no more than that, and the pixel budget keeps big screens cheap.
const MAX_PX = 1.3e6;
const scale = () => {
  const s = Math.min(2, window.devicePixelRatio || 1) * 0.5;
  const px = innerWidth * innerHeight * s * s;
  return px > MAX_PX ? s * Math.sqrt(MAX_PX / px) : s;
};
const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

function seedClouds() {
  // deterministic-ish layout so the sky looks composed on first paint
  const spots = [[0.12, 0.18, 0.55], [0.72, 0.1, 0.7], [0.4, 0.42, 0.5], [0.9, 0.55, 0.6], [0.05, 0.7, 0.65], [0.55, 0.82, 0.8], [0.3, 0.95, 0.6]];
  clouds = spots.map(([x, y, s], i) => ({ x, y, s, v: 0.0035 + (i % 3) * 0.0016, sq: 0.38 + (i % 4) * 0.07, k: 3 + (i % 3) }));
}

function resize() {
  const k = scale();
  W = Math.max(40, Math.round(innerWidth * k));
  H = Math.max(40, Math.round(innerHeight * k));
  canvas.width = W; canvas.height = H;
  ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = "high";
}

function draw() {
  const g = ctx.createLinearGradient(0, 0, 0, H);
  g.addColorStop(0, rgb(sky.top)); g.addColorStop(0.55, rgb(sky.mid)); g.addColorStop(1, rgb(sky.bot));
  ctx.globalCompositeOperation = "source-over";
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
  // low warm glow near the horizon (sun just out of frame)
  const gl = ctx.createRadialGradient(W * 0.78, H * 1.02, 0, W * 0.78, H * 1.02, Math.max(W, H) * 0.75);
  gl.addColorStop(0, rgba(sky.glow)); gl.addColorStop(1, rgba(sky.glow, 0));
  ctx.fillStyle = gl; ctx.fillRect(0, 0, W, H);
  // clouds: clusters of soft elliptical puffs
  const R = Math.max(W, H);
  for (const c of clouds) {
    const cx = ((c.x % 1.4) + 1.4) % 1.4 - 0.2;
    for (let j = 0; j < c.k; j++) {
      const px = (cx + (j - (c.k - 1) / 2) * 0.09 * c.s) * W;
      const py = c.y * H + Math.sin(j * 1.7 + c.y * 9) * 0.02 * H;
      const r = R * 0.16 * c.s * (1 - Math.abs(j - (c.k - 1) / 2) * 0.18);
      ctx.save();
      ctx.translate(px, py); ctx.scale(1, c.sq);
      const rg = ctx.createRadialGradient(0, 0, 0, 0, 0, r);
      rg.addColorStop(0, rgba(sky.cloud)); rg.addColorStop(0.55, rgba(sky.cloud, sky.cloud[3] * 0.45)); rg.addColorStop(1, rgba(sky.cloud, 0));
      ctx.fillStyle = rg;
      ctx.beginPath(); ctx.arc(0, 0, r, 0, Math.PI * 2); ctx.fill();
      ctx.restore();
    }
  }
}

function refreshSky() {
  sky = skyAt(now());
  const root = document.documentElement;
  root.style.setProperty("--sky-top", rgb(sky.top));
  root.style.setProperty("--sky-mid", rgb(sky.mid));
  root.style.setProperty("--sky-bot", rgb(sky.bot));
  root.dataset.sky = sky.name;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = rgb(sky.top);
  const tone = sky.dark ? "dark" : "light";
  if (tone !== lastTone) { lastTone = tone; if (onTone) onTone(tone); }
}

// The page can ask the sky to hold still for a moment (while the user scrolls or an answer
// streams) so the canvas never competes with them for a frame; the clouds drift so slowly that a
// pause is invisible. skyHold(0) releases it.
let holdUntil = 0;
export function skyHold(ms) { holdUntil = ms > 0 ? Math.max(holdUntil, performance.now() + ms) : 0; }

function frame(t) {
  raf = 0;
  if (document.hidden) return;
  if (t < holdUntil) { last = t; raf = requestAnimationFrame(frame); return; }
  const dt = last ? Math.min(0.1, (t - last) / 1000) : 0;
  if (t - last >= 1000 / 20 || !last) { // ~20 fps is plenty for drifting clouds
    for (const c of clouds) c.x += c.v * dt;
    last = t;
    draw();
  }
  raf = requestAnimationFrame(frame);
}
function start() { if (!raf && !reduced() && !document.hidden) { last = 0; raf = requestAnimationFrame(frame); } }
function stop() { if (raf) cancelAnimationFrame(raf); raf = 0; }

export function skyTone() { return lastTone || (sky && sky.dark ? "dark" : "light"); }

// Start the sky. ``toneChanged(tone)`` is called whenever the sky crosses light/dark.
export function initSky(toneChanged) {
  onTone = toneChanged;
  canvas = document.getElementById("sky");
  refreshSky();
  if (!canvas || !canvas.getContext) return;
  ctx = canvas.getContext("2d", { alpha: false });
  seedClouds(); resize(); draw();
  addEventListener("resize", () => { resize(); draw(); });
  document.addEventListener("visibilitychange", () => { if (document.hidden) stop(); else { refreshSky(); draw(); start(); } });
  matchMedia("(prefers-reduced-motion: reduce)").addEventListener?.("change", () => { if (reduced()) stop(); else start(); });
  setInterval(() => { if (!document.hidden) { refreshSky(); if (!raf) draw(); } }, 60000);
  start();
}
