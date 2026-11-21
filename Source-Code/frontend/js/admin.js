import { $, $$, api, confirmBox, download, esc, fmtDate, go, icon, modal, toast } from "./core.js";

const TABS = [["overview", "Overview"], ["keys", "API keys"], ["traces", "Traces"], ["feedback", "Feedback"], ["kb", "Knowledge base"], ["system", "System"]];
let el, tab = "overview";

export function show(root, arg) {
  el = root;
  tab = TABS.some(([k]) => k === arg) ? arg : "overview";
  el.innerHTML = `<div class="wrap"><div class="tabs">${TABS.map(([k, l]) => `<button data-t="${k}" class="${k === tab ? "on" : ""}">${l}</button>`).join("")}</div><div id="adm"></div></div>`;
  $$(".tabs button", el).forEach((b) => b.addEventListener("click", () => go(`#admin/${b.dataset.t}`)));
  ({ overview, keys, traces, feedback, kb, system })[tab]();
}
const box = () => $("#adm");
const loading = () => (box().innerHTML = `<div class="card"><div class="skel"></div><div class="skel"></div><div class="skel"></div></div>`);

/* ---------------- overview ---------------- */
async function overview() {
  loading();
  const o = await api("/api/admin/overview");
  const max = Math.max(1, ...o.per_day.map((d) => d.runs));
  const imax = Math.max(1, ...Object.values(o.intents));
  const emax = Math.max(1, ...Object.values(o.engines));
  const fbTotal = o.feedback.up + o.feedback.down;
  box().innerHTML = `<div class="stack">
    <div class="grid g4"><div class="stat"><b>${o.totals.users}</b><span>users</span></div><div class="stat"><b>${o.totals.reports}</b><span>reports</span></div>
      <div class="stat"><b>${o.totals.runs_7d}</b><span>agent runs (7 days)</span></div><div class="stat"><b>${(o.latency.p50 / 1000).toFixed(1)}s</b><span>median answer time · p95 ${(o.latency.p95 / 1000).toFixed(1)}s</span></div></div>
    <div class="grid g2">
      <div class="card"><h3>Agent runs per day</h3><p class="tiny muted">Red = errors</p><div class="bars">${o.per_day.map((d) => `<div title="${d.runs} runs, ${d.errors} errors, ${d.blocked} blocked, ${d.fallbacks} verifier fallbacks">
        <span>${d.runs || ""}</span><i style="height:${(d.runs / max) * 100}%"></i>${d.errors ? `<i class="err" style="height:${(d.errors / max) * 100}%"></i>` : ""}<span>${esc(d.date.slice(5))}</span></div>`).join("")}</div></div>
      <div class="card"><h3>User feedback (7 days)</h3>${fbTotal ? `<div class="big-label">${Math.round((o.feedback.up / fbTotal) * 100)}% helpful</div><p class="muted small">${o.feedback.up} 👍 · ${o.feedback.down} 👎</p>` : `<p class="muted">No ratings yet.</p>`}
        <button class="btn sm" id="toFb">See comments</button></div>
      <div class="card"><h3>What people ask</h3>${Object.entries(o.intents).map(([k, v]) => `<div class="hbar"><span>${esc(k)}</span><div class="bar"><i style="width:${(v / imax) * 100}%"></i></div><span class="num">${v}</span></div>`).join("") || `<p class="muted">No runs yet.</p>`}</div>
      <div class="card"><h3>Which engine answered</h3>${Object.entries(o.engines).map(([k, v]) => `<div class="hbar"><span title="${esc(k)}" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(k)}</span><div class="bar"><i style="width:${(v / emax) * 100}%"></i></div><span class="num">${v}</span></div>`).join("") || `<p class="muted">No runs yet.</p>`}</div>
    </div></div>`;
  $("#toFb").addEventListener("click", () => go("#admin/feedback"));
}

/* ---------------- key pool controls ---------------- */
async function keys() {
  loading();
  const k = await api("/api/admin/keys");
  const providers = [...new Set(k.slots.map((s) => s.provider))];
  box().innerHTML = `<div class="stack">
    <div class="card"><div class="between"><div><h3>LLM key pool</h3><p class="muted small" style="margin:0">Tried in order. A failing key cools down and the next one answers. With model “auto”, each key asks its provider which models it can use.</p></div>
      <div class="row"><button class="btn" id="resetAll">${icon("refresh")} Reset all cooldowns</button><button class="btn primary" id="testAll">${icon("check")} Test all keys</button></div></div></div>
    ${k.slots.length ? "" : `<div class="banner warn">No API keys in .env — answers use the offline template mode. Add GEMINI_API_KEY_1..3 and GROK_API_KEY_1..3, then restart.</div>`}
    ${providers.map((p) => `<div class="card"><div class="between"><h3>${esc(p === "gemini" ? "Gemini" : p === "groq" ? "Groq" : "xAI Grok")} model</h3>
      <form class="row" data-prov="${esc(p)}"><input class="input" name="m" style="width:280px" placeholder="auto (recommended)" value="${esc((k.slots.find((s) => s.provider === p)?.preferred || []).join(", "))}">
      <button class="btn sm">Save</button></form></div><p class="tiny muted" style="margin:.3rem 0 0">Leave empty for auto-detect, or give 1–2 names separated by commas (pick from the detected list below). .env setting: ${esc((k.config[p] || []).join(", "))}</p></div>`).join("")}
    <div class="grid g2">${k.slots.map(slotCard).join("")}</div></div>`;
  $("#resetAll")?.addEventListener("click", async () => { await api("/api/admin/keys/all/reset", { json: {} }); toast("Cooldowns reset"); keys(); });
  $("#testAll")?.addEventListener("click", async (e) => { e.currentTarget.disabled = true; for (const s of k.slots) await testSlot(s.slot, false); keys(); });
  $$("[data-prov]").forEach((f) => f.addEventListener("submit", async (e) => {
    e.preventDefault();
    const models = f.elements.m.value.split(",").map((x) => x.trim()).filter(Boolean);
    const r = await api("/api/admin/models", { json: { provider: f.dataset.prov, models } }); toast(`Saved: ${r.models.join(", ")}`); keys();
  }));
  $$("[data-test]").forEach((b) => b.addEventListener("click", async () => { b.disabled = true; b.innerHTML = `<span class="spinner"></span>`; await testSlot(b.dataset.test, true); keys(); }));
  $$("[data-reset]").forEach((b) => b.addEventListener("click", async () => { await api(`/api/admin/keys/${encodeURIComponent(b.dataset.reset)}/reset`, { json: {} }); keys(); }));
  $$("[data-enable]").forEach((c) => c.addEventListener("change", async () => { await api(`/api/admin/keys/${encodeURIComponent(c.dataset.enable)}/enabled`, { json: { enabled: c.checked } }); keys(); }));
}
function slotCard(s) {
  const st = s.status === "ready" ? "b-ok" : s.status === "disabled" ? "" : "b-warn";
  return `<div class="slot ${s.enabled ? "" : "off"}"><div class="between"><div class="row"><b>${esc(s.slot)}</b><span class="badge ${st}">${esc(s.status)}</span></div>
    <label class="switch" title="Use this key"><input type="checkbox" data-enable="${esc(s.slot)}" ${s.enabled ? "checked" : ""}><span></span></label></div>
    <div class="tiny muted" style="margin:.3rem 0">${esc(s.host)} · key ${esc(s.key)} · ${s.successes} ok / ${s.failures} failed</div>
    <div class="small">Answering model: <b>${esc(s.active_model || "not yet chosen")}</b></div>
    ${s.models_cooling && s.models_cooling.length ? `<div class="small muted">Resting (quota/timeout): ${esc(s.models_cooling.join(", "))}</div>` : ""}
    ${s.last_error ? `<div class="banner crit tiny" style="margin-top:.4rem">${esc(s.last_error)}</div>` : ""}
    ${s.discovered.length ? `<details class="tiny" style="margin-top:.4rem"><summary>Detected models (${s.discovered.length})</summary><div class="code">${s.discovered.map(esc).join("<br>")}</div></details>` : ""}
    ${s.log.length ? `<div class="log">${s.log.slice().reverse().map((l) => `<div><span class="muted">${esc(l.t)}</span><span class="${l.ok ? "ok" : "bad"}">${l.ok ? "✓" : "✗"}</span><span class="grow">${esc(l.model)}${l.ok ? ` · ${l.ms ?? 0} ms` : ` · ${esc(l.status ?? "")} ${esc(l.error || "")}`}</span></div>`).join("")}</div>` : ""}
    <div class="row" style="margin-top:.6rem"><button class="btn sm primary" data-test="${esc(s.slot)}">${icon("check")} Test key</button><button class="btn sm" data-reset="${esc(s.slot)}">${icon("refresh")} Reset</button></div></div>`;
}
async function testSlot(slot, announce) {
  try {
    const r = await api(`/api/admin/keys/${encodeURIComponent(slot)}/test`, { json: {} });
    if (announce) toast(r.ok ? `${slot}: works with ${r.model} (“${r.reply}”)` : `${slot}: ${(r.errors || ["no model answered"])[0]}`);
  } catch (e) { toast(e.message); }
}

/* ---------------- traces ---------------- */
const tf = { q: "", intent: "", status: "", verified: "", rating: "", offset: 0 };
async function traces() {
  box().innerHTML = `<div class="card stack"><form class="row" id="tfForm">
      <input class="input" name="q" placeholder="Search message or trace id" style="max-width:260px" value="${esc(tf.q)}">
      <select class="input" name="intent" style="width:auto"><option value="">All intents</option>${["lab_report", "mri", "term", "research", "memory", "export", "emergency", "self_harm", "injection"].map((x) => `<option ${tf.intent === x ? "selected" : ""}>${x}</option>`).join("")}</select>
      <select class="input" name="status" style="width:auto"><option value="">Any status</option>${["ok", "blocked", "error"].map((x) => `<option ${tf.status === x ? "selected" : ""}>${x}</option>`).join("")}</select>
      <select class="input" name="verified" style="width:auto"><option value="">Verified?</option><option value="yes" ${tf.verified === "yes" ? "selected" : ""}>verified</option><option value="no" ${tf.verified === "no" ? "selected" : ""}>fallback used</option></select>
      <select class="input" name="rating" style="width:auto"><option value="">Any rating</option><option value="up" ${tf.rating === "up" ? "selected" : ""}>👍</option><option value="down" ${tf.rating === "down" ? "selected" : ""}>👎</option></select>
      <button class="btn">${icon("search")} Filter</button><span class="grow"></span><button class="btn" type="button" id="csv">${icon("dl")} CSV</button></form>
    <div id="trTable"><div class="skel"></div></div></div>`;
  $("#tfForm").addEventListener("submit", (e) => { e.preventDefault(); Object.assign(tf, Object.fromEntries(new FormData(e.target)), { offset: 0 }); loadTraces(); });
  $("#csv").addEventListener("click", () => download(`/api/admin/traces.csv?${qs()}`, "neurolens-traces.csv").catch((e) => toast(e.message)));
  loadTraces();
}
const qs = () => new URLSearchParams(Object.fromEntries(Object.entries(tf).filter(([k, v]) => v !== "" && k !== "offset"))).toString();
async function loadTraces() {
  const d = await api(`/api/admin/traces?${qs()}&limit=40&offset=${tf.offset}`);
  const stepLine = (s) => `<li><code>${esc(s.node)}</code> ${esc([s.result, s.via, s.engine, s.subagents && s.subagents.join(" · "), s.passed === false ? "failed: " + (s.issues || []).join("; ") : s.passed ? "passed" : ""].filter(Boolean).join(" — "))} <span class="muted">${s.ms ?? ""} ms</span></li>`;
  $("#trTable").innerHTML = `<p class="tiny muted">${d.total} runs</p><div class="tbl"><table class="t"><thead><tr><th>When</th><th>Message</th><th>Intent</th><th>Status</th><th>Verified</th><th>Rating</th><th>Time</th></tr></thead><tbody>
    ${d.items.map((t, i) => `<tr class="click" data-i="${i}"><td class="tiny">${esc(fmtDate(t.created_at))}<br>${esc(new Date(t.created_at).toLocaleTimeString())}</td>
      <td style="max-width:320px">${esc(t.message.slice(0, 120))}${t.language !== "en" ? ` <span class="badge">${esc(t.language)}</span>` : ""}</td><td>${esc(t.intent)}</td>
      <td><span class="badge b-${t.status === "ok" ? "ok" : t.status === "blocked" ? "blocked" : "error"}">${esc(t.status)}</span></td><td>${t.verified ? "✓" : `<span class="badge b-warn">fallback</span>`}</td>
      <td>${t.rating > 0 ? "👍" : t.rating < 0 ? "👎" : ""}</td><td class="num">${(t.latency_ms / 1000).toFixed(1)}s</td></tr>
      <tr class="hidden" id="tr-${i}"><td colspan="7"><ul class="steps">${(t.steps || []).map(stepLine).join("")}</ul><p class="tiny muted">Models: ${esc((t.providers || []).join(" → ")) || "none"}${t.guard_flags?.length ? ` · guard flags: ${esc(t.guard_flags.join(", "))}` : ""}${t.comment ? ` · comment: “${esc(t.comment)}”` : ""}</p></td></tr>`).join("")}
    </tbody></table></div>
    <div class="row" style="justify-content:flex-end">${tf.offset ? `<button class="btn sm" id="prev">Previous</button>` : ""}${tf.offset + 40 < d.total ? `<button class="btn sm" id="next">Next</button>` : ""}</div>`;
  $$("tr.click").forEach((r) => r.addEventListener("click", () => $(`#tr-${r.dataset.i}`).classList.toggle("hidden")));
  $("#prev")?.addEventListener("click", () => { tf.offset -= 40; loadTraces(); });
  $("#next")?.addEventListener("click", () => { tf.offset += 40; loadTraces(); });
}

/* ---------------- feedback ---------------- */
async function feedback() {
  loading();
  const f = await api("/api/admin/feedback");
  box().innerHTML = `<div class="card"><div class="card-head"><h3>User feedback</h3><span class="muted small">${f.up} 👍 · ${f.down} 👎</span></div>
    ${f.items.length ? `<div class="tbl"><table class="t"><thead><tr><th></th><th>Question</th><th>Comment</th><th>Engine</th><th>When</th></tr></thead><tbody>
    ${f.items.map((x) => `<tr><td>${x.rating > 0 ? "👍" : "👎"}</td><td style="max-width:320px">${esc(x.message)}</td><td>${esc(x.comment) || `<span class="muted">—</span>`}</td>
      <td class="tiny">${esc(x.engine.replace("explain:", ""))}${x.verified ? "" : ` <span class="badge b-warn">fallback</span>`}</td><td class="tiny">${esc(fmtDate(x.created_at))}</td></tr>`).join("")}</tbody></table></div>`
    : `<div class="empty"><p>No feedback yet. Users rate answers with 👍 / 👎 under each reply.</p></div>`}</div>`;
}

/* ---------------- knowledge base editor ---------------- */
let kbKind = "lab";
async function kb() {
  box().innerHTML = `<div class="stack">
    <div class="card"><div class="between"><div class="seg"><button data-k="lab">Lab tests</button><button data-k="note">Knowledge notes</button></div>
      <div class="row"><input class="input" id="kbSearch" placeholder="Search…" style="width:220px"><button class="btn primary" id="kbNew">${icon("plus")} Add</button></div></div>
      <div id="kbList" class="mt"><div class="skel"></div></div></div>
    <div class="card"><h3>Test the parser</h3><p class="muted small">Paste lines from a report to see exactly what NeuroLens reads — useful after adding aliases.</p>
      <textarea class="input code" id="kbText" rows="4" placeholder="Haemoglobin  10.4  g/dL  12.0 - 15.5"></textarea>
      <div class="row mt"><button class="btn" id="kbParse">Parse</button></div><div id="kbParsed"></div></div></div>`;
  $$("[data-k]").forEach((b) => { b.classList.toggle("on", b.dataset.k === kbKind); b.addEventListener("click", () => { kbKind = b.dataset.k; kb(); }); });
  $("#kbNew").addEventListener("click", () => editItem(null));
  $("#kbSearch").addEventListener("input", () => renderKb());
  $("#kbParse").addEventListener("click", async () => {
    const r = await api("/api/admin/kb/test-parse", { json: { text: $("#kbText").value || " " } }).catch((e) => toast(e.message));
    if (!r) return;
    $("#kbParsed").innerHTML = `<div class="tbl"><table class="t"><thead><tr><th>Test</th><th>Value</th><th>Range</th><th>Status</th><th>From line</th></tr></thead><tbody>
      ${r.results.map((x) => `<tr><td>${esc(x.name)}</td><td class="num">${esc(x.value)} ${esc(x.unit)}</td><td class="num">${x.ref_low ?? ""}–${x.ref_high ?? ""} <span class="tiny muted">${esc(x.range_source)}</span></td><td><span class="badge b-${esc(x.status)}">${esc(x.status)}</span></td><td class="tiny code">${esc(x.raw)}</td></tr>`).join("")}</tbody></table></div>
      ${r.unmatched.length ? `<div class="banner warn tiny">Not recognised: ${r.unmatched.map(esc).join(" · ")}</div>` : ""}${r.report_date ? `<p class="tiny muted">Report date found: ${esc(r.report_date)}</p>` : ""}`;
  });
  kbItems = await api(`/api/admin/kb/${kbKind}`);
  renderKb();
}
let kbItems = [];
function renderKb() {
  const q = ($("#kbSearch")?.value || "").toLowerCase();
  const items = kbItems.filter((x) => !q || JSON.stringify(x).toLowerCase().includes(q));
  $("#kbList").innerHTML = kbKind === "lab"
    ? `<div class="tbl"><table class="t"><thead><tr><th>Test</th><th>Category</th><th>Unit</th><th>Range</th><th>Aliases</th><th></th></tr></thead><tbody>
      ${items.map((t) => `<tr class="click" data-id="${esc(t.id)}"><td><b>${esc(t.name)}</b> ${t._custom ? `<span class="badge b-accent">${t._seed ? "edited" : "custom"}</span>` : ""}</td><td>${esc(t.category)}</td><td>${esc(t.unit)}</td>
        <td class="num">${t.low ?? ""}–${t.high ?? ""}</td><td class="tiny muted">${esc(t.aliases.slice(0, 4).join(", "))}</td><td>${icon("edit")}</td></tr>`).join("")}</tbody></table></div>`
    : `<div class="list">${items.map((n) => `<button class="li" data-id="${esc(n.id)}"><span class="badge">${esc(n.module)}</span><span class="grow"><b>${esc(n.title)}</b><span class="tiny muted">${esc(n.text.slice(0, 110))}…</span></span>${n._custom ? `<span class="badge b-accent">${n._seed ? "edited" : "custom"}</span>` : ""}</button>`).join("")}</div>`;
  $$("#kbList [data-id]").forEach((r) => r.addEventListener("click", () => editItem(kbItems.find((x) => x.id === r.dataset.id))));
}
async function editItem(item) {
  const isLab = kbKind === "lab", isNew = !item;
  item = item || (isLab ? { id: "", name: "", aliases: [], unit: "", low: "", high: "", critical_low: "", critical_high: "", category: "", conversions: {}, what: "", low_means: "", high_means: "" } : { id: "", title: "", module: "general", text: "" });
  const v = (x) => esc(x ?? "");
  const body = isLab ? `<div class="kb-form">
      <label class="field">ID (lowercase, no spaces)<input name="id" value="${v(item.id)}" ${isNew ? "" : "readonly"}></label><label class="field">Name<input name="name" value="${v(item.name)}"></label>
      <label class="field full">Aliases — words printed on reports, comma separated<input name="aliases" value="${v(item.aliases.join(", "))}"></label>
      <label class="field">Unit<input name="unit" value="${v(item.unit)}"></label><label class="field">Category<input name="category" value="${v(item.category)}"></label>
      <label class="field">Range low<input name="low" type="number" step="any" value="${v(item.low)}"></label><label class="field">Range high<input name="high" type="number" step="any" value="${v(item.high)}"></label>
      <label class="field">Critical below<input name="critical_low" type="number" step="any" value="${v(item.critical_low)}"></label><label class="field">Critical above<input name="critical_high" type="number" step="any" value="${v(item.critical_high)}"></label>
      <label class="field full">Unit conversions to ${v(item.unit) || "the unit"} — e.g. mmol/l=18, g/l=0.1<input name="conversions" value="${v(Object.entries(item.conversions || {}).map(([k, f]) => `${k}=${f}`).join(", "))}"></label>
      <label class="field full">What it is (plain words)<textarea name="what" rows="2">${v(item.what)}</textarea></label>
      <label class="field full">If low<textarea name="low_means" rows="2">${v(item.low_means)}</textarea></label><label class="field full">If high<textarea name="high_means" rows="2">${v(item.high_means)}</textarea></label></div>`
    : `<div class="kb-form"><label class="field">ID<input name="id" value="${v(item.id)}" ${isNew ? "" : "readonly"}></label>
      <label class="field">Module<select name="module"><option value="general" ${item.module === "general" ? "selected" : ""}>general</option><option value="neuro" ${item.module === "neuro" ? "selected" : ""}>neuro</option></select></label>
      <label class="field full">Title<input name="title" value="${v(item.title)}"></label><label class="field full">Text (used by RAG)<textarea name="text" rows="7">${v(item.text)}</textarea></label></div>`;
  const extra = !isNew ? `<div class="row">${item._custom && item._seed ? `<button class="btn sm" type="button" id="kbReset">${icon("refresh")} Reset to default</button>` : ""}<button class="btn sm danger" type="button" id="kbDel">${icon("trash")} Delete</button></div>` : "";
  await modal({
    title: isNew ? `Add ${isLab ? "lab test" : "knowledge note"}` : `Edit ${item.name || item.title}`, ok: "Save", wide: true, body: body + `<p class="err" id="kbErr"></p>` + extra,
    onOpen: {
      init: (bg) => {
        $("#kbDel", bg)?.addEventListener("click", async () => { if (await confirmBox("Delete?", "It will no longer be used by the parser and RAG.", "Delete", true)) { await api(`/api/admin/kb/${kbKind}/${item.id}`, { method: "DELETE" }); bg.remove(); toast("Deleted"); kb(); } });
        $("#kbReset", bg)?.addEventListener("click", async () => { await api(`/api/admin/kb/${kbKind}/${item.id}/reset`, { json: {} }); bg.remove(); toast("Reset to default"); kb(); });
      },
      submit: async (bg) => {
        const f = Object.fromEntries($$("input,select,textarea", $(".kb-form", bg)).map((el) => [el.name, el.value]));
        if (isLab) {
          f.aliases = f.aliases.split(",").map((x) => x.trim()).filter(Boolean);
          f.conversions = Object.fromEntries(f.conversions.split(",").map((x) => x.split("=").map((y) => y.trim())).filter((p) => p[0] && p[1]));
          if (item.sex) f.sex = item.sex;
        }
        try { await api(`/api/admin/kb/${kbKind}`, { method: "PUT", json: f }); toast("Saved — the parser and RAG use it now"); kb(); return true; }
        catch (e) { $("#kbErr", bg).textContent = e.message; return false; }
      },
    },
  });
}

/* ---------------- system ---------------- */
async function system() {
  loading();
  const [rev, models] = await Promise.all([api("/api/admin/review"), api("/api/admin/models")]);
  box().innerHTML = `<div class="grid g2" style="align-items:start">
    <div class="card"><div class="card-head"><h3>System review</h3><span class="badge b-${esc(rev.overall)}">${esc(rev.overall)}</span></div>
      ${rev.checks.map((c) => `<div class="check"><span class="badge b-${esc(c.status)}">${esc(c.status)}</span><div><b>${esc(c.check)}</b><div class="tiny muted">${esc(c.detail)}</div></div></div>`).join("")}
      <button class="btn sm mt" id="rerun">${icon("refresh")} Run again</button></div>
    <div class="stack"><div class="card"><h3>Models</h3>
      <div class="check"><span class="badge b-${models.mri.loaded ? "pass" : "warn"}">${models.mri.loaded ? "ok" : "off"}</span><div><b>MRI classifier</b><div class="tiny muted">${esc(models.mri.loaded ? "EfficientNet-B0 loaded" : models.mri.error || "")}</div></div></div>
      <div class="check"><span class="badge b-${models.explainer.mode === "local" ? "pass" : "warn"}">${esc(models.explainer.mode)}</span><div><b>Fine-tuned explainer</b><div class="tiny muted">${esc(models.explainer.mode === "local" ? `${models.explainer.base_model} + ${models.explainer.sft_adapter} + ${models.explainer.dpo_adapter}${models.explainer.error ? " — error: " + models.explainer.error : ""}` : "EXPLAINER_MODE=router — the key pool writes the patient view with the training prompt")}</div></div></div></div>
      <div class="card"><h3>MCP server</h3><div id="mcp"><button class="btn" id="mcpBtn">Check MCP connection</button></div></div></div></div>`;
  $("#rerun").addEventListener("click", system);
  $("#mcpBtn").addEventListener("click", async () => {
    $("#mcp").innerHTML = `<p><span class="spinner"></span> Starting mcp-server-fetch over stdio…</p>`;
    const m = await api("/api/admin/mcp");
    $("#mcp").innerHTML = m.ok ? `<p><span class="badge b-ok">connected</span> ${esc(m.server)}</p>${m.tools.map((t) => `<div class="check"><code>${esc(t.name)}</code><span class="tiny muted">${esc(t.description)}</span></div>`).join("")}`
      : `<p><span class="badge b-warn">not connected</span> ${esc(m.error)}</p><p class="tiny muted">The app falls back to direct HTTPS fetches.</p>`;
  });
}
