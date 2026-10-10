// Shared helpers: DOM, fetch, icons, i18n, preferences, toast, menu and bottom sheet.
export const $ = (s, el = document) => el.querySelector(s);
export const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");

// Late-bound app actions (router, ask, run) so modules don't import each other in a circle.
export const App = {};

export async function jfetch(url, opts = {}) {
  const r = await fetch(url, opts);
  let d = {};
  try { d = await r.json(); } catch { /* empty body */ }
  if (!r.ok) {
    const err = new Error(typeof d.detail === "string" ? d.detail : `request failed (${r.status})`);
    err.status = r.status;
    throw err;
  }
  return d;
}
export const jpost = (url, body, method = "POST") => jfetch(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

// One icon set: Lucide geometry (24 viewBox, round caps and joins), drawn at a 1.5 px stroke
// that stays 1.5 px at every size (non-scaling stroke), so icons match optically everywhere.
const P = (d) => `<svg class="ic" viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
const FILE = '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/>';
export const ICON = {
  home: P('<path d="M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8"/><path d="M3 10a2 2 0 0 1 .709-1.528l7-5.999a2 2 0 0 1 2.582 0l7 5.999A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'),
  user: P('<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>'),
  search: P('<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>'),
  clip: P('<path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/>'),
  send: P('<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>'),
  stop: '<svg class="ic fill" viewBox="0 0 24 24" aria-hidden="true"><rect x="6.5" y="6.5" width="11" height="11" rx="2.5" fill="currentColor" stroke="none"/></svg>',
  plus: P('<path d="M5 12h14"/><path d="M12 5v14"/>'),
  x: P('<path d="M18 6 6 18"/><path d="m6 6 12 12"/>'),
  back: P('<path d="m15 18-6-6 6-6"/>'),
  chev: P('<path d="m6 9 6 6 6-6"/>'),
  more: P('<circle cx="12" cy="12" r="1" fill="currentColor"/><circle cx="19" cy="12" r="1" fill="currentColor"/><circle cx="5" cy="12" r="1" fill="currentColor"/>'),
  file: P(FILE),
  folder: P('<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>'),
  check: P('<path d="M20 6 9 17l-5-5"/>'),
  globe: P('<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/>'),
  download: P('<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>'),
  share: P('<path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><path d="m16 6-4-4-4 4"/><path d="M12 2v13"/>'),
  spark: P('<path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/><path d="M20 3v4"/><path d="M22 5h-4"/>'),
  refresh: P('<path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>'),
  shield: P('<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>'),
  key: P('<path d="M2.586 17.414A2 2 0 0 0 2 18.828V21a1 1 0 0 0 1 1h3a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h1a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h.172a2 2 0 0 0 1.414-.586l.814-.814a6.5 6.5 0 1 0-4-4z"/><circle cx="16.5" cy="7.5" r=".5" fill="currentColor"/>'),
  doc: P(FILE + '<path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>'),
  table: P('<path d="M12 3v18"/><rect width="18" height="18" x="3" y="3" rx="2"/><path d="M3 9h18"/><path d="M3 15h18"/>'),
  image: P('<rect width="18" height="18" x="3" y="3" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>'),
  mic: P('<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><path d="M12 19v3"/>'),
  expand: P('<path d="M15 3h6v6"/><path d="M9 21H3v-6"/><path d="M21 3l-7 7"/><path d="M3 21l7-7"/>'),
  shrink: P('<path d="M4 14h6v6"/><path d="M20 10h-6V4"/><path d="m14 10 7-7"/><path d="m3 21 7-7"/>'),
  arrow: P('<path d="M7 7h10v10"/><path d="M7 17 17 7"/>'),
  reply: P('<path d="m9 17-5-5 5-5"/><path d="M20 18v-2a4 4 0 0 0-4-4H4"/>'),
  chart: P('<path d="M3 3v16a2 2 0 0 0 2 2h16"/><path d="m19 9-5 5-4-4-3 3"/>'),
  bolt: P('<path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"/>'),
  list: P('<path d="M3 12h.01"/><path d="M3 18h.01"/><path d="M3 6h.01"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M8 6h13"/>'),
  clock: P('<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>'),
  news: P('<path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-2 2Zm0 0a2 2 0 0 1-2-2v-9c0-1.1.9-2 2-2h2"/><path d="M18 14h-8"/><path d="M15 18h-5"/><path d="M10 6h8v4h-8V6Z"/>'),
  inbox: P('<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>'),
  chat: P('<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/>'),
};

// The ODAR mark (kept from the original app: a bold "O" in a rounded square).
export const LOGO = `<span class="logo" aria-hidden="true">O</span>`;

// ------------------------------------------------------------------ preferences
export const prefs = {
  get(k, d) { try { const v = localStorage.getItem("odar." + k); return v == null ? d : v; } catch { return d; } },
  set(k, v) { try { localStorage.setItem("odar." + k, v); } catch { /* private mode */ } },
  del(k) { try { localStorage.removeItem("odar." + k); } catch { /* */ } },
};

// Theme: "system" (shown as Auto) follows the sky's brightness; light/dark force the tone.
let skyTone = () => "light";
export function setSkyToneSource(fn) { skyTone = fn; }
export function applyTheme(theme = prefs.get("theme", "system")) {
  const root = document.documentElement;
  root.dataset.theme = theme;
  root.dataset.tone = theme === "light" || theme === "dark" ? theme : skyTone();
}

// ------------------------------------------------------------------ i18n (UI chrome only)
const STR = {
  en: {
    chat: "Chat", projects: "Projects", home: "Home", search: "Search", profile: "Settings",
    ask_ph: "Ask ODAR", followup_ph: "Ask a follow-up", research_ph: "What should ODAR research?",
    check_ph: "Paste an AI answer with its links, or attach a file", references_ph: "Paste a reference list, one per line",
    hello: "Hi there", hello_sub: "Ask anything. Every citation gets checked against its source.",
    morning: "Good morning", afternoon: "Good afternoon", evening: "Good evening",
    new_research: "New research", sources: "sources", verified: "Verified", stop: "Stop", send: "Send",
    attach: "Attach a file", searching: "Searching", reading: "Reading sources", writing: "Writing the answer",
    verifying: "Checking each citation", done: "Done",
  },
  hi: {
    chat: "चैट", projects: "प्रोजेक्ट", home: "होम", search: "खोजें", profile: "प्रोफ़ाइल",
    ask_ph: "ODAR से पूछें", followup_ph: "आगे पूछें", research_ph: "ODAR किस पर रिसर्च करे?",
    check_ph: "किसी AI का जवाब उसके लिंक के साथ पेस्ट करें, या फ़ाइल जोड़ें", references_ph: "संदर्भ सूची पेस्ट करें, हर पंक्ति में एक",
    hello: "नमस्ते", hello_sub: "कुछ भी पूछें। हर स्रोत की जाँच उसके असली पेज से होती है।",
    morning: "सुप्रभात", afternoon: "नमस्कार", evening: "शुभ संध्या",
    new_research: "नई रिसर्च", sources: "स्रोत", verified: "जाँचा गया", stop: "रोकें", send: "भेजें",
    attach: "फ़ाइल जोड़ें", searching: "खोज जारी", reading: "स्रोत पढ़ रहे हैं", writing: "जवाब लिख रहे हैं",
    verifying: "हर स्रोत की जाँच", done: "पूरा",
  },
};
export const lang = () => (prefs.get("lang", "en") === "hi" ? "hi" : "en");
export const t = (k) => STR[lang()][k] ?? STR.en[k] ?? k;

// ------------------------------------------------------------------ time
export function ago(input) {
  const ts = typeof input === "number" ? input * 1000 : Date.parse(input);
  if (!input || isNaN(ts)) return typeof input === "string" && /^\d{4}$/.test(input) ? input : "";
  const s = (Date.now() - ts) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  if (s < 86400 * 7) return `${Math.round(s / 86400)} d ago`;
  return new Date(ts).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}
export function clock(sec) {
  const d = new Date((sec || Date.now() / 1000) * 1000);
  const today = new Date().toDateString() === d.toDateString();
  return (today ? "" : d.toLocaleDateString(undefined, { day: "numeric", month: "short" }) + ", ") +
    d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

// ------------------------------------------------------------------ toast
export function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove("show"), 2800);
}

// ------------------------------------------------------------------ menu (anchored list)
// items: [id, label, sub, on, icon] rows; ["-", "Section title"] starts a section.
export function openMenu(anchor, items, onPick) {
  const m = $("#menu");
  m.innerHTML = items.map(([id, label, sub, on, ic]) => id === "-" ? `<div class="m-sec">${esc(label)}</div>`
    : id === "chips" ? `<div class="m-chips">${label.map(([cid, clabel, con]) => `<button role="menuitemradio" aria-checked="${con ? "true" : "false"}" data-id="${esc(cid)}" class="m-chip ${con ? "on" : ""}">${esc(clabel)}</button>`).join("")}</div>`
    : `<button role="menuitemradio" aria-checked="${on ? "true" : "false"}" data-id="${esc(id)}" class="${on ? "on" : ""}">${ic ? `<span class="m-ic">${ic}</span>` : ""}<span class="m-t">${esc(label)}${sub ? `<small>${esc(sub)}</small>` : ""}</span>${on ? ICON.check : ""}</button>`).join("");
  m.classList.remove("hidden");
  const r = anchor.getBoundingClientRect();
  const h = m.offsetHeight, w = m.offsetWidth;
  m.style.left = Math.max(8, Math.min(anchor.closest(".composer") ? r.right - w + 6 : r.left, innerWidth - w - 8)) + "px";
  const below = r.top < innerHeight / 2;
  m.style.top = (below ? Math.min(r.bottom + 8, innerHeight - h - 8) : Math.max(8, r.top - h - 8)) + "px";
  $$("button", m).forEach((b) => (b.onclick = (e) => { e.stopPropagation(); closeMenu(); onPick(b.dataset.id); }));
  const first = $("button", m); if (first) first.focus({ preventScroll: true });
}
export function closeMenu() { $("#menu").classList.add("hidden"); }
document.addEventListener("click", (e) => { if (!e.target.closest("#menu") && !e.target.closest("[data-menu]")) closeMenu(); });

// ------------------------------------------------------------------ bottom sheet
// Slides up on a spring; drag it down (from the grabber, or anywhere once its content is
// scrolled to the top) to dismiss: it follows the finger, the scrim fades with it, and a quick
// flick or a pull past a third of its height closes it, otherwise it springs back.
let sheetClose = null, sheetTimer = 0;
export const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
// will-change only while something is moving, then dropped so layers don't pile up.
export function willChange(el, prop, ms = 600) {
  if (!el) return;
  el.style.willChange = prop;
  clearTimeout(el._wc); el._wc = setTimeout(() => { el.style.willChange = ""; }, ms);
}
export function openSheet(html, { title = "", onClose = null, tall = false } = {}) {
  const root = $("#sheet");
  const panel = $(".sheet-panel", root);
  clearTimeout(sheetTimer);
  panel.classList.toggle("tall", tall);
  panel.style.transform = ""; $(".sheet-scrim", root).style.opacity = "";
  $(".sheet-body", root).innerHTML = (title ? `<div class="sheet-h"><h2>${esc(title)}</h2><button class="glass-btn round sm" data-close aria-label="Close">${ICON.x}</button></div>` : "") + html;
  const wasOpen = root.classList.contains("open");
  root.classList.remove("hidden");
  if (!wasOpen) {
    willChange(panel, "transform", 700);
    panel.scrollTop = 0;
    // two frames: the closed position must be painted before the transition starts
    requestAnimationFrame(() => requestAnimationFrame(() => root.classList.add("open")));
  }
  sheetClose = onClose;
  $$("[data-close]", root).forEach((b) => (b.onclick = closeSheet));
  panel.focus({ preventScroll: true });
  return $(".sheet-body", root);
}
export function closeSheet() {
  const root = $("#sheet");
  if (root.classList.contains("hidden")) return;
  const panel = $(".sheet-panel", root);
  willChange(panel, "transform", 400);
  root.classList.remove("open", "dragging");
  panel.style.transform = ""; $(".sheet-scrim", root).style.opacity = "";
  clearTimeout(sheetTimer);
  sheetTimer = setTimeout(() => root.classList.add("hidden"), reducedMotion() ? 0 : 340);
  const cb = sheetClose; sheetClose = null;
  if (cb) cb();
}
export function sheetOpen() { return !$("#sheet").classList.contains("hidden"); }

(function sheetDrag() {
  let y0 = null, dy = 0, lastY = 0, lastT = 0, v = 0, dragging = false, raf = 0;
  const root = () => $("#sheet"), panel = () => $(".sheet-panel");
  const paint = () => {
    raf = 0;
    const h = panel().offsetHeight || 1;
    panel().style.transform = `translateY(${dy}px)`;
    $(".sheet-scrim").style.opacity = String(Math.max(0, 1 - dy / h));
  };
  document.addEventListener("touchstart", (e) => {
    if (!root().classList.contains("open") || matchMedia("(min-width:720px)").matches) return;
    const p = e.target.closest(".sheet-panel");
    if (!p) return;
    // from the grabber or title always; from the content only when it is scrolled to the top
    if (!e.target.closest(".sheet-grab, .sheet-h") && p.scrollTop > 0) return;
    y0 = lastY = e.touches[0].clientY; lastT = e.timeStamp; dy = 0; v = 0; dragging = false;
  }, { passive: true });
  document.addEventListener("touchmove", (e) => {
    if (y0 == null) return;
    const y = e.touches[0].clientY;
    const d = y - y0;
    if (!dragging) {
      if (d < 6) { if (d < -6) y0 = null; return; } // scrolling up the content: not a drag
      dragging = true; root().classList.add("dragging"); panel().style.willChange = "transform";
    }
    // rubber band above the resting position
    dy = d > 0 ? d : d / 4;
    const dt = Math.max(1, e.timeStamp - lastT);
    v = 0.8 * ((y - lastY) / dt) + 0.2 * v; lastY = y; lastT = e.timeStamp;
    if (!raf) raf = requestAnimationFrame(paint);
  }, { passive: true });
  const end = () => {
    if (y0 == null) return;
    y0 = null;
    if (!dragging) return;
    dragging = false;
    if (raf) { cancelAnimationFrame(raf); raf = 0; }
    root().classList.remove("dragging");
    panel().style.willChange = "";
    const h = panel().offsetHeight || 1;
    if (dy > h / 3 || (v > 0.55 && dy > 24)) closeSheet();
    else { panel().style.transform = ""; $(".sheet-scrim").style.opacity = ""; }
  };
  document.addEventListener("touchend", end, { passive: true });
  document.addEventListener("touchcancel", end, { passive: true });
})();

// Copy buttons on code cards and text templates copy the card's text.
document.addEventListener("click", async (e) => {
  const b = e.target.closest("[data-copy]");
  if (!b) return;
  const src = b.closest(".tmpl")?.querySelector("[data-copy-src]");
  if (!src) return;
  const text = src.tagName === "OL" ? [...src.children].map((li, i) => `${i + 1}. ${li.textContent}`).join("\n") : src.innerText;
  try { await navigator.clipboard.writeText(text); b.textContent = "Copied"; setTimeout(() => (b.textContent = "Copy"), 1600); } catch { toast("Couldn't copy"); }
});

export async function copyLink(url, label = "Link copied") {
  try { await navigator.clipboard.writeText(url); toast(label); } catch { prompt("Copy this link", url); }
}

// Site icon: Google's favicon service at 64 px, then the site's own /favicon.ico, then a
// monogram in the site's hue. Google answers unknown sites with a 16 px globe; that counts as
// a miss. Link-card, source-tile, source-stack and work-card icons all come through here.
export const hueOf = (s) => { let h = 0; for (const ch of String(s || "")) h = (h * 31 + ch.charCodeAt(0)) % 360; return h; };
export function favicon(domain, kind) {
  if (kind === "file") return `<span class="fav file">${ICON.file}</span>`;
  if (!domain) return `<span class="fav" data-l="?"></span>`;
  const d = String(domain).replace(/^www\./, "").toLowerCase();
  const letter = esc(d[0].toUpperCase());
  return `<span class="fav" data-l="${letter}" style="--fh:${hueOf(d)}"><img src="https://www.google.com/s2/favicons?domain=${encodeURIComponent(d)}&amp;sz=64" data-d="${esc(d)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" onload="__favOk(this)" onerror="__favErr(this)"></span>`;
}
window.__favErr = (img) => {
  if (!img.dataset.f && img.dataset.d) { img.dataset.f = "1"; img.src = `https://${img.dataset.d}/favicon.ico`; return; }
  img.remove();
};
window.__favOk = (img) => {
  if (!img.dataset.f && img.naturalWidth && img.naturalWidth <= 16) { window.__favErr(img); return; }
  img.parentNode && img.parentNode.classList.add("has-img");
};

// The element that scrolls the page (the view is a fixed scroller under the top bar).
export const scroller = () => document.getElementById("view");
