import { $, $$, STATUS_TXT, api, confirmBox, esc, fmtDate, go, icon, makePdf, num, rangeBar, sparkline, state, toast, upload, wireDrop } from "./core.js";
import { startWith } from "./chat.js";

let el, list = [], current = null, filter = "all", tab = "report", catalogue = null;

export async function show(root, arg) {
  el = root;
  el.innerHTML = `<div class="wrap split">
    <div class="stack">
      <label class="drop" id="repDrop"><input type="file" id="repFile" accept=".pdf,.txt,.csv,.png,.jpg,.jpeg,.webp">
        ${icon("upload")}<b>Upload a report</b><span class="tiny">PDF, text or a clear phone photo · max 10 MB</span></label>
      <div class="row tiny muted" style="justify-content:center">Try a sample:
        <button class="chip" data-sample="cbc_report.txt">Anaemia CBC</button><button class="chip" data-sample="metabolic_report.txt">Sugar + lipids</button>
        <button class="chip" data-sample="thyroid_electrolytes_report.txt">Thyroid</button></div>
      <div class="card" style="padding:.7rem">
        <div class="seg" style="width:100%;margin-bottom:.5rem"><button data-tab="report" style="flex:1">Reports</button><button data-tab="trends" style="flex:1">${icon("chart")} Trends</button></div>
        <div class="list hist" id="repList"><div class="skel"></div><div class="skel"></div></div>
      </div>
    </div>
    <div id="repMain"></div>
  </div>`;
  wireDrop($("#repDrop"), $("#repFile"), handleUpload);
  $$("[data-sample]", el).forEach((b) => b.addEventListener("click", async () => {
    const blob = await (await fetch(`/static/samples/${b.dataset.sample}`)).blob();
    handleUpload(new File([blob], b.dataset.sample, { type: "text/plain" }));
  }));
  $$("[data-tab]", el).forEach((b) => b.addEventListener("click", () => go(b.dataset.tab === "trends" ? "#reports/trends" : current ? `#reports/${current.id}` : "#reports")));
  await loadList();
  if (arg === "trends") { tab = "trends"; renderTrends(); }
  else if (arg === "upload") { tab = "report"; emptyMain(); $("#repDrop").classList.add("over"); setTimeout(() => $("#repDrop")?.classList.remove("over"), 1600); }
  else if (arg && !isNaN(+arg)) { tab = "report"; openReport(+arg); }
  else if (list.length) { tab = "report"; openReport(list[0].id); }
  else { tab = "report"; emptyMain(); }
  $$("[data-tab]", el).forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
}

function emptyMain() {
  $("#repMain").innerHTML = `<div class="card empty"><h3>No report selected</h3><p>Upload a blood or lab report on the left. NeuroLens reads each value, compares it with its reference range and flags anything unusual.</p></div>`;
}

async function loadList() {
  list = await api("/api/reports");
  $("#repList").innerHTML = list.length ? list.map((r) => `<button class="li ${current && current.id === r.id ? "on" : ""}" data-id="${r.id}">
    ${icon(r.source.startsWith("photo") ? "camera" : "lab")}<span class="grow"><b>${esc(r.filename)}</b>
    <span class="tiny muted">${esc(fmtDate(r.report_date))}${r.date_printed ? "" : " (uploaded)"} · ${r.summary.total} tests</span></span>
    ${r.status === "needs_review" ? `<span class="badge b-review">check</span>` : r.summary.critical ? `<span class="badge b-critical_low">${r.summary.critical}</span>` : r.summary.flagged ? `<span class="badge b-high">${r.summary.flagged}</span>` : `<span class="badge b-normal">✓</span>`}</button>`).join("")
    : `<p class="tiny muted" style="padding:.4rem">Your reports will appear here.</p>`;
  $$("#repList [data-id]").forEach((b) => b.addEventListener("click", () => go(`#reports/${b.dataset.id}`)));
}

async function handleUpload(file) {
  const photo = /\.(png|jpe?g|webp)$/i.test(file.name);
  $("#repMain").innerHTML = `<div class="card"><p><span class="spinner"></span> ${photo ? "Reading the photo with OCR — this takes a few seconds…" : `Reading ${esc(file.name)}…`}</p></div>`;
  try {
    const r = await upload("/api/reports", file);
    await loadList();
    go(`#reports/${r.id}`);
    if (r.duplicate) toast("This report is already in your history — opened the existing copy");
    else if (r.status === "needs_review") toast(r.source.startsWith("photo") ? "Photo read — please check the values against your paper report" : "Some values look implausible — please check them");
  } catch (e) { $("#repMain").innerHTML = `<div class="card"><div class="banner crit">${esc(e.message)}</div></div>`; }
}

async function openReport(id) {
  try { current = await api(`/api/reports/${id}`); } catch (e) { $("#repMain").innerHTML = `<div class="card"><p class="err">${esc(e.message)}</p></div>`; return; }
  $$("#repList .li").forEach((b) => b.classList.toggle("on", +b.dataset.id === id));
  if (current.status === "needs_review") return renderReview();
  renderReport();
}

function renderReport() {
  const r = current, s = r.summary;
  const rows = r.results.filter((x) => filter === "all" || x.status !== "normal");
  const byCat = {}; rows.forEach((x) => (byCat[x.category] = byCat[x.category] || []).push(x));
  $("#repMain").innerHTML = `<div class="card stack">
    <div class="between"><div class="grow"><h2 style="font-size:1.4rem">${esc(r.filename)}</h2>
      <div class="row tiny muted" style="margin-top:.2rem">${esc(fmtDate(r.report_date))}${r.date_printed ? "" : " (upload date)"} · <span class="badge">${esc(r.source)}</span></div></div>
      <div class="row">
        <button class="btn primary" id="explain">${icon("chat")} Explain</button>
        <button class="btn" id="pdf">${icon("pdf")} PDF${state.lang !== "en" ? " + " + esc(state.config.languages[state.lang].native) : ""}</button>
        <button class="btn" id="cmp">${icon("chart")} Compare</button>
        <button class="icon-btn" id="edit" title="Correct values">${icon("edit")}</button>
        <button class="icon-btn" id="del" title="Delete report">${icon("trash")}</button></div></div>
    <div class="tiles"><div class="tile"><b>${s.total}</b><span>tests read</span></div><div class="tile ${s.flagged ? "high" : ""}"><b>${s.flagged}</b><span>outside range</span></div>
      <div class="tile ${s.critical ? "crit" : ""}"><b>${s.critical}</b><span>critical</span></div><div class="tile"><b>${s.categories.length}</b><span>panels</span></div></div>
    ${s.critical ? `<div class="banner crit"><b>Critical: ${esc(s.critical_tests.join(", "))}.</b>&nbsp;Please contact your doctor today.</div>` : ""}
    <div class="between"><h3>Results</h3><div class="seg" id="filter"><button data-f="all">All</button><button data-f="flag">Outside range</button></div></div>
    <div>${Object.entries(byCat).map(([cat, xs]) => `<div class="cat">${esc(cat)}</div>` + xs.map((x) => `<div class="lrow"><span>${esc(x.name)}</span>
      <span class="val num">${esc(num(x.value))}<small>${esc(x.unit)}</small></span><div class="rng-wrap">${rangeBar(x)}</div>
      <span class="badge b-${esc(x.status)}" style="justify-self:start">${STATUS_TXT[x.status]}</span></div>`).join("")).join("") || `<p class="muted">Everything is within range.</p>`}</div>
    <div id="cmpBox"></div></div>`;
  $$("#filter button").forEach((b) => { b.classList.toggle("on", b.dataset.f === filter); b.addEventListener("click", () => { filter = b.dataset.f; renderReport(); }); });
  $("#explain").addEventListener("click", () => startWith({ report: { id: r.id, filename: r.filename },
    message: state.audience === "doctor" ? "Summarise this report for my doctor." : "Please explain my blood report in simple words." }));
  $("#pdf").addEventListener("click", (e) => makePdf({ report_id: r.id, button: e.currentTarget }));
  $("#edit").addEventListener("click", renderReview);
  $("#cmp").addEventListener("click", compare);
  $("#del").addEventListener("click", async () => {
    if (!(await confirmBox("Delete this report?", "The values and any explanations of this report will be removed.", "Delete", true))) return;
    await api(`/api/reports/${r.id}`, { method: "DELETE" }); current = null; await loadList(); toast("Report deleted");
    list.length ? go(`#reports/${list[0].id}`) : emptyMain();
  });
}

async function compare() {
  const box = $("#cmpBox");
  const prev = await api(`/api/reports/${current.id}/history`);
  if (!prev.length) { box.innerHTML = `<div class="banner info">No earlier report to compare with. Upload an older report and it will appear here.</div>`; return; }
  const p = prev[0], old = Object.fromEntries(p.results.map((x) => [x.test_id, x]));
  const rows = current.results.filter((x) => old[x.test_id]);
  box.innerHTML = `<h3 class="mt">Compared with ${esc(p.filename)} (${esc(fmtDate(p.report_date))})</h3>
    <div class="tbl"><table class="cmp"><thead><tr><th>Test</th><th>Before</th><th>Now</th><th>Change</th><th>Status now</th></tr></thead><tbody>
    ${rows.map((x) => { const a = old[x.test_id].value, d = x.value - a; return `<tr><td>${esc(x.name)}</td><td class="num">${esc(num(a))}</td><td class="num"><b>${esc(num(x.value))}</b> ${esc(x.unit)}</td>
      <td class="num ${d > 0 ? "up" : d < 0 ? "down" : ""}">${d === 0 ? "—" : (d > 0 ? "▲ +" : "▼ ") + esc(num(Math.round(d * 1000) / 1000))}</td><td><span class="badge b-${esc(x.status)}">${STATUS_TXT[x.status]}</span></td></tr>`; }).join("")}
    </tbody></table></div>${rows.length ? "" : `<p class="muted">The two reports have no tests in common.</p>`}`;
  box.scrollIntoView({ behavior: "smooth", block: "start" });
}

/* Review step: photo reports (and corrections). The server recomputes every status. */
async function renderReview() {
  const r = current;
  catalogue = catalogue || await api("/api/catalogue");
  const photo = r.source.startsWith("photo");
  $("#repMain").innerHTML = `<div class="card stack">
    <div><h2 style="font-size:1.4rem">${photo ? "Check the values read from your photo" : r.status === "needs_review" ? "Check these values" : "Correct values"}</h2>
    ${r.summary.suspect_tests?.length ? `<div class="banner warn"><b>Please check:</b>&nbsp;${esc(r.summary.suspect_tests.join(", "))} look${r.summary.suspect_tests.length > 1 ? "" : "s"} implausible next to the printed range — often a unit like lakh/cumm read wrongly.</div>` : ""}
    <p class="muted small">${photo ? `Read by ${esc(r.source.replace("photo:", ""))}. OCR can misread digits, so compare each row with your paper report. Fix any value, remove wrong rows and add missed tests. NeuroLens explains the report only after you confirm.` : "Change a value or range if it was read wrongly. Status is recalculated automatically."}</p></div>
    <div class="row"><label class="field" style="max-width:220px">Report date<input type="date" id="repDate" value="${esc(r.report_date)}"></label></div>
    <div><div class="edit-row head"><span>Test (as read)</span><span>Value</span><span>Range low</span><span>Range high</span><span></span></div>
      <div id="editRows">${r.results.map(rowHtml).join("")}</div></div>
    <div class="row"><select class="input" id="addTest" style="max-width:320px"><option value="">+ Add a test that was missed…</option>
      ${catalogue.map((t) => `<option value="${esc(t.id)}">${esc(t.name)} (${esc(t.unit)})</option>`).join("")}</select></div>
    <div class="row" style="justify-content:flex-end"><button class="btn" id="cancelEdit">Cancel</button><button class="btn primary" id="saveEdit">${icon("check")} ${photo && r.status === "needs_review" ? "Confirm values" : "Save"}</button></div></div>`;
  const bindRemove = () => $$("#editRows [data-rm]").forEach((b) => (b.onclick = () => b.closest(".edit-row").remove()));
  bindRemove();
  $("#addTest").addEventListener("change", (e) => {
    const t = catalogue.find((x) => x.id === e.target.value); if (!t) return;
    $("#editRows").insertAdjacentHTML("beforeend", rowHtml({ test_id: t.id, name: t.name, unit: t.unit, value: "", ref_low: null, ref_high: null, raw: "added by you" }));
    e.target.value = ""; bindRemove(); $("#editRows .edit-row:last-child input").focus();
  });
  $("#cancelEdit").addEventListener("click", () => (r.status === "needs_review" ? toast("The report stays unconfirmed until you check it") : renderReport()));
  $("#saveEdit").addEventListener("click", async () => {
    const rows = $$("#editRows .edit-row").map((d) => ({ test_id: d.dataset.id, value: parseFloat($(".v", d).value),
      ref_low: $(".lo", d).value === "" ? null : parseFloat($(".lo", d).value), ref_high: $(".hi", d).value === "" ? null : parseFloat($(".hi", d).value) }));
    if (rows.some((x) => isNaN(x.value))) { toast("Every row needs a value (or remove the row)"); return; }
    try {
      current = await api(`/api/reports/${r.id}/results`, { method: "PUT", json: { results: rows, report_date: $("#repDate").value || "", confirm: true } });
      toast("Values saved"); await loadList(); renderReport();
    } catch (e) { toast(e.message); }
  });
}
const rowHtml = (x) => `<div class="edit-row ${x.suspect ? "suspect" : ""}" data-id="${esc(x.test_id)}" ${x.suspect ? 'title="This value looks implausible next to its printed range — check the unit"' : ""}><span><b class="small">${esc(x.name)}</b> <span class="tiny muted">${esc(x.unit)}</span><span class="raw" title="${esc(x.raw)}">${esc(x.raw)}</span></span>
  <input class="v num" type="number" step="any" value="${esc(x.value)}" aria-label="value"><input class="lo num" type="number" step="any" value="${x.ref_low ?? ""}" aria-label="range low">
  <input class="hi num" type="number" step="any" value="${x.ref_high ?? ""}" aria-label="range high"><button class="icon-btn" data-rm title="Remove row">${icon("x")}</button></div>`;

async function renderTrends() {
  current = null;
  $$("#repList .li").forEach((b) => b.classList.remove("on"));
  $("#repMain").innerHTML = `<div class="card"><div class="skel"></div><div class="skel"></div></div>`;
  const tr = await api("/api/trends");
  const multi = tr.filter((t) => t.points.length >= 2), single = tr.filter((t) => t.points.length < 2);
  $("#repMain").innerHTML = `<div class="stack"><div class="card"><div class="card-head"><div><h3>Trends over time</h3>
    <p class="muted small" style="margin:0">Each value across your confirmed reports, by report date. The green band is the reference range.</p></div></div>
    ${multi.length ? `<div class="trend-grid">${multi.map((t, i) => { const p = t.points, last = p[p.length - 1]; return `<button class="trend" data-i="${i}">
      <div class="between trend-h"><b title="${esc(t.name)}">${esc(t.name)}</b>${t.direction ? `<span class="badge b-${t.direction === "better" ? "better" : t.direction === "worse" ? "worse" : "x"}">${esc(t.direction)}</span>` : ""}</div>
      <div class="tiny muted">${p.length} results · latest <b class="num">${esc(num(last.value))}</b> ${esc(t.unit)}</div>${sparkline(p)}</button>`; }).join("")}</div>`
      : `<div class="empty"><h3>Not enough reports yet</h3><p>Trends need the same test in at least two reports. Upload an older report to start a trend.</p></div>`}</div>
    <div class="card hidden" id="bigChart"></div>
    ${single.length ? `<div class="card"><h3>Tests with one result so far</h3><p class="small muted">${single.map((t) => esc(t.name)).join(" · ")}</p></div>` : ""}</div>`;
  $$(".trend", el).forEach((b) => b.addEventListener("click", () => {
    const t = multi[+b.dataset.i], box = $("#bigChart");
    box.classList.remove("hidden");
    box.innerHTML = `<div class="card-head"><h3>${esc(t.name)} <span class="muted small">(${esc(t.unit)})</span></h3><button class="icon-btn" id="closeBig">${icon("x")}</button></div>
      <div class="chart-big">${sparkline(t.points, { w: 760, h: 260, big: true })}</div>
      <div class="tbl"><table><thead><tr><th>Date</th><th>Value</th><th>Range</th><th>Status</th><th>Report</th></tr></thead><tbody>
      ${t.points.slice().reverse().map((p) => `<tr><td>${esc(fmtDate(p.date))}</td><td class="num"><b>${esc(num(p.value))}</b></td><td class="num">${p.ref_low ?? ""}–${p.ref_high ?? ""}</td>
        <td><span class="badge b-${esc(p.status)}">${STATUS_TXT[p.status]}</span></td><td><a href="#reports/${p.report_id}">${esc(p.filename)}</a></td></tr>`).join("")}</tbody></table></div>`;
    $("#closeBig").addEventListener("click", () => box.classList.add("hidden"));
    box.scrollIntoView({ behavior: "smooth" });
  }));
}
