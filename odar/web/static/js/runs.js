// Research / Check / References runs, rendered as cards: a live work card while the run
// is going, then a summary card (report), a trust card (check) or a references card.
import { $, esc, safeUrl, ICON, App, jfetch, jpost, openSheet, copyLink } from "./core.js";
import { renderMd, splitTitle } from "./md.js";
import { workCard, donePill, host } from "./work.js";

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

// One short phrase for the status pill, from the run's newest event.
function phrase(e) {
  const k = e.kind.replace(/^telemetry\./, "");
  const d = e.data || {};
  const h = d.url ? host(d.url) : "";
  if (/research_round/.test(k)) return d.sources ? `Read ${d.sources} sources so far` : "Reading sources";
  if (/plan/.test(k)) return "Planning the research";
  if (/academic_search/.test(k)) return "Searching PubMed, arXiv and Crossref";
  if (/search/.test(k)) return d.query ? `Searching “${String(d.query).slice(0, 40)}”` : "Searching the web";
  if (/reflection/.test(k)) return "Looking for gaps";
  if (/writing/.test(k)) return d.sections ? `Writing ${d.sections} sections` : "Writing the report";
  if (/parse|split/.test(k)) return d.claims ? `Splitting into ${d.claims} claims` : "Splitting into claims";
  if (/fetch|opening/.test(k)) return h ? `Reading ${h}` : "Reading sources";
  if (/wayback/.test(k)) return "Trying the Wayback Machine";
  if (/paraphrase/.test(k)) return "Judging paraphrases";
  if (/verify|judg|match/.test(k)) return "Checking quotes";
  if (/replacement/.test(k)) return "Looking for better sources";
  if (/translat/.test(k)) return "Translating";
  if (/valid/.test(k)) return "Looking up each reference";
  if (/format/.test(k)) return "Formatting the bibliography";
  if (/start/.test(k)) return "Getting started";
  return "";
}
function pagesOf(log) {
  const seen = new Map();
  log.forEach((e) => { const u = e.data && e.data.url; const h = u && host(u); if (h && !seen.has(h)) seen.set(h, { domain: h, title: u, done: /verify|judg/.test(e.kind) }); });
  return [...seen.values()];
}
function stageIndex(run) {
  const steps = STEPS[run.mode] || STEPS.check;
  const stage = String(run.stage || "").toLowerCase();
  // research runs report their citation check as "citation check: <step>"
  const i = /^citation check/.test(stage) ? steps.findIndex(([n]) => /citation/i.test(n)) : steps.findIndex(([, re]) => re.test(stage));
  return i < 0 ? 0 : i;
}
function runSteps(run) { return (STEPS[run.mode] || STEPS.check).map(([label], i) => ({ id: "s" + i, label })); }
function runSummary(run, log) {
  const pages = pagesOf(log).length;
  const checks = log.filter((e) => /^verify$|judge/.test(e.kind)).length;
  const mins = Math.max(1, Math.round(((run.updated || run.created) - run.created) / 60));
  return [MODE_LABEL[run.mode] || run.mode, pages ? `${pages} pages read` : "", checks ? `${checks} checks` : "", run.status === "COMPLETE" ? `${mins} min` : ""].filter(Boolean).join(" · ");
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

// Receipt-style card for a finished run: title, a few rows, a black pill action (Download PDF)
// and the other exports as quiet links.
function orderCard(run, ctx, rows) {
  const base = ctx.id ? `/api/runs/${ctx.id}` : `/api/share/${ctx.token}`;
  const when = new Date((run.updated || run.created) * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  return `<div class="order">
    <div class="or-h"><span class="logo">O</span><div class="or-who"><b>${esc(MODE_LABEL[run.mode] || "Report")}</b><small>ODAR · ${esc(when)}</small></div></div>
    <div class="or-t">${esc(run.title)}</div>
    <div class="or-rows">${rows.filter(([, v]) => v != null && v !== "").map(([k, v]) => `<div><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("")}</div>
    <a class="btn black block" href="${base}/export.pdf" download>${ICON.download}Download PDF</a>
    <div class="or-links"><a href="${base}/export.md" download>Markdown</a><a href="${base}/export.docx" download>Word</a><button data-share>Share link</button>${ctx.id ? `<button data-ask-report>Ask about it</button>` : ""}</div></div>`;
}

function resultCard(run, ctx) {
  const r = run.result || {};
  if (run.mode === "research") {
    const { title, body } = splitTitle(r.synthesis || "");
    const cc = r.citation_check;
    const score = cc ? cc.trust_score : null;
    const high = String(r.uncertainty).toUpperCase() === "HIGH";
    const counters = (r.telemetry && r.telemetry.counters) || {};
    return `<div class="card summary">
      <div class="summary-h"><span class="sh-ic">${ICON.doc}</span><div><div class="eyebrow">Report</div><h3>${esc(title || run.title)}</h3></div>${trustBadge(score)}</div>
      ${high ? `<p class="warn">Limited verified evidence: treat this as a starting point.</p>` : ""}
      <div class="report collapsed" lang="${r.language === "hi" ? "hi" : "en"}">${renderMd(body || "(no report was produced)")}</div>
      <button class="more-btn" data-expand>${ICON.chev}<span>Read the full report</span></button>
      ${cc ? `<details class="sub"><summary>Citation check · ${esc(cc.grade || "")}</summary>${checkBody(cc)}</details>` : ""}</div>
      ${orderCard(run, ctx, [["Sources read", counters.sources_added], ["Claims checked", cc ? (cc.claims || []).length : null], ["Trust score", score != null ? `${score} / 100` : null], ["Evidence", String(r.uncertainty || "").replace(/_/g, " ").toLowerCase()]])}`;
  }
  if (run.mode === "check") {
    return `<div class="card summary"><div class="summary-h"><span class="sh-ic">${ICON.shield}</span><div><div class="eyebrow">Citation check</div><h3>${esc(run.title)}</h3></div></div>${checkBody(r)}</div>
      ${orderCard(run, ctx, [["Claims", (r.claims || []).length], ["Trust score", r.trust_score != null ? `${r.trust_score} / 100` : "n/a"], ["Verdict", r.grade || ""]])}`;
  }
  const chips = Object.entries(r.counts || {}).map(([k, v]) => `<span class="pill ${vclass(k)}">${esc(k)} ${v}</span>`).join("");
  return `<div class="card summary"><div class="summary-h"><span class="sh-ic">${ICON.table}</span><div><div class="eyebrow">Reference check</div><h3>${esc(run.title)}</h3></div></div>
    <div class="pills">${chips}</div><div class="claims">${refsBody(r.references || [])}</div>
    ${(r.bibliography || []).length ? `<h4>Bibliography (${esc(String(r.style || "").toUpperCase())})</h4><ol class="bib">${r.bibliography.map((b) => `<li>${esc(b)}</li>`).join("")}</ol>` : ""}</div>
    ${orderCard(run, ctx, [["References", (r.references || []).length], ["Style", String(r.style || "").toUpperCase()]])}`;
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

// Mount a run card into ``el`` and keep it updated until the run finishes: a live work
// card while it runs, then a done pill above the result card.
export function mountRun(el, { id, token }, { onDone, onTick } = {}) {
  const api = id ? `/api/runs/${id}` : `/api/share/${token}`;
  const log = [];
  let after = 0, card = null;
  const poller = { timer: 0 };
  pollers.add(poller);
  el.innerHTML = `<div class="work-slot"><div class="done-wrap"><span class="done-pill"><span class="spin"></span><span>Loading…</span></span></div></div><div class="run-body"></div>`;
  const slot = $(".work-slot", el), body = $(".run-body", el);
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
    const steps = runSteps(run);
    if (live) {
      if (!card) {
        card = workCard(slot, {
          title: MODE_LABEL[run.mode] || run.mode, steps, started: run.created,
          buttons: [
            { id: "expand", icon: ICON.expand, label: id && location.pathname !== `/runs/${id}` ? "Open full view" : "Show details", onClick: (b, w) => { if (id && location.pathname !== `/runs/${id}`) App.go(`/runs/${id}`); else { const big = w.toggleBig(); b.innerHTML = big ? ICON.shrink : ICON.expand; } } },
            { id: "min", icon: ICON.chev, label: "Minimize", onClick: (b, w) => { const mini = w.minimize(); b.classList.toggle("flip", !mini); } },
          ],
        });
      }
      const now = stageIndex(run);
      card.steps(Object.fromEntries(steps.map((s, i) => [s.id, i < now ? "done" : i === now ? "now" : ""])));
      card.title(run.status === "QUEUED" ? "Queued" : steps[now].label);
      const pages = pagesOf(log);
      const rounds = log.filter((e) => /research_round/.test(e.kind) && e.data && e.data.sources);
      card.tiles(pages, false, rounds.length ? +rounds[rounds.length - 1].data.sources || 0 : 0);
      card.more(`<div class="log">${log.slice(-6).map((e) => `<div>${describe(e)}</div>`).join("")}</div><div class="meta">Runs in the background. You can leave and come back.</div>`);
      const last = [...log].reverse().map(phrase).find(Boolean);
      card.status(run.status === "QUEUED" ? "Waiting for a free slot" : last || steps[now].label);
    } else if (run.status === "FAILED") {
      donePill(slot, `${MODE_LABEL[run.mode] || "Run"} failed`, steps, {}, false);
      body.innerHTML = `<div class="bubble err">This ${esc(MODE_LABEL[run.mode] || "run")} failed: ${esc(run.error || "unknown error")}</div>`;
    } else if (run.result) {
      donePill(slot, runSummary(run, log), steps);
      body.innerHTML = resultCard(run, ctx);
      wire(el, run, ctx);
    }
    if (onTick) onTick(run);
    if (live && pollers.has(poller)) poller.timer = setTimeout(tick, 2000);
    else { if (card) card.stopTimer(); pollers.delete(poller); if (onDone) onDone(run); }
  };
  tick();
}

// Standalone run page (/runs/:id, /r/:token: share links and the Chrome extension land here).
export async function showRunPage(view, { id, token }) {
  view.innerHTML = `<div class="page-h"><a class="glass-btn round sm" href="/" data-nav aria-label="Back to chat">${ICON.back}</a><div class="ph-t"><h1 id="run-title">Report</h1></div></div>
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
  view.innerHTML = `<div class="page-h"><a class="glass-btn round sm" href="/home" data-nav aria-label="Back">${ICON.back}</a><div class="ph-t"><h1>Run history</h1></div></div><div id="hist" class="list glass-card"><div class="empty">Loading…</div></div>`;
  try {
    const { runs } = await jfetch("/api/runs");
    $("#hist").innerHTML = runs.length ? runs.map((x) => `<a class="li" href="/runs/${x.run_id}" data-nav><span class="li-ic">${x.mode === "research" ? ICON.doc : ICON.shield}</span><span class="lt">${esc(x.title)}<small>${esc(MODE_LABEL[x.mode] || x.mode)} · ${new Date(x.created * 1000).toLocaleDateString()}</small></span><span class="pill st-${esc(x.status)}">${esc(x.status.toLowerCase())}</span></a>`).join("")
      : `<div class="empty">No deep research, citation checks or reference checks yet.</div>`;
  } catch (ex) { $("#hist").innerHTML = `<div class="empty">${esc(ex.message)}</div>`; }
}
