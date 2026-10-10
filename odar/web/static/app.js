// ODAR web app entry: shell, tabs and routing. Vanilla ES modules, no build step.
import { $, $$, ICON, App, t, prefs, applyTheme, setSkyToneSource, closeMenu, closeSheet, sheetOpen, scroller, reducedMotion } from "./js/core.js";
import { showChat, resumeChat, showThread, showShared, showProjectThread, initComposer, sendAsk, setMode, composer, syncComposer, prefill, S } from "./js/chat.js";
import { initSky, skyTone } from "./js/sky.js";
import { showHome, refreshHome } from "./js/home.js";
import { showProjects, filesSheet, projectAction } from "./js/projects.js";
import { initSearch, closeSearch } from "./js/search.js";
import { openProfile, avatarHTML } from "./js/profile.js";
import { showRunPage, showHistoryPage, stopPolling } from "./js/runs.js";

const view = $("#view");
// Home, Chat and Projects each keep their own pane mounted inside the scroller. Switching tabs
// hides one and shows the other (no rebuild, no refetch), restoring each pane's scroll
// position. Chat-type pages (threads, project threads, runs, shared) all use the chat pane, so
// there is only ever one #thread. A pane rebuilds when its key (the path that built it)
// changes, when it went stale, or when it was marked dirty.
const PANES = {};
for (const k of ["home", "chat", "projects"]) {
  const el = document.createElement("div");
  el.className = "pane"; el.dataset.pane = k; el.hidden = true;
  view.appendChild(el);
  PANES[k] = { el, key: "", at: 0, top: 0 };
}
let curPane = "";
function showPane(k) {
  if (curPane && curPane !== k) PANES[curPane].top = view.scrollTop;
  for (const [n, p] of Object.entries(PANES)) p.el.hidden = n !== k;
  curPane = k;
  return PANES[k].el;
}
// Mount ``k`` for ``key``; ``build(el)`` runs only when the pane must be (re)built, otherwise
// the cached pane comes back at its scroll position and ``refresh`` (if any) updates it quietly.
function mount(k, key, build, { maxAge = Infinity, refresh = null } = {}) {
  const p = PANES[k];
  const el = showPane(k);
  const fresh = p.key === key && Date.now() - p.at < maxAge && el.childElementCount;
  if (fresh) {
    view.scrollTop = p.top;
    if (refresh) refresh(el);
    return undefined;
  }
  p.key = key; p.at = Date.now(); p.top = 0;
  view.scrollTop = 0;
  return build(el);
}
App.dirty = (k) => { if (PANES[k]) PANES[k].key = ""; };

// ------------------------------------------------------------------ shell
// The sky sets the tone (light or dark text) unless the user forced one.
setSkyToneSource(skyTone);
initSky(() => applyTheme());
applyTheme();
const syncTop = () => document.documentElement.style.setProperty("--top", $(".topbar").offsetHeight + "px");
syncTop();
addEventListener("resize", syncTop);
// The scroller's bottom padding follows the composer's real height (chips, a multi-line
// draft), so the last card on Home or in a thread always clears it.
if (window.ResizeObserver) new ResizeObserver(([e]) => document.documentElement.style.setProperty("--comp-h", Math.ceil(e.target.getBoundingClientRect().height) + "px")).observe($(".comp-inner"));
document.documentElement.lang = prefs.get("lang", "en");
$("#btn-search .s-icon").innerHTML = ICON.search;
App.refreshAvatar = () => { $("#btn-profile").innerHTML = avatarHTML(); };
App.refreshAvatar();
App.relabel = () => {
  $("#tab-home").textContent = t("home");
  $("#tab-chat").textContent = t("chat");
  $("#tab-projects").textContent = t("projects");
  $("#btn-search").setAttribute("aria-label", t("search"));
  $("#btn-profile").setAttribute("aria-label", t("profile"));
  syncComposer();
  requestAnimationFrame(moveInd);
};
App.relabel();
initComposer();
initSearch();
$("#btn-search").onclick = () => App.openSearch();
$("#btn-profile").onclick = () => openProfile();
App.reply = (text) => { composer(true); prefill(text); };

App.ask = (q, focus) => sendAsk(q, focus);
App.setMode = setMode;
App.projectFiles = filesSheet;
App.projectAction = (pid, k, project) => projectAction(pid, k, project);

// ------------------------------------------------------------------ routing
App.go = (path, replace = false) => {
  if (replace) history.replaceState({}, "", path); else history.pushState({}, "", path);
  navigate();
};
App.route = () => route();
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[data-nav]");
  if (a && !e.metaKey && !e.ctrlKey && !e.shiftKey && a.target !== "_blank") { e.preventDefault(); App.go(a.getAttribute("href")); }
});
addEventListener("popstate", () => navigate());

// Moving between Home, Chat and Projects animates: a view transition where supported (the page
// slides a little in the direction of travel and cross-fades; bar and composer stay), otherwise a
// quick fade + rise. The new page gets up to 260 ms to render its data inside the transition so
// it doesn't land half-empty; slower loads land on their skeleton instead.
const TAB_ORDER = { home: 0, chat: 1, projects: 2 };
const tabOf = (p) => (p === "/home" ? "home" : p === "/projects" || p.startsWith("/p/") ? "projects" : p === "/" || p === "/settings" || p.startsWith("/c/") ? "chat" : "");
let curPath = location.pathname;
function navigate() {
  const from = tabOf(curPath), to = tabOf(location.pathname);
  const changed = curPath !== location.pathname;
  curPath = location.pathname;
  if (!changed || reducedMotion() || (!from && !to)) return route();
  document.documentElement.dataset.vdir = from && to && TAB_ORDER[to] < TAB_ORDER[from] ? "-1" : "1";
  if (document.startViewTransition) {
    try {
      document.startViewTransition(() => Promise.race([Promise.resolve(route()).catch(() => {}), new Promise((ok) => setTimeout(ok, 260))]));
      return undefined;
    } catch { /* fall through */ }
  }
  view.classList.remove("v-fallback"); void view.offsetWidth; view.classList.add("v-fallback");
  clearTimeout(navigate.t); navigate.t = setTimeout(() => view.classList.remove("v-fallback"), 700);
  return route();
}

function setTab(tab) {
  $$(".seg-tabs [data-tab]").forEach((b) => {
    const on = b.dataset.tab === tab;
    b.classList.toggle("on", on);
    b.setAttribute("aria-selected", on);
  });
  document.body.classList.toggle("on-home", tab === "home");
  moveInd();
}
// The black pill slides under the active tab (hidden when no tab is active).
function moveInd() {
  const on = $(".seg-tabs a.on"), ind = $(".seg-ind");
  if (!ind) return;
  ind.classList.toggle("off", !on);
  if (on) { ind.style.width = on.offsetWidth + "px"; ind.style.transform = `translateX(${on.offsetLeft}px)`; }
}
addEventListener("resize", () => requestAnimationFrame(moveInd));
document.fonts?.ready.then(() => moveInd());
setTimeout(() => $(".seg-tabs").classList.add("anim"), 400);

// Wide screens: Home is a left column next to the chat instead of its own page.
const wide = () => matchMedia("(min-width: 1180px)").matches;
const rail = $("#rail");
function setRail(on) {
  rail.classList.toggle("hidden", !on);
  document.body.classList.toggle("with-rail", on);
  if (on) { PANES.home.el.innerHTML = ""; App.dirty("home"); showHome(rail, { rail: true }); } else rail.innerHTML = "";
}
let wasWide = wide();
addEventListener("resize", () => { if (wide() !== wasWide) { wasWide = wide(); if (location.pathname === "/home") route(); } });

function route() {
  stopPolling();
  closeMenu();
  closeSearch();
  if (sheetOpen()) closeSheet();
  // a stream still running in the chat pane is stopped; that pane is rebuilt next time
  if (S.abort) { S.abort.abort(); App.dirty("chat"); }
  const p = location.pathname;
  const qs = new URLSearchParams(location.search);
  const key = p + location.search;
  let m;
  if (p !== "/home" && document.body.classList.contains("with-rail")) setRail(false);
  if ((m = p.match(/^\/c\/([\w-]+)/))) { setTab("chat"); return mount("chat", key, (el) => showThread(el, m[1]), { refresh: chatBack }); }
  if ((m = p.match(/^\/s\/([\w-]+)/))) { setTab(""); return mount("chat", key, (el) => showShared(el, m[1])); }
  if ((m = p.match(/^\/p\/([\w-]+)/))) { setTab("projects"); App.dirty("projects"); return mount("chat", key, (el) => showProjectThread(el, m[1], qs.get("t") || ""), { refresh: chatBack }); }
  if ((m = p.match(/^\/runs\/([\w-]+)/))) { setTab(""); composer(false); return mount("chat", key + "#" + Date.now(), (el) => showRunPage(el, { id: m[1] })); }
  if ((m = p.match(/^\/r\/([\w-]+)/))) { setTab(""); composer(false); return mount("chat", key + "#" + Date.now(), (el) => showRunPage(el, { token: m[1] })); }
  if (p === "/home") {
    setTab("home");
    if (wide()) { setRail(true); return mount("chat", "/|" + prefs.get("main", ""), (el) => showChat(el, {}), { refresh: chatBack }); }
    composer(true); syncComposer();
    return mount("home", "/home", (el) => showHome(el), { refresh: () => refreshHome(60000) });
  }
  if (p === "/projects") {
    setTab("projects"); composer(false);
    return mount("projects", key, (el) => showProjects(el, { add: qs.get("add") }), { maxAge: 120000 });
  }
  if (p === "/history") { setTab(""); composer(false); return mount("chat", key + "#" + Date.now(), (el) => showHistoryPage(el)); }
  setTab("chat");
  const opts = { mode: qs.get("mode"), q: qs.get("q") };
  // "/" with a prefilled question or mode always rebuilds; plain "/" (and /settings) reuse
  const ck = opts.mode || opts.q ? key + "#" + Date.now() : "/|" + prefs.get("main", "");
  mount("chat", ck, (el) => showChat(el, opts), { refresh: chatBack });
  if (p === "/settings") openProfile();
  return undefined;
}
// Coming back to a cached chat pane: the composer returns as it was for that thread.
function chatBack() { resumeChat(); }

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { closeSearch(); closeSheet(); closeMenu(); }
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); App.openSearch(); }
});
$("#sheet").addEventListener("click", (e) => { if (e.target.classList.contains("sheet-scrim")) closeSheet(); });

route();
