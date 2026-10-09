// Home: a grid of glanceable panels (usage, Discover, recent research, saved reports,
// projects), pull to refresh, and a plus button to start something new.
import { $, $$, esc, safeUrl, ICON, App, jfetch, t, ago, favicon, openSheet, closeSheet, prefs } from "./core.js";
import { MODE_LABEL } from "./runs.js";

let view = null;
let discTopic = prefs.get("disc", "world");

function greeting() {
  const h = new Date().getHours();
  return t(h < 12 ? "morning" : h < 17 ? "afternoon" : "evening");
}

export function showHome(v) {
  view = v;
  const name = prefs.get("name", "");
  view.innerHTML = `<div class="ptr" id="ptr"><span class="spin"></span></div>
    <div class="home-h"><div><h1>${esc(greeting())}${name ? ", " + esc(name) : ""}</h1><p>${esc(new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" }))}</p></div>
      <button class="icon-btn sm" id="home-refresh" aria-label="Refresh">${ICON.refresh}</button></div>
    <div class="panels">
      <section class="panel" id="pn-usage"><div class="pn-h">${ICON.shield}<span>Usage</span><a href="/settings" data-nav>Plan</a></div><div class="pn-b"><div class="skel"></div></div></section>
      <section class="panel" id="pn-projects"><div class="pn-h">${ICON.folder}<span>${esc(t("projects"))}</span><a href="/projects" data-nav>All</a></div><div class="pn-b"><div class="skel"></div></div></section>
      <section class="panel wide" id="pn-discover"><div class="pn-h">${ICON.globe}<span>Discover</span><small id="disc-meta"></small></div><div class="tabs-row" id="disc-tabs"></div><div class="pn-b"><div class="skel"></div><div class="skel"></div></div></section>
      <section class="panel wide" id="pn-recent"><div class="pn-h">${ICON.spark}<span>Recent research</span><a href="/history" data-nav>History</a></div><div class="pn-b"><div class="skel"></div></div></section>
      <section class="panel wide" id="pn-reports"><div class="pn-h">${ICON.doc}<span>Saved reports</span></div><div class="pn-b"><div class="skel"></div></div></section>
    </div>
    <button class="fab" id="fab" aria-label="${esc(t("new_research"))}">${ICON.plus}<span>${esc(t("new_research"))}</span></button>`;
  $("#fab").onclick = newSheet;
  $("#home-refresh").onclick = () => load(true);
  load(false);
}

async function load(force) {
  const btn = $("#home-refresh", view);
  if (btn) btn.classList.add("spinning");
  await Promise.allSettled([usage(), projects(), recent(), discover(force)]);
  if (btn) btn.classList.remove("spinning");
}

function bar(used, limit) {
  const left = Math.max(0, limit - used);
  const pct = limit ? Math.min(100, (100 * left) / limit) : 0;
  return { left, pct, tone: pct > 40 ? "good" : pct > 15 ? "mid" : "low" };
}
async function usage() {
  const box = $("#pn-usage .pn-b", view);
  try {
    const l = await jfetch("/api/limits");
    const a = bar(l.ask_used_this_hour, l.ask_hourly_limit);
    const r = bar(l.used_today || 0, l.daily_limit || 0);
    box.innerHTML = `<div class="big-num">${a.left}<small>/${l.ask_hourly_limit}</small></div><div class="pn-sub">questions left this hour</div>
      <div class="meter ${a.tone}"><i style="width:${a.pct}%"></i></div>
      <div class="pn-sub" style="margin-top:10px">${r.left} of ${l.daily_limit} deep runs left today</div><div class="meter ${r.tone}"><i style="width:${r.pct}%"></i></div>`;
  } catch (ex) { box.innerHTML = `<div class="note">${esc(ex.message)}</div>`; }
}
async function projects() {
  const box = $("#pn-projects .pn-b", view);
  try {
    const { projects: list } = await jfetch("/api/projects");
    box.innerHTML = list.length ? `<div class="big-num">${list.length}</div><div class="pn-sub">project${list.length === 1 ? "" : "s"}</div><div class="mini-list">${list.slice(0, 3).map((p) => `<a href="/p/${p.project_id}" data-nav>${esc(p.name)}<small>${p.files} file${p.files === 1 ? "" : "s"}</small></a>`).join("")}</div>`
      : `<div class="pn-sub">Keep files and instructions together and ask about them.</div><a class="btn sm" href="/projects?add=1" data-nav style="margin-top:10px">${ICON.plus}New project</a>`;
  } catch (ex) { box.innerHTML = `<div class="note">${esc(ex.message)}</div>`; }
}
async function recent() {
  const rbox = $("#pn-recent .pn-b", view);
  const sbox = $("#pn-reports .pn-b", view);
  try {
    const [{ runs }, { threads }] = await Promise.all([jfetch("/api/runs?limit=30"), jfetch("/api/threads?limit=12")]);
    const main = prefs.get("main", "");
    const items = runs.slice(0, 6).map((x) => ({ href: `/runs/${x.run_id}`, title: x.title, sub: MODE_LABEL[x.mode] || x.mode, t: x.created, status: x.status, ic: x.mode === "research" ? ICON.doc : ICON.shield }))
      .concat(threads.filter((x) => x.thread_id !== main).slice(0, 6).map((x) => ({ href: x.project_id ? `/p/${x.project_id}?t=${x.thread_id}` : `/c/${x.thread_id}`, title: x.title, sub: x.project_id ? "Project thread" : "Thread", t: x.updated, ic: ICON.spark })))
      .sort((a, b) => b.t - a.t).slice(0, 6);
    rbox.innerHTML = items.length ? `<div class="list flat">${items.map((x) => `<a class="li" href="${x.href}" data-nav><span class="li-ic">${x.ic}</span><span class="lt">${esc(x.title)}<small>${esc(x.sub)} · ${esc(ago(x.t))}</small></span>${x.status && x.status !== "COMPLETE" ? `<span class="pill st-${esc(x.status)}">${esc(x.status.toLowerCase())}</span>` : ""}</a>`).join("")}</div>`
      : `<div class="pn-sub">Your deep research and threads show up here.</div>`;
    const reports = runs.filter((x) => x.status === "COMPLETE").slice(0, 5);
    sbox.innerHTML = reports.length ? `<div class="list flat">${reports.map((x) => `<div class="li"><span class="li-ic">${x.mode === "research" ? ICON.doc : ICON.shield}</span><a class="lt" href="/runs/${x.run_id}" data-nav>${esc(x.title)}<small>${esc(MODE_LABEL[x.mode] || x.mode)} · ${esc(ago(x.created))}</small></a>
      <span class="dl">${["md", "pdf", "docx"].map((f) => `<a href="/api/runs/${x.run_id}/export.${f}" download aria-label="Download ${f.toUpperCase()}">${f.toUpperCase()}</a>`).join("")}</span></div>`).join("")}</div>`
      : `<div class="pn-sub">Finished reports and checks you can download as Markdown, PDF or Word.</div>`;
  } catch (ex) { rbox.innerHTML = sbox.innerHTML = `<div class="note">${esc(ex.message)}</div>`; }
}
async function discover() {
  const box = $("#pn-discover .pn-b", view);
  try {
    const { topics } = await jfetch("/api/discover");
    if (!topics.find((x) => x.id === discTopic)) discTopic = topics[0] ? topics[0].id : "";
    const draw = () => {
      $("#disc-tabs", view).innerHTML = topics.map((x) => `<button class="tab ${x.id === discTopic ? "on" : ""}" data-t="${x.id}">${esc(x.label)}</button>`).join("");
      $$("#disc-tabs .tab", view).forEach((b) => (b.onclick = () => { discTopic = b.dataset.t; prefs.set("disc", discTopic); draw(); }));
      const topic = topics.find((x) => x.id === discTopic) || { items: [] };
      $("#disc-meta", view).textContent = topic.fetched ? `updated ${ago(topic.fetched)}` : "";
      box.innerHTML = topic.items.length ? `<div class="news">${topic.items.slice(0, 6).map((it, i) => `<button class="news-i" data-i="${i}">
        ${it.image && /^https:/.test(it.image) ? `<img src="${esc(safeUrl(it.image))}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">` : ""}
        <span class="nt">${esc(it.title)}</span><span class="nm">${favicon(it.domain)}${esc(it.source || it.domain || "")}${it.date ? " · " + esc(ago(it.date)) : ""}</span></button>`).join("")}</div>`
        : `<div class="pn-sub">No headlines right now. Pull down to try again.</div>`;
      $$(".news-i", box).forEach((b) => (b.onclick = () => {
        const it = topic.items[+b.dataset.i];
        App.pendingAsk = [`What's the latest on: ${it.title}? Summarize what is known so far.`, "news"];
        App.go("/");
      }));
    };
    draw();
  } catch (ex) { box.innerHTML = `<div class="note">${esc(ex.message)}</div>`; }
}

function newSheet() {
  const body = openSheet(`<div class="new-grid">
    <button data-m="ask">${ICON.spark}<b>Ask</b><small>Fast answer, every citation checked</small></button>
    <button data-m="research">${ICON.doc}<b>Deep research</b><small>A long cited report you can download</small></button>
    <button data-m="check">${ICON.shield}<b>Check an AI answer</b><small>Paste text or attach a PDF/DOCX</small></button>
    <button data-m="references">${ICON.table}<b>Check references</b><small>Find fake references, format the rest</small></button></div>`, { title: t("new_research") });
  $$("[data-m]", body).forEach((b) => (b.onclick = () => { closeSheet(); App.go(`/new?mode=${b.dataset.m}`); }));
}

// Pull to refresh (touch, Home only).
(function pullToRefresh() {
  let y0 = null, dy = 0;
  document.addEventListener("touchstart", (e) => {
    if (!view || !document.body.classList.contains("on-home") || scrollY > 0 || e.target.closest(".sheet")) return;
    y0 = e.touches[0].clientY; dy = 0;
  }, { passive: true });
  document.addEventListener("touchmove", (e) => {
    if (y0 == null) return;
    dy = Math.max(0, Math.min(110, (e.touches[0].clientY - y0) * 0.5));
    const p = $("#ptr", view);
    if (p) { p.style.height = dy + "px"; p.classList.toggle("ready", dy > 64); }
  }, { passive: true });
  document.addEventListener("touchend", () => {
    if (y0 == null) return;
    const p = $("#ptr", view);
    if (p) { p.style.height = ""; p.classList.remove("ready"); }
    if (dy > 64) load(true);
    y0 = null;
  });
})();
