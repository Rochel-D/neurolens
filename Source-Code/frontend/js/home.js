import { $, $$, api, esc, fmtDate, go, icon, sparkline, state } from "./core.js";

export async function show(el) {
  const hour = new Date().getHours();
  const greet = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
  el.innerHTML = `<div class="wrap stack"><div class="hello"><h1>${greet}, ${esc(state.user.name.split(" ")[0])}</h1>
    <p class="muted">Here is what NeuroLens knows about your reports.</p></div><div class="skel" style="height:90px"></div><div class="skel" style="height:220px"></div></div>`;
  let d;
  try { d = await api("/api/dashboard"); } catch (e) { el.querySelector(".wrap").insertAdjacentHTML("beforeend", `<p class="err">${esc(e.message)}</p>`); return; }
  const c = d.counts;
  const latest = d.latest_report;
  const crit = latest?.summary?.critical || 0;
  el.innerHTML = `<div class="wrap stack">
    <div class="hello"><h1>${greet}, ${esc(state.user.name.split(" ")[0])}</h1>
      <p class="muted">${c.reports ? "Here is a summary of your reports and scans." : "Start by uploading a blood report or a brain MRI slice."}</p></div>
    ${c.needs_review ? `<div class="banner warn">${icon("camera")}<div class="grow"><b>${c.needs_review} photo report${c.needs_review > 1 ? "s" : ""} need checking.</b> Compare the values with your paper report before NeuroLens explains them.</div><button class="btn sm" id="goReview">Check now</button></div>` : ""}
    ${crit ? `<div class="banner crit"><div class="grow"><b>Critical value in your latest report:</b> ${esc(latest.summary.critical_tests.join(", "))}. Please contact your doctor today.</div></div>` : ""}
    <div class="grid g4">
      <div class="stat"><b>${c.reports}</b><span>reports</span></div>
      <div class="stat"><b>${c.scans}</b><span>MRI scans</span></div>
      <div class="stat"><b>${c.chats}</b><span>chats</span></div>
      <div class="stat ${latest?.summary?.flagged ? "warn" : ""}"><b>${latest ? latest.summary.flagged : "—"}</b><span>outside range in latest report</span></div>
    </div>
    <div class="grid g3">
      <button class="action" data-go="#reports/upload"><div class="ico">${icon("lab")}</div><div><b>Upload a blood report</b><span>PDF, text or a phone photo</span></div></button>
      <button class="action" data-go="#mri"><div class="ico">${icon("brain")}</div><div><b>Check a brain MRI slice</b><span>Pattern + heatmap</span></div></button>
      <button class="action" data-go="#chat"><div class="ico">${icon("chat")}</div><div><b>Ask a question</b><span>Type or speak, in ${esc(state.config.languages[state.lang].native)}</span></div></button>
    </div>
    <div class="grid g2">
      <div class="card"><div class="card-head"><h3>Recent reports</h3><button class="btn ghost sm" data-go="#reports">See all</button></div>
        ${d.recent_reports.length ? `<div class="list">${d.recent_reports.map((r) => `<button class="li" data-go="#reports/${r.id}">${icon("lab")}
          <span class="grow"><b>${esc(r.filename)}</b><span class="tiny muted">${esc(fmtDate(r.report_date))} · ${r.summary.total} tests</span></span>
          ${r.status === "needs_review" ? `<span class="badge b-review">check values</span>` : r.summary.critical ? `<span class="badge b-critical_low">${r.summary.critical} critical</span>` : r.summary.flagged ? `<span class="badge b-high">${r.summary.flagged} flagged</span>` : `<span class="badge b-normal">all normal</span>`}</button>`).join("")}</div>`
          : `<div class="empty"><p>No reports yet.</p></div>`}</div>
      <div class="card"><div class="card-head"><h3>Trends</h3><button class="btn ghost sm" data-go="#reports/trends">Open trends</button></div>
        ${d.trends.length ? `<div class="list">${d.trends.map((t) => { const p = t.points, last = p[p.length - 1]; return `<button class="li" data-go="#reports/trends">
          <span class="grow"><b>${esc(t.name)}</b><span class="tiny muted">${p.length} results · latest ${esc(String(last.value))} ${esc(t.unit)}</span></span>
          <span style="width:120px">${sparkline(p, { w: 120, h: 36 })}</span>
          <span class="badge b-${t.direction === "better" ? "better" : t.direction === "worse" ? "worse" : "x"}">${esc(t.direction || "")}</span></button>`; }).join("")}</div>`
          : `<div class="empty"><p class="small">Upload two or more reports and NeuroLens charts how each value changes over time.</p></div>`}</div>
    </div>
    ${d.recent_scans.length ? `<div class="card"><div class="card-head"><h3>Recent MRI scans</h3><button class="btn ghost sm" data-go="#mri">Open</button></div>
      <div class="list">${d.recent_scans.map((s) => `<button class="li" data-go="#mri/${s.id}">${icon("brain")}<span class="grow"><b>${esc(s.filename)}</b>
      <span class="tiny muted">${esc(fmtDate(s.created_at))}</span></span><span class="badge">${s.available ? `${esc(s.display)} · ${Math.round(s.confidence * 100)}%` : "not classified"}</span></button>`).join("")}</div></div>` : ""}
    ${!d.memory_on ? `<div class="banner info">${icon("memory")}<div class="grow">Long-term memory is off. Turn it on to use sex-specific ranges and remember your past reports between visits.</div><button class="btn sm" data-go="#memory">Memory settings</button></div>` : ""}
  </div>`;
  $$("[data-go]", el).forEach((b) => b.addEventListener("click", () => go(b.dataset.go)));
  $("#goReview", el)?.addEventListener("click", () => go("#reports"));
}
