// The chat: one continuous thread (plus one per project), texting-style bubbles,
// a composer pinned to the bottom, and rich cards rendered from the Ask stream.
import { $, $$, esc, safeUrl, ICON, App, jfetch, jpost, prefs, t, lang, toast, openMenu, openSheet, closeSheet, copyLink, favicon, ago, scroller } from "./core.js";
import { renderMd } from "./md.js";
import { mountRun, MODE_LABEL, trustBadge } from "./runs.js";
import { workCard, donePill } from "./work.js";

export const FOCUS = {
  all: ["All web", "Search the whole web"],
  academic: ["Academic", "Crossref, PubMed, arXiv, OpenAlex, Semantic Scholar"],
  news: ["News", "Recent headlines, with dates"],
  reddit: ["Discussions", "Reddit and forums (opinions, not facts)"],
  youtube: ["YouTube", "Video titles and descriptions"],
};
export const MODES = {
  ask: ["Ask", "Fast answer, every citation checked"],
  research: ["Deep research", "A long cited report, 5 to 10 minutes"],
  check: ["Check", "Verify an AI answer's citations"],
  references: ["References", "Validate and format a bibliography"],
};
const SRC = { both: "Files + web", files: "My files", web: "Web only" };
const STYLES = { apa: "APA 7", mla: "MLA 9", chicago: "Chicago", ieee: "IEEE" };
const LANGS = { auto: "Same language", en: "English", hi: "हिंदी" };
const tidy = (s) => String(s || "").replace(/\s+([.,;:!?])/g, "$1");
const VERDICT = { supported: "Supported", partial: "Partly supported", unsupported: "Not supported", unchecked: "Not checked", pending: "Checking…" };

export const S = {
  scope: "main", // main | thread | project | shared
  thread: null,
  project: null,
  files: 0,
  mode: "ask",
  focus: prefs.get("focus", "all"),
  src: "both",
  file: null,
  busy: false,
  abort: null,
  opts: { style: "apa", language: "auto", verify: true, academic: true },
};

let view = null;
let lastVerified = null; // the newest answer with checked citations (Home's "Verify" opens it)
App.openVerify = () => { if (lastVerified) verificationSheet(lastVerified); else toast("No checked answer yet"); };
const threadEl = () => (view ? $("#thread", view) : null);

// ------------------------------------------------------------------ scrolling
// The view is a fixed scroller (so the chat can fade out under the top bar).
const nearBottom = () => { const v = scroller(); return v.scrollTop + v.clientHeight > v.scrollHeight - 160; };
let stick = true;
export function toBottom(force = false) {
  if (force || stick) requestAnimationFrame(() => { const v = scroller(); v.scrollTo({ top: v.scrollHeight, behavior: force ? "auto" : "smooth" }); });
}

// ------------------------------------------------------------------ composer
// A glass pill: "Ask ODAR", then + (attach and modes) and the mic on the right; send shows
// once there is text. Anything not default (a mode, a focus, a file) shows as a chip above it.
const ta = () => $("#q");
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null;
export function composer(on) {
  $("#composer-wrap").classList.toggle("hidden", !on);
  document.body.classList.toggle("with-composer", on);
}
function placeholder() {
  if (S.mode !== "ask") return t(S.mode + "_ph");
  if (S.project) return `Ask about ${S.project.name}`;
  return t("ask_ph");
}
const MODE_IC = { ask: ICON.spark, research: ICON.doc, check: ICON.shield, references: ICON.table };
function syncButtons() {
  const has = ta().value.trim().length > 0 || (!!S.file && S.mode !== "ask");
  const streaming = !!(S.busy && S.abort);
  const send = $("#btn-send");
  send.classList.toggle("hidden", !(has || streaming));
  send.innerHTML = streaming ? ICON.stop : ICON.send;
  send.classList.toggle("stop", streaming);
  send.setAttribute("aria-label", streaming ? t("stop") : t("send"));
  send.disabled = S.busy && !streaming;
  $("#btn-mic").classList.toggle("hidden", !SR || ((has || streaming) && !rec));
}
export function syncComposer() {
  const chips = [];
  const x = `<span class="chip-x" aria-hidden="true">${ICON.x}</span>`;
  if (S.scope !== "project" && S.mode !== "ask") chips.push(`<button type="button" class="chip on" id="chip-mode" title="Back to Ask">${MODE_IC[S.mode]}<span>${esc(MODES[S.mode][0])}</span>${x}</button>`);
  if (S.mode === "ask" && S.focus !== "all" && !(S.project && S.src === "files")) chips.push(`<button type="button" class="chip on" id="chip-focus" title="Search the whole web">${ICON.globe}<span>${esc(FOCUS[S.focus][0])}</span>${x}</button>`);
  if (S.mode === "ask" && S.project) chips.push(`<button type="button" class="chip" data-menu id="chip-src" aria-haspopup="menu">${ICON.folder}<span>${esc(SRC[S.src])}</span>${ICON.chev}</button>`);
  if (S.mode !== "ask") {
    chips.push(`<button type="button" class="chip" data-menu id="chip-style">${esc(STYLES[S.opts.style])}${ICON.chev}</button>`);
    if (S.mode !== "references") {
      chips.push(`<button type="button" class="chip" data-menu id="chip-lang">${esc(LANGS[S.opts.language])}${ICON.chev}</button>`);
      chips.push(`<button type="button" class="chip toggle ${S.opts.academic ? "on" : ""}" id="chip-acad" aria-pressed="${S.opts.academic}">Academic sources</button>`);
    }
    if (S.mode === "research") chips.push(`<button type="button" class="chip toggle ${S.opts.verify ? "on" : ""}" id="chip-verify" aria-pressed="${S.opts.verify}">Check citations</button>`);
  }
  if (S.file) chips.push(`<span class="chip file">${ICON.file}<span>${esc(S.file.name)}</span><button type="button" id="unattach" aria-label="Remove file">${ICON.x}</button></span>`);
  $("#comp-chips").innerHTML = chips.join("");
  ta().placeholder = placeholder();
  autoGrow(); // a long placeholder can wrap; Chrome counts it in scrollHeight
  wireChips();
}
function plusMenu(anchor) {
  const items = [];
  if (S.scope !== "project") {
    items.push(["-", "Mode"]);
    Object.entries(MODES).forEach(([k, v]) => items.push(["m:" + k, v[0], v[1], k === S.mode, MODE_IC[k]]));
  }
  if (S.mode === "ask" && !(S.project && S.src === "files")) {
    items.push(["-", "Search in"]);
    items.push(["chips", Object.entries(FOCUS).map(([k, v]) => ["f:" + k, v[0], k === S.focus])]);
  }
  if (S.mode === "ask" && S.project) {
    items.push(["-", "Sources"]);
    items.push(["chips", Object.entries(SRC).map(([k, v]) => ["s:" + k, v, k === S.src])]);
  }
  items.push(["-", "Add"]);
  items.push(["attach", S.project ? "Add a file to this project" : "Attach a file", S.project ? "PDF, DOCX, TXT or MD" : "Check its citations · PDF, DOCX, TXT or MD", false, ICON.clip]);
  if (!S.project) items.push(["project", "Add to a project", "Ask questions about your files", false, ICON.folder]);
  openMenu(anchor, items, (id) => {
    const [k, v] = id.includes(":") ? id.split(":") : [id, ""];
    if (k === "m") setMode(v);
    else if (k === "f") { S.focus = v; prefs.set("focus", v); syncComposer(); ta().focus(); }
    else if (k === "s") { S.src = v; syncComposer(); }
    else if (k === "attach") { if (!S.project) setMode("check"); $("#file").click(); }
    else if (k === "project") App.go("/projects?add=1");
  });
}
function wireChips() {
  const on = (id, fn) => { const el = $("#" + id); if (el) el.onclick = fn; };
  on("chip-mode", () => setMode("ask"));
  on("chip-focus", () => { S.focus = "all"; prefs.set("focus", "all"); syncComposer(); });
  on("chip-src", (e) => openMenu(e.currentTarget, Object.entries(SRC).map(([k, v]) => [k, v, "", k === S.src]), (k) => { S.src = k; syncComposer(); }));
  on("chip-style", (e) => openMenu(e.currentTarget, Object.entries(STYLES).map(([k, v]) => [k, v, "", k === S.opts.style]), (k) => { S.opts.style = k; syncComposer(); }));
  on("chip-lang", (e) => openMenu(e.currentTarget, Object.entries(LANGS).map(([k, v]) => [k, v, "Report language", k === S.opts.language]), (k) => { S.opts.language = k; syncComposer(); }));
  on("chip-acad", () => { S.opts.academic = !S.opts.academic; syncComposer(); });
  on("chip-verify", () => { S.opts.verify = !S.opts.verify; syncComposer(); });
  on("unattach", () => { S.file = null; syncComposer(); });
}
export function setMode(mode) {
  S.mode = MODES[mode] ? mode : "ask";
  if (S.mode === "ask") S.file = null;
  syncComposer();
  ta().focus();
}
function autoGrow() { const el = ta(); el.style.height = "auto"; el.style.height = Math.min(el.scrollHeight, 200) + "px"; syncButtons(); }

// Drop text into the composer (used by Home panels' Reply buttons).
export function prefill(text) {
  ta().value = text; autoGrow();
  ta().focus(); ta().setSelectionRange(text.length, text.length);
}

function dictate() {
  if (!SR) return;
  if (rec) { rec.stop(); return; }
  rec = new SR();
  rec.lang = lang() === "hi" ? "hi-IN" : navigator.language || "en-US";
  rec.interimResults = true;
  const base = ta().value.trim();
  rec.onresult = (e) => { let txt = ""; for (const r of e.results) txt += r[0].transcript; ta().value = (base ? base + " " : "") + txt; autoGrow(); };
  rec.onerror = (e) => { if (e.error === "not-allowed" || e.error === "service-not-allowed") toast("Microphone access is blocked"); };
  rec.onend = () => { rec = null; $("#btn-mic").classList.remove("rec"); syncButtons(); };
  try { rec.start(); $("#btn-mic").classList.add("rec"); } catch { rec = null; }
}

export function initComposer() {
  scroller().addEventListener("scroll", () => { stick = nearBottom(); }, { passive: true });
  $("#btn-plus").innerHTML = ICON.plus;
  $("#btn-mic").innerHTML = ICON.mic;
  $("#btn-plus").onclick = (e) => plusMenu(e.currentTarget);
  $("#btn-mic").onclick = dictate;
  ta().addEventListener("input", autoGrow);
  ta().addEventListener("keydown", (e) => {
    const multi = S.mode === "check" || S.mode === "references";
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing && (!multi || e.metaKey || e.ctrlKey)) { e.preventDefault(); $("#composer").requestSubmit(); }
  });
  $("#file").onchange = async (e) => {
    const f = e.target.files[0]; e.target.value = "";
    if (!f) return;
    if (f.size > 10_000_000) { toast("Files can be up to 10 MB"); return; }
    if (S.project) { await uploadToProject(S.project.project_id, f); return; }
    if (S.mode === "ask" || S.mode === "research") S.mode = "check";
    S.file = f; syncComposer();
  };
  $("#composer").onsubmit = (e) => {
    e.preventDefault();
    if (S.busy) { if (S.abort) S.abort.abort(); return; }
    const text = ta().value.trim();
    if (S.mode === "ask") {
      if (text.length < 3) { toast("Ask a slightly longer question"); return; }
      ta().value = ""; autoGrow();
      if (!threadEl()) { App.pendingAsk = [text]; App.go("/"); return; } // typed on Home
      sendAsk(text);
      return;
    }
    if (!text && !S.file) { toast("Type or attach something first"); return; }
    if (!threadEl()) { App.pendingRun = [S.mode, text, S.file]; App.go("/"); return; }
    startRun(S.mode, text, S.file);
  };
  syncButtons();
}

export async function uploadToProject(pid, f) {
  const fd = new FormData(); fd.append("file", f);
  toast(`Adding ${f.name}…`);
  try {
    await jfetch(`/api/projects/${pid}/files`, { method: "POST", body: fd });
    toast(`${f.name} added`);
    if (S.project && S.project.project_id === pid) {
      S.files += 1;
      if (S.files === 1) S.src = "both";
      const sub = $("#proj-sub"); if (sub) sub.textContent = `${S.files} file${S.files === 1 ? "" : "s"}`;
      syncComposer();
    }
    return true;
  } catch (ex) { toast(ex.message); return false; }
}

// ------------------------------------------------------------------ rows
// No avatars, names or timestamps: the user's messages are pills on the right.
function rowMe(text, { mode } = {}) {
  const r = document.createElement("div");
  r.className = "row me enter";
  r.innerHTML = `<div class="bubble">${mode && mode !== "ask" ? `<span class="b-tag">${esc(MODE_LABEL[mode] || mode)}</span>` : ""}${esc(text)}</div>`;
  threadEl().append(r);
  return r;
}

// ------------------------------------------------------------------ live work (Ask)
function askSteps(focus, srcMode) {
  const where = srcMode === "files" ? "Searching your files" : focus === "academic" ? "Searching papers" : focus === "news" ? "Searching the news" : focus === "reddit" ? "Searching discussions" : focus === "youtube" ? "Searching YouTube" : "Searching the web";
  return [{ id: "search", label: where }, { id: "read", label: t("reading") }, { id: "write", label: t("writing") }, { id: "verify", label: t("verifying") }];
}
function searchPhrase(focus, srcMode) {
  if (srcMode === "files") return "Searching your files";
  if (srcMode === "both") return "Searching your files and the web";
  return { academic: "Searching PubMed, arXiv and Crossref", news: "Searching today's news", reddit: "Searching Reddit and forums", youtube: "Searching YouTube" }[focus] || "Searching the web";
}
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

// ------------------------------------------------------------------ one assistant turn
function makeTurn(data = {}, { live = false } = {}) {
  const el = document.createElement("div");
  el.className = "row bot enter";
  el.innerHTML = `<div class="turn"><div class="work-slot"></div>
    <div class="answer bubbles ${live ? "hidden" : ""}"><div class="bubble"><span class="typing" aria-label="ODAR is typing"><i></i><i></i><i></i></span></div></div>
    <div class="under hidden"></div><div class="c-src"></div><div class="c-img"></div><div class="c-fup"></div></div>`;
  threadEl().append(el);
  const st = { sources: data.sources || [], images: [], verification: null, raw: "", focus: data.focus || S.focus, srcMode: data.sources_mode || "web", timing: {}, stopped: false };
  const steps = askSteps(st.focus, st.srcMode);
  const ws = {};
  const slot = $(".work-slot", el);
  const answer = $(".answer", el);
  const under = $(".under", el);
  let card = null, finished = false;
  if (live) {
    card = workCard(slot, {
      title: steps[0].label, steps,
      buttons: [
        { id: "expand", icon: ICON.expand, label: "Show sources and steps", onClick: (b, api) => { const big = api.toggleBig(); b.innerHTML = big ? ICON.shrink : ICON.expand; } },
        { id: "stop", icon: ICON.x, label: t("stop"), onClick: () => { if (S.abort) S.abort.abort(); } },
      ],
    });
    card.step("search", "now");
    card.status(searchPhrase(st.focus, st.srcMode));
  }
  const finish = (summary, ok = true) => {
    if (finished) return;
    finished = true;
    steps.forEach((s) => { if (ws[s.id] !== "skip") ws[s.id] = "done"; });
    if (card) card.finish(summary, { ok }); else donePill(slot, summary, steps, ws, ok);
  };
  const showAnswer = () => answer.classList.remove("hidden");
  const drawUnder = () => {
    const v = st.verification;
    const bits = [];
    if (v && (v.citations || []).length) {
      const c = v.counts || {};
      const judged = (c.supported || 0) + (c.partial || 0) + (c.unsupported || 0);
      const score = judged ? Math.round((100 * ((c.supported || 0) + 0.5 * (c.partial || 0))) / judged) : null;
      bits.push(trustBadge(score));
      const sum = ["supported", "partial", "unsupported", "unchecked"].filter((k) => c[k]).map((k) => `${c[k]} ${k}`).join(" · ");
      bits.push(`<button class="vsum" data-vsum>${ICON.check}<span>${esc(sum)}</span></button>`);
    } else if (v === "pending") bits.push(`<span class="vsum pending"><span class="spin"></span>${esc(t("verifying"))}…</span>`);
    if (st.timing.total_s != null) bits.push(`<span class="meta">${st.timing.total_s}s</span>`);
    if (st.repairs) bits.push(`<span class="meta">${st.repairs} citation${st.repairs > 1 ? "s" : ""} corrected</span>`);
    under.innerHTML = bits.join("");
    under.classList.toggle("hidden", !bits.length);
    const vb = $("[data-vsum]", under);
    if (vb) vb.onclick = () => verificationSheet(st);
  };
  const turn = {
    el, st,
    sources(list) {
      st.sources = list || [];
      const n = st.sources.length;
      Object.assign(ws, { search: "done", search_n: `${n} found`, read: "done", read_n: plural(n, "source").replace("sources", t("sources")), write: "now" });
      if (card) {
        card.steps(ws);
        card.title(`${t("reading")} · ${n}`);
        card.tiles(st.sources);
        card.more(`<ol class="ho-list">${st.sources.slice(0, 10).map((x) => `<li>${favicon(x.domain, x.kind)}<span>${esc(x.title)}</span></li>`).join("")}</ol>`);
        card.status(n ? `Reading ${plural(n, "source")}` : "Looking further");
      }
      renderSources($(".c-src", el), st);
      toBottom();
    },
    reset() { st.raw = ""; answer.innerHTML = `<div class="bubble"><span class="typing"><i></i><i></i><i></i></span></div>`; },
    delta(text) {
      if (!st.raw && card) { card.status(t("writing")); card.title(t("writing")); card.tiles(st.sources, true); }
      st.raw += text;
      showAnswer();
      answer.innerHTML = renderAnswer(st.raw);
      toBottom();
    },
    done(d) {
      st.raw = d.answer || st.raw;
      st.timing = d.timing || {};
      showAnswer();
      answer.innerHTML = renderAnswer(st.raw);
      wireCites(el, st);
      const cited = !d.abstained && /\[\d+\]/.test(st.raw);
      ws.write = "done";
      if (cited && live) {
        ws.verify = "now"; st.verification = "pending";
        if (card) { card.steps(ws); card.title(t("verifying")); card.status("Checking quotes"); }
      } else if (!st.histVerify) { ws.verify = ws.verify || "skip"; finish(`Read ${st.sources.length} ${t("sources")}`); }
      drawUnder();
      toBottom();
    },
    images(list) { st.images = list || []; renderSources($(".c-src", el), st); renderImages($(".c-img", el), st); },
    verify(v) {
      st.verification = v;
      if ((v.citations || []).length) lastVerified = st;
      if (v.answer && v.answer !== st.raw) {
        st.raw = v.answer;
        answer.innerHTML = renderAnswer(st.raw);
        wireCites(el, st);
        st.repairs = (v.repairs || []).length;
      }
      const cits = v.citations || [];
      $$(".cite", answer).forEach((b) => { const c = cits[+b.dataset.occ]; if (c) b.classList.add(c.verdict); });
      const secs = v.total_s != null ? ` · ${Math.round(v.total_s)}s` : "";
      finish(`Read ${st.sources.length} ${t("sources")} · checked ${plural(cits.length, "citation")}${secs}`);
      drawUnder();
    },
    error(msg) {
      showAnswer();
      answer.innerHTML = `<div class="bubble err"></div>`;
      $(".bubble", answer).textContent = msg;
      finish(st.sources.length ? `Read ${st.sources.length} ${t("sources")}` : "Couldn't finish", false);
    },
    stop() {
      st.stopped = true;
      showAnswer();
      if (!st.raw) answer.innerHTML = `<div class="bubble muted">Stopped.</div>`;
      else { answer.innerHTML = renderAnswer(st.raw); wireCites(el, st); }
      finish("Stopped", false);
      drawUnder();
    },
    followups(question) { renderFollowups($(".c-fup", el), question, st); },
  };
  if (data.answer != null || data.error) {
    if (st.sources.length) turn.sources(st.sources);
    if (data.error && !data.answer) turn.error(data.error);
    else {
      st.histVerify = !!(data.verification && (data.verification.citations || []).length);
      turn.done({ answer: data.answer, timing: data.timing || {}, abstained: data.abstained });
      if (data.verification) turn.verify(data.verification);
    }
    if (data.images) turn.images(data.images);
    if (!data.verification) { ws.verify = "skip"; finish(`Read ${st.sources.length} ${t("sources")}`); }
  }
  return turn;
}

// Split an answer into short bubbles: one per paragraph; a heading rides with the text
// under it; a lead-in line stays with its list; tables get a bubble of their own.
export function chunkAnswer(src) {
  const blocks = [];
  let cur = [];
  const push = () => { if (cur.length) blocks.push(cur.join("\n")); cur = []; };
  for (const line of String(src || "").replace(/\r/g, "").split("\n")) {
    if (!line.trim()) { push(); continue; }
    if (/^#{1,4}\s/.test(line)) push();
    cur.push(line);
  }
  push();
  const out = [];
  for (const b of blocks) {
    const prev = out[out.length - 1];
    if (prev && /^#{1,4}\s[^\n]*$/.test(prev)) out[out.length - 1] = prev + "\n" + b; // lone heading joins the next block
    else if (prev && /:\s*$/.test(prev) && /^\s*([-*•]|\d+[.)])\s/.test(b)) out[out.length - 1] = prev + "\n" + b; // "lead-in:" + list
    else out.push(b);
  }
  return out;
}
function renderAnswer(src) {
  let occ = 0;
  const cites = (n) => `<button class="cite" data-n="${n}" data-occ="${occ++}" aria-label="Source ${n}">${n}</button>`;
  return chunkAnswer(src).map((c) => {
    const table = /\|/.test(c) && /^\s*\|?\s*:?-{2,}/m.test(c);
    return `<div class="bubble${table ? " wide" : ""}">${renderMd(c, { cites })}</div>`;
  }).join("") || `<div class="bubble"><span class="typing"><i></i><i></i><i></i></span></div>`;
}
function wireCites(el, st) {
  $$(".answer .cite", el).forEach((b) => {
    b.onclick = (e) => { e.stopPropagation(); citationSheet(st, +b.dataset.n, +b.dataset.occ); };
  });
}

function citationBody(s, c, pending) {
  const verdict = c ? c.verdict : pending ? "pending" : "unchecked";
  const quote = c && c.quote ? c.quote : "";
  const passage = !quote && s.kind === "file" ? (s.passage || s.snippet || "").slice(0, 700) : "";
  return `<div class="cite-sheet">
    <div class="cs-src">${favicon(s.domain, s.kind)}<span>${esc(s.domain || (s.kind === "file" ? "Your file" : ""))}${s.date ? " · " + esc(ago(s.date)) : ""}</span><span class="badge ${esc(verdict)}">${esc(VERDICT[verdict] || verdict)}</span></div>
    <div class="cs-title">${s.url ? `<a href="${esc(safeUrl(s.url))}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a>` : esc(s.title)}</div>
    ${c && c.claim ? `<div class="cs-label">Claim in the answer</div><p class="cs-claim">${esc(tidy(c.claim))}</p>` : ""}
    ${quote ? `<div class="cs-label">Verified quote from the source</div><blockquote>“${esc(quote)}”</blockquote>` : passage ? `<div class="cs-label">Passage from your file</div><blockquote>${esc(passage)}</blockquote>` : s.snippet ? `<div class="cs-label">From the search result</div><p class="note">${esc(s.snippet.slice(0, 260))}</p>` : ""}
    ${c && c.note ? `<p class="note">${esc(c.note)}</p>` : ""}
    ${pending ? `<p class="note">ODAR is still checking this citation against the page.</p>` : ""}
    ${s.url ? `<a class="btn primary block" href="${esc(safeUrl(s.url))}" target="_blank" rel="noopener noreferrer">Open source ${ICON.share}</a>` : ""}</div>`;
}
function citationSheet(st, n, occ) {
  const s = st.sources[n - 1];
  if (!s) return;
  const v = st.verification && st.verification !== "pending" ? st.verification : null;
  const c = v ? (v.citations || [])[occ] : null;
  openSheet(citationBody(s, c, st.verification === "pending"), { title: `Source ${n}` });
}
function verificationSheet(st) {
  const v = st.verification || {};
  const items = (v.citations || []).map((c) => {
    const s = st.sources[c.n - 1] || { title: c.title, domain: c.domain, url: c.url };
    return `<details class="vitem"><summary><span class="cite ${esc(c.verdict)}">${c.n}</span><span class="v-claim">${esc(tidy(c.claim))}</span><span class="badge ${esc(c.verdict)}">${esc(VERDICT[c.verdict] || c.verdict)}</span></summary>${citationBody(s, c, false)}</details>`;
  }).join("");
  openSheet(`<p class="note">Each cited sentence was checked against its source with a local NLI model and a verbatim-quote judge. Quotes are copied from the page.</p><div class="vlist">${items}</div>`, { title: t("verified"), tall: true });
}

// Link cards: up to three fanned, slightly rotated cards (image or favicon tile, title, site);
// tap opens the page. "All N sources" lists every source in a sheet.
function imageFor(st, s) {
  const im = (st.images || []).find((x) => x.domain && s.domain && x.domain.replace(/^www\./, "") === s.domain.replace(/^www\./, "") && /^https:\/\//.test(x.thumbnail || ""));
  return im ? im.thumbnail : "";
}
function linkCard(s, i, st) {
  const img = imageFor(st, s);
  const site = (s.domain || (s.kind === "file" ? "Your file" : "")).replace(/^www\./, "");
  const letter = esc((site[0] || "?").toUpperCase());
  const inner = `<span class="lc-img">${img ? `<img src="${esc(img)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">` : ""}<span class="lc-ph" data-l="${letter}">${favicon(s.domain, s.kind)}</span></span>
    <span class="lc-t">${esc(s.title)}</span><span class="lc-s">${esc(site)}<i>${s.n}</i></span>`;
  return s.kind === "file" || !s.url ? `<button class="lcard" style="--i:${i}" data-i="${s.n - 1}">${inner}</button>`
    : `<a class="lcard" style="--i:${i}" href="${esc(safeUrl(s.url))}" target="_blank" rel="noopener noreferrer">${inner}</a>`;
}
function renderSources(box, st) {
  const list = st.sources;
  if (!list.length) { box.innerHTML = ""; return; }
  const favs = list.slice(0, 4).map((s) => favicon(s.domain, s.kind)).join("");
  box.innerHTML = `<div class="fan">${list.slice(0, 3).map((s, i) => linkCard(s, i, st)).join("")}</div>
    <button class="src-more"><span class="favs">${favs}</span><span>All ${list.length} ${esc(t("sources"))}</span></button>`;
  $$("button.lcard", box).forEach((b) => (b.onclick = () => openSheet(citationBody(list[+b.dataset.i], null, false), { title: `Source ${list[+b.dataset.i].n}` })));
  $(".src-more", box).onclick = () => sourcesSheet(st);
}
function sourcesSheet(st) {
  const body = openSheet(`<div class="list flat">${st.sources.map((s, i) => `<button class="li" data-i="${i}"><span class="li-ic fav-ic">${favicon(s.domain, s.kind)}</span><span class="lt">${esc(s.title)}<small>${esc((s.domain || (s.kind === "file" ? "your file" : "")).replace(/^www\./, ""))}${s.date ? " · " + esc(ago(s.date)) : ""}</small></span><span class="num">${s.n}</span></button>`).join("")}</div>`, { title: `${st.sources.length} ${t("sources")}`, tall: true });
  $$("[data-i]", body).forEach((b) => (b.onclick = () => { const s = st.sources[+b.dataset.i]; openSheet(citationBody(s, null, false), { title: `Source ${s.n}` }); }));
}

function renderImages(box, st) {
  const used = new Set(st.sources.slice(0, 3).map((s) => imageFor(st, s)).filter(Boolean));
  const imgs = (st.images || []).filter((im) => /^https:\/\//.test(im.thumbnail || "") && !used.has(im.thumbnail));
  if (!imgs.length) { box.innerHTML = ""; return; }
  box.innerHTML = `<button class="img-stack" aria-label="Show ${imgs.length} images">${imgs.slice(0, 3).map((im, i) => `<img src="${esc(im.thumbnail)}" alt="" style="--i:${i}" referrerpolicy="no-referrer">`).join("")}<span>${imgs.length} images</span></button>
    <div class="imgs hidden">${imgs.map((im) => `<a href="${esc(safeUrl(im.url))}" target="_blank" rel="noopener noreferrer"><img src="${esc(im.thumbnail)}" alt="${esc(im.title || "")}" loading="lazy" referrerpolicy="no-referrer" onerror="this.parentNode.remove()"><span>${esc(im.domain || "")}</span></a>`).join("")}</div>`;
  const btn = $(".img-stack", box);
  btn.onclick = () => { $(".imgs", box).classList.toggle("hidden"); btn.classList.toggle("open"); };
  // thumbnails that fail to load disappear; with none left the stack goes too
  $$("img", btn).forEach((im) => im.addEventListener("error", () => { im.remove(); if (!$("img", btn)) box.innerHTML = ""; }));
}

function renderFollowups(box, question, st) {
  $$(".c-fup").forEach((b) => (b.innerHTML = ""));
  if (!question || S.scope === "shared") return;
  const items = [["deep", "Go deeper: full report"], ["simple", "Explain it more simply"]];
  if (st.focus !== "academic" && st.srcMode !== "files") items.push(["academic", "Academic sources"]);
  if (st.focus !== "news" && st.srcMode !== "files") items.push(["news", "Latest news"]);
  items.push(["hindi", "हिंदी में बताइए"]);
  box.innerHTML = `<div class="fup-chips">${items.map(([k, label]) => `<button class="chip fup" data-k="${k}">${esc(label)}</button>`).join("")}</div>`;
  $$(".fup", box).forEach((b) => (b.onclick = () => {
    const k = b.dataset.k;
    if (k === "deep") startRun("research", question);
    else if (k === "simple") sendAsk("Explain that more simply, in plain words.");
    else if (k === "hindi") sendAsk("Answer that in Hindi.");
    else sendAsk(question, k);
  }));
}

// ------------------------------------------------------------------ Ask stream
async function readSSE(res, onEvent) {
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
      if (data) onEvent(ev, JSON.parse(data));
    }
  }
}

function adoptThread(d) {
  if (S.thread && S.thread.thread_id === d.thread_id) return;
  S.thread = d;
  if (S.scope === "main") prefs.set("main", d.thread_id);
}

export async function sendAsk(question, focusOverride) {
  if (S.busy) return;
  clearHero();
  rowMe(question);
  const focus = focusOverride || S.focus;
  const turn = makeTurn({ focus, sources_mode: S.project ? S.src : "web" }, { live: true });
  toBottom(true);
  S.busy = true; S.abort = new AbortController(); syncComposer();
  const body = { question, focus, thread_id: S.thread ? S.thread.thread_id : "", project_id: S.project ? S.project.project_id : "", sources: S.project ? S.src : "web", images: true, verify: true };
  try {
    const res = await fetch("/api/ask/stream", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: S.abort.signal });
    if (!res.ok) { let d = {}; try { d = await res.json(); } catch { /* */ } turn.error(d.detail || "Couldn't answer that right now."); return; }
    await readSSE(res, (ev, d) => {
      if (ev === "thread") adoptThread(d);
      else if (ev === "sources") turn.sources(d.sources);
      else if (ev === "delta") turn.delta(d.text);
      else if (ev === "reset") turn.reset();
      else if (ev === "done") { turn.done(d); turn.followups(question); }
      else if (ev === "images") turn.images(d.images);
      else if (ev === "verification") turn.verify(d);
      else if (ev === "error") turn.error(d.message);
    });
  } catch (ex) {
    if (ex.name === "AbortError") turn.stop();
    else turn.error("The connection dropped: " + ex.message);
  } finally {
    S.busy = false; S.abort = null; syncComposer();
  }
}

// ------------------------------------------------------------------ runs in the thread
export async function startRun(mode, text, file) {
  if (S.busy) return;
  const fd = new FormData();
  fd.append("mode", mode);
  if (file) fd.append("file", file);
  else if (mode === "check" && /^https?:\/\/\S+$/.test(text)) fd.append("url", text);
  else fd.append("text", text);
  fd.append("verify", S.opts.verify);
  fd.append("academic", S.opts.academic);
  fd.append("style", S.opts.style);
  fd.append("language", S.opts.language === "auto" && prefs.get("lang", "en") === "hi" && mode === "research" ? "hi" : S.opts.language);
  fd.append("use_llm", true);
  if (S.scope !== "project" && S.scope !== "shared") fd.append("thread_id", S.thread ? S.thread.thread_id : "new");
  S.busy = true; syncComposer();
  try {
    const d = await jfetch("/api/runs", { method: "POST", body: fd });
    ta().value = ""; S.file = null; autoGrow();
    if (!d.thread_id) { App.go(`/runs/${d.run_id}`); return; }
    if (!S.thread || S.thread.thread_id !== d.thread_id) adoptThread({ thread_id: d.thread_id });
    clearHero();
    rowMe(file ? file.name : mode === "research" ? text : text.slice(0, 600), { mode });
    const row = document.createElement("div");
    row.className = "row bot wide enter";
    row.innerHTML = `<div class="turn"></div>`;
    threadEl().append(row);
    let first = true;
    mountRun($(".turn", row), { id: d.run_id }, { onDone: () => toBottom(), onTick: () => { toBottom(first); first = false; } });
    toBottom(true);
    S.mode = "ask";
  } catch (ex) { toast(ex.message); }
  finally { S.busy = false; syncComposer(); }
}

// ------------------------------------------------------------------ views
function renderMessages(messages) {
  let lastQ = "";
  let lastTurn = null;
  messages.forEach((m, i) => {
    const next = messages[i + 1];
    if (m.role === "user" && m.data.kind !== "run" && (!next || next.role === "user")) {
      rowMe(m.content);
      unanswered(m.content);
      return;
    }
    if (m.role === "user") {
      rowMe(m.content, { mode: m.data.kind === "run" ? m.data.mode : "" });
      lastQ = m.data.kind === "run" ? "" : m.content;
    } else if (m.data.kind === "run" && m.data.run_id) {
      const row = document.createElement("div");
      row.className = "row bot wide";
      row.innerHTML = `<div class="turn"></div>`;
      threadEl().append(row);
      mountRun($(".turn", row), { id: m.data.run_id }, { onDone: () => toBottom(), onTick: () => toBottom() });
      lastTurn = null;
    } else if (S.scope === "shared" && m.data.kind === "run") {
      /* run cards are private */
    } else {
      lastTurn = makeTurn({ ...m.data, answer: m.content || (m.data.error ? "" : m.content) });
      lastTurn.q = lastQ;
    }
  });
  $$(".row.enter", threadEl()).forEach((r) => r.classList.remove("enter"));
  if (lastTurn && lastTurn.q) lastTurn.followups(lastTurn.q);
}

// A question whose answer never finished (closed tab, dropped connection).
function unanswered(question) {
  const row = document.createElement("div");
  row.className = "row bot";
  row.innerHTML = `<div class="bubble muted">This answer didn't finish. <button class="link-btn">Ask again</button></div>`;
  threadEl().append(row);
  $(".link-btn", row).onclick = () => { row.remove(); sendAsk(question); };
}

// Empty chat: ODAR says hello in two short bubbles, then a few action pills to start from.
function hero() {
  const name = prefs.get("name", "");
  const acts = [
    ["ask", "Ask", "how mRNA vaccines work", "How do mRNA vaccines work?"],
    ["ask", "Compare", "EV and petrol running costs in India", "Compare EV and petrol car running costs in India"],
    ["check", "Verify", "the citations in an AI answer", ""],
    ["research", "Research", "intermittent fasting in depth", "Is intermittent fasting effective?"],
  ];
  return `<div class="hero" id="hero">
    <div class="row bot"><div class="bubble">${esc(t("hello"))}${name ? ", " + esc(name) : ""}. ${lang() === "hi" ? "" : "Ask me anything."}</div></div>
    <div class="row bot"><div class="bubble">${esc(t("hello_sub"))}</div></div>
    <div class="acts hero-acts">${acts.map(([m, verb, rest, q]) => `<button class="act" data-m="${m}" data-s="${esc(q)}"><span class="verb">${esc(verb)}</span><span class="act-t">${esc(rest)}</span></button>`).join("")}</div></div>`;
}
function clearHero() { const h = $("#hero", view); if (h) h.remove(); }
function wireHero() {
  $$("#hero [data-m]", view).forEach((b) => (b.onclick = () => {
    if (b.dataset.m === "ask") { sendAsk(b.dataset.s); return; }
    setMode(b.dataset.m);
    if (b.dataset.s) prefill(b.dataset.s);
  }));
}

function reset(scope) {
  S.scope = scope; S.thread = null; S.project = null; S.files = 0; S.file = null; S.mode = "ask";
}

export async function showChat(v, { mode, q } = {}) {
  view = v;
  reset("main");
  composer(true);
  view.innerHTML = `<div class="thread" id="thread"></div>`;
  if (MODES[mode]) S.mode = mode;
  syncComposer();
  const id = prefs.get("main", "");
  let messages = [];
  if (id) {
    try { const d = await jfetch(`/api/threads/${id}?limit=200`); S.thread = d.thread; messages = d.messages; }
    catch (ex) { if (ex.status === 404) prefs.del("main"); }
  }
  if (view !== v || S.scope !== "main") return;
  lastVerified = null;
  if (messages.length) { renderMessages(messages); toBottom(true); }
  else { threadEl().insertAdjacentHTML("beforebegin", hero()); wireHero(); }
  if (App.pendingVerify) { App.pendingVerify = false; setTimeout(() => App.openVerify(), 250); }
  syncComposer();
  if (q) { ta().value = q; autoGrow(); }
  if (App.pendingAsk) { const [pq, pf] = App.pendingAsk; App.pendingAsk = null; sendAsk(pq, pf); return; }
  if (App.pendingRun) { const [pm, pt, pfile] = App.pendingRun; App.pendingRun = null; S.mode = pm; startRun(pm, pt, pfile); return; }
  if (matchMedia("(hover:hover)").matches) ta().focus({ preventScroll: true });
}

function threadHeader(title, sub = "", backHref = "/") {
  return `<div class="page-h"><a class="glass-btn round sm" href="${backHref}" data-nav aria-label="Back">${ICON.back}</a>
    <div class="ph-t"><h1 id="th-title">${esc(title)}</h1>${sub ? `<small id="proj-sub">${esc(sub)}</small>` : ""}</div><div class="ph-a" id="th-actions"></div></div>`;
}

export async function showThread(v, id) {
  view = v;
  if (id === prefs.get("main", "")) { App.go("/", true); return; }
  reset("thread");
  composer(true);
  view.innerHTML = `<div class="empty"><span class="spin"></span></div>`;
  try {
    const { thread, messages } = await jfetch(`/api/threads/${id}?limit=200`);
    if (thread.project_id) { App.go(`/p/${thread.project_id}?t=${id}`, true); return; }
    S.thread = thread;
    view.innerHTML = threadHeader(thread.title) + `<div class="thread" id="thread"></div>`;
    threadActions();
    renderMessages(messages);
    toBottom(true);
  } catch (ex) { view.innerHTML = `<div class="empty">${esc(ex.message)}</div>`; }
  syncComposer();
}
function threadActions() {
  const box = $("#th-actions", view);
  box.innerHTML = `<button class="glass-btn round sm" id="th-share" aria-label="Share">${ICON.share}</button><button class="glass-btn round sm" id="th-more" data-menu aria-label="More">${ICON.more}</button>`;
  $("#th-share").onclick = () => copyLink(`${location.origin}/s/${S.thread.share_token}`, "Share link copied");
  $("#th-more").onclick = (e) => openMenu(e.currentTarget, [["rename", "Rename"], ["delete", "Delete thread"]], async (k) => {
    if (k === "rename") {
      const name = prompt("Rename thread", $("#th-title").textContent);
      if (name && name.trim()) { await jpost(`/api/threads/${S.thread.thread_id}`, { title: name }, "PATCH"); $("#th-title").textContent = name.trim(); }
    } else if (confirm("Delete this thread?")) {
      await jfetch(`/api/threads/${S.thread.thread_id}`, { method: "DELETE" });
      if (prefs.get("main") === S.thread.thread_id) prefs.del("main");
      App.go("/");
    }
  });
}

export async function showShared(v, token) {
  view = v;
  reset("shared");
  composer(false);
  try {
    const { thread, messages } = await jfetch(`/api/shared-threads/${token}`);
    view.innerHTML = `<div class="page-h"><div class="ph-t"><h1>${esc(thread.title)}</h1><small>Shared ODAR thread · read only</small></div></div><div class="thread" id="thread"></div>
      <div class="cta-card"><p>Get answers with every citation checked.</p><a class="btn primary" href="/" data-nav>Ask ODAR</a></div>`;
    renderMessages(messages);
  } catch (ex) { view.innerHTML = `<div class="empty">${esc(ex.message)}</div>`; }
}

// Project thread: the project's latest thread continues; files live in a sheet.
export async function showProjectThread(v, pid, threadId) {
  view = v;
  reset("project");
  composer(true);
  view.innerHTML = `<div class="empty"><span class="spin"></span></div>`;
  let d;
  try { d = await jfetch(`/api/projects/${pid}`); } catch (ex) { view.innerHTML = `<div class="empty">${esc(ex.message)}</div>`; return; }
  S.project = d.project;
  S.files = d.files.length;
  S.src = S.files ? "both" : "web";
  view.innerHTML = threadHeader(d.project.name, `${S.files} file${S.files === 1 ? "" : "s"}`, "/projects") + `<div class="thread" id="thread"></div>`;
  const box = $("#th-actions", view);
  box.innerHTML = `<button class="glass-btn pill sm" id="pj-files">${ICON.folder}<span>Files</span></button><button class="glass-btn round sm" id="pj-more" data-menu aria-label="More">${ICON.more}</button>`;
  $("#pj-files").onclick = () => App.projectFiles(pid);
  $("#pj-more").onclick = (e) => openMenu(e.currentTarget, [["instructions", "Instructions", "How answers here should be written"], ["new", "New thread", "Start fresh in this project"], ["rename", "Rename project"], ["delete", "Delete project", "and its files and threads"]], (k) => App.projectAction(pid, k, d.project));
  const tid = threadId || (d.threads[0] && d.threads[0].thread_id);
  let messages = [];
  if (tid && threadId !== "new") {
    try { const td = await jfetch(`/api/threads/${tid}?limit=200`); S.thread = td.thread; messages = td.messages; } catch { /* start fresh */ }
  }
  if (messages.length) { renderMessages(messages); toBottom(true); }
  else {
    threadEl().insertAdjacentHTML("beforebegin", `<div class="hero small" id="hero"><div class="row bot"><div class="bubble">${S.files ? `Ask about the ${S.files === 1 ? "file" : `${S.files} files`} in ${esc(d.project.name)}, the web, or both.` : `Add files to ${esc(d.project.name)} to ask about them, or ask the web.`}</div></div>
      <div class="acts hero-acts"><button class="act" id="hero-files"><span class="verb">${S.files ? "Manage" : "Add"}</span><span class="act-t">files in this project</span></button></div></div>`);
    $("#hero-files").onclick = () => App.projectFiles(pid);
  }
  syncComposer();
}
export function closeAll() { closeSheet(); }
