"use strict";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function settings() {
  const { server = "http://127.0.0.1:8000", apiKey = "" } = await chrome.storage.sync.get(["server", "apiKey"]);
  return { server: server.replace(/\/$/, ""), apiKey };
}

async function api(path, opts = {}) {
  const { server, apiKey } = await settings();
  if (!apiKey) throw new Error("Add your ODAR API key in Settings first.");
  const res = await fetch(server + path, { ...opts, headers: { ...(opts.headers || {}), Authorization: `Bearer ${apiKey}` } });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Server error ${res.status}`);
  return data;
}

async function check(text) {
  $("#out").innerHTML = "";
  $("#status").textContent = "Starting…";
  const fd = new FormData();
  fd.append("mode", "check");
  fd.append("text", text);
  const { run_id, share_token } = await api("/api/runs", { method: "POST", body: fd });
  for (;;) {
    const { run } = await api(`/api/runs/${run_id}`);
    $("#status").textContent = run.stage || run.status;
    if (run.status === "COMPLETE") return show(run.result, share_token);
    if (run.status === "FAILED") throw new Error(run.error || "check failed");
    await new Promise((r) => setTimeout(r, 2500));
  }
}

async function show(r, token) {
  const { server } = await settings();
  $("#status").innerHTML = `<span class="score">${r.trust_score ?? "n/a"}</span> trust score · ${esc(r.grade)} · <a href="${server}/r/${token}" target="_blank">full report</a>`;
  $("#out").innerHTML = (r.claims || []).map((c) => `<div class="claim"><span class="v v-${esc(c.verdict.split(" ")[0])}">${esc(c.verdict)}</span> ${esc(c.claim)}</div>`).join("");
}

$("#go").onclick = () => {
  const text = $("#text").value.trim();
  if (text.length < 20) { $("#status").textContent = "Paste or select a longer answer."; return; }
  check(text).catch((e) => { $("#status").textContent = e.message; });
};

$("#page").onclick = async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: () => {
      const root = document.querySelector("article, main") || document.body;
      const box = root.cloneNode(true);
      box.querySelectorAll("script,style,nav,footer").forEach((n) => n.remove());
      box.querySelectorAll("a[href]").forEach((a) => { if (/^https?:/.test(a.href)) a.textContent = `${a.textContent} (${a.href})`; });
      return box.innerText.slice(0, 60000);
    },
  });
  $("#text").value = result || "";
};

chrome.storage.local.get(["pendingText"]).then(({ pendingText }) => {
  if (pendingText) { $("#text").value = pendingText; chrome.storage.local.remove(["pendingText", "pendingUrl"]); }
});
