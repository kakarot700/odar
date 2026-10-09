// Projects tab: list, create, rename; each project opens its own thread and a files sheet.
import { $, $$, esc, ICON, App, jfetch, jpost, toast, openSheet, closeSheet, ago, openMenu } from "./core.js";
import { uploadToProject } from "./chat.js";

export async function showProjects(view, { add } = {}) {
  view.innerHTML = `<div class="page-h"><div class="ph-t"><h1>Projects</h1><small>Files, instructions and a thread for each</small></div>
    <div class="ph-a"><button class="btn primary sm" id="pj-new">${ICON.plus}New</button></div></div>
    <div id="pj-list" class="proj-grid"><div class="skel tall"></div><div class="skel tall"></div></div>`;
  $("#pj-new").onclick = () => createSheet();
  if (add) createSheet();
  try {
    const { projects } = await jfetch("/api/projects");
    const box = $("#pj-list", view);
    box.innerHTML = projects.length ? projects.map((p) => `<div class="proj">
        <a class="proj-main" href="/p/${p.project_id}" data-nav><span class="proj-ic">${esc((p.name[0] || "P").toUpperCase())}</span>
          <span class="proj-t">${esc(p.name)}<small>${p.files} file${p.files === 1 ? "" : "s"} · ${esc(ago(p.updated || p.created))}</small>
          ${p.instructions ? `<span class="proj-ins">${esc(p.instructions.slice(0, 90))}</span>` : ""}</span></a>
        <button class="icon-btn sm" data-more="${p.project_id}" data-menu aria-label="Project options">${ICON.more}</button></div>`).join("")
      : `<div class="empty-card">${ICON.folder}<h3>No projects yet</h3><p>A project keeps your files (PDF, DOCX, TXT, MD) and custom instructions together. Questions inside it can search your files, the web, or both.</p><button class="btn primary" id="pj-first">${ICON.plus}Create a project</button></div>`;
    const first = $("#pj-first", view); if (first) first.onclick = () => createSheet();
    $$("[data-more]", box).forEach((b) => (b.onclick = (e) => {
      const p = projects.find((x) => x.project_id === b.dataset.more);
      openMenu(e.currentTarget, [["open", "Open"], ["files", "Files"], ["rename", "Rename"], ["delete", "Delete", "and its files and threads"]], (k) => {
        if (k === "open") App.go(`/p/${p.project_id}`);
        else if (k === "files") filesSheet(p.project_id);
        else projectAction(p.project_id, k, p, () => showProjects(view));
      });
    }));
  } catch (ex) { $("#pj-list", view).innerHTML = `<div class="empty">${esc(ex.message)}</div>`; }
}

function createSheet() {
  const body = openSheet(`<form id="pj-form" class="form">
      <label>Name<input class="field" name="name" maxlength="120" placeholder="e.g. Class 12 biology" required></label>
      <label>Custom instructions <small>optional</small><textarea class="field" name="ins" rows="3" placeholder="e.g. Explain for a class 12 student; prefer Indian sources."></textarea></label>
      <button class="btn primary block">Create project</button></form>`, { title: "New project" });
  $("[name=name]", body).focus();
  $("#pj-form", body).onsubmit = async (e) => {
    e.preventDefault();
    try {
      const { project } = await jpost("/api/projects", { name: e.target.name.value, instructions: e.target.ins.value });
      closeSheet();
      App.go(`/p/${project.project_id}`);
    } catch (ex) { toast(ex.message); }
  };
}

export async function projectAction(pid, k, project, after) {
  if (k === "rename") {
    const n = prompt("Project name", project.name);
    if (n && n.trim()) { await jpost(`/api/projects/${pid}`, { name: n }, "PATCH"); toast("Renamed"); (after || (() => App.route()))(); }
  } else if (k === "delete") {
    if (!confirm("Delete this project, its files and its threads?")) return;
    await jfetch(`/api/projects/${pid}`, { method: "DELETE" });
    toast("Project deleted");
    if (after) after(); else App.go("/projects");
  } else if (k === "new") {
    App.go(`/p/${pid}?t=new`);
  } else if (k === "instructions") {
    const body = openSheet(`<form id="ins-form" class="form"><label>How should answers in this project be written?<textarea class="field" name="ins" rows="5">${esc(project.instructions || "")}</textarea></label><button class="btn primary block">Save</button></form>`, { title: "Instructions" });
    $("#ins-form", body).onsubmit = async (e) => {
      e.preventDefault();
      await jpost(`/api/projects/${pid}`, { instructions: e.target.ins.value }, "PATCH");
      project.instructions = e.target.ins.value;
      closeSheet(); toast("Instructions saved");
    };
  }
}

export async function filesSheet(pid) {
  const body = openSheet(`<div id="files-body"><div class="empty"><span class="spin"></span></div></div>`, { title: "Files", tall: true });
  const draw = async () => {
    const d = await jfetch(`/api/projects/${pid}`);
    $("#files-body", body).innerHTML = `<p class="note">PDF, DOCX, TXT or MD, up to 10 MB each, 20 per project. Text is kept so ODAR can search it, until you remove the file.</p>
      <div class="list flat">${d.files.length ? d.files.map((f) => `<div class="li"><span class="li-ic">${ICON.file}</span><span class="lt">${esc(f.name)}<small>${Math.max(1, Math.round(f.chars / 1000))}k characters · ${esc(ago(f.created))}</small></span><button class="btn sm ghost" data-del="${f.file_id}">Remove</button></div>`).join("") : `<div class="empty">No files yet.</div>`}</div>
      <label class="btn primary block upload">${ICON.plus}Add a file<input type="file" id="pj-file" accept=".pdf,.docx,.txt,.md" hidden></label>
      ${d.threads.length > 1 ? `<h4>Threads in this project</h4><div class="list flat">${d.threads.map((x) => `<a class="li" href="/p/${pid}?t=${x.thread_id}" data-nav><span class="lt">${esc(x.title)}<small>${esc(ago(x.updated))}</small></span></a>`).join("")}</div>` : ""}`;
    $$("[data-del]", body).forEach((b) => (b.onclick = async () => {
      await jfetch(`/api/projects/${pid}/files/${b.dataset.del}`, { method: "DELETE" });
      toast("File removed"); draw(); App.refreshProjectCount?.(pid);
    }));
    $$("a[data-nav]", body).forEach((a) => a.addEventListener("click", closeSheet));
    $("#pj-file", body).onchange = async (e) => {
      const f = e.target.files[0]; e.target.value = "";
      if (!f) return;
      if (f.size > 10_000_000) { toast("Files can be up to 10 MB"); return; }
      if (await uploadToProject(pid, f)) draw();
    };
  };
  draw().catch((ex) => ($("#files-body", body).innerHTML = `<div class="empty">${esc(ex.message)}</div>`));
}
