// ODAR web app entry: shell, tabs and routing. Vanilla ES modules, no build step.
import { $, $$, ICON, LOGO, App, t, prefs, applyTheme, closeMenu, closeSheet, sheetOpen } from "./js/core.js";
import { showChat, showThread, showShared, showProjectThread, initComposer, sendAsk, setMode, composer, syncComposer, S } from "./js/chat.js";
import { showHome } from "./js/home.js";
import { showProjects, filesSheet, projectAction } from "./js/projects.js";
import { initSearch, closeSearch } from "./js/search.js";
import { openProfile, avatarLetter } from "./js/profile.js";
import { showRunPage, showHistoryPage, stopPolling } from "./js/runs.js";

const view = $("#view");

// ------------------------------------------------------------------ shell
applyTheme();
const syncTop = () => document.documentElement.style.setProperty("--top", $(".topbar").offsetHeight + "px");
syncTop();
addEventListener("resize", syncTop);
document.documentElement.lang = prefs.get("lang", "en");
$("#brand").innerHTML = `${LOGO}<span>ODAR</span>`;
$("#btn-home").innerHTML = ICON.home;
$("#btn-search").innerHTML = ICON.search;
App.refreshAvatar = () => { $("#btn-profile").innerHTML = `<span class="avatar">${avatarLetter() || ICON.user}</span>`; };
App.refreshAvatar();
App.relabel = () => {
  $("#tab-chat").textContent = t("chat");
  $("#tab-projects").textContent = t("projects");
  $("#btn-home").setAttribute("aria-label", t("home"));
  $("#btn-search").setAttribute("aria-label", t("search"));
  $("#btn-profile").setAttribute("aria-label", t("profile"));
  syncComposer();
};
App.relabel();
initComposer();
initSearch();
$("#btn-home").onclick = () => App.go("/home");
$("#btn-search").onclick = () => App.openSearch();
$("#btn-profile").onclick = () => openProfile();

App.ask = (q, focus) => sendAsk(q, focus);
App.setMode = setMode;
App.projectFiles = filesSheet;
App.projectAction = (pid, k, project) => projectAction(pid, k, project);

// ------------------------------------------------------------------ routing
App.go = (path, replace = false) => {
  if (replace) history.replaceState({}, "", path); else history.pushState({}, "", path);
  route();
};
App.route = () => route();
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[data-nav]");
  if (a && !e.metaKey && !e.ctrlKey && !e.shiftKey && a.target !== "_blank") { e.preventDefault(); App.go(a.getAttribute("href")); }
});
addEventListener("popstate", route);

function setTab(tab) {
  $$(".tabs [data-tab]").forEach((b) => {
    const on = b.dataset.tab === tab;
    b.classList.toggle("on", on);
    b.setAttribute("aria-selected", on);
  });
  $("#btn-home").classList.toggle("on", tab === "home");
  document.body.classList.toggle("on-home", tab === "home");
}

function route() {
  stopPolling();
  closeMenu();
  closeSearch();
  if (sheetOpen()) closeSheet();
  if (S.abort) S.abort.abort();
  const p = location.pathname;
  const qs = new URLSearchParams(location.search);
  let m;
  window.scrollTo(0, 0);
  if ((m = p.match(/^\/c\/([\w-]+)/))) { setTab("chat"); return showThread(view, m[1]); }
  if ((m = p.match(/^\/s\/([\w-]+)/))) { setTab(""); return showShared(view, m[1]); }
  if ((m = p.match(/^\/p\/([\w-]+)/))) { setTab("projects"); return showProjectThread(view, m[1], qs.get("t") || ""); }
  if ((m = p.match(/^\/runs\/([\w-]+)/))) { setTab(""); composer(false); return showRunPage(view, { id: m[1] }); }
  if ((m = p.match(/^\/r\/([\w-]+)/))) { setTab(""); composer(false); return showRunPage(view, { token: m[1] }); }
  if (p === "/home") { setTab("home"); composer(false); return showHome(view); }
  if (p === "/projects") { setTab("projects"); composer(false); return showProjects(view, { add: qs.get("add") }); }
  if (p === "/history") { setTab(""); composer(false); return showHistoryPage(view); }
  setTab("chat");
  const opts = { mode: qs.get("mode"), q: qs.get("q") };
  showChat(view, opts);
  if (p === "/settings") openProfile();
  return undefined;
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { closeSearch(); closeSheet(); closeMenu(); }
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); App.openSearch(); }
});
$("#sheet").addEventListener("click", (e) => { if (e.target.classList.contains("sheet-scrim")) closeSheet(); });

route();
