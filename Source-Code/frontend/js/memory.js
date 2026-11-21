import { $, $$, api, confirmBox, emit, esc, fmtDate, state, toast } from "./core.js";

let el;
export async function show(root) {
  el = root;
  el.innerHTML = `<div class="wrap grid g2" style="align-items:start">
    <div class="card stack">
      <div class="between"><h3>Long-term memory</h3><label class="switch" title="Memory on/off"><input type="checkbox" id="consent"><span></span></label></div>
      <p class="muted small" id="consentText" style="margin:0"></p>
      <form id="profileForm" class="stack">
        <label class="field">Sex (used for sex-specific ranges)<select name="sex"><option value="">Not set</option><option value="female">Female</option><option value="male">Male</option></select></label>
        <label class="field">Age group<select name="age_group"><option value="">Not set</option><option>18–30</option><option>31–45</option><option>46–60</option><option>61–75</option><option>75+</option></select></label>
        <label class="field">Default explanation<select name="preferred_view"><option value="">Not set</option><option value="patient">Simple</option><option value="doctor">For my doctor</option></select></label>
        <label class="field">Preferred language<select name="preferred_language"><option value="">Not set</option>${Object.entries(state.config.languages).map(([k, v]) => `<option value="${k}">${esc(v.native)}</option>`).join("")}</select></label>
        <label class="field">Notes to remember<input name="notes" maxlength="200" placeholder="e.g. vegetarian, on thyroid medicine"></label>
        <div><button class="btn primary" type="submit">Save profile</button></div>
      </form>
    </div>
    <div class="card stack">
      <h3>What NeuroLens remembers</h3>
      <div id="memItems" class="stack" style="gap:.5rem"></div>
      <div class="banner crit" style="justify-content:space-between;align-items:center"><div><b>Delete my data</b><div class="small">All chats, reports, scans, PDFs, memory and your profile.</div></div>
        <button class="btn danger" id="deleteAll">Delete everything</button></div>
    </div></div>`;
  render(await api("/api/memory"));
  $("#consent").addEventListener("change", async (e) => {
    if (!e.target.checked && !(await confirmBox("Turn memory off?", "Your saved profile and memory items will be deleted.", "Turn off", true))) { e.target.checked = true; return; }
    render(await api("/api/memory/consent", { json: { consent: e.target.checked } }));
    toast(e.target.checked ? "Memory turned on" : "Memory turned off and cleared");
  });
  $("#profileForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const data = Object.fromEntries(new FormData(e.target));
      render(await api("/api/memory/profile", { json: data }));
      toast("Profile saved");
      if (data.preferred_language) { state.lang = data.preferred_language; localStorage.setItem("nl_lang", state.lang); $("#langSel").value = state.lang; emit("lang"); }
    } catch (err) { toast(err.message); }
  });
  $("#deleteAll").addEventListener("click", async () => {
    if (!(await confirmBox("Delete everything?", "All your chats, reports, scans, PDFs, memory and profile will be removed. This cannot be undone.", "Delete everything", true))) return;
    const r = await api("/api/memory/all", { method: "DELETE" });
    toast(r.message); state.thread = null; state.attach = { report: null, mri: null }; emit("threads"); render(await api("/api/memory"));
  });
}

function render(m) {
  $("#consent").checked = m.consent;
  $("#consentText").textContent = m.consent
    ? "On. NeuroLens remembers your profile and short summaries of past reports and scans. You can delete any item, or everything."
    : "Off. NeuroLens only uses the current chat (its last 10 messages). Turn memory on to keep your profile and report summaries between visits.";
  $("#profileForm").classList.toggle("disabled", !m.consent);
  const f = $("#profileForm");
  ["sex", "age_group", "preferred_view", "preferred_language", "notes"].forEach((k) => (f.elements[k].value = (m.profile || {})[k] || ""));
  $("#memItems").innerHTML = m.items.length ? m.items.map((i) => `<div class="mem-item"><div><small>${esc(i.kind)} · ${esc(fmtDate(i.created_at))}</small>${esc(i.content)}</div>
    <button class="btn ghost sm" data-del="${i.id}">Forget</button></div>`).join("")
    : `<p class="muted small">${m.consent ? "Nothing saved yet. Upload a report or say “remember that …” in the chat." : "Memory is off, so nothing is stored long-term."}</p>`;
  $$("[data-del]", el).forEach((b) => b.addEventListener("click", async () => { await api(`/api/memory/items/${b.dataset.del}`, { method: "DELETE" }); render(await api("/api/memory")); }));
}
