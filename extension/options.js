"use strict";
const $ = (s) => document.querySelector(s);
chrome.storage.sync.get(["server", "apiKey"]).then(({ server = "http://127.0.0.1:8000", apiKey = "" }) => {
  $("#server").value = server; $("#key").value = apiKey;
});
$("#save").onclick = async () => {
  const server = $("#server").value.trim().replace(/\/$/, "");
  try {
    const origin = new URL(server).origin + "/*";
    const ok = await chrome.permissions.request({ origins: [origin] });
    if (!ok) { $("#msg").textContent = "Permission to reach the server was declined."; return; }
  } catch { $("#msg").textContent = "Enter a valid server URL."; return; }
  await chrome.storage.sync.set({ server, apiKey: $("#key").value.trim() });
  $("#msg").textContent = "Saved.";
};
