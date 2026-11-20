import { $, $$, SR, api, download, emit, esc, fmtDate, icon, listen, makePdf, md, modal, on, safeUrl, speak, state, toast, upload } from "./core.js";

let el = null;
let rec = null;
const SUGGEST = ["What does a high TSH mean?", "Explain HbA1c in simple words", "What is a meningioma?", "Recent research on glioma MRI diagnosis"];

function build(root) {
  el = root;
  el.innerHTML = `<div class="chat" id="chatLog"></div>
    <div class="composer" style="position:relative">
      <div class="attached" id="attached"></div>
      <div class="box">
        <button class="icon-btn" id="attachBtn" title="Attach a report or MRI">${icon("clip")}</button>
        <textarea id="chatInput" rows="1" maxlength="3000" placeholder="Ask about a result, a term or your scan… (Shift+Enter for a new line)"></textarea>
        <button class="icon-btn mic ${SR ? "" : "hidden"}" id="micBtn" title="Speak your question">${icon("mic")}</button>
        <button class="btn primary" id="sendBtn" title="Send">${icon("send")}</button>
      </div>
      <input type="file" id="chatRep" class="hidden" accept=".pdf,.txt,.csv,.png,.jpg,.jpeg,.webp">
      <input type="file" id="chatMri" class="hidden" accept=".png,.jpg,.jpeg">
    </div>`;
  $("#sendBtn").addEventListener("click", () => send($("#chatInput").value));
  $("#chatInput").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(e.target.value); } });
  $("#chatInput").addEventListener("input", grow);
  $("#micBtn").addEventListener("click", () => {
    if (rec) { rec.stop(); rec = null; return; }
    const base = $("#chatInput").value ? $("#chatInput").value + " " : "";
    rec = listen((t, final) => { $("#chatInput").value = base + t; grow(); if (final) rec = null; }, $("#micBtn"));
  });
  $("#attachBtn").addEventListener("click", openAttachMenu);
  $("#chatRep").addEventListener("change", async (e) => { const f = e.target.files[0]; e.target.value = ""; if (f) uploadAndAttach("/api/reports", f, "report"); });
  $("#chatMri").addEventListener("change", async (e) => { const f = e.target.files[0]; e.target.value = ""; if (f) uploadAndAttach("/api/mri", f, "mri"); });
  empty();
}
function grow() { const t = $("#chatInput"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 160) + "px"; }

function empty() {
  $("#chatLog").innerHTML = `<div class="chat-empty"><h2>What would you like to understand?</h2>
    <p class="muted">Ask about a medical term, attach a report or scan with ${icon("clip")}, or ask for recent research. Answers come in ${esc(state.config?.languages?.[state.lang]?.name || "English")}.</p>
    <div class="chips">${SUGGEST.map((s) => `<button class="chip">${esc(s)}</button>`).join("")}</div></div>`;
  $$(".chat-empty .chip").forEach((b) => b.addEventListener("click", () => send(b.textContent)));
}

export function show(root, threadId) {
  if (!el) build(root);
  if (threadId && threadId !== state.thread) openThread(threadId);
  renderAttached();
  setTimeout(() => $("#chatInput")?.focus(), 50);
}

export function newChat() {
  state.thread = null;
  state.attach = { report: null, mri: null }; // a new chat never carries the previous chat's documents
  if (el) { empty(); renderAttached(); }
  emit("threads");
}

/** Used by the Reports and MRI pages: open a fresh chat about one document and ask a first question. */
export function startWith({ report = null, mri = null, message }) {
  newChat();
  state.attach = { report, mri };
  location.hash = "#chat";
  setTimeout(() => { renderAttached(); send(message); }, 30);
}

async function openThread(id) {
  try {
    const t = await api(`/api/chat/threads/${encodeURIComponent(id)}`);
    state.thread = id;
    state.attach = { report: t.attachments.report, mri: t.attachments.mri };
    $("#chatLog").innerHTML = "";
    for (const m of t.messages) (m.role === "user" ? addUser(m.content) : addBot({ answer: m.content, ...(m.meta || {}) }));
    if (!t.messages.length) empty();
    renderAttached();
    emit("threads");
  } catch (e) { toast(e.message); }
}

function renderAttached() {
  const a = [];
  if (state.attach.report) a.push(`<span class="att">${icon("lab")}${esc(state.attach.report.filename)}<button data-x="report" title="Remove">${icon("x")}</button></span>`);
  if (state.attach.mri) a.push(`<span class="att">${icon("brain")}${esc(state.attach.mri.filename)}<button data-x="mri" title="Remove">${icon("x")}</button></span>`);
  if (!$("#attached")) return;
  $("#attached").innerHTML = a.join("");
  $$("#attached [data-x]").forEach((b) => b.addEventListener("click", () => { state.attach[b.dataset.x] = null; renderAttached(); }));
}

async function uploadAndAttach(path, file, kind) {
  toast(`Uploading ${file.name}…`);
  try {
    const r = await upload(path, file);
    if (kind === "report" && r.status === "needs_review") {
      toast("Photo read. Please check the values first."); location.hash = `#reports/${r.id}`; return;
    }
    state.attach[kind] = { id: r.id, filename: r.filename };
    renderAttached(); toast(`${r.filename} attached`);
  } catch (e) { toast(e.message); }
}

async function openAttachMenu(ev) {
  ev.stopPropagation();
  $(".menu")?.remove();
  const menu = document.createElement("div");
  menu.className = "menu";
  menu.style.bottom = "70px"; menu.style.left = `max(1rem, calc((100% - 860px) / 2))`;
  menu.innerHTML = `<div class="skel"></div><div class="skel"></div>`;
  $(".composer").appendChild(menu);
  const close = (e) => { if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener("click", close); } };
  setTimeout(() => document.addEventListener("click", close), 0);
  try {
    const [reps, scans] = await Promise.all([api("/api/reports"), api("/api/mri")]);
    menu.innerHTML = `<div class="menu-h">Upload</div>
      <button class="li" data-up="rep">${icon("upload")}<span class="grow"><b>New blood or lab report</b><span class="tiny muted">PDF, text or a photo</span></span></button>
      <button class="li" data-up="mri">${icon("brain")}<span class="grow"><b>New brain MRI slice</b><span class="tiny muted">JPG or PNG</span></span></button>
      ${reps.length ? `<div class="menu-h">Your reports</div>` : ""}
      ${reps.slice(0, 8).map((r) => `<button class="li" data-rep="${r.id}" data-name="${esc(r.filename)}">${icon("lab")}<span class="grow"><b>${esc(r.filename)}</b><span class="tiny muted">${esc(fmtDate(r.report_date))} · ${r.summary.flagged} flagged</span></span>${r.status === "needs_review" ? `<span class="badge b-review">check values</span>` : ""}</button>`).join("")}
      ${scans.length ? `<div class="menu-h">Your MRI scans</div>` : ""}
      ${scans.slice(0, 5).map((s) => `<button class="li" data-mri="${s.id}" data-name="${esc(s.filename)}">${icon("brain")}<span class="grow"><b>${esc(s.filename)}</b><span class="tiny muted">${esc(s.display || "not classified")}</span></span></button>`).join("")}`;
    $$("[data-up]", menu).forEach((b) => b.addEventListener("click", () => { menu.remove(); $(b.dataset.up === "rep" ? "#chatRep" : "#chatMri").click(); }));
    $$("[data-rep]", menu).forEach((b) => b.addEventListener("click", () => { state.attach.report = { id: +b.dataset.rep, filename: b.dataset.name }; menu.remove(); renderAttached(); }));
    $$("[data-mri]", menu).forEach((b) => b.addEventListener("click", () => { state.attach.mri = { id: +b.dataset.mri, filename: b.dataset.name }; menu.remove(); renderAttached(); }));
  } catch (e) { menu.innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}

function addUser(text) {
  $(".chat-empty")?.remove();
  const d = document.createElement("div"); d.className = "msg user"; d.textContent = text;
  $("#chatLog").appendChild(d); scroll();
}
function scroll() { const c = $("#chatLog"); c.scrollTop = c.scrollHeight; }

function stepLine(s) {
  const bits = [];
  if (s.result) bits.push(s.result); if (s.via) bits.push(s.via);
  if (s.intents) bits.push(`intents: ${s.intents.join(", ")}`);
  if (s.subagents) bits.push(s.subagents.join(" · "));
  if (s.tools && s.tools.length) bits.push(s.tools.map((t) => t.via || t.tool).filter(Boolean).join(", "));
  if (s.engine) bits.push(s.engine);
  if (s.short_term !== undefined) bits.push(`${s.short_term} recent messages, ${s.long_term} long-term items${s.consent ? "" : " (memory off)"}`);
  if (s.passed !== undefined) bits.push(s.passed ? "passed" : `failed: ${(s.issues || []).join("; ")}`);
  if (s.violations && s.violations.length) bits.push(`softened: ${s.violations.join(", ")}`);
  return `<li><code>${esc(s.node)}</code> ${esc(bits.join(" — "))} <span class="muted">${s.ms ?? ""}${s.ms !== undefined ? " ms" : ""}</span></li>`;
}

function addBot(r, replace) {
  const d = replace || document.createElement("div");
  d.className = "msg bot";
  const v = r.verify;
  const personal = (r.intent || []).some((i) => i === "lab_report" || i === "mri");
  const tags = [];
  if (r.status === "blocked") tags.push(`<span class="badge b-error">Safety response</span>`);
  else if (r.export) tags.push(`<span class="badge b-accent">${icon("pdf")} PDF ready</span>`);
  else if (v) tags.push(v.passed ? `<span class="badge b-ok">${icon("check")} ${personal ? "Verified against your data" : "Checked against sources"}</span>` : `<span class="badge b-warn">Safe summary shown</span>`);
  if (r.engine && !r.export) tags.push(`<span class="badge" title="Which model wrote this">${esc(r.engine)}</span>`);
  if (r.language && r.language !== "en") tags.push(`<span class="badge">${esc(state.config.languages[r.language]?.native || r.language)}</span>`);
  if (r.audience === "doctor") tags.push(`<span class="badge">Doctor view</span>`);
  const cited = new Set([...String(r.english || r.answer || "").matchAll(/[\[【](\d{1,2})[\]】]/g)].map((m) => +m[1]));
  const src = (r.sources || []).slice(0, 8).map((s, i) => (cited.size && !cited.has(i + 1) ? "" : `<a href="${safeUrl(s.url)}" target="_blank" rel="noopener" title="${esc(s.source || "")}"><b>[${i + 1}]</b>${esc(s.title)}</a>`)).join("");
  const steps = (r.steps || []).map(stepLine).join("");
  const exportCard = r.export ? `<div class="export-card"><div class="ico">${icon("pdf")}</div><div class="grow"><b>${esc(r.export.filename)}</b><div class="tiny muted">Link valid for 24 hours</div></div>
      <button class="btn primary sm" data-dl="${esc(r.export.token)}" data-fn="${esc(r.export.filename)}">${icon("dl")} Download</button></div>` : "";
  const note = r.translation_note && !r.english ? `<div class="banner warn tiny" style="margin-top:.5rem">${esc(r.translation_note)}</div>` : "";
  d.innerHTML = `<div class="body">${md(r.answer)}</div>${exportCard}${note}${src ? `<div class="sources">${src}</div>` : ""}
    <div class="msg-actions">${tags.join("")}<span class="sp"></span>
      ${r.english ? `<button class="btn ghost sm" data-en>Show English</button>` : ""}
      ${!r.export && r.status !== "blocked" && (state.attach.report || state.attach.mri) && personal ? `<button class="icon-btn" data-pdf title="Download PDF summary">${icon("pdf")}</button>` : ""}
      <button class="icon-btn" data-speak title="Read aloud">${icon("speak")}</button>
      <button class="icon-btn" data-copy title="Copy">${icon("copy")}</button>
      ${r.trace_id && r.status !== "blocked" ? `<button class="icon-btn" data-fb="1" title="Helpful">${icon("up")}</button><button class="icon-btn" data-fb="-1" title="Not helpful">${icon("down")}</button>` : ""}
    </div>
    ${steps ? `<details class="how"><summary>How I answered</summary><ul class="steps">${steps}</ul>${r.providers?.length ? `<p class="muted">Models: ${esc(r.providers.join(" → "))}</p>` : ""}</details>` : ""}`;
  if (!replace) $("#chatLog").appendChild(d);
  const body = $(".body", d);
  let showingEnglish = false;
  $("[data-en]", d)?.addEventListener("click", (e) => { showingEnglish = !showingEnglish; body.innerHTML = md(showingEnglish ? r.english : r.answer); e.target.textContent = showingEnglish ? "Show translation" : "Show English"; });
  $("[data-dl]", d)?.addEventListener("click", (e) => download(`/api/exports/${e.currentTarget.dataset.dl}`, e.currentTarget.dataset.fn).catch((err) => toast(err.message)));
  $("[data-pdf]", d)?.addEventListener("click", (e) => makePdf({ report_id: state.attach.report?.id, mri_id: state.attach.mri?.id, button: e.currentTarget }));
  $("[data-speak]", d).addEventListener("click", (e) => speak(body.innerText, e.currentTarget));
  $("[data-copy]", d).addEventListener("click", () => navigator.clipboard?.writeText(body.innerText).then(() => toast("Copied")));
  $$("[data-fb]", d).forEach((b) => b.addEventListener("click", async () => {
    const rating = b.classList.contains("on") ? 0 : +b.dataset.fb;
    let comment = "";
    if (rating === -1) comment = (await askComment()) || "";
    try {
      await api("/api/chat/feedback", { json: { trace_id: r.trace_id, rating, comment } });
      $$("[data-fb]", d).forEach((x) => x.classList.toggle("on", rating !== 0 && x === b));
      toast(rating ? "Thanks for the feedback" : "Feedback removed");
    } catch (err) { toast(err.message); }
  }));
  scroll();
}

async function askComment() {
  let val = "";
  await modal({
    title: "What was wrong?", ok: "Send", cancel: "Skip",
    body: `<textarea class="input" id="fbText" rows="3" maxlength="500" placeholder="Optional: too long, wrong value, confusing…"></textarea>`,
    onOpen: { init: (bg) => $("#fbText", bg).focus(), submit: (bg) => { val = $("#fbText", bg).value; return true; } },
  });
  return val;
}

async function send(text) {
  text = (text || "").trim();
  if (!text || state.busy) return;
  state.busy = true; $("#sendBtn").disabled = true; $("#chatInput").value = ""; grow();
  addUser(text);
  const typing = document.createElement("div");
  typing.className = "msg bot";
  typing.innerHTML = `<span class="typing"><i></i><i></i><i></i></span> <span class="muted small">Checking safety, planning, running tools, verifying${state.lang !== "en" ? ", translating" : ""}…</span>`;
  $("#chatLog").appendChild(typing); scroll();
  try {
    const r = await api("/api/chat", { json: { message: text, thread_id: state.thread, audience: state.audience, language: state.lang,
      report_id: state.attach.report?.id ?? null, mri_id: state.attach.mri?.id ?? null } });
    const fresh = state.thread !== r.thread_id;
    state.thread = r.thread_id;
    state.attach = { report: r.attachments?.report || null, mri: r.attachments?.mri || null };
    renderAttached();
    addBot(r, typing);
    if (fresh) history.replaceState(null, "", `#chat/${r.thread_id}`);
    emit("threads");
  } catch (err) { typing.innerHTML = `<p class="err">${esc(err.message)}</p>`; }
  finally { state.busy = false; $("#sendBtn").disabled = false; $("#chatInput").focus(); }
}

on("lang", () => { if (el && $(".chat-empty")) empty(); });
