// Home: Action Cards (frosted pills whose verb is an embedded button) built from your real
// threads, reports and today's Discover, then glass Panels (usage, trust, activity,
// Discover, reports, projects). Each panel has Reply, which drops a prompt about it into
// the composer. On wide screens Home sits as a left column next to the chat.
import { $, $$, esc, safeUrl, ICON, App, jfetch, t, ago, favicon, prefs } from "./core.js";
import { MODE_LABEL } from "./runs.js";

let root = null;
let discTopic = prefs.get("disc", "world");

function greeting() {
  const h = new Date().getHours();
  return t(h < 12 ? "morning" : h < 17 ? "afternoon" : "evening");
}

const panel = (id, ic, title, sub, cls = "") => `<section class="panel ${cls}" id="${id}" data-title="${esc(title)}">
  <header class="pn-h"><span class="pn-ic">${ic}</span><span class="pn-t"><b>${esc(title)}</b> · <span class="pn-sub">${esc(sub)}</span></span>
  <button class="pn-reply" data-reply="${id}" aria-label="Reply about ${esc(title)}">${ICON.reply}<span>Reply</span></button></header>
  <div class="pn-b"><div class="skel"></div><div class="skel short"></div></div></section>`;

// Render Home into ``el``. ``rail``: the narrow left column on wide screens.
export function showHome(el, { rail = false } = {}) {
  root = el;
  const name = prefs.get("name", "");
  el.innerHTML = `<div class="home ${rail ? "in-rail" : ""}">
    <div class="ptr" id="ptr"><span class="spin"></span></div>
    <div class="home-h"><div><h1>${esc(greeting())}${name ? ", " + esc(name) : ""}</h1><p>${esc(new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" }))}</p></div>
      <button class="glass-btn round sm" id="home-refresh" aria-label="Refresh">${ICON.refresh}</button></div>
    <div class="acts" id="acts"><span class="act skel-act"></span><span class="act skel-act short"></span><span class="act skel-act"></span></div>
    <div class="panels">
      ${panel("pn-trust", ICON.shield, "Trust", "Your recent answers")}
      ${panel("pn-usage", ICON.bolt, "Usage", "This hour")}
      ${panel("pn-discover", ICON.news, "Discover", "Today", "wide")}
      ${panel("pn-activity", ICON.chart, "Activity", "Last 7 days")}
      ${panel("pn-reports", ICON.doc, "Reports", "Saved")}
      ${panel("pn-projects", ICON.folder, t("projects"), "Your files")}
    </div></div>`;
  $("#home-refresh", el).onclick = () => load(true);
  $$("[data-reply]", el).forEach((b) => (b.onclick = () => reply(b.dataset.reply)));
  load(false);
}

// Reply prompts: what each panel is about, ready for the user to finish the sentence.
const replies = {};
function reply(id) {
  const text = replies[id] || `About my ${($("#" + id, root) || {}).dataset?.title || "panel"}: `;
  App.reply(text);
}

async function load() {
  const btn = $("#home-refresh", root);
  if (btn) btn.classList.add("spinning");
  const data = {};
  const [runs, threads, main, limits, projects, disc] = await Promise.allSettled([
    jfetch("/api/runs?limit=30"), jfetch("/api/threads?limit=20"),
    prefs.get("main", "") ? jfetch(`/api/threads/${prefs.get("main", "")}?limit=200`) : Promise.resolve({ messages: [] }),
    jfetch("/api/limits"), jfetch("/api/projects"), jfetch("/api/discover"),
  ]);
  data.runs = runs.status === "fulfilled" ? runs.value.runs : [];
  data.threads = threads.status === "fulfilled" ? threads.value.threads : [];
  data.messages = main.status === "fulfilled" ? main.value.messages || [] : [];
  data.limits = limits.status === "fulfilled" ? limits.value : null;
  data.projects = projects.status === "fulfilled" ? projects.value.projects : [];
  data.topics = disc.status === "fulfilled" ? disc.value.topics : [];
  if (!root || !root.isConnected) return;
  actions(data); trust(data); usage(data, limits); discover(data); activity(data); reports(data); projectsPanel(data);
  if (btn) btn.classList.remove("spinning");
}

// ------------------------------------------------------------------ action cards
function actions(d) {
  const box = $("#acts", root);
  const main = prefs.get("main", "");
  const acts = [];
  const research = d.runs.find((r) => r.mode === "research" && r.status === "COMPLETE");
  if (research) acts.push({ verb: "Read", lead: "your report on", subject: short(research.title.replace(/\?$/, ""), 38), logo: `<span class="logo">O</span>`, go: `/runs/${research.run_id}` });
  const running = d.runs.find((r) => r.status === "RUNNING" || r.status === "QUEUED");
  if (running) acts.push({ verb: "Follow", lead: `your ${(MODE_LABEL[running.mode] || "research").toLowerCase()} on`, subject: short(running.title.replace(/\?$/, ""), 34), logo: `<span class="logo">O</span>`, go: `/runs/${running.run_id}` });
  const answered = [...d.messages].reverse().find((m) => m.role === "assistant" && m.content && m.data && m.data.verification);
  const lastQ = answered ? [...d.messages].slice(0, d.messages.indexOf(answered)).reverse().find((m) => m.role === "user") : null;
  if (lastQ) acts.push({ verb: "Verify", lead: "citations in", subject: `“${short(lastQ.content, 34)}”`, logo: ICON.shield, fn: () => { App.pendingVerify = true; App.go("/"); } });
  const thread = d.threads.find((x) => x.thread_id !== main);
  if (thread) acts.push({ verb: "Continue", lead: "research on", subject: short(thread.title, 36), logo: thread.project_id ? ICON.folder : ICON.spark, go: thread.project_id ? `/p/${thread.project_id}?t=${thread.thread_id}` : `/c/${thread.thread_id}` });
  const topic = d.topics.find((x) => x.items && x.items.length);
  if (topic) {
    const it = topic.items[0];
    acts.push({ verb: "Try", lead: "today's Discover:", subject: short(it.title, 40), logo: favicon(it.domain), fn: () => { App.pendingAsk = [`What's the latest on: ${it.title}? Summarize what is known so far.`, "news"]; App.go("/"); } });
  }
  if (research) acts.push({ verb: "Download", lead: "your latest report as", subject: "PDF", logo: ICON.download, href: `/api/runs/${research.run_id}/export.pdf` });
  acts.push({ verb: "Check", lead: "an AI answer for", subject: "fake citations", logo: ICON.shield, go: "/?mode=check" });
  if (!d.projects.length) acts.push({ verb: "Create", lead: "a project for", subject: "your class notes", logo: ICON.folder, go: "/projects?add=1" });
  box.innerHTML = acts.map((a, i) => `<div class="act">${a.href ? `<a class="verb" href="${esc(a.href)}" download>${esc(a.verb)}</a>` : `<button class="verb" data-a="${i}">${esc(a.verb)}</button>`}<span class="act-t">${actText(a)}</span></div>`).join("");
  $$("[data-a]", box).forEach((b) => (b.onclick = () => {
    const a = acts[+b.dataset.a];
    if (a.fn) a.fn(); else App.go(a.go);
  }));
}
// The logo sits inline right before the subject ("at ⓦ Walgreens").
function actText(a) { return `${esc(a.lead)} <span class="act-logo">${a.logo}</span>${esc(a.subject)}`; }
const short = (s, n) => { s = String(s || "").replace(/\s+/g, " ").trim(); return s.length > n ? s.slice(0, n - 1).trimEnd() + "…" : s; };

// ------------------------------------------------------------------ panels
function body(id) { return $(`#${id} .pn-b`, root); }
function spark(values, { w = 280, h = 54, min = null, max = null, dots = true } = {}) {
  if (values.length < 2) values = values.length ? [values[0], values[0]] : [0, 0];
  const lo = min ?? Math.min(...values), hi = max ?? Math.max(...values);
  const span = hi - lo || 1;
  const pts = values.map((v, i) => [(i / (values.length - 1)) * (w - 8) + 4, h - 6 - ((v - lo) / span) * (h - 14)]);
  const line = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("");
  const last = pts[pts.length - 1];
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true"><defs><linearGradient id="spg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-opacity=".16"/><stop offset="1" stop-opacity="0"/></linearGradient></defs><path class="sp-area" d="${line}L${last[0]},${h}L${pts[0][0]},${h}Z"/><path class="sp-line" d="${line}"/>${dots ? `<circle cx="${last[0]}" cy="${last[1]}" r="3.2"/>` : ""}</svg>`;
}
const tile = (label, value, sub = "") => `<div class="tile-n"><small>${esc(label)}</small><b>${value}</b>${sub ? `<span>${esc(sub)}</span>` : ""}</div>`;
const pnBtn = (label, attrs) => `<button class="pn-btn" ${attrs}>${esc(label)}</button>`;

function scoreOf(v) {
  const c = (v && v.counts) || {};
  const judged = (c.supported || 0) + (c.partial || 0) + (c.unsupported || 0);
  return judged ? Math.round((100 * ((c.supported || 0) + 0.5 * (c.partial || 0))) / judged) : null;
}
function trust(d) {
  const answers = d.messages.filter((m) => m.role === "assistant" && m.data && m.data.verification && (m.data.verification.citations || []).length);
  const scores = answers.map((m) => scoreOf(m.data.verification)).filter((x) => x != null);
  const box = body("pn-trust");
  if (!scores.length) {
    box.innerHTML = `<p class="pn-insight">Ask something and every citation in the answer gets checked against its page. <span>Your trust scores show up here.</span></p>${pnBtn("Ask a question", 'data-go="/"')}`;
  } else {
    const last = answers[answers.length - 1].data.verification.counts || {};
    const avg = Math.round(scores.reduce((a, b) => a + b, 0) / scores.length);
    box.innerHTML = `<p class="pn-insight">Sources back ${scores[scores.length - 1]}% of the cited claims in your latest answer <span>· ${scores.length} answer${scores.length === 1 ? "" : "s"} checked, ${avg}% on average.</span></p>
      <div class="tiles">${tile("Supported", last.supported || 0)}${tile("Partly", last.partial || 0)}${tile("Not supported", last.unsupported || 0)}</div>
      ${scores.length > 1 ? spark(scores, { min: Math.max(0, Math.min(...scores) - 15), max: 100 }) : ""}
      ${pnBtn("See the checks", 'data-verify="1"')}`;
  }
  replies["pn-trust"] = "About the citation checks on my last answer: ";
  wireBtns(box);
}
function usage(d) {
  const box = body("pn-usage");
  const l = d.limits;
  if (!l) { box.innerHTML = `<p class="note">Usage isn't available right now.</p>`; return; }
  const left = Math.max(0, l.ask_hourly_limit - l.ask_used_this_hour);
  const runsLeft = Math.max(0, (l.daily_limit || 0) - (l.used_today || 0));
  const pct = (u, m) => (m ? Math.min(100, (100 * u) / m) : 0);
  box.innerHTML = `<p class="pn-insight">${left} questions left this hour <span>and ${runsLeft} deep research run${runsLeft === 1 ? "" : "s"} today.</span></p>
    <div class="tiles">${tile("Questions", `${l.ask_used_this_hour}<i>/${l.ask_hourly_limit}</i>`, "used this hour")}${tile("Deep runs", `${l.used_today || 0}<i>/${l.daily_limit}</i>`, "used today")}</div>
    <div class="bars"><div class="bar"><i style="width:${pct(l.ask_used_this_hour, l.ask_hourly_limit)}%"></i></div><div class="bar"><i style="width:${pct(l.used_today || 0, l.daily_limit)}%"></i></div></div>
    ${pnBtn("Plan & usage", 'data-go="/settings"')}`;
  replies["pn-usage"] = "";
  wireBtns(box);
}
function activity(d) {
  const box = body("pn-activity");
  const day = 86400, nowS = Date.now() / 1000;
  const counts = Array(7).fill(0);
  const add = (ts) => { const k = Math.floor((nowS - ts) / day); if (k >= 0 && k < 7) counts[6 - k] += 1; };
  d.messages.filter((m) => m.role === "user").forEach((m) => add(m.created));
  d.runs.forEach((r) => add(r.created));
  d.threads.filter((x) => x.thread_id !== prefs.get("main", "")).forEach((x) => add(x.updated));
  const total = counts.reduce((a, b) => a + b, 0);
  const qs = d.messages.filter((m) => m.role === "user" && m.data.kind !== "run" && nowS - m.created < 7 * day).length;
  const rs = d.runs.filter((r) => nowS - r.created < 7 * day).length;
  const days = Array.from({ length: 7 }, (_, i) => new Date((nowS - (6 - i) * day) * 1000).toLocaleDateString(undefined, { weekday: "narrow" }));
  box.innerHTML = total ? `<p class="pn-insight">${qs} question${qs === 1 ? "" : "s"} and ${rs} deep run${rs === 1 ? "" : "s"} this week <span>· busiest on ${new Date((nowS - (6 - counts.indexOf(Math.max(...counts))) * day) * 1000).toLocaleDateString(undefined, { weekday: "long" })}.</span></p>
    <div class="cols">${counts.map((c, i) => `<div class="col"><i style="height:${Math.max(4, (c / Math.max(...counts)) * 100)}%"></i><small>${esc(days[i])}</small></div>`).join("")}</div>
    ${pnBtn("See history", 'data-go="/history"')}`
    : `<p class="pn-insight">Nothing yet this week. <span>Your questions and deep research show up here.</span></p>${pnBtn("Start research", 'data-go="/?mode=research"')}`;
  replies["pn-activity"] = "";
  wireBtns(box);
}
function reports(d) {
  const box = body("pn-reports");
  const list = d.runs.filter((x) => x.status === "COMPLETE").slice(0, 4);
  box.innerHTML = list.length ? `<p class="pn-insight">${list.length} report${list.length === 1 ? "" : "s"} ready <span>· as PDF, Word or Markdown.</span></p>
    <div class="thumbs">${list.map((x) => `<a class="th" href="/runs/${x.run_id}" data-nav><span class="th-img">${x.mode === "research" ? ICON.doc : x.mode === "check" ? ICON.shield : ICON.table}</span><span class="th-t">${esc(x.title)}<small>${esc(MODE_LABEL[x.mode] || x.mode)} · ${esc(ago(x.created))}</small></span></a>`).join("")}</div>
    <a class="pn-btn" href="/api/runs/${list[0].run_id}/export.pdf" download>Download latest PDF</a>`
    : `<p class="pn-insight">Finished reports land here <span>for download as PDF, Word or Markdown.</span></p>${pnBtn("Start deep research", 'data-go="/?mode=research"')}`;
  replies["pn-reports"] = list.length ? `About my report “${list[0].title}”: ` : "";
  wireBtns(box);
}
function projectsPanel(d) {
  const box = body("pn-projects");
  const list = d.projects;
  const files = list.reduce((a, p) => a + (p.files || 0), 0);
  box.innerHTML = list.length ? `<div class="tiles">${tile(t("projects"), list.length)}${tile("Files", files)}</div>
    <div class="thumbs">${list.slice(0, 3).map((p) => `<a class="th" href="/p/${p.project_id}" data-nav><span class="th-img letter">${esc((p.name[0] || "P").toUpperCase())}</span><span class="th-t">${esc(p.name)}<small>${p.files} file${p.files === 1 ? "" : "s"} · ${esc(ago(p.updated || p.created))}</small></span></a>`).join("")}</div>
    ${pnBtn("New project", 'data-go="/projects?add=1"')}`
    : `<p class="pn-insight">Keep files and instructions together <span>and ask about them.</span></p>${pnBtn("New project", 'data-go="/projects?add=1"')}`;
  replies["pn-projects"] = list.length ? `In my project ${list[0].name}, ` : "";
  wireBtns(box);
}
function discover(d) {
  const box = body("pn-discover");
  const topics = d.topics;
  if (!topics.length) { box.innerHTML = `<p class="note">No headlines right now. Pull down to try again.</p>`; return; }
  if (!topics.find((x) => x.id === discTopic)) discTopic = topics[0].id;
  const draw = () => {
    const topic = topics.find((x) => x.id === discTopic) || { items: [] };
    $("#pn-discover .pn-sub", root).textContent = `${topic.label || "Today"}${topic.fetched ? " · " + ago(topic.fetched) : ""}`;
    const items = topic.items.slice(0, 4);
    box.innerHTML = `<div class="tabs-row">${topics.map((x) => `<button class="tab ${x.id === discTopic ? "on" : ""}" data-t="${x.id}">${esc(x.label)}</button>`).join("")}</div>
      ${items.length ? `<p class="pn-insight">${esc(items[0].title)} <span>· ${esc(items[0].source || items[0].domain || "")}</span></p>
      <div class="thumbs">${items.slice(1).map((it, i) => `<button class="th" data-i="${i + 1}">${it.image && /^https:/.test(it.image) ? `<span class="th-img"><img src="${esc(safeUrl(it.image))}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()"></span>` : `<span class="th-img">${favicon(it.domain)}</span>`}<span class="th-t">${esc(it.title)}<small>${esc(it.source || it.domain || "")}${it.date ? " · " + esc(ago(it.date)) : ""}</small></span></button>`).join("")}</div>
      <button class="pn-btn" data-i="0">Ask about the top story</button>` : `<p class="note">No headlines right now.</p>`}`;
    $$(".tab", box).forEach((b) => (b.onclick = () => { discTopic = b.dataset.t; prefs.set("disc", discTopic); draw(); }));
    $$("[data-i]", box).forEach((b) => (b.onclick = () => {
      const it = items[+b.dataset.i];
      App.pendingAsk = [`What's the latest on: ${it.title}? Summarize what is known so far.`, "news"];
      App.go("/");
    }));
    replies["pn-discover"] = items[0] ? `About today's ${topic.label || ""} story “${items[0].title}”: ` : "";
  };
  draw();
}
function wireBtns(box) {
  $$("[data-go]", box).forEach((b) => (b.onclick = () => App.go(b.dataset.go)));
  $$("[data-verify]", box).forEach((b) => (b.onclick = () => { App.pendingVerify = true; App.go("/"); }));
}

// Pull to refresh (touch, full-screen Home only).
(function pullToRefresh() {
  let y0 = null, dy = 0;
  const sc = () => document.getElementById("view");
  document.addEventListener("touchstart", (e) => {
    if (!root || !document.body.classList.contains("on-home") || sc().scrollTop > 0 || e.target.closest(".sheet")) return;
    y0 = e.touches[0].clientY; dy = 0;
  }, { passive: true });
  document.addEventListener("touchmove", (e) => {
    if (y0 == null) return;
    dy = Math.max(0, Math.min(110, (e.touches[0].clientY - y0) * 0.5));
    const p = $("#ptr", root);
    if (p) { p.style.height = dy + "px"; p.classList.toggle("ready", dy > 64); }
  }, { passive: true });
  document.addEventListener("touchend", () => {
    if (y0 == null) return;
    const p = $("#ptr", root);
    if (p) { p.style.height = ""; p.classList.remove("ready"); }
    if (dy > 64) load(true);
    y0 = null;
  });
})();
