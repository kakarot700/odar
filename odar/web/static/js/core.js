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

const P = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
export const ICON = {
  home: P('<path d="M3.5 10.5 12 3.5l8.5 7"/><path d="M5.5 9.5V20h4.5v-5.5h4V20h4.5V9.5"/>'),
  user: P('<circle cx="12" cy="8.5" r="3.8"/><path d="M4.5 20c1.4-3.6 4.2-5.5 7.5-5.5s6.1 1.9 7.5 5.5"/>'),
  search: P('<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4-4"/>'),
  clip: P('<path d="m20.5 11.5-8 8a5 5 0 0 1-7-7l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7L10.2 17a1.7 1.7 0 0 1-2.4-2.4l7.4-7.4"/>'),
  send: P('<path d="M12 19V5"/><path d="m5.5 11.5 6.5-6.5 6.5 6.5"/>'),
  stop: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="2.5" fill="currentColor" stroke="none"/></svg>',
  plus: P('<path d="M12 5v14M5 12h14"/>'),
  x: P('<path d="M6 6l12 12M18 6 6 18"/>'),
  back: P('<path d="M15 5l-7 7 7 7"/>'),
  chev: P('<path d="m6 9 6 6 6-6"/>'),
  more: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1.6" fill="currentColor"/><circle cx="12" cy="12" r="1.6" fill="currentColor"/><circle cx="19" cy="12" r="1.6" fill="currentColor"/></svg>',
  file: P('<path d="M14 3H6.5v18h11V6.5z"/><path d="M14 3v3.5h3.5"/>'),
  folder: P('<path d="M3.5 6.5h6l2 2h9v10h-17z"/>'),
  check: P('<path d="m5 12.5 4.5 4.5L19 7.5"/>'),
  globe: P('<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.5 2.6 3.5 5.4 3.5 8.5s-1 5.9-3.5 8.5c-2.5-2.6-3.5-5.4-3.5-8.5s1-5.9 3.5-8.5z"/>'),
  download: P('<path d="M12 4v11"/><path d="m7 10.5 5 5 5-5"/><path d="M5 19.5h14"/>'),
  share: P('<path d="M12 15V4"/><path d="m7.5 8 4.5-4.5L16.5 8"/><path d="M5.5 12v7.5h13V12"/>'),
  spark: P('<path d="M12 3.5v4M12 16.5v4M3.5 12h4M16.5 12h4M6 6l2.6 2.6M15.4 15.4 18 18M18 6l-2.6 2.6M8.6 15.4 6 18"/>'),
  refresh: P('<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M19.5 4.5v4h-4"/>'),
  shield: P('<path d="M12 3.5 5 6v5.5c0 4.3 3 7.6 7 9 4-1.4 7-4.7 7-9V6z"/><path d="m9 12 2.2 2.2L15.5 10"/>'),
  key: P('<circle cx="8" cy="15" r="4"/><path d="m11 12 8.5-8.5M16.5 6.5l2.5 2.5"/>'),
  doc: P('<path d="M6.5 3.5h8l3 3v14h-11z"/><path d="M9 11h6M9 14.5h6M9 18h4"/>'),
  table: P('<rect x="3.5" y="5" width="17" height="14" rx="2"/><path d="M3.5 10h17M10 10v9"/>'),
  image: P('<rect x="3.5" y="5" width="17" height="14" rx="2.5"/><circle cx="9" cy="10" r="1.6"/><path d="m4.5 17.5 5-4.5 4 3.5 2.5-2 4 3"/>'),
};

// The ODAR mark (kept from the original app: a bold "O" in a rounded square).
export const LOGO = `<span class="logo" aria-hidden="true">O</span>`;

// ------------------------------------------------------------------ preferences
export const prefs = {
  get(k, d) { try { const v = localStorage.getItem("odar." + k); return v == null ? d : v; } catch { return d; } },
  set(k, v) { try { localStorage.setItem("odar." + k, v); } catch { /* private mode */ } },
  del(k) { try { localStorage.removeItem("odar." + k); } catch { /* */ } },
};

export function applyTheme(theme = prefs.get("theme", "system")) {
  document.documentElement.dataset.theme = theme;
  const dark = theme === "dark" || (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = dark ? "#151412" : "#f7f4ef";
}
matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", () => applyTheme());

// ------------------------------------------------------------------ i18n (UI chrome only)
const STR = {
  en: {
    chat: "Chat", projects: "Projects", home: "Home", search: "Search", profile: "Profile",
    ask_ph: "Message ODAR", followup_ph: "Ask a follow-up", research_ph: "What should ODAR research?",
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
export function openMenu(anchor, items, onPick) {
  const m = $("#menu");
  m.innerHTML = items.map(([id, label, sub, on]) => `<button role="menuitemradio" aria-checked="${on ? "true" : "false"}" data-id="${esc(id)}" class="${on ? "on" : ""}"><span>${esc(label)}${sub ? `<small>${esc(sub)}</small>` : ""}</span>${on ? ICON.check : ""}</button>`).join("");
  m.classList.remove("hidden");
  const r = anchor.getBoundingClientRect();
  const h = m.offsetHeight, w = m.offsetWidth;
  m.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + "px";
  m.style.top = (r.top - h - 8 > 8 ? r.top - h - 8 : r.bottom + 8) + "px";
  $$("button", m).forEach((b) => (b.onclick = (e) => { e.stopPropagation(); closeMenu(); onPick(b.dataset.id); }));
  const first = $("button", m); if (first) first.focus({ preventScroll: true });
}
export function closeMenu() { $("#menu").classList.add("hidden"); }
document.addEventListener("click", (e) => { if (!e.target.closest("#menu") && !e.target.closest("[data-menu]")) closeMenu(); });

// ------------------------------------------------------------------ bottom sheet
let sheetClose = null;
export function openSheet(html, { title = "", onClose = null, tall = false } = {}) {
  const root = $("#sheet");
  const panel = $(".sheet-panel", root);
  panel.classList.toggle("tall", tall);
  $(".sheet-body", root).innerHTML = (title ? `<div class="sheet-h"><h2>${esc(title)}</h2><button class="icon-btn sm" data-close aria-label="Close">${ICON.x}</button></div>` : "") + html;
  root.classList.remove("hidden");
  requestAnimationFrame(() => root.classList.add("open"));
  sheetClose = onClose;
  $$("[data-close]", root).forEach((b) => (b.onclick = closeSheet));
  panel.focus({ preventScroll: true });
  return $(".sheet-body", root);
}
export function closeSheet() {
  const root = $("#sheet");
  if (root.classList.contains("hidden")) return;
  root.classList.remove("open");
  setTimeout(() => root.classList.add("hidden"), 220);
  const cb = sheetClose; sheetClose = null;
  if (cb) cb();
}
export function sheetOpen() { return !$("#sheet").classList.contains("hidden"); }

// Drag the sheet down to dismiss (touch).
(function sheetDrag() {
  let y0 = null, dy = 0;
  document.addEventListener("touchstart", (e) => {
    const grab = e.target.closest(".sheet-grab, .sheet-h");
    if (!grab) return;
    y0 = e.touches[0].clientY; dy = 0;
  }, { passive: true });
  document.addEventListener("touchmove", (e) => {
    if (y0 == null) return;
    dy = Math.max(0, e.touches[0].clientY - y0);
    $(".sheet-panel").style.transform = `translateY(${dy}px)`;
  }, { passive: true });
  document.addEventListener("touchend", () => {
    if (y0 == null) return;
    $(".sheet-panel").style.transform = "";
    if (dy > 90) closeSheet();
    y0 = null;
  });
})();

export async function copyLink(url, label = "Link copied") {
  try { await navigator.clipboard.writeText(url); toast(label); } catch { prompt("Copy this link", url); }
}

export function favicon(domain, kind) {
  if (kind === "file") return `<span class="fav file">${ICON.file}</span>`;
  if (!domain) return `<span class="fav">?</span>`;
  const letter = esc(domain.replace(/^www\./, "")[0].toUpperCase());
  return `<span class="fav" data-l="${letter}"><img src="https://${esc(domain)}/favicon.ico" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()"></span>`;
}
