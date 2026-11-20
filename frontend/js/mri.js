import { $, $$, api, confirmBox, esc, fmtDate, go, icon, makePdf, state, toast, upload, wireDrop } from "./core.js";
import { startWith } from "./chat.js";

const CLS = { glioma: "Glioma", meningioma: "Meningioma", notumor: "No tumour", pituitary: "Pituitary" };
let el;

export async function show(root, arg) {
  el = root;
  el.innerHTML = `<div class="wrap split">
    <div class="stack">
      <label class="drop" id="mriDrop"><input type="file" id="mriFile" accept=".png,.jpg,.jpeg">${icon("brain")}<b>Upload a brain MRI slice</b>
        <span class="tiny">One greyscale JPG/PNG slice · colour photos are rejected</span></label>
      <div class="banner info tiny">Neuro module only. Research classifier trained on a public dataset (glioma, meningioma, pituitary tumour, no tumour). It cannot diagnose — a radiologist must read the full scan.</div>
      ${state.config.mri_model ? "" : `<div class="banner warn tiny">The MRI model file is not installed on this server, so scans are stored but not classified.</div>`}
      <div class="card" style="padding:.7rem"><div class="menu-h">Your scans</div><div class="list hist" id="mriList"><div class="skel"></div></div></div>
    </div>
    <div id="mriMain"><div class="card empty"><h3>No scan selected</h3><p>Upload an MRI slice to see the predicted pattern, the class probabilities and a Grad-CAM heatmap showing where the model looked.</p></div></div>
  </div>`;
  wireDrop($("#mriDrop"), $("#mriFile"), handle);
  const scans = await loadList();
  const id = arg && !isNaN(+arg) ? +arg : scans[0]?.id;
  if (id) openScan(id);
}

async function loadList() {
  const scans = await api("/api/mri");
  $("#mriList").innerHTML = scans.length ? scans.map((s) => `<button class="li" data-id="${s.id}">${icon("brain")}<span class="grow"><b>${esc(s.filename)}</b>
    <span class="tiny muted">${esc(fmtDate(s.created_at))}</span></span><span class="badge">${s.available ? `${esc(CLS[s.label])} ${Math.round(s.confidence * 100)}%` : "—"}</span></button>`).join("")
    : `<p class="tiny muted" style="padding:.4rem">Your scans will appear here.</p>`;
  $$("#mriList [data-id]").forEach((b) => b.addEventListener("click", () => go(`#mri/${b.dataset.id}`)));
  return scans;
}

async function handle(file) {
  $("#mriMain").innerHTML = `<div class="card"><p><span class="spinner"></span> Analysing ${esc(file.name)}…</p></div>`;
  try { const r = await upload("/api/mri", file); await loadList(); go(`#mri/${r.id}`); }
  catch (e) { $("#mriMain").innerHTML = `<div class="card"><div class="banner crit">${esc(e.message)}</div></div>`; }
}

async function openScan(id) {
  let r;
  try { r = await api(`/api/mri/${id}`); } catch (e) { $("#mriMain").innerHTML = `<div class="card"><p class="err">${esc(e.message)}</p></div>`; return; }
  $$("#mriList .li").forEach((b) => b.classList.toggle("on", +b.dataset.id === id));
  const img = r.image_png ? `<figure><img src="data:image/png;base64,${r.image_png}" alt="MRI slice"><figcaption>Your image</figcaption></figure>` : "";
  if (!r.available) {
    $("#mriMain").innerHTML = `<div class="card stack"><h2 style="font-size:1.4rem">${esc(r.filename)}</h2><div class="scan-pair">${img}</div><div class="banner warn">${esc(r.message)}</div></div>`;
    return;
  }
  const probs = Object.entries(r.probabilities).sort((a, b) => b[1] - a[1]).map(([k, p]) =>
    `<div class="prob ${k === r.label ? "top" : ""}"><span>${CLS[k]}</span><div class="bar"><i style="width:${(p * 100).toFixed(1)}%"></i></div><span class="num">${(p * 100).toFixed(0)}%</span></div>`).join("");
  $("#mriMain").innerHTML = `<div class="card stack">
    <div class="between"><div><p class="muted small" style="margin:0">${esc(r.filename)} · ${esc(fmtDate(r.created_at))}</p><div class="big-label">${esc(r.display)}</div></div>
      <div class="row"><button class="btn primary" id="explain">${icon("chat")} Explain</button><button class="btn" id="pdf">${icon("pdf")} PDF</button>
      <button class="icon-btn" id="del" title="Delete scan">${icon("trash")}</button></div></div>
    ${r.confidence < 0.6 ? `<div class="banner warn">Low confidence (${Math.round(r.confidence * 100)}%): the model is not sure about this image. Don't rely on it.</div>` : ""}
    <div class="scan-pair">${img}<figure><img src="data:image/png;base64,${r.heatmap_png}" alt="Grad-CAM heatmap"><figcaption>Grad-CAM: where the model looked</figcaption></figure></div>
    <div>${probs}</div></div>`;
  $("#explain").addEventListener("click", () => startWith({ mri: { id: r.id, filename: r.filename },
    message: state.audience === "doctor" ? "Summarise this MRI classifier result for a clinician, with relevant research." : "What does this MRI result mean, in simple words?" }));
  $("#pdf").addEventListener("click", (e) => makePdf({ mri_id: r.id, button: e.currentTarget }));
  $("#del").addEventListener("click", async () => {
    if (!(await confirmBox("Delete this scan?", "The prediction and heatmap will be removed.", "Delete", true))) return;
    await api(`/api/mri/${r.id}`, { method: "DELETE" }); toast("Scan deleted"); go("#mri");
  });
}
