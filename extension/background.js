// Right-click selected text -> "Check citations with ODAR"
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({ id: "odar-check", title: "Check citations with ODAR", contexts: ["selection"] });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== "odar-check") return;
  // Re-read the selection with its links (the context menu only gives plain text).
  let text = info.selectionText || "";
  try {
    const [{ result }] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: selectionWithLinks });
    if (result && result.length > text.length / 2) text = result;
  } catch (e) { /* restricted page: plain text is fine */ }
  await chrome.storage.local.set({ pendingText: text, pendingUrl: tab.url });
  chrome.action.openPopup?.();
});

function selectionWithLinks() {
  const sel = window.getSelection();
  if (!sel || !sel.rangeCount) return "";
  const box = document.createElement("div");
  box.append(sel.getRangeAt(0).cloneContents());
  box.querySelectorAll("a[href]").forEach((a) => {
    if (/^https?:/.test(a.href)) a.textContent = `${a.textContent} (${a.href})`;
  });
  return box.innerText;
}
