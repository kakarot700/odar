// Research / Check / References runs, rendered as cards: a live work card while the run
// is going, then a summary card (report), a trust card (check) or a references card.
import { $, esc, safeUrl, ICON, jfetch, jpost, openSheet, copyLink } from "./core.js";
import { renderMd, splitTitle } from "./md.js";

export const MODE_LABEL = { research: "Deep research", check: "Citation check", references: "Reference check" };
const STEPS = {
  research: [["Planning the research", /plan|start|transl/], ["Searching and reading sources", /search|read|fill|decid|research|round/], ["Writing the report", /writ|audit/], ["Checking every citation", /citation|checking the report/], ["Report ready", /finished/]],
  check: [["Reading the answer", /reading the answer|translat|start/], ["Splitting it into claims", /splitting/], ["Opening each cited page", /opening|wayback/], ["Matching claims to sources", /matching|judging|replacement|academic/], ["Check ready", /finished/]],
  references: [["Looking up each reference", /validat|start/], ["Formatting the bibliography", /format/], ["Done", /finished/]],
};

// Active pollers, cleared on every route change.
const pollers = new Set();
export function stopPolling() { pollers.forEach((p) => clearTimeout(p.timer)); pollers.clear(); }

export function trustTone(score) { return score == null ? "none" : score >= 80 ? "good" : score >= 50 ? "mid" : "low"; }
export function trustBadge(score, label = "Trust") {
  if (score == null) return "";
  return `<span class="trust ${trustTone(score)}" title="Share of cited claims that their sources support">${ICON.shield}<b>${score}</b><span>${esc(label)}</span></span>`;
}

function describe(e) {
  const d = e.data || {};
  const kind = e.kind.replace(/^telemetry\./, "").replace(/_/g, " ");
  const bit = d.query ? `“${d.query}”` : d.url ? d.url.replace(/^https?:\/\/(www\.)?/, "").slice(0, 60) : d.claim ? d.claim.slice(0, 80) : d.verdict || d.state || (d.hits != null ? `${d.hits} results` : "") || d.error || "";
  return `${esc(kind)}${bit ? ` · <span>${esc(bit)}</span>` : ""}`;
}

function elapsed(run) {
  const s = Math.max(0, Date.now() / 1000 - (run.created || Date.now() / 1000));
  return run.status === "QUEUED" ? "queued" : s < 60 ? `${Math.round(s)}s` : `${Math.floor(s / 60)} min`;
}
function workCard(run, log) {
  const steps = STEPS[run.mode] || STEPS.check;
  const stage = String(run.stage || "").toLowerCase();
  let now = steps.findIndex(([, re]) => re.test(stage));
  if (now < 0) now = 0;
  const reads = log.filter((e) => /fetch|opening|read/.test(e.kind) && e.data && e.data.url).length;
  return `<div class="card work live">
    <div class="work-h"><span class="pulse"></span><b>${esc(MODE_LABEL[run.mode] || run.mode)}</b><small>${esc(elapsed(run))}</small></div>
    <ol class="steps">${steps.map(([name], i) => `<li class="${i < now ? "done" : i === now ? "now" : ""}"><i></i><span>${esc(name)}${i === 1 && reads && i <= now ? ` <small>${reads} pages</small>` : ""}</span></li>`).join("")}</ol>
    <div class="log">${log.slice(-4).map((e) => `<div>${describe(e)}</div>`).join("")}</div>
    <div class="meta">Runs in the background. You can leave and come back.</div></div>`;
}

function vclass(v) { return "v-" + String(v).split(" ")[0].toLowerCase(); }
function renderClaims(result) {
  return (result.claims || []).map((c, i) => `<details class="claim"><summary><span class="v ${vclass(c.verdict)}">${esc(c.verdict)}</span><span class="text">${i + 1}. ${esc(c.claim)}</span></summary>
    ${c.confidence ? `<div class="note">${esc(c.confidence)} confidence</div>` : ""}
    ${c.freshness ? `<div class="warn">${esc(c.freshness)}</div>` : ""}
    ${(c.citations || []).map((x) => `<div class="cit">${esc(x.marker || "link")}
      ${x.url ? `<a href="${esc(safeUrl(x.url))}" target="_blank" rel="noopener noreferrer">${esc(x.url.replace(/^https?:\/\/(www\.)?/, "").slice(0, 70))}</a>` : "<i>no reference entry</i>"}
      ${x.source_type ? `<span class="tag">${esc(x.source_type)}</span>` : ""}${x.source_year ? `<span class="note"> · ${esc(x.source_year)}</span>` : ""}
      <span class="note"> · ${esc(x.verdict)} · link ${esc(x.link_state || "n/a")}</span>
      ${x.quote ? `<blockquote>“${esc(x.quote)}”</blockquote>` : ""}${x.note ? `<div class="note">${esc(x.note)}</div>` : ""}</div>`).join("")}
    ${c.replacement ? `<div class="rep">Suggested source: <a href="${esc(safeUrl(c.replacement.url))}" target="_blank" rel="noopener noreferrer">${esc(c.replacement.title || c.replacement.url)}</a>${c.replacement.quote ? `<blockquote>“${esc(c.replacement.quote)}”</blockquote>` : ""}${c.replacement.citation ? `<div class="note">${esc(c.replacement.citation)}</div>` : ""}</div>`
      : c.replacement_searched ? `<div class="note">No supporting replacement source found.</div>` : ""}
    ${c.note ? `<div class="note">${esc(c.note)}</div>` : ""}</details>`).join("") || `<div class="empty">No claims found.</div>`;
}

function checkBody(r) {
  const chips = Object.entries(r.counts || {}).filter(([, v]) => v).map(([k, v]) => `<span class="pill ${vclass(k)}">${esc(k)} ${v}</span>`).join("");
  return `<div class="trust-row">${r.trust_score == null ? `<span class="note">No checkable citations</span>` : trustBadge(r.trust_score, r.grade || "trust score")}</div>
    ${r.translated ? `<p class="note">Checked an English translation of your Hindi text.</p>` : ""}
    ${r.confidence === "low" ? `<p class="warn">Low confidence: too many sources couldn't be read for a firm score.</p>` : ""}
    <div class="pills">${chips}</div><div class="claims">${renderClaims(r)}</div>
    ${(r.references || []).length ? `<h4>Reference check</h4>${refsBody(r.references)}` : ""}`;
}
function refsBody(refs) {
  return refs.map((x, i) => `<details class="claim"><summary><span class="v ${vclass(x.status)}">${esc(x.status)}</span><span class="text">${i + 1}. ${esc(x.raw)}</span></summary>
    ${(x.problems || []).map((p) => `<div class="note">${esc(p)}</div>`).join("")}
    ${x.matched ? `<div class="cit">Found: <a href="${esc(safeUrl(x.matched.link))}" target="_blank" rel="noopener noreferrer">${esc(x.matched.title)}</a>
      <span class="tag">${x.matched.peer_reviewed ? "peer-reviewed" : esc(x.matched.kind || "record")}</span>${x.formatted ? `<div class="note">${esc(x.formatted)}</div>` : ""}</div>` : ""}
    ${(x.suggestions || []).length ? `<div class="rep">Real papers you could cite instead:<ul>${x.suggestions.map((p) => `<li><a href="${esc(safeUrl(p.link))}" target="_blank" rel="noopener noreferrer">${esc(p.title)}</a> (${esc(p.year || "n.d.")})</li>`).join("")}</ul></div>` : ""}
  </details>`).join("") || `<div class="empty">No references found.</div>`;
}

function exportsRow(base, ctx) {
  return `<div class="card-actions">${["md", "pdf", "docx"].map((f) => `<a class="btn sm" href="${base}/export.${f}" download>${ICON.download}${f.toUpperCase()}</a>`).join("")}
    <button class="btn sm" data-share>${ICON.share}Share</button>${ctx.id ? `<button class="btn sm" data-ask-report>Ask about it</button>` : ""}</div>`;
}

function resultCard(run, ctx) {
  const r = run.result || {};
  const base = ctx.id ? `/api/runs/${ctx.id}` : `/api/share/${ctx.token}`;
  if (run.mode === "research") {
    const { title, body } = splitTitle(r.synthesis || "");
    const score = r.citation_check ? r.citation_check.trust_score : null;
    const high = String(r.uncertainty).toUpperCase() === "HIGH";
    return `<div class="card summary">
      <div class="summary-h">${ICON.doc}<div><div class="eyebrow">Report</div><h3>${esc(title || run.title)}</h3></div>${trustBadge(score)}</div>
      ${high ? `<p class="warn">Limited verified evidence: treat this as a starting point.</p>` : ""}
      <div class="report collapsed" lang="${r.language === "hi" ? "hi" : "en"}">${renderMd(body || "(no report was produced)")}</div>
      <button class="more-btn" data-expand>${ICON.chev}<span>Read the full report</span></button>
      ${exportsRow(base, ctx)}
      ${r.citation_check ? `<details class="sub"><summary>Citation check · ${esc(r.citation_check.grade || "")}</summary>${checkBody(r.citation_check)}</details>` : ""}
      <div class="meta">uncertainty ${esc(r.uncertainty || "n/a")} · ${esc(r.status || "")}</div></div>`;
  }
  if (run.mode === "check") {
    return `<div class="card summary"><div class="summary-h">${ICON.shield}<div><div class="eyebrow">Citation check</div><h3>${esc(run.title)}</h3></div></div>${checkBody(r)}${exportsRow(base, ctx)}</div>`;
  }
  const chips = Object.entries(r.counts || {}).map(([k, v]) => `<span class="pill ${vclass(k)}">${esc(k)} ${v}</span>`).join("");
  return `<div class="card summary"><div class="summary-h">${ICON.doc}<div><div class="eyebrow">Reference check</div><h3>${esc(run.title)}</h3></div></div>
    <div class="pills">${chips}</div><div class="claims">${refsBody(r.references || [])}</div>
    ${(r.bibliography || []).length ? `<h4>Bibliography (${esc(String(r.style || "").toUpperCase())})</h4><ol class="bib">${r.bibliography.map((b) => `<li>${esc(b)}</li>`).join("")}</ol>` : ""}
    ${exportsRow(base, ctx)}</div>`;
}

function wire(el, run, ctx) {
  const ex = $("[data-expand]", el);
  if (ex) ex.onclick = () => {
    const rep = $(".report", el);
    const open = rep.classList.toggle("collapsed") === false;
    ex.classList.toggle("open", open);
    $("span", ex).textContent = open ? "Show less" : "Read the full report";
  };
  const sh = $("[data-share]", el);
  if (sh) sh.onclick = () => copyLink(`${location.origin}/r/${ctx.token || run.share_token}`, "Share link copied");
  const ask = $("[data-ask-report]", el);
  if (ask) ask.onclick = () => reportFollowups(ctx.id, run.title);
}

// Follow-up questions answered only from a finished report (bottom sheet).
export async function reportFollowups(id, title) {
  const body = openSheet(`<p class="note">Answers come only from this report; if it isn't covered, ODAR says so.</p>
    <div id="fups" class="fups"></div>
    <form id="fupf" class="inline-form"><input class="field" name="q" maxlength="500" placeholder="e.g. Which claims had no real source?" required><button class="btn primary">Ask</button></form>`, { title: `Ask about: ${title}`.slice(0, 80), tall: true });
  const items = [];
  const draw = () => {
    $("#fups", body).innerHTML = items.map((f) => `<div class="row me"><div class="bubble">${esc(f.question)}</div></div><div class="row bot"><div class="bubble ${f.abstained ? "muted" : ""}">${esc(f.answer || f.error || "")}</div></div>`).join("");
  };
  try { items.push(...(await jfetch(`/api/runs/${id}/followups`)).followups); } catch { /* none */ }
  draw();
  $("#fupf", body).onsubmit = async (e) => {
    e.preventDefault();
    const q = e.target.q.value.trim(); if (!q) return;
    const btn = $("button", e.target); btn.disabled = true; btn.textContent = "…";
    try { items.push({ question: q, ...(await jpost(`/api/runs/${id}/ask`, { question: q })) }); }
    catch (ex) { items.push({ question: q, answer: ex.message, abstained: true }); }
    e.target.q.value = ""; btn.disabled = false; btn.textContent = "Ask"; draw();
  };
}

// Mount a run card into ``el`` and keep it updated until the run finishes.
export function mountRun(el, { id, token }, { onDone } = {}) {
  const api = id ? `/api/runs/${id}` : `/api/share/${token}`;
  const log = [];
  let after = 0;
  const poller = { timer: 0 };
  pollers.add(poller);
  el.innerHTML = `<div class="card work live"><div class="work-h"><span class="pulse"></span><b>Loading…</b></div></div>`;
  const tick = async () => {
    let data;
    try { data = await jfetch(`${api}?after=${after}`); }
    catch (ex) {
      if (ex.status === 404) { el.innerHTML = `<div class="bubble muted">This run is no longer available.</div>`; pollers.delete(poller); return; }
      poller.timer = setTimeout(tick, 4000); return;
    }
    const { run, events } = data;
    events.forEach((e) => { after = Math.max(after, e.seq); log.push(e); });
    const ctx = { id, token: token || run.share_token };
    const live = run.status === "RUNNING" || run.status === "QUEUED";
    if (live) el.innerHTML = workCard(run, log);
    else if (run.status === "FAILED") el.innerHTML = `<div class="bubble err">This ${esc(MODE_LABEL[run.mode] || "run")} failed: ${esc(run.error || "unknown error")}</div>`;
    else if (run.result) { el.innerHTML = resultCard(run, ctx); wire(el, run, ctx); }
    if (live && pollers.has(poller)) poller.timer = setTimeout(tick, 2000);
    else { pollers.delete(poller); if (onDone) onDone(run); }
  };
  tick();
}

// Standalone run page (/runs/:id, /r/:token: share links and the Chrome extension land here).
export async function showRunPage(view, { id, token }) {
  view.innerHTML = `<div class="page-h"><a class="icon-btn sm" href="/" data-nav aria-label="Back to chat">${ICON.back}</a><h1 id="run-title">Report</h1></div>
    <div class="thread"><div class="row me hidden" id="run-q"><div class="bubble"></div></div><div class="row bot wide"><div class="turn" id="run-card"></div></div></div>`;
  mountRun($("#run-card", view), { id, token }, {
    onDone: (run) => {
      $("#run-title").textContent = run.title;
      const q = run.mode === "research" ? run.title : run.input_text || run.title;
      $("#run-q .bubble").textContent = String(q).slice(0, 600);
      $("#run-q").classList.remove("hidden");
    },
  });
  // first paint title even while running
  try {
    const { run } = await jfetch(id ? `/api/runs/${id}` : `/api/share/${token}`);
    $("#run-title").textContent = run.title;
    const q = run.mode === "research" ? run.title : run.input_text || run.title;
    $("#run-q .bubble").textContent = String(q).slice(0, 600);
    $("#run-q").classList.remove("hidden");
  } catch { /* shown by the card */ }
}

export async function showHistoryPage(view) {
  view.innerHTML = `<div class="page-h"><a class="icon-btn sm" href="/home" data-nav aria-label="Back">${ICON.back}</a><h1>Run history</h1></div><div id="hist" class="list"><div class="empty">Loading…</div></div>`;
  try {
    const { runs } = await jfetch("/api/runs");
    $("#hist").innerHTML = runs.length ? runs.map((x) => `<a class="li" href="/runs/${x.run_id}" data-nav><span class="li-ic">${x.mode === "research" ? ICON.doc : ICON.shield}</span><span class="lt">${esc(x.title)}<small>${esc(MODE_LABEL[x.mode] || x.mode)} · ${new Date(x.created * 1000).toLocaleDateString()}</small></span><span class="pill st-${esc(x.status)}">${esc(x.status.toLowerCase())}</span></a>`).join("")
      : `<div class="empty">No deep research, citation checks or reference checks yet.</div>`;
  } catch (ex) { $("#hist").innerHTML = `<div class="empty">${esc(ex.message)}</div>`; }
}
