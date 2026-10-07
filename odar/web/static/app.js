"use strict";
// ODAR web app: a chat-shaped, mobile-first client. Vanilla JS, no build step.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
const view = $("#view");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");
const jfetch = async (url, opts = {}) => {
  const r = await fetch(url, opts);
  let d = {};
  try { d = await r.json(); } catch { /* empty body */ }
  if (!r.ok) throw new Error(d.detail || `request failed (${r.status})`);
  return d;
};
const jpost = (url, body, method = "POST") => jfetch(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

const ICON = {
  home: '<svg viewBox="0 0 24 24"><path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V20h5v-6h4v6h5V9.5"/></svg>',
  search: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
  user: '<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/></svg>',
  clip: '<svg viewBox="0 0 24 24"><path d="m21 11-8.5 8.5a5 5 0 0 1-7-7L14 4a3.5 3.5 0 0 1 5 5l-8.5 8.5a2 2 0 0 1-3-3L15 7"/></svg>',
  send: '<svg viewBox="0 0 24 24"><path d="M12 19V5"/><path d="m5 12 7-7 7 7"/></svg>',
  file: '<svg viewBox="0 0 24 24" style="width:12px;height:12px"><path d="M14 3H6v18h12V7z"/><path d="M14 3v4h4"/></svg>',
};
$("#btn-home").innerHTML = ICON.home;
$("#btn-search").innerHTML = ICON.search;
$("#btn-profile").innerHTML = ICON.user;
$("#btn-attach").innerHTML = ICON.clip;
$("#btn-send").innerHTML = ICON.send;

const FOCUS = {
  all: ["All web", "Search the whole web"],
  academic: ["Academic", "Crossref, PubMed, arXiv, OpenAlex, Semantic Scholar"],
  news: ["News", "Recent headlines, with dates"],
  reddit: ["Discussions", "Reddit and forums (opinions, not facts)"],
  youtube: ["YouTube", "Video titles and descriptions"],
};
const MODES = {
  ask: ["Ask", "Fast answer with checked citations", "Ask anything…"],
  research: ["Research", "Deep cited report, 5 to 10 minutes", "Ask a research question…"],
  check: ["Check", "Verify an AI answer's citations", "Paste an AI answer with its links, or attach a PDF/DOCX…"],
  references: ["References", "Validate and format a bibliography", "Paste a reference list, one per line…"],
};
const SRC = { both: "My files + web", files: "My files", web: "Web only" };
const S = {
  focus: localStorage.getItem("odar.focus") || "all",
  mode: "ask",
  src: "both",
  file: null,
  thread: null, // {thread_id, share_token, title, project_id}
  project: null, // {project_id, name}
  busy: false,
  readOnly: false,
};
let pollTimer = null;

// ---------------------------------------------------------------- toast, menu, popover
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toast.timer); toast.timer = setTimeout(() => t.classList.add("hidden"), 2600);
}
function openMenu(anchor, items, onPick) {
  const m = $("#menu");
  m.innerHTML = items.map(([id, label, sub, on]) => `<button data-id="${esc(id)}" class="${on ? "on" : ""}">${esc(label)}${sub ? `<small>${esc(sub)}</small>` : ""}</button>`).join("");
  m.classList.remove("hidden");
  const r = anchor.getBoundingClientRect();
  const h = m.offsetHeight;
  m.style.left = Math.min(r.left, innerWidth - m.offsetWidth - 8) + "px";
  m.style.top = (r.top - h - 8 > 8 ? r.top - h - 8 : r.bottom + 8) + "px";
  $$("button", m).forEach((b) => (b.onclick = (e) => { e.stopPropagation(); m.classList.add("hidden"); onPick(b.dataset.id); }));
}
document.addEventListener("click", (e) => {
  if (!e.target.closest("#menu") && !e.target.closest(".chip")) $("#menu").classList.add("hidden");
  if (!e.target.closest("#pop") && !e.target.closest(".cite") && !e.target.closest(".src")) hidePop();
});
function showPop(anchor, html) {
  const p = $("#pop"); p.innerHTML = html; p.classList.remove("hidden");
  const r = anchor.getBoundingClientRect();
  let top = r.bottom + 8;
  if (top + p.offsetHeight > innerHeight - 10) top = Math.max(10, r.top - p.offsetHeight - 8);
  p.style.top = top + "px";
  p.style.left = Math.max(12, Math.min(r.left - 20, innerWidth - p.offsetWidth - 12)) + "px";
}
function hidePop() { $("#pop").classList.add("hidden"); }

// ---------------------------------------------------------------- routing
function go(path) { history.pushState({}, "", path); route(); }
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[data-nav]");
  if (a && !e.metaKey && !e.ctrlKey) { e.preventDefault(); go(a.getAttribute("href")); }
});
window.addEventListener("popstate", route);

function route() {
  clearTimeout(pollTimer);
  hidePop();
  $("#menu").classList.add("hidden");
  const p = location.pathname;
  const qs = new URLSearchParams(location.search);
  let m;
  S.readOnly = false;
  window.scrollTo(0, 0);
  if ((m = p.match(/^\/c\/([\w-]+)/))) return showThread(m[1]);
  if ((m = p.match(/^\/s\/([\w-]+)/))) return showShared(m[1]);
  if ((m = p.match(/^\/p\/([\w-]+)/))) return showProject(m[1]);
  if ((m = p.match(/^\/runs\/([\w-]+)/))) return showRun({ id: m[1] });
  if ((m = p.match(/^\/r\/([\w-]+)/))) return showRun({ token: m[1] });
  if (p === "/home") return showHome();
  if (p === "/history") return showHistory();
  if (p === "/settings") return showSettings();
  return showNew(qs.get("mode"), qs.get("q"));
}

function composer(on) {
  $("#composer-wrap").classList.toggle("hidden", !on);
  view.classList.toggle("no-composer", !on);
}

// ---------------------------------------------------------------- sidebar
async function refreshSidebar() {
  try {
    const [{ threads }, { projects }] = await Promise.all([jfetch("/api/threads?limit=40"), jfetch("/api/projects")]);
    const cur = location.pathname;
    $("#side-threads").innerHTML = threads.length ? threads.map((t) => `<a class="side-link ${cur === "/c/" + t.thread_id ? "active" : ""}" href="/c/${t.thread_id}" data-nav title="${esc(t.title)}">${esc(t.title)}</a>`).join("") : `<div class="side-empty">Your questions show up here.</div>`;
    $("#side-projects").innerHTML = projects.length ? projects.map((x) => `<a class="side-link ${cur === "/p/" + x.project_id ? "active" : ""}" href="/p/${x.project_id}" data-nav>${esc(x.name)}</a>`).join("") : `<div class="side-empty">No projects yet.</div>`;
  } catch { /* sidebar is best-effort */ }
}

// ---------------------------------------------------------------- composer
const ta = $("#q");
function syncComposer() {
  $("#chip-mode").innerHTML = `<b>${esc(MODES[S.mode][0])}</b> ▾`;
  $("#chip-focus").innerHTML = `${esc(FOCUS[S.focus][0])} ▾`;
  $("#chip-focus").classList.toggle("hidden", S.mode !== "ask" || (S.project && S.src === "files"));
  $("#chip-src").classList.toggle("hidden", !(S.mode === "ask" && S.project));
  $("#chip-src").textContent = `${SRC[S.src]} ▾`;
  ta.placeholder = S.project && S.mode === "ask" ? `Ask about ${S.project.name}…` : S.thread && S.mode === "ask" ? "Ask a follow-up…" : MODES[S.mode][2];
  const opts = $("#opts");
  opts.classList.toggle("hidden", S.mode === "ask");
  if (S.mode !== "ask") {
    opts.innerHTML = `${S.mode === "research" ? `<label><input type="checkbox" id="o-verify" checked> check every citation</label>` : ""}
      ${S.mode !== "references" ? `<label><input type="checkbox" id="o-acad" checked> academic sources</label>` : ""}
      <label>style <select id="o-style"><option value="apa">APA 7</option><option value="mla">MLA 9</option><option value="chicago">Chicago</option><option value="ieee">IEEE</option></select></label>
      ${S.mode !== "references" ? `<label>language <select id="o-lang"><option value="auto">same as input</option><option value="en">English</option><option value="hi">हिंदी</option></select></label>` : ""}`;
  }
  const at = $("#attached");
  at.classList.toggle("hidden", !S.file);
  at.innerHTML = S.file ? `<span class="chip">${ICON.file} ${esc(S.file.name)} <a href="#" id="unattach" aria-label="Remove">✕</a></span>` : "";
  if (S.file) $("#unattach").onclick = (e) => { e.preventDefault(); S.file = null; syncComposer(); };
  $("#btn-send").disabled = S.busy;
}
function autoGrow() { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 220) + "px"; }
ta.addEventListener("input", autoGrow);
ta.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing && S.mode !== "check" && S.mode !== "references") { e.preventDefault(); $("#composer").requestSubmit(); }
});
$("#chip-mode").onclick = (e) => openMenu(e.currentTarget, Object.entries(MODES).map(([k, v]) => [k, v[0], v[1], k === S.mode]), (k) => {
  if (k !== "ask" && S.thread) { go(`/new?mode=${k}`); return; }
  S.mode = k; syncComposer(); ta.focus();
});
$("#chip-focus").onclick = (e) => openMenu(e.currentTarget, Object.entries(FOCUS).map(([k, v]) => [k, v[0], v[1], k === S.focus]), (k) => {
  S.focus = k; localStorage.setItem("odar.focus", k); syncComposer();
});
$("#chip-src").onclick = (e) => openMenu(e.currentTarget, Object.entries(SRC).map(([k, v]) => [k, v, "", k === S.src]), (k) => { S.src = k; syncComposer(); });
$("#btn-attach").onclick = () => $("#file").click();
$("#file").onchange = async (e) => {
  const f = e.target.files[0]; e.target.value = "";
  if (!f) return;
  if (f.size > 10_000_000) { toast("Files can be up to 10 MB"); return; }
  if (S.mode === "ask" && S.project) { await uploadToProject(S.project.project_id, f); return; }
  if (S.mode === "ask" || S.mode === "research") {
    if (S.thread) { go("/new?mode=check"); }
    S.mode = "check"; toast("Switched to Check: ODAR will verify this file's citations. Use a Project to ask about your files.");
  }
  S.file = f; syncComposer();
};
$("#composer").onsubmit = async (e) => {
  e.preventDefault();
  if (S.busy) return;
  const text = ta.value.trim();
  if (S.mode === "ask") {
    if (text.length < 3) { toast("Ask a slightly longer question"); return; }
    ta.value = ""; autoGrow();
    return sendAsk(text);
  }
  if (!text && !S.file) { toast("Type or attach something first"); return; }
  const fd = new FormData();
  fd.append("mode", S.mode);
  if (S.file) fd.append("file", S.file);
  else if (S.mode === "check" && /^https?:\/\/\S+$/.test(text)) fd.append("url", text);
  else fd.append("text", text);
  fd.append("verify", $("#o-verify") ? $("#o-verify").checked : true);
  fd.append("academic", $("#o-acad") ? $("#o-acad").checked : true);
  fd.append("style", $("#o-style") ? $("#o-style").value : "apa");
  fd.append("language", $("#o-lang") ? $("#o-lang").value : "auto");
  fd.append("use_llm", true);
  S.busy = true; syncComposer();
  try {
    const d = await jfetch("/api/runs", { method: "POST", body: fd });
    ta.value = ""; S.file = null; autoGrow();
    go(`/runs/${d.run_id}`);
  } catch (ex) { toast(ex.message); }
  finally { S.busy = false; syncComposer(); }
};

// ---------------------------------------------------------------- new chat / landing
function showNew(mode, q) {
  S.thread = null; S.project = null; S.mode = MODES[mode] ? mode : "ask"; S.file = null;
  composer(true); syncComposer();
  const hello = { ask: ["What do you want to know?", "Fast answers. Every citation checked against its source."],
    research: ["Research a question", "A deep, cited report. You can leave the page while it runs."],
    check: ["Check an AI answer", "Paste an answer from any chatbot, attach a file, or paste an article URL."],
    references: ["Check a reference list", "Find fake or mismatched references and format the rest."] }[S.mode];
  view.innerHTML = `<div class="hero"><h1>${esc(hello[0])}</h1><p>${esc(hello[1])}</p>
    ${S.mode === "ask" ? `<div class="suggest">${["How do mRNA vaccines work?", "Is intermittent fasting effective?", "What changed in India's DPDP rules?"].map((s) => `<button class="chip" data-s="${esc(s)}">${esc(s)}</button>`).join("")}</div>` : ""}</div><div id="chat"></div>`;
  $$("[data-s]").forEach((b) => (b.onclick = () => sendAsk(b.dataset.s)));
  if (q) { ta.value = q; autoGrow(); }
  ta.focus();
  refreshSidebar();
}

// ---------------------------------------------------------------- Ask (SSE)
function rowMe(text) {
  const r = document.createElement("div"); r.className = "row me";
  r.innerHTML = `<div class="bubble">${esc(text)}</div>`;
  return r;
}
function chatBox() {
  let c = $("#chat");
  if (!c) { view.insertAdjacentHTML("beforeend", `<div id="chat"></div>`); c = $("#chat"); }
  return c;
}

async function sendAsk(question, focusOverride) {
  if (S.busy) return;
  const hero = $(".hero"); if (hero) hero.remove();
  const chat = chatBox();
  chat.append(rowMe(question));
  const turn = makeTurn(chat);
  turn.el.scrollIntoView({ behavior: "smooth", block: "start" });
  S.busy = true; syncComposer();
  const body = { question, focus: focusOverride || S.focus, thread_id: S.thread ? S.thread.thread_id : "", project_id: S.project ? S.project.project_id : "", sources: S.project ? S.src : "web", images: true, verify: true };
  try {
    const res = await fetch("/api/ask/stream", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!res.ok) { let d = {}; try { d = await res.json(); } catch { /* */ } turn.error(d.detail || "Couldn't answer that right now."); return; }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, i); buf = buf.slice(i + 2);
        let ev = "message", data = "";
        for (const line of block.split("\n")) {
          if (line.startsWith("event: ")) ev = line.slice(7);
          else if (line.startsWith("data: ")) data += line.slice(6);
        }
        if (data) handleEvent(turn, ev, JSON.parse(data));
      }
    }
  } catch (ex) {
    turn.error("The connection dropped: " + ex.message);
  } finally {
    S.busy = false; syncComposer(); refreshSidebar();
  }
}

function handleEvent(turn, ev, d) {
  if (ev === "thread") {
    if (!S.thread || S.thread.thread_id !== d.thread_id) {
      S.thread = d;
      history.replaceState({}, "", `/c/${d.thread_id}`);
      setThreadTitle(d.title);
    }
  } else if (ev === "sources") turn.sources(d.sources);
  else if (ev === "delta") turn.delta(d.text);
  else if (ev === "done") turn.done(d);
  else if (ev === "images") turn.images(d.images);
  else if (ev === "verification") turn.verify(d);
  else if (ev === "error") turn.error(d.message);
}

// One assistant turn: text bubble + cards under it.
function makeTurn(parent, data = {}) {
  const el = document.createElement("div");
  el.className = "row bot";
  el.innerHTML = `<div class="bubble"><span class="typing"><i></i><i></i><i></i></span></div><div class="meta hidden"></div>
    <div class="c-src"></div><div class="c-img"></div><div class="c-ver"></div>`;
  parent.append(el);
  const st = { sources: data.sources || [], verification: null, raw: "" };
  const bubble = $(".bubble", el);
  const t = {
    el,
    sources(list) {
      st.sources = list || [];
      if (!st.sources.length) return;
      $(".c-src", el).innerHTML = `<div class="card"><div class="card-h">Sources <small>${st.sources.length}</small></div>
        <div class="srcs">${st.sources.map(srcCard).join("")}</div></div>`;
      $$(".src", el).forEach((a) => {
        if (a.dataset.file) a.onclick = (e) => { e.preventDefault(); showPop(a, sourcePop(st.sources[+a.dataset.i])); };
      });
    },
    delta(text) {
      st.raw += text;
      bubble.innerHTML = `<p>${esc(st.raw).replace(/\n/g, "<br>")}</p>`;
    },
    done(d) {
      st.raw = d.answer || st.raw;
      bubble.innerHTML = renderAnswer(st.raw);
      wireCites(el, st);
      const tm = d.timing || {};
      const meta = $(".meta", el);
      if (tm.total_s != null) { meta.textContent = `answered in ${tm.total_s}s${tm.ttft_s ? ` · first words at ${tm.ttft_s}s` : ""}`; meta.classList.remove("hidden"); }
      if (!d.abstained && /\[\d+\]/.test(st.raw)) $(".c-ver", el).innerHTML = `<div class="card"><div class="card-h">Verified <small><span class="spinner"></span>checking each citation against its source…</small></div></div>`;
    },
    images(list) {
      if (!list || !list.length) return;
      $(".c-img", el).innerHTML = `<div class="card"><div class="card-h">Images</div><div class="imgs">${list.map((im) =>
        `<a href="${esc(safeUrl(im.url))}" target="_blank" rel="noopener noreferrer"><img src="${esc(safeUrl(im.thumbnail))}" alt="${esc(im.title)}" loading="lazy" referrerpolicy="no-referrer" onerror="this.parentNode.remove()"><span>${esc(im.domain)}</span></a>`).join("")}</div></div>`;
    },
    verify(v) {
      st.verification = v;
      const cits = v.citations || [];
      $$(".cite", el).forEach((b) => { const c = cits[+b.dataset.occ]; if (c) b.classList.add(c.verdict); });
      if (!cits.length) { $(".c-ver", el).innerHTML = ""; return; }
      const counts = v.counts || {};
      const sum = ["supported", "partial", "unsupported", "unchecked"].filter((k) => counts[k]).map((k) => `${counts[k]} ${k}`).join(" · ");
      $(".c-ver", el).innerHTML = `<div class="card"><div class="card-h">Verified <small>${esc(sum)}</small></div><div class="vlist">${cits.map((c) => `
        <div class="vitem"><span class="cite ${esc(c.verdict)}">${c.n}</span> <span class="badge ${esc(c.verdict)}">${esc(c.verdict)}</span>
        <div class="v-claim">${esc(c.claim)}</div>
        ${c.quote ? `<blockquote>“${esc(c.quote)}”</blockquote>` : ""}
        <div class="v-src">${esc(c.title || "")}${c.domain ? " · " + esc(c.domain) : ""}${c.note ? " · " + esc(c.note) : ""}</div></div>`).join("")}</div>
        <div class="meta">Checked with a local NLI model; quotes are copied verbatim from the source.</div></div>`;
    },
    error(msg) {
      bubble.classList.add("err");
      bubble.textContent = msg;
    },
  };
  if (data.answer != null) {
    t.sources(data.sources || []);
    t.done({ answer: data.answer, timing: data.timing || {}, abstained: data.abstained });
    if (data.images) t.images(data.images);
    if (data.verification) t.verify(data.verification);
    else if (!data.error) $(".c-ver", el).innerHTML = "";
    if (data.error) t.error(data.error);
  }
  return t;
}

function favicon(domain, kind) {
  if (kind === "file") return `<span class="fav">${ICON.file}</span>`;
  if (!domain) return `<span class="fav">?</span>`;
  return `<span class="fav"><img src="https://${esc(domain)}/favicon.ico" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.replaceWith(document.createTextNode('${esc(domain[0].toUpperCase())}'))"></span>`;
}
function ago(iso) {
  const t = Date.parse(iso);
  if (!iso || isNaN(t)) return iso && /^\d{4}$/.test(iso) ? iso : "";
  const s = (Date.now() - t) / 1000;
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  if (s < 86400 * 30) return `${Math.round(s / 86400)} d ago`;
  return new Date(t).toLocaleDateString();
}
function kindLabel(k) { return k === "forum" ? "forum" : k === "video" ? "video" : k === "paper" ? "paper" : k === "file" ? "file" : k === "news" ? "news" : ""; }
function srcCard(s, i) {
  const when = ago(s.date || "");
  const kl = kindLabel(s.kind);
  const inner = `<div class="s-top">${favicon(s.domain, s.kind)}<span class="num">${s.n}</span> ${esc(s.domain || "")}${kl ? ` <span class="kind ${esc(s.kind)}">${kl}</span>` : ""}${when ? ` · ${esc(when)}` : ""}</div><div class="s-title">${esc(s.title)}</div>`;
  if (s.kind === "file") return `<button class="src" data-file="1" data-i="${i}">${inner}</button>`;
  return `<a class="src" href="${esc(safeUrl(s.url))}" target="_blank" rel="noopener noreferrer">${inner}</a>`;
}
function sourcePop(s, c) {
  const verdict = c ? `<span class="badge ${esc(c.verdict)}">${esc(c.verdict)}</span>` : `<span class="badge pending">checking…</span>`;
  const quote = c && c.quote ? c.quote : "";
  const body = quote ? `<blockquote>“${esc(quote)}”</blockquote>` : s.kind === "file" ? `<blockquote>${esc((s.passage || s.snippet || "").slice(0, 600))}</blockquote>` : s.snippet ? `<div class="note" style="margin-top:6px">${esc(s.snippet.slice(0, 220))}</div>` : "";
  return `${verdict}<div class="p-title">${s.url ? `<a href="${esc(safeUrl(s.url))}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a>` : esc(s.title)}</div>
    <div class="p-dom">${esc(s.domain || "")}${s.date ? " · " + esc(ago(s.date)) : ""}</div>${body}${c && c.note ? `<div class="note" style="margin-top:6px">${esc(c.note)}</div>` : ""}`;
}
function wireCites(el, st) {
  $$(".bubble .cite", el).forEach((b) => {
    const show = () => {
      const s = st.sources[+b.dataset.n - 1];
      if (!s) return;
      const c = st.verification ? (st.verification.citations || [])[+b.dataset.occ] : null;
      showPop(b, sourcePop(s, c));
    };
    b.onclick = (e) => { e.stopPropagation(); show(); };
    b.onmouseenter = () => { if (matchMedia("(hover:hover)").matches) show(); };
  });
}

// Markdown-lite + citation markers. Marker occurrences are numbered in reading
// order, matching the server's verification list.
function renderAnswer(src) {
  let occ = 0;
  const inline = (t) => esc(t)
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\[(\d{1,2})\]/g, (m, n) => `<button class="cite" data-n="${n}" data-occ="${occ++}" aria-label="Source ${n}">${n}</button>`);
  return mdBlocks(src, inline);
}
function mdBlocks(src, inline) {
  let out = "", list = false;
  for (const raw of String(src || "").split("\n")) {
    const line = raw.trimEnd();
    const li = line.match(/^\s*(?:[-*•]|\d+\.)\s+(.*)/);
    if (list && !li) { out += "</ul>"; list = false; }
    if (!line.trim()) continue;
    const h = line.match(/^(#{1,4})\s+(.*)/);
    if (h) out += `<p><b>${inline(h[2])}</b></p>`;
    else if (li) { if (!list) { out += "<ul>"; list = true; } out += `<li>${inline(li[1])}</li>`; }
    else out += `<p>${inline(line)}</p>`;
  }
  return out + (list ? "</ul>" : "");
}

// ---------------------------------------------------------------- threads
function setThreadTitle(title) {
  let h = $(".thread-title");
  if (!h) {
    view.insertAdjacentHTML("afterbegin", `<div class="thread-title"><h1></h1><div class="actions"></div></div>`);
    h = $(".thread-title");
  }
  $("h1", h).textContent = title;
  if (!S.readOnly && S.thread) {
    $(".actions", h).innerHTML = `<button class="btn" id="t-share">Share</button><button class="btn" id="t-more" aria-label="More">⋯</button>`;
    $("#t-share").onclick = () => copyLink(`${location.origin}/s/${S.thread.share_token}`);
    $("#t-more").onclick = (e) => openMenu(e.currentTarget, [["rename", "Rename"], ["delete", "Delete thread"]], async (k) => {
      if (k === "rename") {
        const t = prompt("Rename thread", $("h1", h).textContent);
        if (t && t.trim()) { await jpost(`/api/threads/${S.thread.thread_id}`, { title: t }, "PATCH"); $("h1", h).textContent = t.trim(); refreshSidebar(); }
      } else if (confirm("Delete this thread?")) {
        await jfetch(`/api/threads/${S.thread.thread_id}`, { method: "DELETE" }); refreshSidebar(); go("/");
      }
    });
  }
}
async function copyLink(url) {
  try { await navigator.clipboard.writeText(url); toast("Share link copied"); } catch { prompt("Share link", url); }
}
function renderMessages(messages) {
  const chat = chatBox();
  messages.forEach((m) => {
    if (m.role === "user") chat.append(rowMe(m.content));
    else makeTurn(chat, { ...m.data, answer: m.content || (m.data.error ? "" : m.content) });
  });
}
async function showThread(id) {
  S.mode = "ask"; S.file = null;
  composer(true);
  view.innerHTML = `<div class="empty"><span class="spinner"></span>Loading…</div>`;
  try {
    const { thread, messages } = await jfetch(`/api/threads/${id}`);
    S.thread = thread;
    S.project = null;
    if (thread.project_id) {
      try { S.project = (await jfetch(`/api/projects/${thread.project_id}`)).project; } catch { /* */ }
    }
    view.innerHTML = "";
    setThreadTitle(thread.title);
    if (S.project) $(".thread-title").insertAdjacentHTML("afterend", `<p class="meta">in <a href="/p/${S.project.project_id}" data-nav>${esc(S.project.name)}</a></p>`);
    renderMessages(messages);
  } catch (ex) {
    view.innerHTML = `<div class="empty">${esc(ex.message)}</div>`;
  }
  syncComposer();
  refreshSidebar();
}
async function showShared(token) {
  S.readOnly = true; S.thread = null; S.project = null;
  composer(false);
  try {
    const { thread, messages } = await jfetch(`/api/shared-threads/${token}`);
    view.innerHTML = "";
    setThreadTitle(thread.title);
    renderMessages(messages);
    view.insertAdjacentHTML("beforeend", `<p class="empty">A shared ODAR thread. <a href="/" data-nav>Ask your own question</a></p>`);
  } catch (ex) { view.innerHTML = `<div class="empty">${esc(ex.message)}</div>`; }
}

// ---------------------------------------------------------------- Home
async function showHome() {
  S.thread = null; S.project = null; S.mode = "ask";
  composer(true); syncComposer();
  view.innerHTML = `<div class="sec-h"><h2>Discover</h2><small class="meta" id="disc-meta"></small></div>
    <div class="tabs" id="disc-tabs"></div><div class="grid" id="disc" style="margin-top:12px"><div class="empty"><span class="spinner"></span>Loading headlines…</div></div>
    <div class="sec-h" id="projects"><h2>Projects</h2><button id="p-new">+ New project</button></div>
    <div id="p-form" class="card hidden"><div class="form-row"><input class="field" id="p-name" placeholder="Project name" maxlength="120">
      <textarea class="field" id="p-ins" rows="3" placeholder="Custom instructions (optional), e.g. Explain for a class 12 student; prefer Indian sources."></textarea></div>
      <button class="btn primary" id="p-save">Create project</button></div>
    <div id="p-list" class="grid"></div>
    <div class="sec-h"><h2>Recent threads</h2></div><div id="t-list" class="list"></div>
    <div class="sec-h"><h2>Tools</h2><a href="/history" data-nav>Run history</a></div>
    <div class="tools">
      <a class="tool" href="/new?mode=research" data-nav><b>Research</b><small>Deep, cited report with a citation check</small></a>
      <a class="tool" href="/new?mode=check" data-nav><b>Check</b><small>Verify an AI answer's citations</small></a>
      <a class="tool" href="/new?mode=references" data-nav><b>References</b><small>Find fake references, format the rest</small></a>
    </div>`;
  $("#p-new").onclick = () => { $("#p-form").classList.toggle("hidden"); $("#p-name").focus(); };
  $("#p-save").onclick = async () => {
    try {
      const { project } = await jpost("/api/projects", { name: $("#p-name").value, instructions: $("#p-ins").value });
      go(`/p/${project.project_id}`);
    } catch (ex) { toast(ex.message); }
  };
  if (location.hash === "#projects") { $("#p-form").classList.remove("hidden"); $("#projects").scrollIntoView(); }
  jfetch("/api/projects").then(({ projects }) => {
    $("#p-list").innerHTML = projects.length ? projects.map((p) => `<a class="panel" href="/p/${p.project_id}" data-nav style="text-decoration:none;color:inherit"><div class="pb"><div class="pt">${esc(p.name)}</div>
      <div class="pm">${p.files} file${p.files === 1 ? "" : "s"}${p.instructions ? " · " + esc(p.instructions.slice(0, 70)) : ""}</div></div></a>`).join("") : `<div class="empty">Projects keep your files and instructions together; questions inside one can use your files.</div>`;
  }).catch(() => {});
  jfetch("/api/threads?limit=8").then(({ threads }) => {
    $("#t-list").innerHTML = threads.length ? threads.map((t) => `<a class="li" href="/c/${t.thread_id}" data-nav><span class="lt">${esc(t.title)}</span><small>${esc(ago(new Date(t.updated * 1000).toISOString()))}</small></a>`).join("") : `<div class="empty">No threads yet. Ask something below.</div>`;
  }).catch(() => {});
  try {
    const { topics } = await jfetch("/api/discover");
    let cur = topics[0] ? topics[0].id : "";
    const draw = () => {
      $("#disc-tabs").innerHTML = topics.map((t) => `<button class="chip ${t.id === cur ? "on" : ""}" data-t="${t.id}">${esc(t.label)}</button>`).join("");
      $$("#disc-tabs .chip").forEach((b) => (b.onclick = () => { cur = b.dataset.t; draw(); }));
      const topic = topics.find((t) => t.id === cur) || { items: [] };
      $("#disc-meta").textContent = topic.fetched ? `updated ${ago(new Date(topic.fetched * 1000).toISOString())}` : "";
      $("#disc").innerHTML = topic.items.length ? topic.items.map((it, i) => `<button class="panel" data-i="${i}">
        ${it.image ? `<img src="${esc(safeUrl(it.image))}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">` : ""}
        <div class="pb"><div class="pt">${esc(it.title)}</div><div class="pm">${favicon(it.domain)} ${esc(it.source)}${it.date ? " · " + esc(ago(it.date)) : ""}</div></div></button>`).join("")
        : `<div class="empty">No headlines right now. Try again in a bit.</div>`;
      $$("#disc .panel").forEach((b) => (b.onclick = () => {
        const it = topic.items[+b.dataset.i];
        S.thread = null; S.project = null;
        view.innerHTML = `<div id="chat"></div>`;
        sendAsk(`What's the latest on: ${it.title}? Summarize what is known so far.`, "news");
      }));
    };
    draw();
  } catch (ex) { $("#disc").innerHTML = `<div class="empty">${esc(ex.message)}</div>`; }
  refreshSidebar();
}

// ---------------------------------------------------------------- Projects
async function uploadToProject(pid, f) {
  const fd = new FormData(); fd.append("file", f);
  toast(`Adding ${f.name}…`);
  try {
    await jfetch(`/api/projects/${pid}/files`, { method: "POST", body: fd });
    toast(`${f.name} added to the project`);
    if (location.pathname === `/p/${pid}`) showProject(pid);
  } catch (ex) { toast(ex.message); }
}
async function showProject(pid) {
  S.thread = null; S.mode = "ask"; S.file = null;
  composer(true);
  view.innerHTML = `<div class="empty"><span class="spinner"></span>Loading…</div>`;
  let d;
  try { d = await jfetch(`/api/projects/${pid}`); } catch (ex) { view.innerHTML = `<div class="empty">${esc(ex.message)}</div>`; return; }
  S.project = d.project;
  S.src = d.files.length ? (S.src || "both") : "web";
  syncComposer();
  view.innerHTML = `<div class="thread-title"><h1>${esc(d.project.name)}</h1><div class="actions"><button class="btn" id="pj-more">⋯</button></div></div>
    <div class="card"><div class="card-h">Instructions <button class="btn" id="pj-save">Save</button></div>
      <textarea class="field" id="pj-ins" rows="3" placeholder="How should answers in this project be written?">${esc(d.project.instructions)}</textarea></div>
    <div class="card"><div class="card-h">Files <small>PDF, DOCX, TXT, MD · 10 MB each</small></div>
      <div class="list" style="box-shadow:none">${d.files.length ? d.files.map((f) => `<div class="li"><span class="lt">${ICON.file} ${esc(f.name)}</span><small>${Math.round(f.chars / 1000)}k chars · <a href="#" data-del="${f.file_id}">remove</a></small></div>`).join("") : `<div class="empty">No files yet. Use the paperclip below to add one.</div>`}</div></div>
    <div class="sec-h"><h2>Threads</h2></div><div class="list">${d.threads.length ? d.threads.map((t) => `<a class="li" href="/c/${t.thread_id}" data-nav><span class="lt">${esc(t.title)}</span><small>${esc(ago(new Date(t.updated * 1000).toISOString()))}</small></a>`).join("") : `<div class="empty">Ask a question below to start one.</div>`}</div>
    <div id="chat"></div>`;
  $("#pj-save").onclick = async () => { await jpost(`/api/projects/${pid}`, { instructions: $("#pj-ins").value }, "PATCH"); toast("Instructions saved"); };
  $$("[data-del]").forEach((a) => (a.onclick = async (e) => { e.preventDefault(); await jfetch(`/api/projects/${pid}/files/${a.dataset.del}`, { method: "DELETE" }); showProject(pid); }));
  $("#pj-more").onclick = (e) => openMenu(e.currentTarget, [["rename", "Rename project"], ["delete", "Delete project", "and its files and threads"]], async (k) => {
    if (k === "rename") {
      const n = prompt("Project name", d.project.name);
      if (n && n.trim()) { await jpost(`/api/projects/${pid}`, { name: n }, "PATCH"); showProject(pid); refreshSidebar(); }
    } else if (confirm("Delete this project, its files and its threads?")) {
      await jfetch(`/api/projects/${pid}`, { method: "DELETE" }); refreshSidebar(); go("/home");
    }
  });
  refreshSidebar();
}

// ---------------------------------------------------------------- search overlay
$("#btn-search").onclick = async () => {
  $("#search").classList.remove("hidden");
  const q = $("#search-q"); q.value = ""; q.focus();
  let items = [];
  try {
    const [{ threads }, { runs }] = await Promise.all([jfetch("/api/threads?limit=200"), jfetch("/api/runs?limit=200")]);
    items = threads.map((t) => ({ title: t.title, href: `/c/${t.thread_id}`, tag: "thread", t: t.updated }))
      .concat(runs.map((r) => ({ title: r.title, href: `/runs/${r.run_id}`, tag: r.mode, t: r.created })));
  } catch { /* */ }
  const draw = () => {
    const needle = q.value.trim().toLowerCase();
    const hits = items.filter((x) => !needle || x.title.toLowerCase().includes(needle)).slice(0, 40);
    $("#search-res").innerHTML = `<div class="list">${hits.length ? hits.map((x) => `<a class="li" href="${x.href}" data-nav><span class="lt">${esc(x.title)}</span><small>${esc(x.tag)}</small></a>`).join("") : `<div class="empty">Nothing found.${needle ? ` <a href="/?q=${encodeURIComponent(needle)}" data-nav>Ask ODAR instead</a>` : ""}</div>`}</div>`;
    $$("#search-res a").forEach((a) => a.addEventListener("click", closeSearch));
  };
  q.oninput = draw; draw();
};
function closeSearch() { $("#search").classList.add("hidden"); }
$("#search-x").onclick = closeSearch;
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeSearch(); hidePop(); } });

// ---------------------------------------------------------------- history (runs)
async function showHistory() {
  composer(false);
  view.innerHTML = `<div class="sec-h"><h2>Run history</h2></div><div id="hist" class="list"><div class="empty">Loading…</div></div>`;
  const { runs } = await jfetch("/api/runs");
  $("#hist").innerHTML = runs.length ? runs.map((x) => `<a class="li" href="/runs/${x.run_id}" data-nav><span class="lt">${esc(x.title)}</span>
    <small>${esc(x.mode)} · <span class="pill ${x.status}">${x.status}</span> · ${new Date(x.created * 1000).toLocaleDateString()}</small></a>`).join("")
    : `<div class="empty">No Research, Check or References runs yet.</div>`;
  refreshSidebar();
}

// ---------------------------------------------------------------- settings / profile
async function showSettings() {
  composer(false);
  view.innerHTML = `<div class="sec-h"><h2>Profile &amp; settings</h2></div>
    <div class="card"><div class="card-h">Usage</div><div id="lim" class="note">Loading…</div></div>
    <div class="card"><div class="card-h">API keys</div>
      <p class="note">Use ODAR from scripts or the Chrome extension: send <code>Authorization: Bearer &lt;key&gt;</code>. Docs at <a href="/api/docs">/api/docs</a>.</p>
      <div id="keys" class="empty">Loading…</div>
      <div class="actions" style="margin-top:10px"><input class="field" id="klabel" placeholder="Label, e.g. chrome extension" maxlength="60" style="flex:1"><button class="btn primary" id="knew">Create key</button></div>
      <p id="kshow" class="rep hidden"></p></div>
    <div class="card"><div class="card-h">Privacy</div>
      <p class="note">Files attached to Check are read in memory and never stored. Project files are kept as text so you can ask about them, until you delete them. Share links are unlisted and not indexed. Threads and runs are deleted automatically after the retention period.</p>
      <button class="btn danger" id="forget">Delete all my threads, projects, runs and keys</button></div>`;
  const load = async () => {
    const { keys } = await jfetch("/api/keys");
    $("#keys").className = keys.length ? "" : "empty";
    $("#keys").innerHTML = keys.length ? `<table class="hist"><tr><th>Key</th><th>Label</th><th>Used today</th><th></th></tr>${keys.map((k) =>
      `<tr><td><code>${esc(k.prefix)}…</code></td><td>${esc(k.label)}</td><td>${k.used_today}/${k.daily_limit}</td><td><button class="btn" data-rev="${esc(k.prefix)}">Revoke</button></td></tr>`).join("")}</table>` : "No keys yet.";
    $$("[data-rev]").forEach((b) => (b.onclick = async () => { await jfetch(`/api/keys/${b.dataset.rev}`, { method: "DELETE" }); load(); }));
    const l = await jfetch("/api/limits");
    $("#lim").innerHTML = `Questions: ${l.ask_used_this_hour}/${l.ask_hourly_limit} this hour<br>Research/Check runs from your network: ${l.used_this_hour}/${l.hourly_limit} this hour, ${l.used_today}/${l.daily_limit} today<br>Kept for ${l.retention_days} days`;
  };
  $("#knew").onclick = async () => {
    const box = $("#kshow"); box.classList.remove("hidden");
    try { const d = await jpost("/api/keys", { label: $("#klabel").value }); box.innerHTML = `Your new key (shown once): <code>${esc(d.key)}</code>`; }
    catch (ex) { box.textContent = ex.message; }
    load();
  };
  $("#forget").onclick = async () => {
    if (!confirm("Delete every thread, project, run and API key tied to this browser? This can't be undone.")) return;
    await jfetch("/api/me", { method: "DELETE" }); go("/");
  };
  load().catch((ex) => ($("#lim").textContent = ex.message));
  refreshSidebar();
}

// ---------------------------------------------------------------- Research / Check / References runs
const STEPS = {
  research: [["Planning", /plan/], ["Searching and reading sources", /search|read|fill|decid|research/], ["Writing the report", /writ|audit/], ["Checking citations", /citation check|checking the report/], ["Done", /finished/]],
  check: [["Reading the answer", /reading the answer|translat|starting/], ["Splitting claims", /splitting/], ["Opening cited pages", /opening|wayback/], ["Matching claims to sources", /matching|judging|replacement|academic/], ["Done", /finished/]],
  references: [["Looking up each reference", /validat|starting/], ["Formatting", /format/], ["Done", /finished/]],
};
async function showRun({ id, token }) {
  composer(false);
  const api = id ? `/api/runs/${id}` : `/api/share/${token}`;
  view.innerHTML = `<div id="run"><div class="empty"><span class="spinner"></span>Loading…</div></div>`;
  let after = 0;
  const log = [];
  const tick = async () => {
    const r = await fetch(`${api}?after=${after}`);
    if (!r.ok) { $("#run").innerHTML = `<div class="empty">This report was not found.</div>`; return; }
    const { run, events } = await r.json();
    events.forEach((e) => { after = Math.max(after, e.seq); log.push(e); });
    renderRun(run, log, { id, token: token || run.share_token });
    if (run.status === "RUNNING" || run.status === "QUEUED") pollTimer = setTimeout(tick, 2000);
  };
  tick();
  refreshSidebar();
}
function describe(e) {
  const d = e.data || {};
  const bits = [d.verdict, d.state, d.url, d.query, d.claim, d.hits != null ? `${d.hits} results` : "", d.error].filter(Boolean);
  return `${esc(e.kind.replace(/^telemetry\./, ""))}${bits.length ? ": " + esc(bits.join(" · ").slice(0, 160)) : ""}`;
}
function progressCard(run, log) {
  const steps = STEPS[run.mode] || STEPS.check;
  const stage = String(run.stage || "").toLowerCase();
  let now = steps.findIndex(([, re]) => re.test(stage));
  if (now < 0) now = 0;
  return `<div class="card"><div class="card-h">${esc(MODES[run.mode] ? MODES[run.mode][0] : run.mode)} in progress <small>${esc(run.stage || "queued")}</small></div>
    <ol class="steps">${steps.map(([name], i) => `<li class="${i < now ? "done" : i === now ? "now" : ""}">${esc(name)}</li>`).join("")}</ol>
    <div class="log">${log.slice(-30).map((e) => `<div>${describe(e)}</div>`).join("")}</div>
    <div class="meta">You can leave this page; it keeps running.</div></div>`;
}
function renderRun(run, log, ctx) {
  const live = run.status === "RUNNING" || run.status === "QUEUED";
  const done = run.status === "COMPLETE";
  const base = ctx.id ? `/api/runs/${ctx.id}` : `/api/share/${ctx.token}`;
  const ask = run.mode === "research" ? run.title : run.input_text || run.title;
  let html = `<div class="thread-title"><h1>${esc(run.title)}</h1><div class="actions">${done ? ["md", "docx", "pdf"].map((f) => `<a class="btn" href="${base}/export.${f}">${f.toUpperCase()}</a>`).join("") : ""}
    <button class="btn" id="share">Share</button>${ctx.id ? `<button class="btn" id="del">Delete</button>` : ""}</div></div>
    <div class="row me"><div class="bubble">${esc(String(ask).slice(0, 600))}${String(ask).length > 600 ? "…" : ""}</div></div>`;
  if (live) html += progressCard(run, log);
  if (run.status === "FAILED") html += `<div class="row bot"><div class="bubble err">This run failed: ${esc(run.error || "unknown error")}</div></div>`;
  if (done && run.result) {
    const body = run.mode === "check" ? renderCheck(run.result) : run.mode === "references" ? renderReferences(run.result) : renderResearch(run.result);
    html += `<div class="row bot"><div class="card">${body}</div></div>`;
  }
  if (done && ctx.id) html += `<div class="card"><div class="card-h">Ask about this report</div><div id="fups"></div>
    <form id="askf" class="actions"><input class="field" name="q" maxlength="500" placeholder="e.g. Which claims had no real source? / कौन से दावे गलत थे?" required style="flex:1"><button class="btn primary">Ask</button></form>
    <p class="meta">Answers come only from this report; if it isn't covered, ODAR says so.</p></div>`;
  $("#run").innerHTML = html;
  if (done && ctx.id) setupFollowups(ctx.id);
  $("#share").onclick = () => copyLink(`${location.origin}/r/${ctx.token}`);
  const del = $("#del");
  if (del) del.onclick = async () => {
    if (!confirm("Delete this run from your history?")) return;
    await jfetch(`/api/runs/${ctx.id}`, { method: "DELETE" }); go("/history");
  };
}
async function setupFollowups(id) {
  const show = (items) => { $("#fups").innerHTML = items.map((f) => `<div class="fup"><div class="row me"><div class="bubble">${esc(f.question)}</div></div><div class="row bot"><div class="bubble ${f.abstained ? "note" : ""}">${esc(f.answer || f.error || "")}</div></div></div>`).join(""); };
  const r = await fetch(`/api/runs/${id}/followups`);
  const items = r.ok ? (await r.json()).followups : [];
  show(items);
  $("#askf").onsubmit = async (e) => {
    e.preventDefault();
    const q = e.target.q.value.trim(); if (!q) return;
    const btn = e.target.querySelector("button"); btn.disabled = true; btn.textContent = "Thinking…";
    try { const d = await jpost(`/api/runs/${id}/ask`, { question: q }); items.push({ question: q, ...d }); }
    catch (ex) { items.push({ question: q, answer: ex.message, abstained: true }); }
    show(items); e.target.q.value = ""; btn.disabled = false; btn.textContent = "Ask";
  };
}
function vclass(v) { return "v-" + String(v).split(" ")[0]; }
function renderClaims(result) {
  return (result.claims || []).map((c, i) => `<div class="claim"><span class="v ${vclass(c.verdict)}">${esc(c.verdict)}</span>
    <div class="text">${i + 1}. ${esc(c.claim)}${c.confidence ? ` <span class="conf ${esc(c.confidence)}">${esc(c.confidence)} confidence</span>` : ""}</div>
    ${c.freshness ? `<div class="warn">⚠ ${esc(c.freshness)}</div>` : ""}
    ${(c.citations || []).map((t) => `<div class="cit">${esc(t.marker || "link")}
      ${t.url ? `<a class="url" href="${esc(safeUrl(t.url))}" target="_blank" rel="noopener noreferrer">${esc(t.url)}</a>` : "<i>no reference entry</i>"}
      ${t.source_type ? `<span class="src-type ${esc(t.source_type)}">${esc(t.source_type)}</span>` : ""}${t.source_year ? `<span class="note"> · ${t.source_year}</span>` : ""}
      <span class="note"> · ${esc(t.verdict)} · link ${esc(t.link_state || "n/a")}</span>
      ${t.quote ? `<blockquote>“${esc(t.quote)}”</blockquote>` : ""}${t.note ? `<div class="note">${esc(t.note)}</div>` : ""}</div>`).join("")}
    ${c.replacement ? `<div class="rep">Suggested source${c.replacement.source_type ? ` <span class="src-type ${esc(c.replacement.source_type)}">${esc(c.replacement.source_type)}</span>` : ""}: <a href="${esc(safeUrl(c.replacement.url))}" target="_blank" rel="noopener noreferrer">${esc(c.replacement.title || c.replacement.url)}</a><blockquote>“${esc(c.replacement.quote)}”</blockquote>${c.replacement.citation ? `<div class="note">${esc(c.replacement.citation)}</div>` : ""}</div>`
      : c.replacement_searched ? `<div class="note">No supporting replacement source found.</div>` : ""}
    ${c.note ? `<div class="note">${esc(c.note)}</div>` : ""}</div>`).join("") || `<div class="empty">No claims found.</div>`;
}
function renderCheck(r) {
  const score = r.trust_score == null ? "n/a" : r.trust_score;
  const chips = Object.entries(r.counts || {}).filter(([, v]) => v).map(([k, v]) => `<span class="chip small">${esc(k)}: ${v}</span>`).join("");
  return `<div class="score"><b>${score}</b><span>trust score${r.trust_score == null ? " (no checkable citations)" : "/100"} · ${esc(r.grade || "")}</span></div>
    ${r.translated ? `<p class="note">Checked an English translation of your Hindi text.</p>` : ""}
    ${r.confidence === "low" ? `<p class="warn">Low confidence: too many sources couldn't be read to give a firm score.</p>` : ""}
    <div class="chips">${chips}</div>${renderClaims(r)}
    ${(r.references || []).length ? `<h3>Reference check</h3>${renderRefs(r.references)}` : ""}`;
}
function renderRefs(refs) {
  return refs.map((x, i) => `<div class="claim"><span class="v ${vclass(x.status)}">${esc(x.status)}</span>
    <div class="text">${i + 1}. ${esc(x.raw)}</div>
    ${(x.problems || []).map((p) => `<div class="note">${esc(p)}</div>`).join("")}
    ${x.matched ? `<div class="cit">Found: <a href="${esc(safeUrl(x.matched.link))}" target="_blank" rel="noopener noreferrer">${esc(x.matched.title)}</a>
      <span class="src-type ${x.matched.peer_reviewed ? "peer-reviewed" : "preprint"}">${x.matched.peer_reviewed ? "peer-reviewed" : esc(x.matched.kind || "record")}</span>
      ${x.formatted ? `<div class="note">${esc(x.formatted)}</div>` : ""}</div>` : ""}
    ${(x.suggestions || []).length ? `<div class="rep">Real papers you could cite instead:<ul>${x.suggestions.map((p) => `<li><a href="${esc(safeUrl(p.link))}" target="_blank" rel="noopener noreferrer">${esc(p.title)}</a> (${esc(p.year || "n.d.")})</li>`).join("")}</ul></div>` : ""}
  </div>`).join("") || `<div class="empty">No references found.</div>`;
}
function renderReferences(r) {
  const chips = Object.entries(r.counts || {}).map(([k, v]) => `<span class="chip small">${esc(k)}: ${v}</span>`).join("");
  return `<div class="chips">${chips}</div>${renderRefs(r.references)}
    ${(r.bibliography || []).length ? `<h3>Bibliography (${esc(String(r.style).toUpperCase())})</h3><ol class="bib">${r.bibliography.map((b) => `<li>${esc(b)}</li>`).join("")}</ol>` : ""}`;
}
function md(src) {
  const inline = (t) => esc(t)
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (m, a, u) => `<a href="${u}" target="_blank" rel="noopener noreferrer">${a}</a>`)
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, (m, p, u) => `${p}<a href="${u}" target="_blank" rel="noopener noreferrer">${u}</a>`);
  let out = "", list = false;
  for (const raw of String(src || "").split("\n")) {
    const line = raw.trimEnd();
    const li = line.match(/^\s*(?:[-*]|\d+\.)\s+(.*)/);
    if (list && !li) { out += "</ul>"; list = false; }
    if (!line.trim()) continue;
    const h = line.match(/^(#{1,4})\s+(.*)/);
    if (h) out += `<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`;
    else if (li) { if (!list) { out += "<ul>"; list = true; } out += `<li>${inline(li[1])}</li>`; }
    else if (/^---+$/.test(line)) out += "<hr>";
    else out += `<p>${inline(line)}</p>`;
  }
  return out + (list ? "</ul>" : "");
}
function renderResearch(r) {
  let html = String(r.uncertainty).toUpperCase() === "HIGH" ? `<p class="warn">ODAR found limited verified evidence for this question; treat the report as a starting point, not an answer.</p>` : "";
  html += `<div class="report" lang="${r.language === "hi" ? "hi" : "en"}">${md(r.synthesis || "(no report was produced)")}</div>
    <p class="meta">status ${esc(r.status)} · uncertainty ${esc(r.uncertainty)}</p>`;
  if (r.citation_check) html += `<h3>Citation check</h3>${renderCheck(r.citation_check)}`;
  return html;
}

route();
