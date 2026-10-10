// The live work card, after Hark's Handoff card: a pale frosted container that shows the pages
// being worked on as white page cards in a row; inside it, bottom left, a white status pill with
// a dotted spinner and one short phrase that cross-fades as the work moves, and round white
// buttons on the right (a "Steps" pill to expand, stop or minimise). Under the container a ghost
// pill names the current step and the elapsed time. When the work finishes it folds into a small
// ghost "done" pill that can show the steps again.
import { $, $$, esc, ICON, favicon, reducedMotion } from "./core.js";

const host = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } };
export { host };

// steps: [{id, label}] ; buttons: [{id, icon, label, onClick}]
// A ring of eight dots that turns in steps (Hark's status spinner).
export const DOTS = `<svg class="dots" viewBox="0 0 20 20" aria-hidden="true">${Array.from({ length: 8 }, (_, i) => {
  const a = (i / 8) * Math.PI * 2;
  return `<circle cx="${(10 + 7 * Math.sin(a)).toFixed(2)}" cy="${(10 - 7 * Math.cos(a)).toFixed(2)}" r="1.55" fill="currentColor" opacity="${(0.18 + (0.82 * i) / 7).toFixed(2)}"/>`;
}).join("")}</svg>`;

// steps: [{id, label}] ; buttons: [{id, icon, label, text?, onClick}] (text makes a labelled pill)
export function workCard(slot, { title = "Working on it", steps = [], buttons = [], started = Date.now() / 1000 } = {}) {
  slot.innerHTML = `<div class="ho enter">
      <div class="ho-card">
        <div class="ho-tiles"></div>
        <div class="ho-more hidden"><ol class="ho-steps"></ol><div class="ho-more-x"></div></div>
        <div class="ho-bar">
          <div class="status-pill" aria-live="polite">${DOTS}<span class="sp-w"><span class="sp on"></span><span class="sp"></span></span></div>
          <div class="ho-fab">${buttons.map((b) => `<button type="button" class="fab-g${b.text ? " txt" : ""}" data-b="${esc(b.id)}" aria-label="${esc(b.label)}" title="${esc(b.label)}">${b.icon}${b.text ? `<span>${esc(b.text)}</span>` : ""}</button>`).join("")}</div>
        </div>
      </div>
      <div class="ho-sub"><span class="ho-t"></span><span class="ho-time"></span></div>
    </div>`;
  const card = $(".ho-card", slot);
  const pill = $(".status-pill", slot);
  const state = {};
  let cur = "", timer = 0, tiles = [], found = 0;
  buttons.forEach((b) => { const el = $(`[data-b="${b.id}"]`, slot); if (el) el.onclick = (e) => { e.stopPropagation(); b.onClick(el, api); }; });

  const tick = () => {
    if (!slot.isConnected) { clearInterval(timer); return; }
    const s = Math.max(0, Math.round(Date.now() / 1000 - started));
    $(".ho-time", slot).textContent = s < 60 ? `${s}s` : `${Math.floor(s / 60)} min`;
  };
  timer = setInterval(tick, 1000); tick();

  // Steps are updated in place (class + text), so a step turning "now" -> "done" cross-fades
  // through CSS transitions instead of the whole list being rebuilt.
  const stepHtml = (s) => `${esc(s.label)}${state[s.id + "_n"] ? ` <small>${esc(state[s.id + "_n"])}</small>` : ""}`;
  const drawSteps = () => {
    const ol = $(".ho-steps", slot);
    steps.forEach((s) => {
      const st = state[s.id] || "";
      let li = ol.querySelector(`[data-s="${s.id}"]`);
      if (st === "skip") { if (li) li.remove(); return; }
      if (!li) { li = document.createElement("li"); li.dataset.s = s.id; li.innerHTML = "<i></i><span></span>"; ol.append(li); }
      if (li.className !== st) li.className = st;
      const h = stepHtml(s);
      if (li._h !== h) { li._h = h; li.lastChild.innerHTML = h; }
    });
  };
  // Page cards: a white mini page per source (site, title, a few text lines), newest last.
  const page = (x, i, cls = "") => `<span class="tile page ${cls}" style="--d:${i}" title="${esc(x.title || x.domain || "")}">
      <span class="pg-h">${favicon(x.domain, x.kind)}<em>${esc((x.domain || (x.kind === "file" ? "your file" : "")).replace(/^www\./, ""))}</em></span>
      <b class="pg-t">${esc(x.title && !/^https?:/.test(x.title) ? x.title : (x.domain || "").replace(/^www\./, ""))}</b>
      <i class="pg-l"></i><i class="pg-l"></i><i class="pg-l s"></i></span>`;
  const drawTiles = () => {
    const box = $(".ho-tiles", slot);
    if (!tiles.length) {
      box._key = "";
      box.innerHTML = (found ? `<span class="tile page count"><b>${found}</b><small>sources found</small></span>` : "") + Array.from({ length: found ? 2 : 3 }, (_, i) => `<span class="tile page ph" style="--d:${i}"><i class="pg-l"></i><i class="pg-l s"></i><i class="pg-l"></i><i class="pg-l"></i></span>`).join("");
      return;
    }
    const shown = tiles.length > 6 ? tiles.slice(0, 5) : tiles;
    const firstOpen = shown.findIndex((x) => !x.done);
    // Same pages as before (e.g. all marked read when writing starts): restyle them in place so
    // they cross-fade instead of the row being rebuilt and dealt in again.
    const key = shown.map((x) => x.url || x.title || x.domain).join("|") + "#" + tiles.length;
    if (box._key === key) {
      $$(".tile.page:not(.more)", box).forEach((el, i) => {
        const x = shown[i]; if (!x) return;
        el.classList.toggle("read", !!x.done); el.classList.toggle("now", !x.done && i === firstOpen);
      });
      return;
    }
    box._key = key;
    box.innerHTML = shown.map((x, i) => page(x, i, x.done ? "read" : i === firstOpen ? "now" : "")).join("")
      + (tiles.length > shown.length ? `<span class="tile page more"><b>+${tiles.length - shown.length}</b><small>more</small></span>` : "");
  };

  const api = {
    el: slot,
    title(text) {
      const el = $(".ho-t", slot);
      if (el.textContent === text) return;
      el.textContent = text;
      if (!reducedMotion()) el.animate?.([{ opacity: 0, transform: "translateY(3px)" }, { opacity: 1, transform: "none" }], { duration: 200, easing: "cubic-bezier(.2,.8,.2,1)" });
    },
    // Cross-fade the status pill to a new phrase.
    status(text) {
      if (!text || text === cur) return;
      cur = text;
      const on = $(".sp.on", pill), off = $$(".sp", pill).find((x) => x !== on);
      off.textContent = text;
      off.classList.add("on"); on.classList.remove("on");
      clearTimeout(api._t); api._t = setTimeout(() => { if (!on.classList.contains("on")) on.textContent = ""; }, 450);
    },
    step(id, st, n) { state[id] = st; if (n != null) state[id + "_n"] = n; drawSteps(); },
    steps(map) { Object.assign(state, map); drawSteps(); },
    tiles(list, allDone = false, count = 0) { tiles = list.map((x) => ({ ...x, done: allDone || x.done })); found = count; drawTiles(); },
    more(html) { $(".ho-more-x", slot).innerHTML = html; },
    toggleBig() { const big = card.classList.toggle("big"); $(".ho-more", slot).classList.toggle("hidden", !big); return big; },
    minimize() { return slot.querySelector(".ho").classList.toggle("mini"); },
    // Fold into the small done pill.
    finish(summary, { ok = true } = {}) {
      clearInterval(timer);
      steps.forEach((s) => { if (state[s.id] !== "skip" && state[s.id] !== "fail") state[s.id] = ok ? "done" : state[s.id] === "now" ? "fail" : state[s.id] || ""; });
      donePill(slot, summary, steps, state, ok);
    },
    stopTimer() { clearInterval(timer); },
  };
  drawSteps(); drawTiles(); api.title(title);
  return api;
}

export function donePill(slot, summary, steps, state = {}, ok = true) {
  const fold = !!slot.querySelector(".ho"); // folding a live card: fade the pill in
  slot.innerHTML = `<div class="done-wrap${fold ? " d-in" : ""}"><button type="button" class="done-pill" aria-expanded="false">${ok ? `<span class="ok-dot">${ICON.check}</span>` : `<span class="ok-dot off">${ICON.x}</span>`}<span class="dp-t">${esc(summary)}</span>${ICON.chev}</button>
    <ol class="steps glass-list hidden">${steps.filter((s) => state[s.id] !== "skip").map((s) => `<li class="${state[s.id] || "done"}"><i></i><span>${esc(s.label)}${state[s.id + "_n"] ? ` <small>${esc(state[s.id + "_n"])}</small>` : ""}</span></li>`).join("")}</ol></div>`;
  const b = $(".done-pill", slot);
  b.onclick = () => { const open = $(".steps", slot).classList.toggle("hidden") === false; b.setAttribute("aria-expanded", open); };
}
