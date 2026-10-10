// Small, safe Markdown renderer: headings, lists, quotes, rules, pipe tables, fenced code
// (a code card with Copy), links, bold/italic/code. Everything is escaped first; only whitelisted tags are produced.
import { esc } from "./core.js";

const LINK = (u, label) => `<a href="${u}" target="_blank" rel="noopener noreferrer">${label}</a>`;

export function inlineMd(text, { cites = null } = {}) {
  let s = esc(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/(^|[\s(])\*([^*\s][^*]*?)\*(?=[\s).,;:!?]|$)/g, "$1<i>$2</i>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (m, a, u) => LINK(u, a))
    .replace(/(^|[\s(])(https?:\/\/[^\s<)"]+)/g, (m, p, u) => `${p}${LINK(u, u.replace(/^https?:\/\/(www\.)?/, "").slice(0, 60))}`);
  if (cites) s = s.replace(/\[(\d{1,2})\]/g, (m, n) => cites(+n));
  // keep a citation chip and the punctuation after it on one line
  if (cites) s = s.replace(/(<button class="cite"[^>]*>\d+<\/button>)([.,;:!?)]+)/g, '<span class="nw">$1$2</span>');
  return s;
}

function splitRow(line) {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  return s.split("|").map((c) => c.trim());
}
const isSep = (line) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);

// Returns HTML. ``opts.cites(n)`` renders a citation marker (in reading order).
export function renderMd(src, opts = {}) {
  const lines = String(src || "").replace(/\r/g, "").split("\n");
  const inl = (x) => inlineMd(x, opts);
  let out = "", list = null, quote = [];
  const closeList = () => { if (list) { out += `</${list}>`; list = null; } };
  const closeQuote = () => { if (quote.length) { out += `<blockquote>${quote.map(inl).join("<br>")}</blockquote>`; quote = []; } };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trimEnd();
    const fence = line.match(/^\s*```\s*([\w+#.-]*)/);
    if (fence) {
      closeList(); closeQuote();
      const code = [];
      for (i += 1; i < lines.length && !/^\s*```/.test(lines[i]); i++) code.push(lines[i]);
      out += codeCard(code.join("\n"), fence[1]);
      continue;
    }
    // pipe table: a row with "|" followed by a separator row
    if (line.includes("|") && i + 1 < lines.length && isSep(lines[i + 1])) {
      closeList(); closeQuote();
      const head = splitRow(line);
      const rows = [];
      i += 2;
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) { rows.push(splitRow(lines[i])); i++; }
      i--;
      out += `<div class="tcard" role="region" aria-label="Table" tabindex="0"><table><thead><tr>${head.map((h) => `<th>${inl(h)}</th>`).join("")}</tr></thead><tbody>${
        rows.map((r) => `<tr>${head.map((_, j) => `<td>${inl(r[j] || "")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
      continue;
    }
    const q = line.match(/^\s*>\s?(.*)/);
    if (q) { closeList(); quote.push(q[1]); continue; }
    closeQuote();
    const ul = line.match(/^\s*[-*•]\s+(.*)/);
    const ol = line.match(/^\s*\d+[.)]\s+(.*)/);
    if (ul || ol) {
      const kind = ul ? "ul" : "ol";
      if (list !== kind) { closeList(); out += `<${kind}>`; list = kind; }
      out += `<li>${inl((ul || ol)[1])}</li>`;
      continue;
    }
    closeList();
    if (!line.trim()) continue;
    const h = line.match(/^(#{1,4})\s+(.*)/);
    if (h) { const lv = Math.min(4, h[1].length + 1); out += `<h${lv}>${inl(h[2])}</h${lv}>`; }
    else if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) out += "<hr>";
    else out += `<p>${inl(line)}</p>`;
  }
  closeList(); closeQuote();
  return out;
}

// Hark's code card: a white card with the language and a Copy button over monospaced text.
export function codeCard(code, langName = "") {
  return `<div class="codecard tmpl"><div class="tmpl-h"><span>${esc(langName || "Code")}</span><button type="button" class="tmpl-copy" data-copy>Copy</button></div><pre data-copy-src><code>${esc(code)}</code></pre></div>`;
}

// A copyable text template (citations, a bibliography, a drafted message).
export function textTemplate(title, lines, { ordered = false } = {}) {
  const tag = ordered ? "ol" : "div";
  const body = ordered ? lines.map((l) => `<li>${esc(l)}</li>`).join("") : lines.map((l) => `<p>${esc(l)}</p>`).join("");
  return `<div class="tmpl"><div class="tmpl-h"><span>${esc(title)}</span><button type="button" class="tmpl-copy" data-copy>Copy</button></div><${tag} class="tmpl-b" data-copy-src>${body}</${tag}></div>`;
}

// Split a report into its title (first heading) and the rest.
export function splitTitle(src) {
  const m = String(src || "").match(/^\s*#{1,2}\s+(.+)\n?/);
  return m ? { title: m[1].trim(), body: String(src).slice(m[0].length) } : { title: "", body: String(src || "") };
}
