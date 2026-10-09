// The chat: one continuous thread (plus one per project), texting-style bubbles,
// a composer pinned to the bottom, and rich cards rendered from the Ask stream.
import { $, $$, esc, safeUrl, ICON, LOGO, App, jfetch, jpost, prefs, t, toast, openMenu, openSheet, closeSheet, copyLink, favicon, ago, clock } from "./core.js";
import { renderMd } from "./md.js";
import { mountRun, MODE_LABEL, trustBadge } from "./runs.js";

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
const threadEl = () => $("#thread", view);

// ------------------------------------------------------------------ scrolling
const nearBottom = () => innerHeight + scrollY > document.documentElement.scrollHeight - 160;
let stick = true;
addEventListener("scroll", () => { stick = nearBottom(); }, { passive: true });
export function toBottom(force = false) {
  if (force || stick) requestAnimationFrame(() => window.scrollTo({ top: document.documentElement.scrollHeight, behavior: force ? "auto" : "smooth" }));
}

// ------------------------------------------------------------------ composer
const ta = () => $("#q");
export function composer(on) {
  $("#composer-wrap").classList.toggle("hidden", !on);
  document.body.classList.toggle("with-composer", on);
}
function placeholder() {
  if (S.mode !== "ask") return t(S.mode + "_ph");
  if (S.project) return `Ask about ${S.project.name}`;
  return S.thread ? t("followup_ph") : t("ask_ph");
}
export function syncComposer() {
  const chips = [];
  if (S.scope !== "project") chips.push(`<button type="button" class="chip mode-${S.mode}" data-menu id="chip-mode" aria-haspopup="menu">${S.mode === "ask" ? ICON.spark : S.mode === "research" ? ICON.doc : ICON.shield}<span>${esc(MODES[S.mode][0])}</span>${ICON.chev}</button>`);
  if (S.mode === "ask" && !(S.project && S.src === "files")) chips.push(`<button type="button" class="chip" data-menu id="chip-focus" aria-haspopup="menu">${ICON.globe}<span>${esc(FOCUS[S.focus][0])}</span>${ICON.chev}</button>`);
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
  const send = $("#btn-send");
  const streaming = S.busy && S.abort;
  send.innerHTML = streaming ? ICON.stop : ICON.send;
  send.classList.toggle("stop", !!streaming);
  send.setAttribute("aria-label", streaming ? t("stop") : t("send"));
  send.disabled = S.busy && !streaming;
  wireChips();
}
function wireChips() {
  const on = (id, fn) => { const el = $("#" + id); if (el) el.onclick = fn; };
  on("chip-mode", (e) => openMenu(e.currentTarget, Object.entries(MODES).map(([k, v]) => [k, v[0], v[1], k === S.mode]), (k) => setMode(k)));
  on("chip-focus", (e) => openMenu(e.currentTarget, Object.entries(FOCUS).map(([k, v]) => [k, v[0], v[1], k === S.focus]), (k) => { S.focus = k; prefs.set("focus", k); syncComposer(); }));
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
function autoGrow() { const el = ta(); el.style.height = "auto"; el.style.height = Math.min(el.scrollHeight, 200) + "px"; }

export function initComposer() {
  $("#btn-attach").innerHTML = ICON.clip;
  $("#btn-attach").setAttribute("aria-label", t("attach"));
  ta().addEventListener("input", autoGrow);
  ta().addEventListener("keydown", (e) => {
    const multi = S.mode === "check" || S.mode === "references";
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing && (!multi || e.metaKey || e.ctrlKey)) { e.preventDefault(); $("#composer").requestSubmit(); }
  });
  $("#btn-attach").onclick = (e) => {
    if (S.project) { $("#file").click(); return; }
    openMenu(e.currentTarget, [["check", "Check a file's citations", "PDF, DOCX, TXT or MD, up to 10 MB"], ["project", "Add to a project", "Ask questions about your files"]], (k) => {
      if (k === "check") { setMode("check"); $("#file").click(); }
      else App.go("/projects?add=1");
    });
  };
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
      sendAsk(text);
      return;
    }
    if (!text && !S.file) { toast("Type or attach something first"); return; }
    startRun(S.mode, text, S.file);
  };
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
let lastDay = "";
function daySep(sec) {
  const d = new Date((sec || Date.now() / 1000) * 1000);
  const key = d.toDateString();
  if (key === lastDay) return;
  lastDay = key;
  const today = new Date().toDateString() === key;
  const label = today ? "Today" : d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  threadEl().insertAdjacentHTML("beforeend", `<div class="day-sep"><span>${esc(label)}</span></div>`);
}
function timeEl(sec) { return `<time datetime="${new Date((sec || Date.now() / 1000) * 1000).toISOString()}">${esc(clock(sec))}</time>`; }
function rowMe(text, { created, mode } = {}) {
  daySep(created);
  const r = document.createElement("div");
  r.className = "row me enter";
  r.innerHTML = `<div class="bubble">${mode && mode !== "ask" ? `<span class="b-tag">${esc(MODE_LABEL[mode] || mode)}</span>` : ""}${esc(text)}</div>${timeEl(created)}`;
  threadEl().append(r);
  return r;
}

// Long-press (touch) shows a message's time; hover does on desktop.
let pressTimer = 0;
document.addEventListener("touchstart", (e) => {
  const row = e.target.closest(".row");
  if (!row) return;
  pressTimer = setTimeout(() => row.classList.toggle("show-time"), 420);
}, { passive: true });
["touchend", "touchmove", "touchcancel"].forEach((ev) => document.addEventListener(ev, () => clearTimeout(pressTimer), { passive: true }));

// ------------------------------------------------------------------ work card (Ask)
function askSteps(focus, srcMode) {
  const where = srcMode === "files" ? "Searching your files" : focus === "academic" ? "Searching papers" : focus === "news" ? "Searching the news" : focus === "reddit" ? "Searching discussions" : focus === "youtube" ? "Searching YouTube" : "Searching the web";
  return [{ id: "search", label: where }, { id: "read", label: t("reading") }, { id: "write", label: t("writing") }, { id: "verify", label: t("verifying") }];
}
function workHtml(steps, state, open) {
  const doneAll = state.finished;
  const summary = state.summary || "Working on it";
  return `<button class="work-h" aria-expanded="${open}">${doneAll ? `<span class="ok-dot">${ICON.check}</span>` : `<span class="pulse"></span>`}<b>${esc(summary)}</b>${ICON.chev}</button>
    <ol class="steps ${open ? "" : "hidden"}">${steps.map((s) => `<li class="${state[s.id] || ""}"><i></i><span>${esc(s.label)}${state[s.id + "_n"] ? ` <small>${esc(state[s.id + "_n"])}</small>` : ""}</span></li>`).join("")}</ol>`;
}

// ------------------------------------------------------------------ one assistant turn
function makeTurn(data = {}, { live = false, created } = {}) {
  const el = document.createElement("div");
  el.className = "row bot enter";
  el.innerHTML = `<div class="turn"><div class="card work ${live ? "live" : ""}"></div>
    <div class="bubble answer"><span class="typing" aria-label="ODAR is typing"><i></i><i></i><i></i></span></div>
    <div class="under hidden"></div><div class="c-src"></div><div class="c-img"></div><div class="c-fup"></div></div>${timeEl(created)}`;
  threadEl().append(el);
  const st = { sources: data.sources || [], verification: null, raw: "", focus: data.focus || S.focus, srcMode: data.sources_mode || "web", timing: {}, stopped: false };
  const steps = askSteps(st.focus, st.srcMode);
  const ws = { search: "now", summary: steps[0].label + "…" };
  let open = live;
  const work = $(".work", el);
  const bubble = $(".answer", el);
  const under = $(".under", el);
  const drawWork = () => {
    work.innerHTML = workHtml(steps, ws, open);
    $(".work-h", work).onclick = () => { open = !open; drawWork(); };
  };
  drawWork();
  const finish = (summary) => {
    Object.assign(ws, { finished: true, summary });
    steps.forEach((s) => { if (ws[s.id] !== "skip") ws[s.id] = "done"; });
    work.classList.remove("live");
    setTimeout(() => { open = false; drawWork(); }, live ? 900 : 0);
    drawWork();
  };
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
    if (st.stopped) bits.push(`<span class="meta">stopped</span>`);
    under.innerHTML = bits.join("");
    under.classList.toggle("hidden", !bits.length);
    const vb = $("[data-vsum]", under);
    if (vb) vb.onclick = () => verificationSheet(st);
  };
  const turn = {
    el, st,
    sources(list) {
      st.sources = list || [];
      Object.assign(ws, { search: "done", search_n: `${st.sources.length} found`, read: "done", read_n: `${st.sources.length} ${t("sources")}`, write: "now", summary: `${t("writing")}…` });
      drawWork();
      renderSources($(".c-src", el), st);
      toBottom();
    },
    reset() { st.raw = ""; bubble.innerHTML = `<span class="typing"><i></i><i></i><i></i></span>`; },
    delta(text) {
      st.raw += text;
      bubble.innerHTML = renderAnswer(st.raw);
      toBottom();
    },
    done(d) {
      st.raw = d.answer || st.raw;
      st.timing = d.timing || {};
      bubble.innerHTML = renderAnswer(st.raw);
      wireCites(el, st);
      const cited = !d.abstained && /\[\d+\]/.test(st.raw);
      ws.write = "done";
      if (cited && live) { ws.verify = "now"; ws.summary = `${t("verifying")}…`; st.verification = "pending"; drawWork(); }
      else finish(`Read ${st.sources.length} ${t("sources")}`);
      drawUnder();
      toBottom();
    },
    images(list) { renderImages($(".c-img", el), list || []); },
    verify(v) {
      st.verification = v;
      if (v.answer && v.answer !== st.raw) {
        st.raw = v.answer;
        bubble.innerHTML = renderAnswer(st.raw);
        wireCites(el, st);
        st.repairs = (v.repairs || []).length;
      }
      const cits = v.citations || [];
      $$(".cite", bubble).forEach((b) => { const c = cits[+b.dataset.occ]; if (c) b.classList.add(c.verdict); });
      const secs = v.total_s != null ? ` · ${Math.round(v.total_s)}s` : "";
      finish(`Read ${st.sources.length} ${t("sources")} · checked ${cits.length} citation${cits.length === 1 ? "" : "s"}${secs}`);
      drawUnder();
    },
    error(msg) {
      bubble.classList.add("err");
      bubble.textContent = msg;
      finish(st.sources.length ? `Read ${st.sources.length} ${t("sources")}` : "Couldn't finish");
    },
    stop() {
      st.stopped = true;
      if (!st.raw) bubble.innerHTML = `<span class="muted">Stopped.</span>`;
      else { bubble.innerHTML = renderAnswer(st.raw); wireCites(el, st); }
      finish("Stopped");
      drawUnder();
    },
    followups(question) { renderFollowups($(".c-fup", el), question, st); },
  };
  if (data.answer != null || data.error) {
    if (st.sources.length) turn.sources(st.sources);
    if (data.error && !data.answer) turn.error(data.error);
    else {
      turn.done({ answer: data.answer, timing: data.timing || {}, abstained: data.abstained });
      if (data.verification) turn.verify(data.verification);
    }
    if (data.images) turn.images(data.images);
    if (!data.verification) { ws.verify = "skip"; finish(`Read ${st.sources.length} ${t("sources")}`); }
  }
  return turn;
}

function renderAnswer(src) {
  let occ = 0;
  return renderMd(src, { cites: (n) => `<button class="cite" data-n="${n}" data-occ="${occ++}" aria-label="Source ${n}">${n}</button>` });
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

function renderSources(box, st) {
  const list = st.sources;
  if (!list.length) { box.innerHTML = ""; return; }
  const favs = list.slice(0, 4).map((s) => favicon(s.domain, s.kind)).join("");
  box.innerHTML = `<button class="src-cluster" aria-expanded="false"><span class="favs">${favs}</span><span>${list.length} ${esc(t("sources"))}</span>${ICON.chev}</button>
    <div class="srcs hidden">${list.map((s, i) => {
      const inner = `<div class="s-top">${favicon(s.domain, s.kind)}<span class="s-dom">${esc(s.domain || (s.kind === "file" ? "your file" : ""))}</span>${s.kind && s.kind !== "web" ? `<span class="tag">${esc(s.kind)}</span>` : ""}<span class="num">${s.n}</span></div><div class="s-title">${esc(s.title)}</div>${s.date ? `<div class="s-date">${esc(ago(s.date))}</div>` : ""}`;
      return s.kind === "file" || !s.url ? `<button class="src" data-i="${i}">${inner}</button>` : `<a class="src" href="${esc(safeUrl(s.url))}" target="_blank" rel="noopener noreferrer">${inner}</a>`;
    }).join("")}</div>`;
  const btn = $(".src-cluster", box);
  btn.onclick = () => { const open = $(".srcs", box).classList.toggle("hidden") === false; btn.setAttribute("aria-expanded", open); btn.classList.toggle("open", open); };
  $$("button.src", box).forEach((b) => (b.onclick = () => openSheet(citationBody(list[+b.dataset.i], null, false), { title: `Source ${list[+b.dataset.i].n}` })));
}

function renderImages(box, list) {
  const imgs = list.filter((im) => /^https:\/\//.test(im.thumbnail || ""));
  if (!imgs.length) { box.innerHTML = ""; return; }
  box.innerHTML = `<button class="img-stack" aria-label="Show ${imgs.length} images">${imgs.slice(0, 3).map((im, i) => `<img src="${esc(im.thumbnail)}" alt="" style="--i:${i}" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">`).join("")}<span>${imgs.length} images</span></button>
    <div class="imgs hidden">${imgs.map((im) => `<a href="${esc(safeUrl(im.url))}" target="_blank" rel="noopener noreferrer"><img src="${esc(im.thumbnail)}" alt="${esc(im.title || "")}" loading="lazy" referrerpolicy="no-referrer" onerror="this.parentNode.remove()"><span>${esc(im.domain || "")}</span></a>`).join("")}</div>`;
  const btn = $(".img-stack", box);
  btn.onclick = () => { $(".imgs", box).classList.toggle("hidden"); btn.classList.toggle("open"); };
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
    row.innerHTML = `<div class="turn"></div>${timeEl()}`;
    threadEl().append(row);
    mountRun($(".turn", row), { id: d.run_id }, { onDone: () => toBottom() });
    toBottom(true);
    S.mode = "ask";
  } catch (ex) { toast(ex.message); }
  finally { S.busy = false; syncComposer(); }
}

// ------------------------------------------------------------------ views
function renderMessages(messages) {
  lastDay = "";
  let lastQ = "";
  let lastTurn = null;
  messages.forEach((m, i) => {
    const next = messages[i + 1];
    if (m.role === "user" && m.data.kind !== "run" && (!next || next.role === "user")) {
      rowMe(m.content, { created: m.created });
      unanswered(m.content);
      return;
    }
    if (m.role === "user") {
      rowMe(m.content, { created: m.created, mode: m.data.kind === "run" ? m.data.mode : "" });
      lastQ = m.data.kind === "run" ? "" : m.content;
    } else if (m.data.kind === "run" && m.data.run_id) {
      const row = document.createElement("div");
      row.className = "row bot wide";
      row.innerHTML = `<div class="turn"></div>${timeEl(m.created)}`;
      threadEl().append(row);
      mountRun($(".turn", row), { id: m.data.run_id });
      lastTurn = null;
    } else if (S.scope === "shared" && m.data.kind === "run") {
      /* run cards are private */
    } else {
      lastTurn = makeTurn({ ...m.data, answer: m.content || (m.data.error ? "" : m.content) }, { created: m.created });
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

function hero() {
  const name = prefs.get("name", "");
  const sugg = ["How do mRNA vaccines work?", "Is intermittent fasting effective?", "What changed in India's DPDP rules?", "Compare EV and petrol running costs in India"];
  return `<div class="hero" id="hero">${LOGO.replace('class="logo"', 'class="logo big"')}
    <h1>${esc(t("hello"))}${name ? ", " + esc(name) : ""}</h1><p>${esc(t("hello_sub"))}</p>
    <div class="suggest">${sugg.map((s) => `<button class="sugg" data-s="${esc(s)}">${ICON.spark}<span>${esc(s)}</span></button>`).join("")}</div></div>`;
}
function clearHero() { const h = $("#hero", view); if (h) h.remove(); }
function wireHero() { $$("[data-s]", view).forEach((b) => (b.onclick = () => sendAsk(b.dataset.s))); }

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
  if (messages.length) { renderMessages(messages); toBottom(true); }
  else { threadEl().insertAdjacentHTML("beforebegin", hero()); wireHero(); }
  syncComposer();
  if (q) { ta().value = q; autoGrow(); }
  if (App.pendingAsk) { const [pq, pf] = App.pendingAsk; App.pendingAsk = null; sendAsk(pq, pf); return; }
  if (matchMedia("(hover:hover)").matches) ta().focus({ preventScroll: true });
}

function threadHeader(title, sub = "", backHref = "/") {
  return `<div class="page-h"><a class="icon-btn sm" href="${backHref}" data-nav aria-label="Back">${ICON.back}</a>
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
  box.innerHTML = `<button class="icon-btn sm" id="th-share" aria-label="Share">${ICON.share}</button><button class="icon-btn sm" id="th-more" data-menu aria-label="More">${ICON.more}</button>`;
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
  box.innerHTML = `<button class="btn sm" id="pj-files">${ICON.folder}Files</button><button class="icon-btn sm" id="pj-more" data-menu aria-label="More">${ICON.more}</button>`;
  $("#pj-files").onclick = () => App.projectFiles(pid);
  $("#pj-more").onclick = (e) => openMenu(e.currentTarget, [["instructions", "Instructions", "How answers here should be written"], ["new", "New thread", "Start fresh in this project"], ["rename", "Rename project"], ["delete", "Delete project", "and its files and threads"]], (k) => App.projectAction(pid, k, d.project));
  const tid = threadId || (d.threads[0] && d.threads[0].thread_id);
  let messages = [];
  if (tid && threadId !== "new") {
    try { const td = await jfetch(`/api/threads/${tid}?limit=200`); S.thread = td.thread; messages = td.messages; } catch { /* start fresh */ }
  }
  if (messages.length) { renderMessages(messages); toBottom(true); }
  else {
    threadEl().insertAdjacentHTML("beforebegin", `<div class="hero small" id="hero">${ICON.folder}<h1>${esc(d.project.name)}</h1><p>${S.files ? "Ask about your files, the web, or both." : "Add files to ask about them, or ask the web."}</p>
      <div class="suggest"><button class="sugg" id="hero-files">${ICON.clip}<span>${S.files ? "Manage files" : "Add files"}</span></button></div></div>`);
    $("#hero-files").onclick = () => App.projectFiles(pid);
  }
  syncComposer();
}
export function closeAll() { closeSheet(); }
