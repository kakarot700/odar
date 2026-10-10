// Settings sheet (opened from the avatar): Billing, Profile, Connected apps, System preferences, Security, Feedback, About, Sign out.
import { $, $$, esc, hueOf, ICON, App, jfetch, jpost, prefs, applyTheme, openSheet, closeSheet, toast, copyLink } from "./core.js";

function seg(name, options, cur) {
  return `<div class="seg" role="radiogroup" data-seg="${name}">${options.map(([v, label]) => `<button role="radio" aria-checked="${v === cur}" class="${v === cur ? "on" : ""}" data-v="${v}">${esc(label)}</button>`).join("")}</div>`;
}

export function avatarLetter() {
  const n = prefs.get("name", "").trim();
  return n ? esc(n[0].toUpperCase()) : "";
}
// The avatar: a monogram on a soft two-stop gradient whose hue comes from the name, so the
// same name always gets the same colour. No name yet: a neutral graphite disc with a person.
export function avatarHTML(cls = "") {
  const n = prefs.get("name", "").trim();
  return n ? `<span class="avatar mono ${cls}" style="--ah:${hueOf(n.toLowerCase())}">${avatarLetter()}</span>` : `<span class="avatar ${cls}">${ICON.user}</span>`;
}

// Settings, in Hark's order where ODAR has the thing: Billing (plan and usage), Profile,
// Connected apps (API keys), System preferences (language, text colour), Security (privacy and
// data), Feedback, About, Sign out. Each section is a white group under a grey heading.
export function openProfile() {
  const name = prefs.get("name", "");
  const body = openSheet(`
    <div class="prof-card">${avatarHTML("lg")}<div><b>${esc(name || "You")}</b><small>Free plan · data tied to this browser</small></div></div>
    <section class="ps"><div class="ps-h">Billing</div><div class="ps-g">
      <div class="plan"><div><b>Free</b><small>Free models only · every citation checked</small></div><span class="pill">Beta</span></div>
      <div id="ps-usage" class="usage"><div class="skel"></div></div></div></section>
    <section class="ps"><div class="ps-h">Profile</div><div class="ps-g">
      <label class="ps-row">Name shown in greetings<input class="field" id="ps-name" maxlength="40" value="${esc(name)}" placeholder="Your first name"></label>
      <p class="note">No account needed: your threads, projects and keys are tied to this browser.</p></div></section>
    <section class="ps"><div class="ps-h">Connected apps</div><div class="ps-g">
      <p class="note">API keys let scripts and the Chrome extension use ODAR: <code>Authorization: Bearer &lt;key&gt;</code>. Docs at <a href="/api/docs">/api/docs</a>.</p>
      <div id="ps-keys"><div class="skel"></div></div>
      <form id="ps-kform" class="inline-form"><input class="field" name="label" maxlength="60" placeholder="Label, e.g. chrome extension"><button class="btn sm black">${ICON.key}Create key</button></form>
      <div id="ps-kshow"></div></div></section>
    <section class="ps"><div class="ps-h">System preferences</div><div class="ps-g">
      <div class="ps-row">Language${seg("lang", [["en", "English"], ["hi", "हिंदी"]], prefs.get("lang", "en"))}</div>
      <p class="note">Hindi sets the app's labels and writes deep research reports in Hindi.</p>
      <div class="ps-row">Text colour${seg("theme", [["system", "Auto"], ["light", "Light"], ["dark", "Dark"]], prefs.get("theme", "system"))}</div>
      <p class="note">Auto follows the sky behind the app: light by day, dark at dusk and night.</p></div></section>
    <section class="ps"><div class="ps-h">Security</div><div class="ps-g">
      <p class="note">Files attached to Check are read in memory and never stored. Project files are kept as text until you delete them. Share links are unlisted and not indexed. Threads and runs are deleted after the retention period.</p>
      <div class="ps-links"><a href="/history" data-nav>${ICON.doc}Run history</a></div>
      <button class="btn danger block" id="ps-forget">Delete all my threads, projects, runs and keys</button></div></section>
    <section class="ps"><div class="ps-h">Feedback</div><div class="ps-g">
      <a class="ps-link" href="https://github.com/kakarot700/odar/issues" target="_blank" rel="noopener noreferrer"><span>Send feedback or report a bad citation</span>${ICON.arrow}</a></div></section>
    <section class="ps"><div class="ps-h">About</div><div class="ps-g">
      <p class="note"><b>ODAR</b> answers questions with sources and checks every citation against the page it cites: a local NLI model plus a verbatim-quote judge, on free models. Not offered on purpose: plagiarism detection or writing assignments.</p>
      <a class="ps-link" href="/api/docs" target="_blank" rel="noopener"><span>API docs</span>${ICON.arrow}</a></div></section>
    <button class="btn block ps-out" id="ps-signout">Sign out</button>`, { title: "Settings", tall: true });

  $("#ps-name", body).onchange = (e) => { prefs.set("name", e.target.value.trim()); App.refreshAvatar(); toast("Saved"); };
  $$("[data-seg]", body).forEach((g) => $$("button", g).forEach((b) => (b.onclick = () => {
    $$("button", g).forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-checked", x === b); });
    if (g.dataset.seg === "theme") { prefs.set("theme", b.dataset.v); applyTheme(b.dataset.v); }
    else { prefs.set("lang", b.dataset.v); document.documentElement.lang = b.dataset.v; App.relabel(); }
  })));
  $("#ps-signout", body).onclick = () => toast("Accounts are coming soon. Your data stays tied to this browser.");
  $("#ps-forget", body).onclick = async () => {
    if (!confirm("Delete every thread, project, run and API key tied to this browser? This can't be undone.")) return;
    await jfetch("/api/me", { method: "DELETE" });
    prefs.del("main");
    closeSheet(); toast("All your data was deleted"); ["home", "chat", "projects"].forEach((k) => App.dirty(k)); App.go("/");
  };
  $$("a[data-nav]", body).forEach((a) => a.addEventListener("click", closeSheet));
  $("#ps-kform", body).onsubmit = async (e) => {
    e.preventDefault();
    const box = $("#ps-kshow", body);
    try {
      const d = await jpost("/api/keys", { label: e.target.label.value });
      box.innerHTML = `<div class="keyshow"><small>Your new key (shown once)</small><code>${esc(d.key)}</code><button class="btn sm" id="ps-copy">Copy</button></div>`;
      $("#ps-copy", body).onclick = () => copyLink(d.key, "Key copied");
      e.target.label.value = "";
    } catch (ex) { box.innerHTML = `<p class="warn">${esc(ex.message)}</p>`; }
    keys();
  };
  const keys = async () => {
    const box = $("#ps-keys", body);
    try {
      const { keys: list } = await jfetch("/api/keys");
      box.innerHTML = list.length ? `<div class="list flat">${list.map((k) => `<div class="li"><span class="li-ic">${ICON.key}</span><span class="lt"><code>${esc(k.prefix)}…</code> ${esc(k.label)}<small>${k.used_today}/${k.daily_limit} runs today</small></span><button class="btn sm ghost" data-rev="${esc(k.prefix)}">Revoke</button></div>`).join("")}</div>` : `<p class="note">No keys yet.</p>`;
      $$("[data-rev]", box).forEach((b) => (b.onclick = async () => { await jfetch(`/api/keys/${b.dataset.rev}`, { method: "DELETE" }); keys(); }));
    } catch (ex) { box.innerHTML = `<p class="note">${esc(ex.message)}</p>`; }
  };
  const usage = async () => {
    const box = $("#ps-usage", body);
    try {
      const l = await jfetch("/api/limits");
      const row = (label, used, limit) => {
        const pct = limit ? Math.min(100, (100 * used) / limit) : 0;
        return `<div class="u-row"><span>${esc(label)}</span><b>${used}/${limit}</b></div><div class="meter ${pct > 85 ? "low" : pct > 60 ? "mid" : "good"}"><i style="width:${pct}%"></i></div>`;
      };
      box.innerHTML = row("Questions this hour", l.ask_used_this_hour, l.ask_hourly_limit) +
        (l.hourly_limit != null ? row("Deep runs this hour", l.used_this_hour, l.hourly_limit) : "") +
        row("Deep runs today", l.used_today, l.daily_limit) +
        (l.retention_days != null ? `<p class="note">History is kept for ${l.retention_days} days.</p>` : "");
    } catch (ex) { box.innerHTML = `<p class="note">${esc(ex.message)}</p>`; }
  };
  keys(); usage();
}
