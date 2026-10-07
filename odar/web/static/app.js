"use strict";
const $ = (s, el = document) => el.querySelector(s);
const view = $("#view");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");
let pollTimer = null;

const PLACEHOLDER = {
  research: "Ask a research question, e.g. What does the evidence say about intermittent fasting for weight loss?",
  check: "Paste an AI answer or essay with its links or numbered references, e.g. from ChatGPT or Perplexity.",
  references: "Paste a reference list (APA, MLA, Chicago, IEEE, or DOIs), one reference per line.",
};
const HINT = {
  research: "Deep research takes about 5 to 10 minutes on free models. You can leave this page; it keeps running.",
  check: "A check takes about 1 to 3 minutes, depending on how many sources are cited.",
  references: "Each reference is looked up in Crossref, PubMed, arXiv and OpenAlex. Takes under a minute.",
};

// ---------------------------------------------------------------- routing
function go(path) { history.pushState({}, "", path); route(); }
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[data-nav]");
  if (a && !e.metaKey && !e.ctrlKey) { e.preventDefault(); go(a.getAttribute("href")); }
});
window.addEventListener("popstate", route);

function route() {
  clearTimeout(pollTimer);
  const p = location.pathname;
  let m;
  if ((m = p.match(/^\/runs\/([\w-]+)/))) return showRun({ id: m[1] });
  if ((m = p.match(/^\/r\/([\w-]+)/))) return showRun({ token: m[1] });
  if (p === "/history") return showHistory();
  if (p === "/settings") return showSettings();
  return showNew();
}

// ---------------------------------------------------------------- new run
function showNew() {
  view.innerHTML = "";
  view.append($("#tpl-new").content.cloneNode(true));
  const form = $("#run-form");
  let mode = "research", src = "text";
  const sync = () => {
    document.querySelectorAll(".mode").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
    document.querySelectorAll(".src").forEach((b) => b.classList.toggle("active", b.dataset.src === src));
    document.querySelectorAll(".pane").forEach((p) => p.classList.toggle("hidden", p.dataset.pane !== src));
    document.querySelectorAll(".opts label").forEach((l) => l.classList.toggle("hidden", !l.dataset.for.split(" ").includes(mode)));
    form.text.placeholder = PLACEHOLDER[mode];
    $("#hint").textContent = HINT[mode];
  };
  document.querySelectorAll(".mode").forEach((b) => (b.onclick = () => { mode = b.dataset.mode; sync(); }));
  document.querySelectorAll(".src").forEach((b) => (b.onclick = () => { src = b.dataset.src; sync(); }));
  sync();
  form.onsubmit = async (e) => {
    e.preventDefault();
    const err = $("#form-err");
    err.classList.add("hidden");
    const fd = new FormData();
    fd.append("mode", mode);
    if (src === "text") fd.append("text", form.text.value);
    if (src === "url") fd.append("url", form.url.value);
    if (src === "file" && form.file.files[0]) fd.append("file", form.file.files[0]);
    fd.append("verify", form.verify.checked);
    fd.append("use_llm", form.use_llm.checked);
    fd.append("keep_input", form.keep_input.checked);
    fd.append("academic", form.academic.checked);
    fd.append("style", form.style.value);
    fd.append("language", form.language.value);
    const btn = $(".go", form);
    btn.disabled = true;
    try {
      const r = await fetch("/api/runs", { method: "POST", body: fd });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || "something went wrong");
      go(`/runs/${data.run_id}`);
    } catch (ex) {
      err.textContent = ex.message;
      err.classList.remove("hidden");
      btn.disabled = false;
    }
  };
}

// ---------------------------------------------------------------- history
async function showHistory() {
  view.innerHTML = `<section class="card"><h2>History</h2><div id="hist" class="empty">Loading…</div></section>`;
  const r = await fetch("/api/runs");
  const { runs } = await r.json();
  const box = $("#hist");
  if (!runs.length) { box.innerHTML = `No runs yet. <a href="/" data-nav>Start one</a>.`; return; }
  box.className = "";
  box.innerHTML = `<table class="hist"><tr><th>Run</th><th>Mode</th><th>Status</th><th>When</th></tr>${runs
    .map((x) => `<tr><td><a href="/runs/${x.run_id}" data-nav>${esc(x.title)}</a></td><td class="tag">${x.mode}</td>
      <td><span class="pill ${x.status}">${x.status}</span></td><td class="meta">${new Date(x.created * 1000).toLocaleString()}</td></tr>`)
    .join("")}</table>`;
}

// ---------------------------------------------------------------- settings
async function showSettings() {
  view.innerHTML = `<section class="card"><h2>API keys</h2>
    <p class="hint">Use ODAR from scripts or the Chrome extension: send <code>Authorization: Bearer &lt;key&gt;</code> to <code>/api/runs</code>. Docs at <a href="/api/docs">/api/docs</a>.</p>
    <div id="keys" class="empty">Loading…</div>
    <p><input id="klabel" placeholder="Label, e.g. chrome extension" maxlength="60"> <button id="knew">Create key</button></p>
    <p id="kshow" class="rep hidden"></p>
    <h2>Privacy</h2><div id="lim" class="hint"></div>
    <p class="hint">Uploaded files are read in memory and never stored. Checked text is kept only if you tick "Keep". Share links are unlisted and not indexed by search engines.</p>
    <button id="forget">Delete all my runs and keys</button></section>`;
  const load = async () => {
    const { keys } = await (await fetch("/api/keys")).json();
    $("#keys").className = keys.length ? "" : "empty";
    $("#keys").innerHTML = keys.length ? `<table class="hist"><tr><th>Key</th><th>Label</th><th>Used today</th><th></th></tr>${keys.map((k) =>
      `<tr><td><code>${esc(k.prefix)}…</code></td><td>${esc(k.label)}</td><td>${k.used_today}/${k.daily_limit}</td><td><button data-rev="${esc(k.prefix)}">Revoke</button></td></tr>`).join("")}</table>` : "No keys yet.";
    document.querySelectorAll("[data-rev]").forEach((b) => b.onclick = async () => { await fetch(`/api/keys/${b.dataset.rev}`, { method: "DELETE" }); load(); });
    const l = await (await fetch("/api/limits")).json();
    $("#lim").textContent = `Runs from your network: ${l.used_this_hour}/${l.hourly_limit} this hour, ${l.used_today}/${l.daily_limit} today. Runs are deleted automatically after ${l.retention_days} days.`;
  };
  $("#knew").onclick = async () => {
    const r = await fetch("/api/keys", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ label: $("#klabel").value }) });
    const d = await r.json();
    const box = $("#kshow"); box.classList.remove("hidden");
    box.innerHTML = r.ok ? `Your new key (shown once): <code>${esc(d.key)}</code>` : esc(d.detail || "Could not create a key");
    load();
  };
  $("#forget").onclick = async () => {
    if (!confirm("Delete every run and API key tied to this browser? This can't be undone.")) return;
    await fetch("/api/me", { method: "DELETE" }); go("/");
  };
  load();
}

// ---------------------------------------------------------------- run view
async function showRun({ id, token }) {
  const api = id ? `/api/runs/${id}` : `/api/share/${token}`;
  view.innerHTML = `<section class="card" id="run"><div class="empty">Loading…</div></section>`;
  let after = 0, log = [];
  const tick = async () => {
    const r = await fetch(`${api}?after=${after}`);
    if (!r.ok) { $("#run").innerHTML = `<div class="empty">This report was not found.</div>`; return; }
    const { run, events } = await r.json();
    events.forEach((e) => { after = Math.max(after, e.seq); log.push(e); });
    renderRun(run, log, { id, token: token || run.share_token, api });
    if (run.status === "RUNNING" || run.status === "QUEUED") pollTimer = setTimeout(tick, 2000);
  };
  tick();
}

function describe(e) {
  const d = e.data || {};
  const bits = [d.verdict, d.state, d.url, d.query, d.claim, d.hits != null ? `${d.hits} results` : "", d.error].filter(Boolean);
  return `${esc(e.kind.replace(/^telemetry\./, ""))}${bits.length ? ": " + esc(bits.join(" · ").slice(0, 160)) : ""}`;
}

function renderRun(run, log, ctx) {
  const live = run.status === "RUNNING" || run.status === "QUEUED";
  const base = ctx.id ? `/api/runs/${ctx.id}` : `/api/share/${ctx.token}`;
  const shareUrl = `${location.origin}/r/${ctx.token}`;
  const done = run.status === "COMPLETE";
  let html = `<div class="runhead"><div><span class="tag">${run.mode}</span><h1>${esc(run.title)}</h1>
    <div class="meta"><span class="pill ${run.status}">${run.status}</span> ${new Date(run.created * 1000).toLocaleString()}
    ${run.input_meta && run.input_meta.source ? " · from " + esc(run.input_meta.source) : ""}</div></div>
    <div class="actions">${done ? ["md", "docx", "pdf"].map((f) => `<a href="${base}/export.${f}">${f.toUpperCase()}</a>`).join("") : ""}
    <button id="share">Copy share link</button>${ctx.id ? `<button id="del">Delete</button>` : ""}</div></div>`;
  if (live) html += `<div class="stage"><span class="spinner"></span>${esc(run.stage || "working")}</div>`;
  if (run.status === "FAILED") html += `<p class="err">This run failed: ${esc(run.error || "unknown error")}</p>`;
  if (live || !done) html += `<div class="progress">${log.slice(-40).map((e) => `<div>${describe(e)}</div>`).join("")}</div>`;
  if (done && run.result) html += run.mode === "check" ? renderCheck(run.result) : run.mode === "references" ? renderReferences(run.result) : renderResearch(run.result);
  if (done && ctx.id) html += `<div class="ask"><h2>Ask about this report</h2><div id="fups"></div>
    <form id="askf"><input name="q" maxlength="500" placeholder="e.g. Which claims had no real source? / कौन से दावे गलत थे?" required><button>Ask</button></form>
    <p class="hint">Answers come only from this report; if it isn't covered, ODAR says so.</p></div>`;
  $("#run").innerHTML = html;
  if (done && ctx.id) setupAsk(ctx.id);
  $("#share").onclick = async () => {
    try { await navigator.clipboard.writeText(shareUrl); $("#share").textContent = "Link copied"; }
    catch { prompt("Share link", shareUrl); }
  };
  const del = $("#del");
  if (del) del.onclick = async () => {
    if (!confirm("Delete this run from your history?")) return;
    await fetch(`/api/runs/${ctx.id}`, { method: "DELETE" });
    go("/history");
  };
}

async function setupAsk(id) {
  const show = (items) => { $("#fups").innerHTML = items.map((f) => `<div class="fup"><b>Q: ${esc(f.question)}</b><div class="${f.abstained ? "note" : ""}">${esc(f.answer || f.error || "")}</div></div>`).join(""); };
  const r = await fetch(`/api/runs/${id}/followups`);
  let items = r.ok ? (await r.json()).followups : [];
  show(items);
  $("#askf").onsubmit = async (e) => {
    e.preventDefault();
    const q = e.target.q.value.trim(); if (!q) return;
    const btn = e.target.querySelector("button"); btn.disabled = true; btn.textContent = "Thinking…";
    const res = await fetch(`/api/runs/${id}/ask`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: q }) });
    const d = await res.json();
    items.push(res.ok ? { question: q, ...d } : { question: q, answer: d.detail || "Failed", abstained: true });
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
  const chips = Object.entries(r.counts || {}).filter(([, v]) => v).map(([k, v]) => `<span class="chip">${esc(k)}: ${v}</span>`).join("");
  return `<div class="score"><b>${score}</b><span>trust score${r.trust_score == null ? " (no checkable citations)" : "/100"} · ${esc(r.grade || "")}</span></div>
    ${r.translated ? `<p class="note">Checked an English translation of your Hindi text.</p>` : ""}
    ${r.confidence === "low" ? `<p class="warn">Low confidence: too many sources couldn't be read to give a firm score.</p>` : ""}
    <div class="chips">${chips}</div>${renderClaims(r)}
    ${(r.references || []).length ? `<h2>Reference check</h2>${renderRefs(r.references)}` : ""}`;
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
  const chips = Object.entries(r.counts || {}).map(([k, v]) => `<span class="chip">${esc(k)}: ${v}</span>`).join("");
  return `<div class="chips">${chips}</div>${renderRefs(r.references)}
    ${(r.bibliography || []).length ? `<h2>Bibliography (${esc(String(r.style).toUpperCase())})</h2><ol class="bib">${r.bibliography.map((b) => `<li>${esc(b)}</li>`).join("")}</ol>` : ""}`;
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
  if (r.citation_check) html += `<h2>Citation check</h2>${renderCheck(r.citation_check)}`;
  return html;
}

route();
