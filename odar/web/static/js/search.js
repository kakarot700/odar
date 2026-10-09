// Search overlay: client-side search over your threads (titles and messages) and reports.
import { $, $$, esc, ICON, App, jfetch, ago, prefs } from "./core.js";
import { MODE_LABEL } from "./runs.js";

let corpus = null;
let loading = null;

async function loadCorpus() {
  const [{ threads }, { runs }] = await Promise.all([jfetch("/api/threads?limit=200"), jfetch("/api/runs?limit=200")]);
  const items = [];
  const main = prefs.get("main", "");
  threads.forEach((th) => { if (th.thread_id === main) th.title = "Chat"; });
  const hrefOf = (th) => (th.project_id ? `/p/${th.project_id}?t=${th.thread_id}` : th.thread_id === main ? "/" : `/c/${th.thread_id}`);
  threads.forEach((th) => items.push({ kind: "thread", title: th.title, text: th.title, href: hrefOf(th), t: th.updated }));
  runs.forEach((r) => items.push({ kind: "report", title: r.title, text: r.title, sub: MODE_LABEL[r.mode] || r.mode, href: `/runs/${r.run_id}`, t: r.created }));
  corpus = items;
  // messages of the most recent threads, loaded in the background
  const recent = threads.slice(0, 25);
  await Promise.allSettled(recent.map(async (th) => {
    const { messages } = await jfetch(`/api/threads/${th.thread_id}?limit=120`);
    messages.forEach((m) => {
      if (!m.content) return;
      items.push({ kind: "message", title: th.title, text: m.content, role: m.role, href: hrefOf(th), t: m.created });
    });
  }));
}

function snippet(text, needle) {
  const flat = String(text).replace(/\s+/g, " ").replace(/\[(\d+)\]/g, "");
  const i = flat.toLowerCase().indexOf(needle);
  if (i < 0) return esc(flat.slice(0, 140));
  const start = Math.max(0, i - 40);
  const part = flat.slice(start, start + 120);
  const j = i - start;
  return (start ? "…" : "") + esc(part.slice(0, j)) + `<mark>${esc(part.slice(j, j + needle.length))}</mark>` + esc(part.slice(j + needle.length)) + "…";
}

export function openSearch() {
  const ov = $("#search");
  ov.classList.remove("hidden");
  requestAnimationFrame(() => ov.classList.add("open"));
  const q = $("#search-q");
  q.value = "";
  q.focus();
  corpus = null;
  const draw = () => {
    const needle = q.value.trim().toLowerCase();
    const res = $("#search-res");
    if (!corpus) { res.innerHTML = `<div class="empty"><span class="spin"></span></div>`; return; }
    if (!needle) {
      const recent = corpus.filter((x) => x.kind !== "message").sort((a, b) => b.t - a.t).slice(0, 8);
      res.innerHTML = recent.length ? `<div class="s-group">Recent</div><div class="list flat">${recent.map(row(needle)).join("")}</div>` : `<div class="empty">Your threads and reports show up here.</div>`;
    } else {
      const hits = corpus.filter((x) => x.text.toLowerCase().includes(needle));
      const groups = [["thread", "Threads"], ["report", "Reports"], ["message", "Messages"]].map(([k, label]) => [label, hits.filter((x) => x.kind === k).sort((a, b) => b.t - a.t).slice(0, k === "message" ? 20 : 8)]).filter(([, l]) => l.length);
      res.innerHTML = groups.length ? groups.map(([label, list]) => `<div class="s-group">${label}</div><div class="list flat">${list.map(row(needle)).join("")}</div>`).join("")
        : `<div class="empty">Nothing found. <a href="/?q=${encodeURIComponent(q.value.trim())}" data-nav>Ask ODAR instead</a></div>`;
    }
    $$("#search-res a").forEach((a) => a.addEventListener("click", closeSearch));
  };
  q.oninput = draw;
  draw();
  loading = loadCorpus().catch(() => { corpus = corpus || []; }).finally(draw);
}
const row = (needle) => (x) => `<a class="li" href="${x.href}" data-nav><span class="li-ic">${x.kind === "report" ? ICON.doc : x.kind === "message" ? (x.role === "user" ? ICON.search : ICON.spark) : ICON.spark}</span>
  <span class="lt">${x.kind === "message" ? snippet(x.text, needle) : esc(x.title)}<small>${x.kind === "message" ? esc(x.title) + " · " : x.sub ? esc(x.sub) + " · " : ""}${esc(ago(x.t))}</small></span></a>`;

export function closeSearch() {
  const ov = $("#search");
  ov.classList.remove("open");
  setTimeout(() => ov.classList.add("hidden"), 160);
}
export function initSearch() {
  $("#search-x").innerHTML = ICON.x;
  $("#search-x").onclick = closeSearch;
  $("#search-ic").innerHTML = ICON.search;
  $("#search").addEventListener("click", (e) => { if (e.target.id === "search") closeSearch(); });
  App.openSearch = openSearch;
}
