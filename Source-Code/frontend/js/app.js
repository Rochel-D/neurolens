import { $, $$, api, confirmBox, emit, esc, go, icon, on, state, toast } from "./core.js";
import * as admin from "./admin.js";
import * as chat from "./chat.js";
import * as home from "./home.js";
import * as memory from "./memory.js";
import * as mri from "./mri.js";
import * as reports from "./reports.js";

const VIEWS = { home, chat, reports, mri, memory, admin };
const TITLES = { home: "Home", chat: "Assistant", reports: "Reports", mri: "Brain MRI", memory: "Memory", admin: "Admin" };
const NAV = [["home", "home", "Home"], ["chat", "chat", "Assistant"], ["reports", "lab", "Reports"], ["mri", "brain", "Brain MRI"], ["memory", "memory", "Memory"], ["admin", "shield", "Admin"]];

/* ---------------- theme ---------------- */
function applyTheme(t) {
  if (t === "auto") document.documentElement.removeAttribute("data-theme"); else document.documentElement.dataset.theme = t;
  const dark = t === "dark" || (t === "auto" && matchMedia("(prefers-color-scheme: dark)").matches);
  $("#themeBtn").innerHTML = icon(dark ? "sun" : "moon");
}
let theme = localStorage.getItem("nl_theme") || "auto";
applyTheme(theme);
$("#themeBtn").addEventListener("click", () => {
  const dark = document.documentElement.dataset.theme === "dark" || (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
  theme = dark ? "light" : "dark"; localStorage.setItem("nl_theme", theme); applyTheme(theme);
});

/* ---------------- auth ---------------- */
let authMode = "login";
$$("#authTabs button").forEach((b) => b.addEventListener("click", () => {
  authMode = b.dataset.tab;
  $$("#authTabs button").forEach((x) => x.classList.toggle("on", x === b));
  $$(".reg-only").forEach((x) => x.classList.toggle("hidden", authMode !== "register"));
  $("#authSubmit").textContent = authMode === "login" ? "Sign in" : "Create account";
  $("#authErr").textContent = "";
}));
$("#authForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const body = { email: f.get("email"), password: f.get("password") };
  if (authMode === "register") body.name = f.get("name");
  $("#authSubmit").disabled = true; $("#authErr").textContent = "";
  try {
    const r = await api(`/api/auth/${authMode}`, { json: body });
    state.token = r.token; sessionStorage.setItem("nl_token", r.token); await boot();
  } catch (err) { $("#authErr").textContent = err.message; }
  finally { $("#authSubmit").disabled = false; }
});
function logout() {
  state.token = null; state.user = null; sessionStorage.removeItem("nl_token");
  $("#app").classList.add("hidden"); $("#auth").classList.remove("hidden");
  $("#authForm").reset(); $('#authTabs button[data-tab="login"]').click(); // signing out always returns to "Sign in"
  state.thread = null; state.attach = { report: null, mri: null };
}
on("logout", logout);
$("#logout").innerHTML = icon("out");
$("#logout").addEventListener("click", logout);

/* ---------------- top bar controls ---------------- */
function setAudience(a) { state.audience = a; $$("#audience button").forEach((b) => b.classList.toggle("on", b.dataset.aud === a)); }
$$("#audience button").forEach((b) => b.addEventListener("click", () => setAudience(b.dataset.aud)));
$("#langSel").addEventListener("change", (e) => { state.lang = e.target.value; localStorage.setItem("nl_lang", state.lang); emit("lang"); });

/* ---------------- threads ---------------- */
export async function loadThreads() {
  try {
    const t = await api("/api/chat/threads");
    $("#threadList").innerHTML = t.map((x) => `<div class="thread ${x.thread_id === state.thread ? "on" : ""}">
      <button class="t" data-t="${esc(x.thread_id)}" title="${esc(x.title)}">${x.has_attachment ? "📎 " : ""}${esc(x.title) || "Chat"}</button>
      <button class="icon-btn del" data-del="${esc(x.thread_id)}" title="Delete chat">${icon("trash")}</button></div>`).join("")
      || `<p class="tiny muted" style="padding:.3rem .65rem">No chats yet</p>`;
    $$("#threadList [data-t]").forEach((b) => b.addEventListener("click", () => go(`#chat/${b.dataset.t}`)));
    $$("#threadList [data-del]").forEach((b) => b.addEventListener("click", async () => {
      if (!(await confirmBox("Delete this chat?", "The messages in this chat will be removed. Reports and scans stay.", "Delete", true))) return;
      await api(`/api/chat/threads/${encodeURIComponent(b.dataset.del)}`, { method: "DELETE" });
      if (state.thread === b.dataset.del) { chat.newChat(); go("#chat"); }
      loadThreads();
    }));
  } catch { /* ignore */ }
}
on("threads", loadThreads);
$("#newChat").addEventListener("click", () => { chat.newChat(); go("#chat"); });

/* ---------------- routing ---------------- */
function route() {
  if (!state.user) return;
  const [name, arg] = (location.hash.slice(1) || "home").split("/");
  const view = VIEWS[name] && (name !== "admin" || state.user.role === "admin") ? name : "home";
  $$("#nav button").forEach((b) => b.classList.toggle("on", b.dataset.view === view));
  $$(".view").forEach((v) => v.classList.toggle("hidden", v.id !== `view-${view}`));
  $("#viewTitle").textContent = TITLES[view];
  $("#audience").classList.toggle("hidden", !["chat", "reports", "mri"].includes(view));
  VIEWS[view].show($(`#view-${view}`), arg ? decodeURIComponent(arg) : null);
}
window.addEventListener("hashchange", route);
on("route", route);

async function boot() {
  if (!state.token) { $("#auth").classList.remove("hidden"); return; }
  try { [state.user, state.config] = await Promise.all([api("/api/auth/me"), api("/api/config")]); } catch { logout(); return; }
  $("#auth").classList.add("hidden"); $("#app").classList.remove("hidden");
  $("#meName").textContent = state.user.name; $("#meEmail").textContent = state.user.email;
  $("#meAvatar").textContent = (state.user.name || "?").trim()[0].toUpperCase();
  $("#nav").innerHTML = NAV.filter(([v]) => v !== "admin" || state.user.role === "admin")
    .map(([v, ic, label]) => `<button data-view="${v}">${icon(ic)}<span>${label}</span><span class="count" id="nav-${v}"></span></button>`).join("");
  $$("#nav button").forEach((b) => b.addEventListener("click", () => go(`#${b.dataset.view}`)));
  const langs = state.config.languages;
  $("#langSel").innerHTML = Object.entries(langs).map(([k, v]) => `<option value="${k}">${esc(v.native)}</option>`).join("");
  try {
    const m = await api("/api/memory");
    if (m.profile?.preferred_view) setAudience(m.profile.preferred_view);
    if (m.profile?.preferred_language && !localStorage.getItem("nl_lang")) state.lang = m.profile.preferred_language;
  } catch { /* ignore */ }
  if (!langs[state.lang]) state.lang = "en";
  $("#langSel").value = state.lang;
  loadThreads();
  route();
}
boot();
