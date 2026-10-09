// The live work card (like a handoff window): a rounded dark glass card showing the
// sources being read as tiles and the step list, with floating round glass buttons,
// and under it a translucent status pill whose one short phrase cross-fades as work moves.
// When the work finishes it folds into a small "done" pill that can show the steps again.
import { $, $$, esc, ICON, favicon } from "./core.js";

const host = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } };
export { host };

// steps: [{id, label}] ; buttons: [{id, icon, label, onClick}]
export function workCard(slot, { title = "Working on it", steps = [], buttons = [], started = Date.now() / 1000 } = {}) {
  slot.innerHTML = `<div class="ho enter">
      <div class="ho-card">
        <div class="ho-top"><span class="ho-dot"></span><span class="ho-t"></span><span class="ho-time"></span></div>
        <div class="ho-tiles"></div>
        <ol class="ho-steps"></ol>
        <div class="ho-more hidden"></div>
        <div class="ho-fab">${buttons.map((b) => `<button type="button" class="fab-g" data-b="${esc(b.id)}" aria-label="${esc(b.label)}" title="${esc(b.label)}">${b.icon}</button>`).join("")}</div>
      </div>
      <div class="status-pill" aria-live="polite"><span class="sp on"></span><span class="sp"></span></div>
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

  const drawSteps = () => {
    $(".ho-steps", slot).innerHTML = steps.map((s) => {
      const st = state[s.id] || "";
      if (st === "skip") return "";
      return `<li class="${st}"><i></i><span>${esc(s.label)}${state[s.id + "_n"] ? ` <small>${esc(state[s.id + "_n"])}</small>` : ""}</span></li>`;
    }).join("");
  };
  const drawTiles = () => {
    const box = $(".ho-tiles", slot);
    if (!tiles.length) {
      box.innerHTML = (found ? `<span class="tile count"><b>${found}</b><small>sources found</small></span>` : "") + Array.from({ length: found ? 3 : 4 }, (_, i) => `<span class="tile ph" style="--d:${i}"></span>`).join("");
      return;
    }
    const shown = tiles.length > 4 ? tiles.slice(0, 3) : tiles;
    box.innerHTML = shown.map((x, i) => `<span class="tile ${x.done ? "read" : ""}" style="--d:${i}" title="${esc(x.title || x.domain || "")}">${favicon(x.domain, x.kind)}<b>${esc((x.domain || (x.kind === "file" ? "your file" : "")).replace(/^www\./, ""))}</b></span>`).join("")
      + (tiles.length > shown.length ? `<span class="tile more">+${tiles.length - shown.length}</span>` : "");
  };

  const api = {
    el: slot,
    title(text) { $(".ho-t", slot).textContent = text; },
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
    more(html) { const m = $(".ho-more", slot); m.innerHTML = html; },
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
  slot.innerHTML = `<div class="done-wrap"><button type="button" class="done-pill" aria-expanded="false">${ok ? `<span class="ok-dot">${ICON.check}</span>` : `<span class="ok-dot off">${ICON.x}</span>`}<span>${esc(summary)}</span>${ICON.chev}</button>
    <ol class="steps glass-list hidden">${steps.filter((s) => state[s.id] !== "skip").map((s) => `<li class="${state[s.id] || "done"}"><i></i><span>${esc(s.label)}${state[s.id + "_n"] ? ` <small>${esc(state[s.id + "_n"])}</small>` : ""}</span></li>`).join("")}</ol></div>`;
  const b = $(".done-pill", slot);
  b.onclick = () => { const open = $(".steps", slot).classList.toggle("hidden") === false; b.setAttribute("aria-expanded", open); };
}
